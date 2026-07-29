"""The core surface seam: the privileged SurfaceContext (admit + identity + workspace write) and the
durable WritebackPoller. A minimal recording surface stands in for a real extension surface — the
dependency, never the thing asserted; every assertion reads durable rows (turn, surface_identity,
writeback) and blob bytes that core wrote. The full end-to-end through a real registered surface is
proven by the sample-extension conformance probe and the Slack extension's own tests."""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import lz4.frame
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

import ufo.ext.surface as surface_module
from ufo.audience import (
    SHARED_AUDIENCE,
    conversation_audience,
    foreign_room_audience,
    room_audience,
)
from ufo.blob import FilesystemBlobStore
from ufo.credentials import (
    CredentialRequestInvalid,
    CredentialRequestState,
    CredentialStore,
    seal_credential_request,
)
from ufo.db import workspace_tx
from ufo.ext.surface import (
    OPERATOR_EMAIL_DOMAIN,
    WRITEBACK_CLAIMED,
    WRITEBACK_DELIVERED,
    WRITEBACK_FAILED,
    WRITEBACK_MAX_AGE_SECONDS,
    WRITEBACK_WORKSPACE_BATCH,
    SharedArtifact,
    SurfaceContext,
    SurfaceDeliveryError,
    SurfaceRoute,
    SurfaceSpec,
    Writeback,
    WritebackPoller,
    writeback_workspaces,
)
from ufo.hub import InProcessHub
from ufo.loop.queue import _load_turn
from ufo.models.interface import Message, TextBlock, ToolResultBlock, ToolUseBlock
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint
from ufo.schema import tables
from ufo.schema.records import WRITEBACK_PENDING, TerminalFrame, TurnContext
from ufo.surfaces.admission import Admission, MemberAdmission
from ufo.surfaces.hub_tail import HubTailer
from ufo.transcript import (
    CompactionSummary,
    CompactionWindow,
    Conversation,
    compaction_key,
    encode,
    transcript_key,
)
from ufo.workspace import ws

SURFACE = "test_surface"


@dataclass
class StubDbos:
    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)


@dataclass
class RecordingSurface:
    """A surface stand-in for the poller: `post` returns a canned ref (or raises when told to),
    `attach` records what it was handed. The poller's claim/lease/retry logic is what the tests
    assert, read back off the durable writeback row — this fake is only the delivery dependency."""

    ref: str = "posted-ref"
    fail_post: bool = False
    fail_attach_attempts: int = 0
    posted: list[UUID] = field(default_factory=list)
    attach_attempts: int = 0
    attached: list[tuple[UUID, str, tuple[str, ...]]] = field(default_factory=list)

    async def post(self, ctx: SurfaceContext, writeback: Writeback) -> str:
        self.posted.append(writeback.turn_id)
        if self.fail_post:
            raise RuntimeError("post failed")
        return self.ref

    async def attach(self, ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None:
        self.attach_attempts += 1
        if self.attach_attempts <= self.fail_attach_attempts:
            raise RuntimeError("attach failed")
        self.attached.append(
            (writeback.turn_id, reply_ref, tuple(a.filename for a in writeback.artifacts))
        )


@dataclass
class RetryAfterSurface(RecordingSurface):
    failures_remaining: int = 1
    retry_after_seconds: int = 17

    async def post(self, ctx: SurfaceContext, writeback: Writeback) -> str:
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise SurfaceDeliveryError(
                "chat.postMessage HTTP 429",
                retry_after_seconds=self.retry_after_seconds,
            )
        return self.ref


@dataclass
class BlockingSurface(RecordingSurface):
    blocked_workspace: UUID | None = None
    blocked: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    fast: asyncio.Event = field(default_factory=asyncio.Event)

    async def post(self, ctx: SurfaceContext, writeback: Writeback) -> str:
        self.posted.append(writeback.turn_id)
        if ctx.workspace_id == self.blocked_workspace:
            self.blocked.set()
            await self.release.wait()
        else:
            self.fast.set()
        return self.ref


async def _unused_ingest(ctx: SurfaceContext, request: object) -> object:
    raise AssertionError("ingest is not exercised by the poller tests")


async def _unused_identify(request: object, auth: object) -> None:
    raise AssertionError("identify is not exercised by the poller tests")


async def _seed(*, member_email: str | None = None) -> tuple[UUID, UUID, UUID | None]:
    workspace_id, agent_id = uuid4(), uuid4()
    member_id = uuid4() if member_email is not None else None
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model="claude-opus-4-8",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        if member_email is not None:
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email=member_email,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return workspace_id, agent_id, member_id


def _sandboxes(root: Path) -> ConversationSandbox:
    return ConversationSandbox(
        carrier=LocalCarrier(),
        backend="local",
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        proxy=ProxyEndpoint(port=1, ca_cert="test-ca"),
        workspace_root=root,
    )


def _context(
    workspace_id: UUID,
    dbos: StubDbos,
    blob: FilesystemBlobStore,
    sandboxes: ConversationSandbox | None = None,
) -> SurfaceContext:
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    return SurfaceContext(
        workspace_id=workspace_id,
        surface=SURFACE,
        blob=blob,
        _sandboxes=sandboxes if sandboxes is not None else _sandboxes(blob.root / "workspaces"),
        _admitter=MemberAdmission(
            workspace_id=workspace_id,
            admission=Admission(dbos=dbos, durable_surfaces=frozenset({SURFACE})),
        ),
        _tailer=HubTailer(hub=InProcessHub()),
        _credentials=store,
        _artifact_token_secret="artifact-token-secret",
        _public_base_url="https://ufo.example.test",
    )


async def _seed_turn(
    workspace_id: UUID,
    queue_key: str,
    status: str,
    text: str,
    artifacts: tuple[SharedArtifact, ...] = (),
    *,
    surface: str = SURFACE,
) -> UUID:
    conversation_id, turn_id = uuid4(), uuid4()
    terminal = (
        TerminalFrame(status=status, text=text).model_dump(mode="json")
        if status in ("done", "failed", "cancelled")
        else None
    )
    async with workspace_tx() as connection:
        agent_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == workspace_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface=surface,
                queue_key=queue_key,
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status=status,
                inbound="ask",
                terminal=terminal,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.writeback).values(
                turn_id=turn_id,
                workspace_id=workspace_id,
                status=WRITEBACK_PENDING,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        for artifact in artifacts:
            await connection.execute(
                sa.insert(tables.shared_artifact).values(
                    turn_id=turn_id,
                    blob_key=artifact.blob_key,
                    workspace_id=workspace_id,
                    filename=artifact.filename,
                    subject=artifact.subject,
                    media_type=artifact.media_type,
                    size_bytes=artifact.size_bytes,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return turn_id


async def _writeback(turn_id: UUID) -> sa.Row:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(
                    tables.writeback.c.status,
                    tables.writeback.c.reply_ref,
                    tables.writeback.c.claimed_by,
                    tables.writeback.c.claim_expires_at,
                    tables.writeback.c.last_error,
                ).where(tables.writeback.c.turn_id == turn_id)
            )
        ).one()


async def _set_writeback(turn_id: UUID, **values: object) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.writeback)
            .where(tables.writeback.c.turn_id == turn_id)
            .values(**values)
        )


def _poller(
    workspace_id: UUID, surface: RecordingSurface, blob: FilesystemBlobStore
) -> tuple[WritebackPoller, RecordingSurface]:
    context = _context(workspace_id, StubDbos(), blob)
    return _fleet_poller({workspace_id: context}, surface), surface


def _fleet_poller(
    contexts: dict[UUID, SurfaceContext],
    surface: RecordingSurface,
    worker_id: str = "worker-1",
) -> WritebackPoller:
    spec = SurfaceSpec(
        name=SURFACE,
        routes=(SurfaceRoute(method="POST", path="", handler=_unused_ingest),),
        identify=_unused_identify,
        post=surface.post,
        attach=surface.attach,
    )
    return WritebackPoller(
        worker_id=worker_id,
        surfaces={SURFACE: spec},
        context_for=lambda workspace_id, _name: contexts[workspace_id],
        candidates=writeback_workspaces(),
    )


def test_surface_delivery_error_rejects_negative_retry_delay() -> None:
    """The public SDK seam fails loud on a negative delay; without it a negative flows into the
    retry-at computation and re-claims the row immediately, busy-looping the failing surface."""
    with pytest.raises(ValueError, match="retry_after_seconds must be nonnegative"):
        SurfaceDeliveryError("rate limited", retry_after_seconds=-1)


async def test_writeback_due_index_is_installed(db: None) -> None:
    async with workspace_tx() as connection:
        dialect = connection.dialect.name
        indexes = await connection.run_sync(lambda sync: sa.inspect(sync).get_indexes("writeback"))
    due = next(index for index in indexes if index["name"] == "writeback_due")
    assert due["column_names"] == ["workspace_id", "created_at"]
    predicate = str(due["dialect_options"][f"{dialect}_where"])
    assert "pending" in predicate
    assert "claimed" in predicate


async def test_admit_queues_a_turn_and_registers_a_writeback(db: None, tmp_path) -> None:
    workspace_id, _, member_id = await _seed(member_email="bee@example.com")
    dbos = StubDbos()
    context = _context(workspace_id, dbos, FilesystemBlobStore(root=tmp_path))
    conversation_id = await context.conversation_for("C1:1.0", SHARED_AUDIENCE)
    admitted = await context.admit(
        conversation_id,
        "hello",
        idempotency_key="C1:1.0",
        speaker_member_id=member_id,
    )
    turn_id = admitted.turn_id
    assert admitted.opened_run
    assert dbos.enqueued == [str(turn_id)]
    async with workspace_tx() as connection:
        turn = (
            await connection.execute(
                sa.select(
                    tables.turn.c.status,
                    tables.turn.c.inbound,
                    tables.turn.c.speaker_member_id,
                    tables.conversation.c.member_id,
                )
                .select_from(tables.turn.join(tables.conversation))
                .where(tables.turn.c.id == turn_id)
            )
        ).one()
    assert turn.status == "queued"
    assert turn.inbound == "hello"
    assert turn.speaker_member_id == member_id
    assert turn.member_id is None
    assert (await _writeback(turn_id)).status == WRITEBACK_PENDING
    # A redelivery of the same message joins the one turn and one writeback.
    again = await context.admit(
        conversation_id,
        "hello",
        idempotency_key="C1:1.0",
        speaker_member_id=member_id,
    )
    assert again.turn_id == turn_id
    assert not again.opened_run


async def test_admit_refuses_a_speaker_from_another_workspace(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    _, _, foreign_member_id = await _seed(member_email="foreign@example.com")
    assert foreign_member_id is not None
    dbos = StubDbos()
    context = _context(workspace_id, dbos, FilesystemBlobStore(root=tmp_path))
    conversation_id = await context.conversation_for("C1:1.0", SHARED_AUDIENCE)

    with pytest.raises(ValueError, match="not a member of this workspace"):
        await context.admit(
            conversation_id,
            "hello",
            speaker_member_id=foreign_member_id,
        )

    assert dbos.enqueued == []
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert turns == 0


async def test_find_conversation_reads_without_creating(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await context.find_conversation("C1:1.0") is None
    async with workspace_tx() as connection:
        created = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.conversation))
        ).scalar_one()
    assert created == 0
    conversation_id = await context.conversation_for("C1:1.0", SHARED_AUDIENCE)
    assert await context.find_conversation("C1:1.0") == conversation_id
    assert await replace(context, surface="other").find_conversation("C1:1.0") is None


async def test_conversation_for_claims_a_memberless_conversation(db: None, tmp_path) -> None:
    """A conversation created before its speaker could resolve is claimed by the first resolving
    turn, and never re-claimed from the member who owns it."""
    workspace_id, _, member_id = await _seed(member_email="bee@example.com")
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    conversation_id = await context.conversation_for("D9", SHARED_AUDIENCE)

    async def _owner() -> UUID | None:
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(tables.conversation.c.member_id).where(
                        tables.conversation.c.id == conversation_id
                    )
                )
            ).scalar_one()

    assert await _owner() is None
    assert await context.conversation_for("D9", conversation_audience(member_id)) == conversation_id
    assert await _owner() == member_id
    admitted = await context.admit(
        conversation_id,
        "private",
        idempotency_key="D9:1",
        speaker_member_id=member_id,
    )
    _, _, audience = await _load_turn(admitted.turn_id)
    assert audience == conversation_audience(member_id)
    other_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=other_id,
                workspace_id=workspace_id,
                email="other@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with pytest.raises(ValueError, match="audience changed"):
        await context.conversation_for("D9", conversation_audience(other_id))
    assert await _owner() == member_id


async def test_conversation_audience_only_narrows(db: None, tmp_path: Path) -> None:
    workspace_id, _, _ = await _seed()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    room = room_audience("slack", "C1")
    foreign = foreign_room_audience("slack", "C1")
    conversation_id = await context.conversation_for("C1:1.0", SHARED_AUDIENCE)

    async def current_audience() -> str:
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(tables.conversation.c.audience).where(
                        tables.conversation.c.id == conversation_id
                    )
                )
            ).scalar_one()

    await context.conversation_for("C1:1.0", room)
    assert await current_audience() == room
    await context.conversation_for("C1:1.0", SHARED_AUDIENCE)
    assert await current_audience() == room
    await context.conversation_for("C1:1.0", foreign)
    assert await current_audience() == foreign
    await context.conversation_for("C1:1.0", room)
    assert await current_audience() == foreign


async def test_list_agents_orders_main_first_then_name(db: None, tmp_path) -> None:
    workspace_id, agent_id, _ = await _seed()
    await _seed()
    second = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=second,
                workspace_id=workspace_id,
                name="helpdesk",
                prompt="be helpful",
                model="claude-sonnet-5",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    listed = await context.list_agents()
    assert [(agent.name, agent.main) for agent in listed] == [
        ("assistant", True),
        ("helpdesk", False),
    ]
    assert listed[0].id == agent_id
    assert listed[0].model == "claude-opus-4-8"
    assert listed[1].id == second
    assert listed[1].model == "claude-sonnet-5"


async def test_conversation_for_binds_an_explicit_agent(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    second = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=second,
                workspace_id=workspace_id,
                name="helpdesk",
                prompt="be helpful",
                model="claude-sonnet-5",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    conversation_id = await context.conversation_for(
        f"{second}/bee@example.com", SHARED_AUDIENCE, agent_id=second
    )
    async with workspace_tx() as connection:
        bound = (
            await connection.execute(
                sa.select(tables.conversation.c.agent_id).where(
                    tables.conversation.c.id == conversation_id
                )
            )
        ).scalar_one()
    assert bound == second
    assert (
        await context.conversation_for(f"{second}/bee@example.com", SHARED_AUDIENCE)
        == conversation_id
    )


async def test_conversation_for_refuses_a_foreign_agent(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    _, foreign_agent, _ = await _seed()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    with pytest.raises(ValueError, match="not an agent of this workspace"):
        await context.conversation_for("stray-key", SHARED_AUDIENCE, agent_id=foreign_agent)
    assert await context.find_conversation("stray-key") is None


async def test_admitted_context_round_trips_to_the_loaded_turn(db: None, tmp_path) -> None:
    """Both ends of the turn.context column: the surface admits its ambient TurnContext, and the
    queue loader — the engine's one read path — validates the same record back off the row, with
    the admission stamp the engine renders from."""
    workspace_id, _, member_id = await _seed(member_email="bee@example.com")
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    conversation_id = await context.conversation_for("C1:1.0", SHARED_AUDIENCE)
    ambient = TurnContext(sender="Bee Jones (bee@example.com)", timezone="America/New_York")
    admitted = await context.admit(
        conversation_id,
        "hello",
        idempotency_key="C1:2.0",
        context=ambient,
        speaker_member_id=member_id,
    )
    turn, _, audience = await _load_turn(admitted.turn_id)
    assert turn.context == ambient
    assert turn.speaker_member_id == member_id
    assert audience == SHARED_AUDIENCE
    assert turn.created_at.tzinfo is not None


async def test_link_member_provisions_a_surface_identity(db: None, tmp_path) -> None:
    workspace_id, _, member_id = await _seed(member_email="bee@example.com")
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await context.linked_member("UBEE") is None
    assert await context.link_member("UBEE", "BEE@example.com") == member_id
    assert await context.linked_member("UBEE") == member_id
    async with workspace_tx() as connection:
        linked = (
            await connection.execute(
                sa.select(tables.surface_identity.c.member_id).where(
                    tables.surface_identity.c.surface == SURFACE,
                    tables.surface_identity.c.external_id == "UBEE",
                )
            )
        ).one()
    assert linked.member_id == member_id
    assert await context.link_member("UNOBODY", "nobody@example.com") is None


async def test_write_workspace_file_streams_into_the_conversation_workspace(
    db: None, tmp_path
) -> None:
    """The buffered chunks land through the carrier in the conversation's own workspace directory —
    the same `/workspace` the agent's file tools read on its next turn."""
    workspace_id, _, _ = await _seed()
    root = tmp_path / "workspaces"
    context = _context(
        workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path), _sandboxes(root)
    )

    async def _chunks():
        yield b"hello "
        yield b"world"

    with ws(workspace_id):
        conversation_id = await _conversation_row(workspace_id, queue_key="inbox")
        await context.write_workspace_file(conversation_id, "slack-inbox/note.txt", _chunks())
    landed = root / str(conversation_id) / "slack-inbox/note.txt"
    assert landed.read_bytes() == b"hello world"


async def test_write_workspace_file_refuses_an_uncapped_stream_while_it_accumulates(
    db: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bound trips inside the accumulation loop, so a stream with no cap of its own is refused
    at the limit instead of first sitting whole in this process — and nothing lands."""
    monkeypatch.setattr(surface_module, "WORKSPACE_WRITE_MAX_BYTES", 8)
    workspace_id, _, _ = await _seed()
    root = tmp_path / "workspaces"
    context = _context(
        workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path), _sandboxes(root)
    )
    consumed = 0

    async def _unbounded():
        nonlocal consumed
        while True:
            consumed += 1
            yield b"xxxx"

    with ws(workspace_id):
        conversation_id = await _conversation_row(workspace_id, queue_key="inbox")
        with pytest.raises(ValueError, match="byte limit"):
            await context.write_workspace_file(conversation_id, "big.bin", _unbounded())

    assert consumed <= 4
    assert not (root / str(conversation_id) / "big.bin").exists()


async def test_join_member_creates_a_same_domain_member_and_links(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    early = datetime(2026, 1, 1, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=uuid4(),
                workspace_id=workspace_id,
                email="owner@example.com",
                created_at=early,
                updated_at=early,
            )
        )
    joined = await context.join_member("UNEW", "New.Joiner@Example.com")
    assert joined is not None
    assert await context.linked_member("UNEW") == joined
    async with workspace_tx() as connection:
        email = (
            await connection.execute(
                sa.select(tables.member.c.email).where(tables.member.c.id == joined)
            )
        ).scalar_one()
    assert email == "new.joiner@example.com"
    # A second surface identity for the same email resolves to the one member, never a duplicate.
    assert await context.join_member("UNEW2", "new.joiner@example.com") == joined


async def test_join_member_refuses_without_a_domain_match(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed(member_email="owner@example.com")
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    for external_id, email in (
        ("UGIGI", "gigi@elsewhere.com"),
        ("UBARE", "example.com"),
        ("UEMPTY", "@example.com"),
    ):
        assert await context.join_member(external_id, email) is None
        assert await context.linked_member(external_id) is None
    memberless = _context(uuid4(), StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await memberless.join_member("UFIRST", "first@example.com") is None
    async with workspace_tx() as connection:
        members = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.member)
                .where(tables.member.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert members == 1


async def test_is_operator_workspace_matches_the_owner_email_domain(db: None, tmp_path) -> None:
    operator_id, _, _ = await _seed(member_email=f"owner@{OPERATOR_EMAIL_DOMAIN}")
    operator = _context(operator_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await operator.is_operator_workspace() is True
    customer_id, _, _ = await _seed(member_email="owner@customer.example")
    customer = _context(customer_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await customer.is_operator_workspace() is False
    ownerless_id, _, _ = await _seed()
    ownerless = _context(ownerless_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await ownerless.is_operator_workspace() is False


async def test_is_operator_workspace_keys_on_the_earliest_member(db: None, tmp_path) -> None:
    early = datetime(2026, 1, 1, tzinfo=UTC)

    async def _backdated_member(workspace_id: UUID, email: str) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.member).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    email=email,
                    created_at=early,
                    updated_at=early,
                )
            )

    operator_id, _, _ = await _seed(member_email="joiner@customer.example")
    await _backdated_member(operator_id, f"owner@{OPERATOR_EMAIL_DOMAIN}")
    operator = _context(operator_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await operator.is_operator_workspace() is True
    customer_id, _, _ = await _seed(member_email=f"support@{OPERATOR_EMAIL_DOMAIN}")
    await _backdated_member(customer_id, "owner@customer.example")
    customer = _context(customer_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await customer.is_operator_workspace() is False


def test_public_base_url_is_the_wired_connect_base(tmp_path) -> None:
    context = _context(uuid4(), StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert context.public_base_url == "https://ufo.example.test"
    assert replace(context, _public_base_url=None).public_base_url is None


async def test_poller_delivers_a_done_turn_and_attaches_its_files(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    blob = FilesystemBlobStore(root=tmp_path)
    await blob.put("artifacts/x/report.pdf", b"PDF")
    artifact = SharedArtifact(
        blob_key="artifacts/x/report.pdf",
        filename="report.pdf",
        subject=None,
        media_type="application/pdf",
        size_bytes=3,
    )
    turn_id = await _seed_turn(workspace_id, "C5:2.0", "done", "hi there", artifacts=(artifact,))
    poller, surface = _poller(workspace_id, RecordingSurface(ref="C5:9.9"), blob)
    await poller.drain()
    row = await _writeback(turn_id)
    assert row.status == WRITEBACK_DELIVERED
    assert row.reply_ref == "C5:9.9"
    assert surface.attached == [(turn_id, "C5:9.9", ("report.pdf",))]


async def test_workspace_candidates_rotate_and_recover_from_cursor_deletion_and_restart(
    db: None,
) -> None:
    workspaces: list[UUID] = []
    turns: dict[UUID, UUID] = {}
    for index in range(WRITEBACK_WORKSPACE_BATCH + 1):
        workspace_id, _, _ = await _seed()
        turns[workspace_id] = await _seed_turn(workspace_id, f"C{index}:1.0", "done", str(index))
        workspaces.append(workspace_id)
    candidates = writeback_workspaces()
    first = await candidates()
    second = await candidates()
    assert len(first) == WRITEBACK_WORKSPACE_BATCH
    assert set(first).isdisjoint(second)
    assert set((*first, *second)) == set(workspaces)
    await _set_writeback(turns[second[-1]], status=WRITEBACK_DELIVERED)
    assert set(await candidates()) == set(first)
    assert set(await writeback_workspaces()()) == set(first)


async def test_a_slow_workspace_does_not_block_another_workspace(db: None, tmp_path) -> None:
    slow_workspace, _, _ = await _seed()
    fast_workspace, _, _ = await _seed()
    slow_turn = await _seed_turn(slow_workspace, "CSLOW:1.0", "done", "slow")
    fast_turn = await _seed_turn(fast_workspace, "CFAST:1.0", "done", "fast")
    blob = FilesystemBlobStore(root=tmp_path)
    contexts = {
        workspace_id: _context(workspace_id, StubDbos(), blob)
        for workspace_id in (slow_workspace, fast_workspace)
    }
    surface = BlockingSurface(blocked_workspace=slow_workspace)
    drain = asyncio.create_task(_fleet_poller(contexts, surface).drain())
    try:
        await asyncio.wait_for(surface.blocked.wait(), timeout=1)
        await asyncio.wait_for(surface.fast.wait(), timeout=1)
        deadline = asyncio.get_running_loop().time() + 1
        while (await _writeback(fast_turn)).status != WRITEBACK_DELIVERED:
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)
        assert fast_turn in surface.posted
    finally:
        surface.release.set()
        await asyncio.wait_for(drain, timeout=1)
    assert (await _writeback(slow_turn)).status == WRITEBACK_DELIVERED


async def test_runner_pages_beyond_a_slow_workspace(
    db: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(surface_module, "WRITEBACK_POLL_SECONDS", 0.01)
    monkeypatch.setattr(surface_module, "WRITEBACK_WORKSPACE_BATCH", 2)
    monkeypatch.setattr(surface_module, "WRITEBACK_WORKSPACE_IN_FLIGHT", 4)
    monkeypatch.setattr(surface_module, "WRITEBACK_WORKSPACE_CONCURRENCY", 2)
    workspaces: list[UUID] = []
    turns: dict[UUID, UUID] = {}
    for index in range(3):
        workspace_id, _, _ = await _seed()
        turns[workspace_id] = await _seed_turn(workspace_id, f"CPAGE:{index}.0", "done", str(index))
        workspaces.append(workspace_id)
    ordered = sorted(workspaces)
    blocked_workspace, later_workspace = ordered[0], ordered[-1]
    blob = FilesystemBlobStore(root=tmp_path)
    contexts = {
        workspace_id: _context(workspace_id, StubDbos(), blob) for workspace_id in workspaces
    }
    surface = BlockingSurface(blocked_workspace=blocked_workspace)
    running = asyncio.create_task(_fleet_poller(contexts, surface).run())
    try:
        await asyncio.wait_for(surface.blocked.wait(), timeout=1)
        deadline = asyncio.get_running_loop().time() + 1
        while (await _writeback(turns[later_workspace])).status != WRITEBACK_DELIVERED:
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)
        assert (await _writeback(turns[blocked_workspace])).status == WRITEBACK_CLAIMED
    finally:
        surface.release.set()
        deadline = asyncio.get_running_loop().time() + 1
        while (await _writeback(turns[blocked_workspace])).status != WRITEBACK_DELIVERED:
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


async def test_poller_leaves_a_non_terminal_turn_undelivered(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    turn_id = await _seed_turn(workspace_id, "C6:1.0", "queued", "")
    poller, surface = _poller(workspace_id, RecordingSurface(), FilesystemBlobStore(root=tmp_path))
    await poller.drain()
    assert surface.attached == []
    assert (await _writeback(turn_id)).status == WRITEBACK_PENDING


async def test_poller_resumes_attachments_from_a_recorded_ref_without_reposting(
    db: None, tmp_path
) -> None:
    workspace_id, _, _ = await _seed()
    artifact = SharedArtifact(
        blob_key="artifacts/x/resume.txt",
        filename="resume.txt",
        subject=None,
        media_type="text/plain",
        size_bytes=6,
    )
    turn_id = await _seed_turn(
        workspace_id, "C7:1.0", "done", "already sent", artifacts=(artifact,)
    )
    await _set_writeback(
        turn_id,
        status=WRITEBACK_CLAIMED,
        claimed_by="dead-worker",
        reply_ref="C7:5.5",
        claim_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    poller, surface = _poller(workspace_id, RecordingSurface(), FilesystemBlobStore(root=tmp_path))
    await poller.drain()
    assert surface.posted == []
    assert surface.attached == [(turn_id, "C7:5.5", ("resume.txt",))]
    row = await _writeback(turn_id)
    assert row.status == WRITEBACK_DELIVERED
    assert row.reply_ref == "C7:5.5"


async def test_attachment_failure_retries_from_the_recorded_reply(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    artifact = SharedArtifact(
        blob_key="artifacts/x/retry.txt",
        filename="retry.txt",
        subject=None,
        media_type="text/plain",
        size_bytes=5,
    )
    turn_id = await _seed_turn(workspace_id, "C8:1.0", "done", "sent once", artifacts=(artifact,))
    surface = RecordingSurface(fail_attach_attempts=1)
    poller, _ = _poller(workspace_id, surface, FilesystemBlobStore(root=tmp_path))
    await poller.drain()
    failed = await _writeback(turn_id)
    assert failed.status == WRITEBACK_PENDING
    assert failed.reply_ref == "posted-ref"
    await _set_writeback(turn_id, claim_expires_at=None)
    await poller.drain()
    assert surface.posted == [turn_id]
    assert surface.attach_attempts == 2
    assert surface.attached == [(turn_id, "posted-ref", ("retry.txt",))]
    assert (await _writeback(turn_id)).status == WRITEBACK_DELIVERED


async def test_live_delivery_renews_its_claim_before_a_peer_can_recover_it(
    db: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cycles: dict[UUID, int] = {}
    refreshed_once: set[UUID] = set()
    refreshed_twice: set[UUID] = set()
    all_refreshed_once = asyncio.Event()
    all_refreshed_twice = asyncio.Event()
    next_refresh = asyncio.Event()
    hold_renewals = asyncio.Event()
    refresh_claim = WritebackPoller._refresh_claim

    async def controlled_refresh(self: WritebackPoller, turn_id: UUID) -> None:
        cycle = cycles.get(turn_id, 0) + 1
        cycles[turn_id] = cycle
        await refresh_claim(self, turn_id)
        if cycle == 1:
            refreshed_once.add(turn_id)
            if len(refreshed_once) == len(turn_ids):
                all_refreshed_once.set()
            await next_refresh.wait()
            return
        if cycle == 2:
            refreshed_twice.add(turn_id)
            if len(refreshed_twice) == len(turn_ids):
                all_refreshed_twice.set()
            await hold_renewals.wait()
            return
        raise AssertionError(f"unexpected refresh cycle {cycle}")

    monkeypatch.setattr(surface_module, "WRITEBACK_CLAIM_REFRESH_SECONDS", 0.0)
    monkeypatch.setattr(WritebackPoller, "_refresh_claim", controlled_refresh)
    workspace_id, _, _ = await _seed()
    turn_ids = (
        await _seed_turn(workspace_id, "CLEASE:1.0", "done", "slow"),
        await _seed_turn(workspace_id, "CLEASE:2.0", "done", "waiting"),
    )
    blob = FilesystemBlobStore(root=tmp_path)
    contexts = {workspace_id: _context(workspace_id, StubDbos(), blob)}
    surface = BlockingSurface(blocked_workspace=workspace_id)
    first = _fleet_poller(contexts, surface, worker_id="worker-1")
    running = asyncio.create_task(first.drain())
    try:
        await asyncio.wait_for(
            asyncio.gather(surface.blocked.wait(), all_refreshed_once.wait()), timeout=1
        )
        expired = datetime.now(UTC) - timedelta(seconds=1)
        for turn_id in turn_ids:
            await _set_writeback(turn_id, claim_expires_at=expired)
        expired_claims = {
            turn_id: (await _writeback(turn_id)).claim_expires_at for turn_id in turn_ids
        }
        next_refresh.set()
        await asyncio.wait_for(all_refreshed_twice.wait(), timeout=1)
        for turn_id in turn_ids:
            renewed = await _writeback(turn_id)
            assert renewed.status == WRITEBACK_CLAIMED
            assert renewed.claimed_by == "worker-1"
            assert renewed.claim_expires_at != expired_claims[turn_id]
        peer_surface = RecordingSurface()
        await _fleet_poller(contexts, peer_surface, worker_id="worker-2").drain()
        assert peer_surface.posted == []
    finally:
        surface.release.set()
        await asyncio.wait_for(running, timeout=1)
    assert [(await _writeback(turn_id)).status for turn_id in turn_ids] == [
        WRITEBACK_DELIVERED,
        WRITEBACK_DELIVERED,
    ]


async def test_the_delivered_commit_never_contends_with_its_own_claim_renewal(
    db: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The renewal and the delivered commit write the same writeback row. If the commit runs while
    this row's refresher still holds the row lock, it waits on its own lease — the delivered write
    is serialized behind a refresh under a loaded runner. Assert the lease is closed first: no
    refresh for a turn is ever in flight when that turn is marked delivered.

    Every wait here is bounded by the drain, never by a wall clock: a loaded runner stretches the
    drain arbitrarily while the delivery it must prove stays correct, and the drain ending is the
    only way the posts and their refreshers never arrive."""
    in_flight: dict[UUID, int] = {}
    contended = 0
    mark_delivered = WritebackPoller._mark_delivered
    all_renewing = asyncio.Event()
    hold_renewals = asyncio.Event()

    async def tracked_renew(self: WritebackPoller, turn_id: UUID) -> None:
        in_flight[turn_id] = in_flight.get(turn_id, 0) + 1
        if len(in_flight) == len(turn_ids):
            all_renewing.set()
        try:
            await hold_renewals.wait()
        finally:
            in_flight[turn_id] -= 1

    async def watched_mark(self: WritebackPoller, turn_id: UUID) -> None:
        nonlocal contended
        if in_flight.get(turn_id):
            contended += 1
        await mark_delivered(self, turn_id)

    monkeypatch.setattr(WritebackPoller, "_renew_claim", tracked_renew)
    monkeypatch.setattr(WritebackPoller, "_mark_delivered", watched_mark)
    workspace_id, _, _ = await _seed()
    turn_ids = tuple(
        [await _seed_turn(workspace_id, f"CCOMMIT:{index}.0", "done", "slow") for index in range(4)]
    )
    blob = FilesystemBlobStore(root=tmp_path)
    contexts = {workspace_id: _context(workspace_id, StubDbos(), blob)}
    surface = BlockingSurface(blocked_workspace=workspace_id)
    running = asyncio.create_task(_fleet_poller(contexts, surface, worker_id="worker-1").drain())
    renewing = asyncio.gather(surface.blocked.wait(), all_renewing.wait())
    try:
        await asyncio.wait((renewing, running), return_when=asyncio.FIRST_COMPLETED)
        assert renewing.done(), "the drain ended before every turn posted and renewed"
        assert all(in_flight.get(turn_id) == 1 for turn_id in turn_ids)
    finally:
        renewing.cancel()
        surface.release.set()
        await running
    assert [(await _writeback(turn_id)).status for turn_id in turn_ids] == [
        WRITEBACK_DELIVERED
    ] * len(turn_ids)
    assert contended == 0


async def test_claim_renewal_cancels_external_delivery_when_ownership_changes(
    db: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(surface_module, "WRITEBACK_CLAIM_REFRESH_SECONDS", 0.01)
    workspace_id, _, _ = await _seed()
    turn_id = await _seed_turn(workspace_id, "CSTOLEN:1.0", "done", "slow")
    blob = FilesystemBlobStore(root=tmp_path)
    contexts = {workspace_id: _context(workspace_id, StubDbos(), blob)}
    surface = BlockingSurface(blocked_workspace=workspace_id)
    running = asyncio.create_task(_fleet_poller(contexts, surface, worker_id="worker-1").drain())
    try:
        await asyncio.wait_for(surface.blocked.wait(), timeout=1)
        await _set_writeback(
            turn_id,
            claimed_by="worker-2",
            claim_expires_at=datetime.now(UTC) + timedelta(seconds=60),
        )
        await asyncio.wait_for(running, timeout=1)
    finally:
        surface.release.set()
        if not running.done():
            running.cancel()
            await asyncio.gather(running, return_exceptions=True)
    row = await _writeback(turn_id)
    assert row.status == WRITEBACK_CLAIMED
    assert row.claimed_by == "worker-2"
    assert surface.posted == [turn_id]
    assert surface.attach_attempts == 0


async def test_poller_backs_off_a_young_failure_then_terminally_fails_when_aged_out(
    db: None, tmp_path
) -> None:
    workspace_id, _, _ = await _seed()
    blob = FilesystemBlobStore(root=tmp_path)
    turn_id = await _seed_turn(workspace_id, "C9:1.0", "done", "hi")
    poller, _ = _poller(workspace_id, RecordingSurface(fail_post=True), blob)
    await poller.drain()
    row = await _writeback(turn_id)
    assert row.status == WRITEBACK_PENDING
    assert row.claim_expires_at is not None
    assert row.last_error is not None
    await _set_writeback(
        turn_id,
        created_at=datetime.now(UTC) - timedelta(seconds=WRITEBACK_MAX_AGE_SECONDS + 60),
        claim_expires_at=None,
    )
    await poller.drain()
    assert (await _writeback(turn_id)).status == WRITEBACK_FAILED


async def test_writeback_retains_latest_error_and_honors_retry_after(
    db: None, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id, _, _ = await _seed()
    turn_id = await _seed_turn(workspace_id, "C429:1.0", "done", "hi")
    poller, _ = _poller(workspace_id, RetryAfterSurface(), FilesystemBlobStore(root=tmp_path))
    with caplog.at_level(logging.INFO, logger="ufo"):
        await poller.drain()
        pending = await _writeback(turn_id)
        assert pending.status == WRITEBACK_PENDING
        assert pending.claim_expires_at is not None
        assert pending.last_error == "chat.postMessage HTTP 429; retry_after_seconds=17"
        due = pending.claim_expires_at.replace(tzinfo=UTC)
        assert 16 <= (due - datetime.now(UTC)).total_seconds() <= 17
        await _set_writeback(turn_id, claim_expires_at=None)
        await poller.drain()
    delivered = await _writeback(turn_id)
    assert delivered.status == WRITEBACK_DELIVERED
    assert delivered.last_error == "chat.postMessage HTTP 429; retry_after_seconds=17"
    failure_log = next(
        record for record in caplog.records if record.getMessage() == "surface.writeback_failed"
    )
    failure_fields = failure_log.__dict__["ufo"]
    assert failure_fields["turn_id"] == str(turn_id)
    assert failure_fields["phase"] == "post"
    assert failure_fields["outcome"] == "retry"
    assert failure_fields["last_error"] == delivered.last_error
    delivered_log = next(
        record for record in caplog.records if record.getMessage() == "surface.writeback_delivered"
    )
    assert delivered_log.__dict__["ufo"]["turn_id"] == str(turn_id)


async def test_writeback_caps_retry_after_at_the_writeback_window(db: None, tmp_path) -> None:
    """A hostile or absurd Retry-After cannot park a row past the writeback window: the delay is
    capped there, and the age give-up fails the row on the attempt after it ages out."""
    workspace_id, _, _ = await _seed()
    turn_id = await _seed_turn(workspace_id, "C429:2.0", "done", "hi")
    surface = RetryAfterSurface(retry_after_seconds=WRITEBACK_MAX_AGE_SECONDS * 10)
    poller, _ = _poller(workspace_id, surface, FilesystemBlobStore(root=tmp_path))

    await poller.drain()

    writeback = await _writeback(turn_id)
    assert writeback.status == WRITEBACK_PENDING
    due = writeback.claim_expires_at.replace(tzinfo=UTC)
    assert (due - datetime.now(UTC)).total_seconds() <= WRITEBACK_MAX_AGE_SECONDS


async def test_writeback_retry_after_does_not_defer_another_surface(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    delayed_turn_id = await _seed_turn(workspace_id, "C429:3.0", "done", "delayed")
    delivered_turn_id = await _seed_turn(
        workspace_id,
        "C200:1.0",
        "done",
        "delivered",
        surface="other",
    )
    delayed = RetryAfterSurface()
    delivered = RecordingSurface()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    poller = WritebackPoller(
        worker_id="worker-1",
        surfaces={
            SURFACE: SurfaceSpec(
                name=SURFACE,
                routes=(SurfaceRoute(method="POST", path="", handler=_unused_ingest),),
                identify=_unused_identify,
                post=delayed.post,
                attach=delayed.attach,
            ),
            "other": SurfaceSpec(
                name="other",
                routes=(SurfaceRoute(method="POST", path="", handler=_unused_ingest),),
                identify=_unused_identify,
                post=delivered.post,
                attach=delivered.attach,
            ),
        },
        context_for=lambda _workspace_id, _surface: context,
        candidates=writeback_workspaces(),
    )

    await poller.drain()

    assert (await _writeback(delayed_turn_id)).status == WRITEBACK_PENDING
    assert (await _writeback(delivered_turn_id)).status == WRITEBACK_DELIVERED
    assert delivered.posted == [delivered_turn_id]


async def test_writeback_retry_after_leaves_nonterminal_rows_untouched(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    terminal_turn_id = await _seed_turn(workspace_id, "C429:terminal", "done", "done")
    running_turn_id = await _seed_turn(workspace_id, "C429:running", "running", "")
    await _set_writeback(
        running_turn_id,
        created_at=datetime.now(UTC) - timedelta(seconds=WRITEBACK_MAX_AGE_SECONDS - 10),
    )
    poller, _ = _poller(
        workspace_id,
        RetryAfterSurface(retry_after_seconds=17),
        FilesystemBlobStore(root=tmp_path),
    )

    await poller.drain()

    assert (await _writeback(terminal_turn_id)).claim_expires_at is not None
    running = await _writeback(running_turn_id)
    assert running.status == WRITEBACK_PENDING
    assert running.claim_expires_at is None
    assert running.last_error is None


async def test_credential_prompts_gate_per_slot_on_seal_workspace_and_marker(
    db: None, tmp_path
) -> None:
    """The per-slot render gate over the real methods: every named prompt of a live seal is
    pending, garbage and a foreign workspace never are, and fulfilling one slot silences exactly
    that prompt — its sibling keeps asking, so a disconnect mid-entry or a rotation over
    already-stored slots never strands a prompt. Expiry is proven where the seal contract lives
    (test_credentials)."""
    workspace_id, _, _ = await _seed()
    blob = FilesystemBlobStore(root=tmp_path)
    context = _context(workspace_id, StubDbos(), blob)
    member_id = uuid4()
    sealed = seal_credential_request(
        context._credentials.fernet,
        CredentialRequestState(workspace_id=workspace_id, member_id=member_id, slots=("a", "b")),
    )
    assert await context.credential_prompt_pending(sealed, "a") is True
    assert await context.credential_prompt_pending(sealed, "b") is True
    assert await context.credential_prompt_pending(sealed, "unnamed") is False
    assert await context.credential_prompt_pending("garbage", "a") is False
    foreign = seal_credential_request(
        context._credentials.fernet,
        CredentialRequestState(workspace_id=uuid4(), member_id=member_id, slots=("a",)),
    )
    assert await context.credential_prompt_pending(foreign, "a") is False
    await context.fulfill_credential_request(sealed, "a", "one", member_id)
    assert await context.credential_prompt_pending(sealed, "a") is False
    assert await context.credential_prompt_pending(sealed, "b") is True
    await context.fulfill_credential_request(sealed, "b", "two", member_id)
    assert await context.credential_prompt_pending(sealed, "b") is False
    rotation = seal_credential_request(
        context._credentials.fernet,
        CredentialRequestState(workspace_id=workspace_id, member_id=member_id, slots=("a", "b")),
    )
    assert await context.credential_prompt_pending(rotation, "a") is True
    with pytest.raises(CredentialRequestInvalid, match="member"):
        await context.fulfill_credential_request(sealed, "a", "hijack", uuid4())
    with pytest.raises(CredentialRequestInvalid, match="slot"):
        await context.fulfill_credential_request(sealed, "c", "off-seal", member_id)


async def test_join_member_auto_seats_while_a_seat_is_open(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    early = datetime(2026, 1, 1, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.workspace)
            .values(seat_limit=2, updated_at=sa.func.now())
            .where(tables.workspace.c.id == workspace_id)
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=uuid4(),
                workspace_id=workspace_id,
                email="owner@example.com",
                seated_at=early,
                created_at=early,
                updated_at=early,
            )
        )
    seated_join = await context.join_member("USEATED", "second@example.com")
    assert seated_join is not None
    unseated_join = await context.join_member("USIXTH", "third@example.com")
    assert unseated_join is not None
    assert await context.linked_member("USIXTH") == unseated_join
    async with workspace_tx() as connection:
        rows = {
            row.id: row.seated_at
            for row in (
                await connection.execute(
                    sa.select(tables.member.c.id, tables.member.c.seated_at).where(
                        tables.member.c.workspace_id == workspace_id
                    )
                )
            ).all()
        }
    assert rows[seated_join] is not None
    assert rows[unseated_join] is None


# --- read views: the debug surface's data half ---------------------------------------------------


async def _conversation_row(
    workspace_id: UUID,
    *,
    surface: str = SURFACE,
    queue_key: str,
    member_id: UUID | None = None,
    created_at: datetime | None = None,
) -> UUID:
    conversation_id = uuid4()
    moment = created_at or datetime.now(UTC)
    async with workspace_tx() as connection:
        agent_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == workspace_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface=surface,
                queue_key=queue_key,
                member_id=member_id,
                created_at=moment,
                updated_at=moment,
            )
        )
    return conversation_id


async def _turn_row(
    workspace_id: UUID,
    conversation_id: UUID,
    agent_id: UUID,
    seq: int,
    *,
    status: str = "done",
    parent_turn_id: UUID | None = None,
    moment: datetime | None = None,
) -> UUID:
    turn_id = uuid4()
    stamp = moment or datetime.now(UTC)
    terminal = (
        TerminalFrame(
            status=status, text="answer", tokens=7, cost_micro_usd=42, model="claude-opus-4-8"
        ).model_dump(mode="json")
        if status in ("done", "failed", "cancelled")
        else None
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=seq,
                status=status,
                inbound=f"ask {seq}",
                parent_turn_id=parent_turn_id,
                subagent_profile="research" if parent_turn_id is not None else None,
                terminal=terminal,
                created_at=stamp,
                updated_at=stamp,
            )
        )
    return turn_id


async def test_list_conversations_orders_by_activity_and_scopes_to_the_workspace(
    db: None, tmp_path
) -> None:
    workspace_id, agent_id, member_id = await _seed(member_email="bee@example.com")
    foreign_workspace, foreign_agent, _ = await _seed()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    early = datetime(2026, 7, 1, tzinfo=UTC)
    late = datetime(2026, 7, 2, tzinfo=UTC)
    quiet = await _conversation_row(
        workspace_id, queue_key="quiet", member_id=member_id, created_at=late
    )
    busy = await _conversation_row(workspace_id, queue_key="busy", created_at=early)
    await _turn_row(workspace_id, busy, agent_id, 1, moment=early)
    await _turn_row(workspace_id, busy, agent_id, 2, moment=datetime(2026, 7, 3, tzinfo=UTC))
    foreign = await _conversation_row(foreign_workspace, queue_key="foreign")
    await _turn_row(foreign_workspace, foreign, foreign_agent, 1)

    listed = await context.list_conversations()
    assert [entry.id for entry in listed] == [busy, quiet]
    by_id = {entry.id: entry for entry in listed}
    assert by_id[busy].turn_count == 2
    assert by_id[busy].last_turn_at == datetime(2026, 7, 3, tzinfo=UTC)
    assert by_id[busy].member_email is None
    assert by_id[quiet].turn_count == 0
    assert by_id[quiet].last_turn_at is None
    assert by_id[quiet].member_email == "bee@example.com"
    assert await context.list_conversations(limit=1) == (by_id[busy],)


async def test_list_turns_returns_full_rows_oldest_first(db: None, tmp_path) -> None:
    workspace_id, agent_id, _ = await _seed()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    conversation_id = await _conversation_row(workspace_id, queue_key="busy")
    first = await _turn_row(workspace_id, conversation_id, agent_id, 1)
    second = await _turn_row(workspace_id, conversation_id, agent_id, 2, status="running")

    turns = await context.list_turns(conversation_id)
    assert [turn.id for turn in turns] == [first, second]
    assert turns[0].terminal is not None
    assert turns[0].terminal.cost_micro_usd == 42
    assert turns[0].updated_at is not None
    assert turns[1].status == "running"
    assert turns[1].terminal is None
    capped = await context.list_turns(conversation_id, limit=1)
    assert [turn.id for turn in capped] == [second]


async def test_turn_detail_includes_ledger_and_subagent_children(db: None, tmp_path) -> None:
    workspace_id, agent_id, _ = await _seed()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    conversation_id = await _conversation_row(workspace_id, queue_key="busy")
    parent = await _turn_row(workspace_id, conversation_id, agent_id, 1)
    child_conversation = await _conversation_row(workspace_id, queue_key="subagent:1")
    child = await _turn_row(workspace_id, child_conversation, agent_id, 1, parent_turn_id=parent)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.ledger).values(
                id=uuid4(),
                workspace_id=workspace_id,
                turn_id=parent,
                dimension="tokens",
                amount=1234,
                priced_micro_usd=42,
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )

    detail = await context.turn_detail(parent)
    assert detail is not None
    assert detail.turn.id == parent
    assert detail.turn.terminal is not None
    assert [entry.dimension for entry in detail.ledger] == ["tokens"]
    assert detail.ledger[0].amount == 1234
    assert [turn.id for turn in detail.children] == [child]
    assert detail.children[0].subagent_profile == "research"
    assert await context.turn_detail(uuid4()) is None
    foreign_workspace, _, _ = await _seed()
    foreign_context = _context(foreign_workspace, StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await foreign_context.turn_detail(parent) is None


async def test_read_transcript_gates_ownership_before_the_blob(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    foreign_workspace, _, _ = await _seed()
    blob = FilesystemBlobStore(root=tmp_path)
    context = _context(workspace_id, StubDbos(), blob)
    conversation_id = await _conversation_row(workspace_id, queue_key="busy")
    stored = Conversation(
        seq=1,
        messages=(
            Message(role="user", content="hi"),
            Message(
                role="assistant",
                content=(
                    ToolUseBlock(id="t1", name="bash", input={"command": "ls"}),
                    ToolResultBlock(tool_use_id="t1", content="README.md"),
                    TextBlock(text="done"),
                ),
            ),
        ),
    )
    await blob.put(transcript_key(conversation_id), encode(stored))

    read = await context.read_transcript(conversation_id)
    assert read == stored
    empty = await _conversation_row(workspace_id, queue_key="empty")
    assert await context.read_transcript(empty) is None
    foreign_context = _context(foreign_workspace, StubDbos(), blob)
    assert await foreign_context.read_transcript(conversation_id) is None


async def test_compaction_records_list_and_read_back(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    blob = FilesystemBlobStore(root=tmp_path)
    context = _context(workspace_id, StubDbos(), blob)
    conversation_id = await _conversation_row(workspace_id, queue_key="busy")
    summary = CompactionSummary(intent="ship", current_work="reading", next_step="write")
    window = (Message(role="user", content="hi"),)
    for index in (1, 2):
        for half, payload in (
            ("before", CompactionWindow(messages=window)),
            ("after", CompactionWindow(messages=window)),
            ("summary", summary),
        ):
            await blob.put(
                compaction_key(conversation_id, index, half),
                lz4.frame.compress(payload.model_dump_json().encode()),
            )

    assert await context.list_compactions(conversation_id) == (1, 2)
    record = await context.read_compaction(conversation_id, 1)
    assert record is not None
    assert record.summary == summary
    assert record.before == window
    assert await context.read_compaction(conversation_id, 3) is None
    foreign_workspace, _, _ = await _seed()
    foreign_context = _context(foreign_workspace, StubDbos(), blob)
    assert await foreign_context.list_compactions(conversation_id) == ()
    assert await foreign_context.read_compaction(conversation_id, 1) is None


async def test_workspace_files_list_and_stream_scoped_to_the_conversation(
    db: None, tmp_path
) -> None:
    """List and read are live sandbox reads through the carrier: what a write landed comes back,
    an absent path is None, an escaping path raises, and another workspace's context sees
    nothing."""
    workspace_id, _, _ = await _seed()
    blob = FilesystemBlobStore(root=tmp_path)
    sandboxes = _sandboxes(tmp_path / "workspaces")
    context = _context(workspace_id, StubDbos(), blob, sandboxes)

    async def _chunks() -> AsyncIterator[bytes]:
        yield b"hello "
        yield b"world"

    with ws(workspace_id):
        conversation_id = await _conversation_row(workspace_id, queue_key="busy")
        await context.write_workspace_file(conversation_id, "report/out.txt", _chunks())
        files = await context.list_workspace_files(conversation_id)
        assert [entry.path for entry in files] == ["report/out.txt"]
        assert files[0].size_bytes == 11

        stream = await context.read_workspace_file(conversation_id, "report/out.txt")
        assert stream is not None
        body = b"".join([chunk async for chunk in stream])
        assert body == b"hello world"
        assert await context.read_workspace_file(conversation_id, "report/absent.txt") is None
        with pytest.raises(ValueError):
            await context.read_workspace_file(conversation_id, "../messages.json.lz4")
    foreign_workspace, _, _ = await _seed()
    foreign_context = _context(foreign_workspace, StubDbos(), blob, sandboxes)
    with ws(foreign_workspace):
        assert await foreign_context.list_workspace_files(conversation_id) == ()
        assert await foreign_context.read_workspace_file(conversation_id, "report/out.txt") is None


async def test_installation_reads_the_peer_surface_identity(db: None, tmp_path) -> None:
    workspace_id, agent_id, _ = await _seed()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.surface_installation).values(
                workspace_id=workspace_id,
                surface="slack",
                installation_id="team:T042",
                agent_id=agent_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    assert await context.installation("slack") == "team:T042"
    assert await context.installation("teams") is None
