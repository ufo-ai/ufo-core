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
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import lz4.frame
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_testsupport.surfaces import EMPTY_SKILL_REGISTRY, no_user_skills

import ufo.ext.surface as surface_module
from ufo.audience import (
    SHARED_AUDIENCE,
    conversation_audience,
    foreign_room_audience,
    room_audience,
)
from ufo.bearer import UFO_TOKEN_SECRET_ENV
from ufo.blob import FilesystemBlobStore
from ufo.connectors import DIRECT_ACCOUNT
from ufo.credentials import (
    CredentialRequestInvalid,
    CredentialRequestState,
    CredentialStore,
    DeclaredSlot,
    seal_credential_request,
)
from ufo.db import workspace_tx
from ufo.ext.surface import (
    MAX_CONVERSATION_SPEAKERS,
    NOTHING_DELIVERED,
    OPENING_MESSAGE_CHARS,
    OPERATOR_EMAIL_DOMAIN,
    SILENCE_SENTINEL,
    TRANSCRIPT_ACCESS_WINDOW,
    WRITEBACK_CLAIMED,
    WRITEBACK_DELIVERED,
    WRITEBACK_FAILED,
    WRITEBACK_MAX_AGE_SECONDS,
    WRITEBACK_WORKSPACE_BATCH,
    NothingDelivered,
    SharedArtifact,
    SurfaceContext,
    SurfaceDeliveryError,
    SurfaceRoute,
    SurfaceSpec,
    Writeback,
    WritebackPoller,
    fence_member_message,
    inbox_name,
    is_silence_sentinel,
    mint_marker,
    record_transcript_access,
    writeback_workspaces,
)
from ufo.hub import InProcessHub
from ufo.loop.queue import _load_turn
from ufo.models.interface import Message, TextBlock, ToolResultBlock, ToolUseBlock
from ufo.sandbox.containment import ContainmentError
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.ingress_host import parse_site_label
from ufo.sandbox.ingress_token import (
    INGRESS_SESSION_KIND,
    INGRESS_VIEW_KIND,
    INGRESS_VIEW_PATH,
    INGRESS_VIEW_TTL_SECONDS,
    IngressTokenError,
    verify_ingress_token,
)
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint
from ufo.schema import tables
from ufo.schema.records import (
    SUBAGENT_RESULT_KEY_PREFIX,
    SUBAGENT_SURFACE,
    WRITEBACK_PENDING,
    TerminalFrame,
    ToolIntent,
    TurnContext,
)
from ufo.sources.backend import binding_name
from ufo.subjects import SHARED_SUBJECT
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
WRITEBACK_READ_INTERVAL_SECONDS = 0.01
WEDGE_WATCHDOG_SECONDS = 30


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
    targets: list[tuple[UUID, UUID]] = field(default_factory=list)
    attach_attempts: int = 0
    attached: list[tuple[UUID, str, tuple[str, ...]]] = field(default_factory=list)

    async def post(self, ctx: SurfaceContext, writeback: Writeback) -> str:
        self.posted.append(writeback.turn_id)
        self.targets.append((writeback.conversation_id, writeback.agent_id))
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
class SilentSurface(RecordingSurface):
    """A surface whose delivery for this turn was to send nothing — the shape Slack takes when a
    turn's whole answer is the silence sentinel."""

    async def post(self, ctx: SurfaceContext, writeback: Writeback) -> NothingDelivered:
        self.posted.append(writeback.turn_id)
        return NOTHING_DELIVERED


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
        _declared_slots=(),
        _artifact_token_secret="artifact-token-secret",
        _skills=EMPTY_SKILL_REGISTRY,
        _user_skills=no_user_skills,
        _subagents=(),
        _public_base_url="https://ufo.example.test",
        _ingress_public_url="https://sites.example.test",
        _deploy_sandbox_internet=False,
        _models=("auto", "claude-opus-4-8", "claude-sonnet-5"),
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


async def _await_status(turn_id: UUID, status: str, poller: asyncio.Task[None]) -> None:
    """Read a writeback until it reaches `status`. A loaded runner stretches every read while the
    scheduling this proves stays correct, so the bound is never how long the runner may take: a
    poller that ended reports its own failure first, and `WEDGE_WATCHDOG_SECONDS` names a wedge
    rather than hanging the shard for fifteen minutes and taking every other result with it."""
    deadline = asyncio.get_running_loop().time() + WEDGE_WATCHDOG_SECONDS
    while (await _writeback(turn_id)).status != status:
        if poller.done():
            await poller
            raise AssertionError(f"the poller ended before {turn_id} reached {status}")
        assert asyncio.get_running_loop().time() < deadline, (
            f"the poller wedged before {turn_id} reached {status}"
        )
        await asyncio.sleep(WRITEBACK_READ_INTERVAL_SECONDS)


async def _await_posted(waiter: asyncio.Future, poller: asyncio.Task[None], what: str) -> None:
    """Wait for a post, on the same terms as `_await_status`: the poller ending re-raises whatever
    ended it, so a crash reports its own cause instead of this wait's generic message, and the
    watchdog names a wedge instead of hanging."""
    await asyncio.wait(
        (waiter, poller), timeout=WEDGE_WATCHDOG_SECONDS, return_when=asyncio.FIRST_COMPLETED
    )
    if poller.done():
        await poller
    assert waiter.done(), f"the poller ended or wedged before {what}"


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


async def test_conversation_for_takes_a_caller_named_id_only_at_creation(
    db: None, tmp_path
) -> None:
    """A caller may name the new conversation's id, so state keyed by that id can precede the
    conversation; an existing conversation keeps its own id, so a lost creation race lands on the
    survivor."""
    workspace_id, _, _member_id = await _seed(member_email="bee@example.com")
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    named = uuid4()
    assert await context.conversation_for("C1:1.0", SHARED_AUDIENCE, conversation_id=named) == (
        named
    )
    other = uuid4()
    assert await context.conversation_for("C1:1.0", SHARED_AUDIENCE, conversation_id=other) == (
        named
    )


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
    workspace_id, agent_id, _ = await _seed()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await context.find_conversation("C1:1.0") is None
    async with workspace_tx() as connection:
        created = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.conversation))
        ).scalar_one()
    assert created == 0
    conversation_id = await context.conversation_for("C1:1.0", SHARED_AUDIENCE)
    assert await context.find_conversation("C1:1.0") == conversation_id
    assert await context.conversation_agent(conversation_id) == agent_id
    assert await replace(context, surface="other").find_conversation("C1:1.0") is None
    other_workspace, _, _ = await _seed()
    assert (
        await _context(
            other_workspace, StubDbos(), FilesystemBlobStore(root=tmp_path)
        ).conversation_agent(conversation_id)
        is None
    )


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
    assert listed[0].internet_access_allowed
    assert listed[1].id == second
    assert listed[1].model == "claude-sonnet-5"


async def _seed_agent(workspace_id: UUID, name: str) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=name,
                prompt="be brief",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


async def _seed_connection(
    workspace_id: UUID, agent_id: UUID, owner_member_id: UUID, provider: str, *, shared: bool
) -> None:
    conversation_id, connection_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface=SURFACE,
                queue_key=conversation_id.hex,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider=provider,
                account_id=f"{provider}-account",
                host="api.example.test",
                owner_member_id=owner_member_id,
                conversation_id=conversation_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.connector_grant).values(
                id=uuid4(),
                workspace_id=workspace_id,
                agent_id=agent_id,
                connection_id=connection_id,
                conversation_id=conversation_id,
                shared=shared,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def test_agent_connections_hold_the_wall_and_the_member_gate(db: None, tmp_path) -> None:
    workspace_id, agent_id, owner = await _seed(member_email="owner@example.com")
    other_agent = await _seed_agent(workspace_id, "ops")
    peer = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=peer,
                workspace_id=workspace_id,
                email="peer@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    await _seed_connection(workspace_id, agent_id, owner, "github", shared=False)
    await _seed_connection(workspace_id, agent_id, peer, "slack", shared=True)
    await _seed_connection(workspace_id, other_agent, owner, "asana", shared=True)
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    owner_view = await context.list_agent_connections(agent_id, owner, admin=False)
    assert [(view.provider, view.shared) for view in owner_view] == [
        ("github", False),
        ("slack", True),
    ]
    assert owner_view[0].owner_email == "owner@example.com"
    peer_view = await context.list_agent_connections(agent_id, peer, admin=False)
    assert [view.provider for view in peer_view] == ["slack"]
    admin_view = await context.list_agent_connections(agent_id, peer, admin=True)
    assert [view.provider for view in admin_view] == ["github", "slack"]
    assert [
        view.provider
        for view in await context.list_agent_connections(other_agent, owner, admin=True)
    ] == ["asana"]


async def test_credential_slots_report_fill_state_and_no_value(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot="acme_api_key",
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    context = replace(
        _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path)),
        _declared_slots=(
            DeclaredSlot(name="acme_api_key", description="ACME key", extension="acme"),
            DeclaredSlot(name="beta_token", description="Beta token", extension="beta"),
        ),
    )
    listed = await context.list_credential_slots()
    assert [(view.slot, view.filled) for view in listed] == [
        ("acme_api_key", True),
        ("beta_token", False),
    ]
    assert all("sealed" not in view.model_dump_json() for view in listed)


async def test_sources_gate_on_subject_and_skip_removed(db: None, tmp_path) -> None:
    workspace_id, _agent_id, owner = await _seed(member_email="owner@example.com")
    peer = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=peer,
                workspace_id=workspace_id,
                email="peer@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        for backend, subject, owner_id, removed in (
            ("folder", "shared", None, False),
            ("github", f"member:{owner}", owner, False),
            ("asana", "shared", None, True),
        ):
            await connection.execute(
                sa.insert(tables.source).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    backend=backend,
                    config={},
                    subject=subject,
                    owner_member_id=owner_id,
                    next_sync_at=sa.func.now(),
                    removed_at=sa.func.now() if removed else None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    owner_view = await context.list_sources(owner, admin=False)
    assert [(view.backend, view.shared) for view in owner_view] == [
        ("folder", True),
        ("github", False),
    ]
    assert owner_view[1].owner_email == "owner@example.com"
    peer_view = await context.list_sources(peer, admin=False)
    assert [view.backend for view in peer_view] == ["folder"]
    admin_view = await context.list_sources(peer, admin=True)
    assert [view.backend for view in admin_view] == ["folder", "github"]


async def test_a_connector_row_carries_the_binding_the_kind_names(db: None, tmp_path) -> None:
    """A connector-registered row projects the `source` kind's own identity, so the panel's acts
    name the object the chat verbs mutate: the binding name is `binding_name`'s digest over
    exactly what the row authenticates as, and the spec fields round-trip — a brokered account
    verbatim, `DIRECT_ACCOUNT` as the empty account the kind's spec uses, and an absent tenant URL
    as the empty string. A row whose config is not a connector's stays unmanaged, which the
    subject-gating test above covers at the None polarity."""
    workspace_id, _, _ = await _seed()
    async with workspace_tx() as connection:
        for backend, config in (
            ("asana", {"account": "acct-7", "stream": "tasks"}),
            ("fresh_desk", {"account": DIRECT_ACCOUNT, "stream": "tickets", "base_url": "t.io"}),
        ):
            await connection.execute(
                sa.insert(tables.source).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    backend=backend,
                    config=config,
                    subject=SHARED_SUBJECT,
                    owner_member_id=None,
                    next_sync_at=sa.func.now(),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))

    listed = await context.list_sources(uuid4(), admin=True)

    brokered = next(view for view in listed if view.backend == "asana")
    assert brokered.name == binding_name("asana", "acct-7", None)
    assert (brokered.stream, brokered.account_id, brokered.base_url) == ("tasks", "acct-7", "")
    direct = next(view for view in listed if view.backend == "fresh_desk")
    assert direct.name == binding_name("fresh_desk", DIRECT_ACCOUNT, "t.io")
    assert direct.name.startswith("fresh-desk-")  # the kind's name never carries an underscore
    assert (direct.stream, direct.account_id, direct.base_url) == ("tickets", "", "t.io")


async def test_a_windowed_connector_row_projects_its_window(db: None, tmp_path) -> None:
    """A panel act submits `{...row.apply, <the one thing it changes>}`, so this projection has to
    carry every field the binding's identity is built from — not just the ones a column displays.
    `backfill_days` is here for no display purpose at all: it is carried solely so the round-trip
    is faithful. Omitted, it reaches the verb as its default and reads as an edit nobody made,
    which refuses the act outright — the extension suite drives both acts that die that way."""
    workspace_id, _, _ = await _seed()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=uuid4(),
                workspace_id=workspace_id,
                backend="gmail",
                config={
                    "account": "acct-7",
                    "stream": "messages",
                    "backfill_days": 7,
                    "backfill_after": "2026-07-30T03:50:40Z",
                },
                subject=SHARED_SUBJECT,
                owner_member_id=None,
                next_sync_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))

    [view] = await context.list_sources(uuid4(), admin=True)

    assert view.backfill_days == 7
    assert (view.stream, view.account_id, view.base_url) == ("messages", "acct-7", "")


async def test_list_installations_orders_by_surface(db: None, tmp_path) -> None:
    workspace_id, agent_id, _ = await _seed()
    async with workspace_tx() as connection:
        for surface, installation_id in (("slack", "team:T123"), ("chime", "room:R9")):
            await connection.execute(
                sa.insert(tables.surface_installation).values(
                    workspace_id=workspace_id,
                    surface=surface,
                    installation_id=installation_id,
                    agent_id=agent_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    listed = await context.list_installations()
    assert [(entry.surface, entry.agent_id) for entry in listed] == [
        ("chime", agent_id),
        ("slack", agent_id),
    ]
    other = _context(uuid4(), StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await other.list_installations() == ()


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


async def test_an_intent_admission_stamps_the_turn_and_requires_a_speaker(
    db: None, tmp_path
) -> None:
    """Both ends of the intent admission: the turn row carries the 'intent' source with the
    envelope as its inbound (the audit record the engine dispatches verbatim); a speakerless
    intent and a body disagreeing with the envelope are both refused, and neither refusal admits
    a turn."""
    workspace_id, _, member_id = await _seed(member_email="bee@example.com")
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    conversation_id = await context.conversation_for("intent/one", conversation_audience(member_id))
    intent = ToolIntent(
        tool="object_apply",
        input={"manifest": "kind: agent", "user_description": "apply settings"},
    )
    admitted = await context.admit(
        conversation_id, intent.model_dump_json(), speaker_member_id=member_id, intent=intent
    )
    turn, _, _ = await _load_turn(admitted.turn_id)
    assert turn.admission_source == "intent"
    assert turn.speaker_member_id == member_id
    assert ToolIntent.model_validate_json(turn.inbound) == intent

    async def turn_count() -> int:
        async with workspace_tx() as connection:
            return (
                await connection.execute(sa.select(sa.func.count()).select_from(tables.turn))
            ).scalar_one()

    admitted_turns = await turn_count()
    with pytest.raises(ValueError, match="requires a speaking member"):
        await context.admit(
            conversation_id, intent.model_dump_json(), speaker_member_id=None, intent=intent
        )
    with pytest.raises(ValueError, match="body and intent disagree"):
        await context.admit(
            conversation_id, "member prose", speaker_member_id=member_id, intent=intent
        )
    assert await turn_count() == admitted_turns


async def test_admitted_context_round_trips_to_the_loaded_turn(db: None, tmp_path) -> None:
    """Both ends of the turn.context column: the surface admits its ambient TurnContext, and the
    queue loader — the engine's one read path — validates the same record back off the row, with
    the admission stamp the engine renders from."""
    workspace_id, _, member_id = await _seed(member_email="bee@example.com")
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    conversation_id = await context.conversation_for("C1:1.0", SHARED_AUDIENCE)
    ambient = TurnContext(
        sender="Bee Jones (bee@example.com)",
        timezone="America/New_York",
        source="https://app.slack.com/client/T1/C1/thread/C1-1.0",
    )
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


async def test_link_member_answers_the_oldest_of_two_cased_rows(db: None, tmp_path) -> None:
    """Member uniqueness compares bytes, so two rows can carry one address in different casings:
    the lookup answers the oldest of them rather than raising on the ambiguity, so requests for that
    address serve under one member."""
    workspace_id, _, member_id = await _seed(member_email="bee@example.com")
    later = datetime.now(UTC) + timedelta(days=1)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=uuid4(),
                workspace_id=workspace_id,
                email="BEE@example.com",
                created_at=later,
                updated_at=later,
            )
        )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await context.link_member("UBEE", "bee@example.com") == member_id
    assert await context.linked_member("UBEE") == member_id


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


def test_the_silence_sentinel_is_the_whole_answer_or_it_is_not_silence() -> None:
    """The predicate is the only reader of the token, and it decides whether a member sees nothing
    at all — so both directions are pinned. Whitespace around and between the tags is tolerated and
    the self-closing form counts, because the model produces both; the element inside a longer
    reply, or discussed in prose, is a normal reply that must still be posted."""
    assert is_silence_sentinel(SILENCE_SENTINEL)
    assert is_silence_sentinel(f"  {SILENCE_SENTINEL}\n")
    assert is_silence_sentinel("<response>   </response>")
    assert is_silence_sentinel("<response>\n</response>")
    assert is_silence_sentinel("<response/>")
    assert is_silence_sentinel("<response />")
    assert not is_silence_sentinel("")
    assert not is_silence_sentinel("   ")
    assert not is_silence_sentinel(f"Nothing further from me. {SILENCE_SENTINEL}")
    assert not is_silence_sentinel(f"{SILENCE_SENTINEL} {SILENCE_SENTINEL}")
    assert not is_silence_sentinel(
        f"Send `{SILENCE_SENTINEL}` when the message is not for you — that is the whole delivery."
    )
    assert not is_silence_sentinel("<response>no</response>")
    assert not is_silence_sentinel("<responses></responses>")


async def test_a_surface_that_delivered_nothing_settles_the_writeback(
    db: None, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """A silent turn is delivered, not retried and not failed: nothing was owed, so the row closes
    with no reply ref recorded and no attachment phase — there is no message to attach to. A `None`
    ref instead of the explicit outcome would read as "not posted yet" and re-post every drain."""
    workspace_id, _, _ = await _seed()
    turn_id = await _seed_turn(workspace_id, "CQUIET:1.0", "done", SILENCE_SENTINEL)
    poller, surface = _poller(workspace_id, SilentSurface(), FilesystemBlobStore(root=tmp_path))

    with caplog.at_level(logging.INFO, logger="ufo"):
        await poller.drain()
        await poller.drain()

    row = await _writeback(turn_id)
    assert row.status == WRITEBACK_DELIVERED
    assert row.reply_ref is None
    assert row.last_error is None
    assert surface.posted == [turn_id]
    assert surface.attached == []
    reported = next(
        record
        for record in caplog.records
        if record.getMessage() == "surface.writeback_nothing_delivered"
    )
    assert reported.__dict__["ufo"]["turn_id"] == str(turn_id)


def test_an_inbox_name_is_one_leaf_however_the_surface_was_handed_it() -> None:
    """The one guard both inboxes name a delivered file with. A traversing name keeps only its leaf,
    both separators are read, a name with nothing usable in it falls back, the charset collapses and
    the length caps — the cap and the charset used to be the web surface's alone, which is the
    per-surface drift this replaces — and a repeat in one batch is numbered, never overwritten.

    The charset is word characters, so a member whose filename is not ASCII reads their own name
    back in the note: unifying the two surfaces must not cost Slack's names their script.

    The cap falls on the stem so an ordinary long attachment keeps the suffix a read routes on —
    a capped `.txt` that came back extensionless was read as a binary and refused. A suffix with no
    room under the cap is not an extension and is cut like anything else."""
    used: set[str] = set()
    assert inbox_name("../../etc/passwd", used) == "passwd"
    assert inbox_name(r"C:\\Users\\me\\report.txt", used) == "report.txt"
    assert inbox_name("..", used) == "file"
    assert inbox_name("we ird&name!!.txt", used) == "we-ird-name--.txt"
    assert inbox_name("x" * 300 + ".txt", used) == "x" * 76 + ".txt"
    assert inbox_name("y." + "z" * 300, used) == "z" * 79
    assert inbox_name("passwd", used) == "passwd-1"
    assert inbox_name("отчёт.pdf", used) == "отчёт.pdf"
    assert used == {
        "passwd",
        "passwd-1",
        "report.txt",
        "file",
        "we-ird-name--.txt",
        "x" * 76 + ".txt",
        "z" * 79,
        "отчёт.pdf",
    }


async def test_write_workspace_file_replaces_a_planted_symlink(db: None, tmp_path) -> None:
    """An inbound attachment lands through the carrier's containment guard, so a link the agent left
    in its own inbox directory on an earlier turn is replaced rather than written through: the host
    file it pointed at is untouched, and the member's attachment still arrives.

    Replacing is what keeps the surface working. `slack-inbox/<name>` is Slack's name, not ours, so
    refusing here would let one planted link deny every later message carrying a file of that
    name — and the rename cannot follow the link anyway."""
    workspace_id, _, _ = await _seed()
    root = tmp_path / "workspaces"
    context = _context(
        workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path), _sandboxes(root)
    )
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"host secret")

    async def _chunks():
        yield b"inbound bytes"

    with ws(workspace_id):
        conversation_id = await _conversation_row(workspace_id, queue_key="inbox")
        inbox = root / str(conversation_id) / "slack-inbox"
        inbox.mkdir(parents=True)
        landed = inbox / "note.txt"
        landed.symlink_to(outside)
        await context.write_workspace_file(conversation_id, "slack-inbox/note.txt", _chunks())
    assert outside.read_bytes() == b"host secret"
    assert not landed.is_symlink()
    assert landed.read_bytes() == b"inbound bytes"


async def test_write_workspace_file_refuses_a_link_out_of_the_workspace(db: None, tmp_path) -> None:
    """A link at a *directory* on the way, pointing out of the conversation's workspace, is refused
    outright: there is no name inside the workspace for the bytes to land on, so the delivery fails
    loudly instead of writing into whatever the link named."""
    workspace_id, _, _ = await _seed()
    root = tmp_path / "workspaces"
    context = _context(
        workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path), _sandboxes(root)
    )
    outside = tmp_path / "outside"
    outside.mkdir()

    async def _chunks():
        yield b"inbound bytes"

    with ws(workspace_id):
        conversation_id = await _conversation_row(workspace_id, queue_key="inbox")
        workspace = root / str(conversation_id)
        workspace.mkdir(parents=True)
        (workspace / "slack-inbox").symlink_to(outside, target_is_directory=True)
        with pytest.raises(ContainmentError):
            await context.write_workspace_file(conversation_id, "slack-inbox/note.txt", _chunks())
    assert list(outside.iterdir()) == []


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
        # Malformed at the workspace's own domain: a surface asserts the address, so the shape
        # rule is what stops a row no sign-in normalizes to and no later join equals. A rule
        # reading only the last `@` admits both — they carry `example.com` — and seats them.
        ("USPACE", "jane doe@example.com"),
        ("UTWICE", "a@b@example.com"),
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


def test_ingress_url_addresses_the_site_the_ingress_resolves(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mint and the ingress agree by construction: the label the surface puts in the hostname
    parses back to the same `(conversation, port)`, and the view token in the path verifies to the
    workspace the context is bound to. It is a *view* token, never the session kind the ingress
    accepts as a cookie, so the link cannot be pasted into a jar to skip the handshake. The secret
    stays core-side — a surface holds neither end."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    workspace_id, conversation_id = uuid4(), uuid4()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    url = context.ingress_url(conversation_id, 8000, "/")
    assert url is not None
    base = urlsplit(url)
    label, _, host = base.netloc.partition(".")
    assert host == "sites.example.test"
    assert parse_site_label(label) == (conversation_id, 8000)
    minted = base.path.removeprefix(f"{INGRESS_VIEW_PATH}/")
    claims = verify_ingress_token(minted, datetime.now(UTC), INGRESS_VIEW_KIND)
    assert (claims.workspace_id, claims.conversation_id, claims.port) == (
        workspace_id,
        conversation_id,
        8000,
    )
    with pytest.raises(IngressTokenError):
        verify_ingress_token(minted, datetime.now(UTC), INGRESS_SESSION_KIND)


def test_ingress_url_mints_the_configured_ttl_and_keeps_the_bases_port(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two decisions this mint sells but nothing read. The TTL is the containment the docstring
    claims — a leaked link stops opening sessions — so the minted `expires_at` is asserted, not just
    that the token verifies (which only says it has not expired *yet*: a ten-year token passes it).
    And the label goes in front of `netloc`, not `hostname`, so a base carrying a port keeps it;
    `hostname` would drop it and every site on that deploy would 404 at a label the config
    deliberately admits."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    workspace_id, conversation_id = uuid4(), uuid4()
    context = replace(
        _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path)),
        _ingress_public_url="https://sites.example.test:8443",
    )
    before = int(datetime.now(UTC).timestamp())
    url = context.ingress_url(conversation_id, 8000, "/")
    assert url is not None
    base = urlsplit(url)
    assert base.netloc.endswith(".sites.example.test:8443")
    assert base.port == 8443
    claims = verify_ingress_token(
        base.path.removeprefix(f"{INGRESS_VIEW_PATH}/"), datetime.now(UTC), INGRESS_VIEW_KIND
    )
    assert claims.expires_at - before == pytest.approx(INGRESS_VIEW_TTL_SECONDS, abs=2)


def test_ingress_url_carries_the_entry_path_the_frame_asked_for(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry path rides after the token, where the ingress reads it as where to land the session
    it binds — the site's own paths are reachable no other way from outside. The root appends
    nothing, so the common link keeps the shape it always had, and a path is quoted rather than
    trusted, so a space cannot split the URL. The token stays parseable either way: `sign_token`
    emits `base64url.base64url`, so the first `/` after it is always the boundary."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    workspace_id, conversation_id = uuid4(), uuid4()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    rooted = urlsplit(context.ingress_url(conversation_id, 8000, "/") or "").path
    assert not rooted.endswith("/")
    deep = urlsplit(context.ingress_url(conversation_id, 8000, "/send/a b") or "").path
    assert deep.endswith("/send/a%20b")
    token, _, entry = deep.removeprefix(f"{INGRESS_VIEW_PATH}/").partition("/")
    assert entry == "send/a%20b"
    assert verify_ingress_token(token, datetime.now(UTC), INGRESS_VIEW_KIND).port == 8000


def test_ingress_url_is_none_without_a_configured_base(tmp_path) -> None:
    """A deploy that serves no sandbox port has no address to mint against, so the surface names no
    site rather than inventing a hostname."""
    context = _context(uuid4(), StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert replace(context, _ingress_public_url=None).ingress_url(uuid4(), 8000, "/") is None


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
    async with workspace_tx() as connection:
        target = (
            await connection.execute(
                sa.select(tables.turn.c.conversation_id, tables.turn.c.agent_id).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).one()
    assert surface.targets == [(target.conversation_id, target.agent_id)]


async def test_poller_attaches_same_instant_files_in_key_order(db: None, tmp_path) -> None:
    """The poller's artifact read carries the same `(created_at, blob_key)` order as
    `shared_artifacts`, so two files sharing one timestamp reach a durable surface's `attach`
    deterministically — the identity both docstrings claim."""
    workspace_id, _, _ = await _seed()
    blob = FilesystemBlobStore(root=tmp_path)
    await blob.put("artifacts/x/report.pdf", b"PDF")
    await blob.put("artifacts/x/data.csv", b"CSV")
    report = SharedArtifact(
        blob_key="artifacts/x/report.pdf",
        filename="report.pdf",
        subject=None,
        media_type="application/pdf",
        size_bytes=3,
    )
    data = SharedArtifact(
        blob_key="artifacts/x/data.csv",
        filename="data.csv",
        subject=None,
        media_type="text/csv",
        size_bytes=3,
    )
    turn_id = await _seed_turn(workspace_id, "C7:1.0", "done", "files", artifacts=(report, data))
    poller, surface = _poller(workspace_id, RecordingSurface(ref="C7:9.9"), blob)
    await poller.drain()
    assert surface.attached == [(turn_id, "C7:9.9", ("data.csv", "report.pdf"))]


async def test_shared_artifacts_reads_a_turns_files_deterministically(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    report = SharedArtifact(
        blob_key="artifacts/x/report.pdf",
        filename="report.pdf",
        subject="the report",
        media_type="application/pdf",
        size_bytes=3,
    )
    data = SharedArtifact(
        blob_key="artifacts/x/data.csv",
        filename="data.csv",
        subject=None,
        media_type="text/csv",
        size_bytes=9,
    )
    turn_id = await _seed_turn(workspace_id, "C6:1.0", "done", "files", artifacts=(report, data))
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await context.shared_artifacts(turn_id) == (data, report)
    assert await context.shared_artifacts(uuid4()) == ()
    foreign = _context(uuid4(), StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await foreign.shared_artifacts(turn_id) == ()


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
    """A workspace stuck in its post never holds another workspace's delivery.

    No wait is bounded by how long a loaded runner may take, since the independence this proves has
    no time semantics; each is bounded by the drain ending and by the wedge watchdog."""
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
    both_posted = asyncio.gather(surface.blocked.wait(), surface.fast.wait())
    try:
        await _await_posted(both_posted, drain, "both workspaces posted")
        await _await_status(fast_turn, WRITEBACK_DELIVERED, drain)
        assert fast_turn in surface.posted
    finally:
        both_posted.cancel()
        surface.release.set()
        await asyncio.wait_for(drain, timeout=WEDGE_WATCHDOG_SECONDS)
    assert (await _writeback(slow_turn)).status == WRITEBACK_DELIVERED


async def test_runner_pages_beyond_a_slow_workspace(
    db: None, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A workspace stuck in its post occupies one in-flight slot, so the runner pages past it to a
    workspace outside the first batch and delivers there while the stuck claim is still held.

    No wait is bounded by how long a loaded runner may take, since the paging this proves has no
    time semantics; each is bounded by the runner ending and by the wedge watchdog. `run()` loops
    forever and logs every exception rather than raising, so the watchdog is what actually fails a
    regression here — without it a broken pager hangs the shard and every other test in it."""
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
    blocked = asyncio.ensure_future(surface.blocked.wait())
    try:
        await _await_posted(blocked, running, "the slow workspace posted")
        await _await_status(turns[later_workspace], WRITEBACK_DELIVERED, running)
        assert (await _writeback(turns[blocked_workspace])).status == WRITEBACK_CLAIMED
    finally:
        blocked.cancel()
        surface.release.set()
        await _await_status(turns[blocked_workspace], WRITEBACK_DELIVERED, running)
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
    """A live delivery holds its claim by renewing it, so a peer draining the same workspace
    recovers nothing while the delivery is still in flight.

    Every wait is bounded by the drain, never by a wall clock. The claim this proves is renewed
    regardless of expiry, so nothing here has time semantics: a loaded runner stretches each
    refresh arbitrarily while the renewal it must prove stays correct, and the drain ending early
    is the only way the posts and their refreshes never arrive."""
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
    posted_and_refreshed = asyncio.gather(surface.blocked.wait(), all_refreshed_once.wait())
    renewed_twice = asyncio.ensure_future(all_refreshed_twice.wait())
    try:
        await asyncio.wait((posted_and_refreshed, running), return_when=asyncio.FIRST_COMPLETED)
        assert posted_and_refreshed.done(), "the drain ended before every turn posted and refreshed"
        expired = datetime.now(UTC) - timedelta(seconds=1)
        for turn_id in turn_ids:
            await _set_writeback(turn_id, claim_expires_at=expired)
        expired_claims = {
            turn_id: (await _writeback(turn_id)).claim_expires_at for turn_id in turn_ids
        }
        next_refresh.set()
        await asyncio.wait((renewed_twice, running), return_when=asyncio.FIRST_COMPLETED)
        assert renewed_twice.done(), "the drain ended before every claim renewed a second time"
        for turn_id in turn_ids:
            renewed = await _writeback(turn_id)
            assert renewed.status == WRITEBACK_CLAIMED
            assert renewed.claimed_by == "worker-1"
            assert renewed.claim_expires_at != expired_claims[turn_id]
        peer_surface = RecordingSurface()
        await _fleet_poller(contexts, peer_surface, worker_id="worker-2").drain()
        assert peer_surface.posted == []
    finally:
        posted_and_refreshed.cancel()
        renewed_twice.cancel()
        surface.release.set()
        await running
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
    """A refresh that finds the claim taken abandons the delivery it was holding open, leaving the
    row to its new owner.

    The drain *ending* is this test's assertion, so unlike its siblings there is no event to race
    it against: a poller that ignores the lost claim does nothing observable, because
    `_refresh_claim` raises `_WritebackClaimLost`, which kills the renewal that would otherwise
    keep counting. Failure here is silence, and `WEDGE_WATCHDOG_SECONDS` is what names it."""
    monkeypatch.setattr(surface_module, "WRITEBACK_CLAIM_REFRESH_SECONDS", 0.01)
    workspace_id, _, _ = await _seed()
    turn_id = await _seed_turn(workspace_id, "CSTOLEN:1.0", "done", "slow")
    blob = FilesystemBlobStore(root=tmp_path)
    contexts = {workspace_id: _context(workspace_id, StubDbos(), blob)}
    surface = BlockingSurface(blocked_workspace=workspace_id)
    running = asyncio.create_task(_fleet_poller(contexts, surface, worker_id="worker-1").drain())
    posted = asyncio.ensure_future(surface.blocked.wait())
    try:
        await _await_posted(posted, running, "the turn posted")
        await _set_writeback(
            turn_id,
            claimed_by="worker-2",
            claim_expires_at=datetime.now(UTC) + timedelta(seconds=60),
        )
        await asyncio.wait_for(running, timeout=WEDGE_WATCHDOG_SECONDS)
    finally:
        posted.cancel()
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


async def _seed_conversation(
    workspace_id: UUID,
    agent_id: UUID,
    *,
    queue_key: str,
    audience: str,
    member_id: UUID | None,
    surface: str = SURFACE,
) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface=surface,
                queue_key=queue_key,
                member_id=member_id,
                audience=audience,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


async def _recorded_disclosures(workspace_id: UUID) -> int:
    """How many disclosures the workspace holds. No surface lists these rows — the record is the
    operator's, read with `ufoctl transcript-reads` — so a test counts them at the table."""
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.transcript_access)
                .where(tables.transcript_access.c.workspace_id == workspace_id)
            )
        ).scalar_one()


async def _seed_conversation_turn(
    workspace_id: UUID,
    conversation_id: UUID,
    agent_id: UUID,
    *,
    seq: int,
    inbound: str,
    parent_turn_id: UUID | None = None,
    subagent_profile: str | None = None,
    speaker_member_id: UUID | None = None,
    context: TurnContext | None = None,
    admission_source: str = "member",
    idempotency_key: str | None = None,
) -> UUID:
    turn_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=seq,
                admission_source=admission_source,
                idempotency_key=idempotency_key,
                status="done",
                inbound=inbound,
                terminal=TerminalFrame(status="done", text="ok").model_dump(mode="json"),
                parent_turn_id=parent_turn_id,
                subagent_profile=subagent_profile,
                speaker_member_id=speaker_member_id,
                context=None if context is None else context.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


async def _seed_member_row(workspace_id: UUID, email: str) -> UUID:
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=email,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id


async def _seed_agent_row(workspace_id: UUID, name: str) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=name,
                prompt="be operational",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


async def test_agent_conversations_list_by_audience_and_wall(db: None, tmp_path) -> None:
    """The list a member reads: their own conversations and the workspace-shared ones, never
    another member's private one, a room's, or an externally-shared channel's. An admin lists every
    conversation of the agent as administration metadata, and each row says whether its content is
    readable — false exactly where an admin lists what they may not read. Another agent's
    conversations are absent whoever asks, and a subagent conversation never lists beside its
    parent."""
    workspace_id, agent_id, member_id = await _seed(member_email="m@example.com")
    assert member_id is not None
    other_id = await _seed_member_row(workspace_id, "n@example.com")
    second_agent = await _seed_agent_row(workspace_id, "ops")
    mine = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="mine",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
    )
    shared = await _seed_conversation(
        workspace_id, agent_id, queue_key="shared", audience=str(SHARED_AUDIENCE), member_id=None
    )
    theirs = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="theirs",
        audience=str(conversation_audience(other_id)),
        member_id=other_id,
    )
    room = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="room",
        audience=str(room_audience("slack", "C7")),
        member_id=None,
    )
    foreign = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="foreign",
        audience=str(foreign_room_audience("slack", "C8")),
        member_id=None,
    )
    subagent = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="child",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
        surface=SUBAGENT_SURFACE,
    )
    walled = await _seed_conversation(
        workspace_id,
        second_agent,
        queue_key="walled",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
    )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))

    listed = await context.list_agent_conversations(agent_id, member_id, admin=False, limit=50)
    assert {entry.summary.id for entry in listed} == {mine, shared}
    assert all(entry.readable for entry in listed)
    assert {entry.summary.member_email for entry in listed} == {"m@example.com", None}

    as_admin = await context.list_agent_conversations(agent_id, member_id, admin=True, limit=50)
    assert {entry.summary.id for entry in as_admin} == {mine, shared, theirs, room, foreign}
    assert {entry.summary.id for entry in as_admin if not entry.readable} == {theirs, room, foreign}
    assert {entry.summary.id for entry in as_admin if entry.disclosable} == {theirs}
    assert subagent not in {entry.summary.id for entry in as_admin}
    assert walled not in {entry.summary.id for entry in as_admin}

    other_agent = await context.list_agent_conversations(
        second_agent, member_id, admin=True, limit=50
    )
    assert {entry.summary.id for entry in other_agent} == {walled}


async def test_agent_conversations_order_and_bound_by_activity(db: None, tmp_path) -> None:
    """Newest activity first, with the caller's limit as the bound."""
    workspace_id, agent_id, member_id = await _seed(member_email="m@example.com")
    assert member_id is not None
    older = await _seed_conversation(
        workspace_id, agent_id, queue_key="older", audience=str(SHARED_AUDIENCE), member_id=None
    )
    newer = await _seed_conversation(
        workspace_id, agent_id, queue_key="newer", audience=str(SHARED_AUDIENCE), member_id=None
    )
    await _seed_conversation_turn(workspace_id, older, agent_id, seq=1, inbound="first")
    await _seed_conversation_turn(workspace_id, newer, agent_id, seq=1, inbound="second")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(updated_at=datetime.now(UTC) - timedelta(hours=2))
            .where(tables.turn.c.conversation_id == older)
        )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))

    listed = await context.list_agent_conversations(agent_id, member_id, admin=False, limit=50)
    assert [entry.summary.id for entry in listed] == [newer, older]
    assert [entry.summary.turn_count for entry in listed] == [1, 1]
    assert all(entry.summary.last_turn_at is not None for entry in listed)
    bounded = await context.list_agent_conversations(agent_id, member_id, admin=False, limit=1)
    assert [entry.summary.id for entry in bounded] == [newer]
    linked = await context.list_agent_conversations(
        agent_id,
        member_id,
        admin=False,
        limit=1,
        conversation_id=older,
    )
    assert [entry.summary.id for entry in linked] == [older]


async def test_agent_conversations_carry_their_opening_words_and_their_speakers(
    db: None, tmp_path
) -> None:
    """What a conversation is about and who is in it, off the page's turns. The opening words are
    the member's own out of the first turn — the ambient digest a channel surface renders around
    them is not what the conversation is about — capped, and empty where no turn has landed.
    Speakers run in order of first appearance, once each however often they speak, carrying the
    display line the surface reported and the address where it reported none."""
    workspace_id, agent_id, member_id = await _seed(member_email="m@example.com")
    assert member_id is not None
    peer_id = await _seed_member_row(workspace_id, "peer@example.com")
    channel = await _seed_conversation(
        workspace_id, agent_id, queue_key="C7", audience=str(SHARED_AUDIENCE), member_id=None
    )
    marker = mint_marker()
    await _seed_conversation_turn(
        workspace_id,
        channel,
        agent_id,
        seq=1,
        inbound=fence_member_message(
            marker,
            "<ambient_1>\nbystander: deploy is red again\n</ambient_1>\n",
            "can you take a look at the failing deploy",
            "",
        ),
        speaker_member_id=member_id,
        context=TurnContext(sender="Mel Okafor (m@example.com)"),
    )
    await _seed_conversation_turn(
        workspace_id,
        channel,
        agent_id,
        seq=2,
        inbound="thanks",
        speaker_member_id=peer_id,
        context=TurnContext(sender="Pat Reyes (peer@example.com)"),
    )
    await _seed_conversation_turn(
        workspace_id, channel, agent_id, seq=3, inbound="anything else?", speaker_member_id=peer_id
    )
    await _seed_conversation_turn(
        workspace_id, channel, agent_id, seq=4, inbound="no", speaker_member_id=member_id
    )
    await _seed_conversation_turn(workspace_id, channel, agent_id, seq=5, inbound="a timer fired")
    long_open = await _seed_conversation(
        workspace_id, agent_id, queue_key="long", audience=str(SHARED_AUDIENCE), member_id=None
    )
    await _seed_conversation_turn(
        workspace_id, long_open, agent_id, seq=1, inbound="w" * (OPENING_MESSAGE_CHARS + 50)
    )
    turnless = await _seed_conversation(
        workspace_id, agent_id, queue_key="quiet", audience=str(SHARED_AUDIENCE), member_id=None
    )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))

    listed = await context.list_agent_conversations(agent_id, member_id, admin=False, limit=50)
    by_id = {entry.summary.id: entry for entry in listed}

    assert by_id[channel].opening_message == "can you take a look at the failing deploy"
    assert "bystander" not in by_id[channel].opening_message
    assert [(who.email, who.sender) for who in by_id[channel].speakers] == [
        ("m@example.com", "Mel Okafor (m@example.com)"),
        ("peer@example.com", "Pat Reyes (peer@example.com)"),
    ]
    assert by_id[long_open].opening_message == "w" * OPENING_MESSAGE_CHARS
    assert by_id[turnless].opening_message == ""
    assert by_id[turnless].speakers == ()


async def test_an_unreadable_conversation_carries_no_words_and_no_speakers(
    db: None, tmp_path
) -> None:
    """A row an admin lists but may not read is administration metadata and nothing more: it states
    whose it is and how busy, and carries neither the words that opened it nor who else is in it.
    Reading it is the acknowledgement's act, audited by `record_transcript_access` — a listing that
    quoted the first message would hand an admin the content the acknowledgement exists to record.
    A room stays walled on the same read for the same admin."""
    workspace_id, agent_id, admin_id = await _seed(member_email="boss@example.com")
    assert admin_id is not None
    owner_id = await _seed_member_row(workspace_id, "owner@example.com")
    theirs = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="theirs",
        audience=str(conversation_audience(owner_id)),
        member_id=owner_id,
    )
    room = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="room",
        audience=str(room_audience("slack", "C7")),
        member_id=None,
    )
    for conversation_id in (theirs, room):
        await _seed_conversation_turn(
            workspace_id,
            conversation_id,
            agent_id,
            seq=1,
            inbound="the salary review spreadsheet",
            speaker_member_id=owner_id,
            context=TurnContext(sender="Robin Vale (owner@example.com)"),
        )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))

    listed = await context.list_agent_conversations(agent_id, admin_id, admin=True, limit=50)
    by_id = {entry.summary.id: entry for entry in listed}

    assert by_id[theirs].disclosable is True
    assert by_id[theirs].summary.member_email == "owner@example.com"
    for conversation_id in (theirs, room):
        assert by_id[conversation_id].readable is False
        assert by_id[conversation_id].opening_message == ""
        assert by_id[conversation_id].speakers == ()


async def test_agent_conversation_speakers_stop_at_the_bound(db: None, tmp_path) -> None:
    """A conversation more members have spoken in than a row can name carries the first
    `MAX_CONVERSATION_SPEAKERS` of them and no more — the read is bounded by the page, never by
    how loud one channel is."""
    workspace_id, agent_id, member_id = await _seed(member_email="m@example.com")
    assert member_id is not None
    crowded = await _seed_conversation(
        workspace_id, agent_id, queue_key="crowd", audience=str(SHARED_AUDIENCE), member_id=None
    )
    speakers = [member_id] + [
        await _seed_member_row(workspace_id, f"member{index}@example.com")
        for index in range(MAX_CONVERSATION_SPEAKERS + 3)
    ]
    for seq, speaker in enumerate(speakers, start=1):
        await _seed_conversation_turn(
            workspace_id, crowded, agent_id, seq=seq, inbound="hi", speaker_member_id=speaker
        )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))

    listed = await context.list_agent_conversations(agent_id, member_id, admin=False, limit=50)

    assert len(listed) == 1
    assert [who.email for who in listed[0].speakers] == [
        "m@example.com",
        *(f"member{index}@example.com" for index in range(MAX_CONVERSATION_SPEAKERS - 1)),
    ]


async def test_readable_conversation_holds_the_audience_and_the_wall(db: None, tmp_path) -> None:
    """The one content gate: a member's own conversation and a shared one read, another member's
    private one and a room's do not, and the same conversation under another agent's id is
    unreadable — the answer every content route fails closed on."""
    workspace_id, agent_id, member_id = await _seed(member_email="m@example.com")
    assert member_id is not None
    other_id = await _seed_member_row(workspace_id, "n@example.com")
    second_agent = await _seed_agent_row(workspace_id, "ops")
    mine = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="mine",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
    )
    shared = await _seed_conversation(
        workspace_id, agent_id, queue_key="shared", audience=str(SHARED_AUDIENCE), member_id=None
    )
    theirs = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="theirs",
        audience=str(conversation_audience(other_id)),
        member_id=other_id,
    )
    room = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="room",
        audience=str(room_audience("slack", "C7")),
        member_id=None,
    )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))

    assert await context.readable_conversation(mine, agent_id, member_id) is True
    assert await context.readable_conversation(shared, agent_id, member_id) is True
    assert await context.readable_conversation(theirs, agent_id, member_id) is False
    assert await context.readable_conversation(room, agent_id, member_id) is False
    assert await context.readable_conversation(mine, second_agent, member_id) is False
    assert await context.readable_conversation(uuid4(), agent_id, member_id) is False
    assert await context.readable_conversation(theirs, agent_id, other_id) is True


async def test_an_admin_reads_a_private_transcript_only_once_recorded(db: None, tmp_path) -> None:
    """Admin alone does not open another member's private conversation: the gate answers false
    until `record_transcript_access` writes the disclosure, and true afterwards. A room stays shut
    to that same admin, because no portal read can check a peer surface's roster."""
    workspace_id, agent_id, member_id = await _seed(member_email="admin@example.com")
    assert member_id is not None
    other_id = await _seed_member_row(workspace_id, "n@example.com")
    theirs = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="theirs",
        audience=str(conversation_audience(other_id)),
        member_id=other_id,
    )
    room = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="room",
        audience=str(room_audience("slack", "C7")),
        member_id=None,
    )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))

    assert await context.readable_conversation(theirs, agent_id, member_id, admin=True) is False
    recorded = await record_transcript_access(workspace_id, theirs, agent_id, member_id)
    assert recorded is not None
    assert (recorded.reader_email, recorded.subject_email) == ("admin@example.com", "n@example.com")
    assert await context.readable_conversation(theirs, agent_id, member_id, admin=True) is True
    assert await context.readable_conversation(theirs, agent_id, member_id) is False
    assert await record_transcript_access(workspace_id, room, agent_id, member_id) is None
    assert await context.readable_conversation(room, agent_id, member_id, admin=True) is False
    mine = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="mine",
        audience=str(conversation_audience(member_id)),
        member_id=member_id,
    )
    assert await record_transcript_access(workspace_id, mine, agent_id, member_id) is None
    assert await _recorded_disclosures(workspace_id) == 1


async def test_a_disclosure_reports_itself_to_the_operator(
    db: None, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """No member surface lists these rows, so the disclosure reports itself where an operator
    watches: one `surface.transcript_disclosed` record per acknowledgement, naming the reader, the
    subject, and the conversation. A refused acknowledgement writes nothing and reports nothing."""
    workspace_id, agent_id, member_id = await _seed(member_email="boss@example.com")
    assert member_id is not None
    subject = await _seed_member_row(workspace_id, "m@example.com")
    theirs = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="theirs",
        audience=str(conversation_audience(subject)),
        member_id=subject,
    )
    room = await _seed_conversation(
        workspace_id, agent_id, queue_key="room", audience="room:slack:C7", member_id=None
    )
    with caplog.at_level(logging.INFO, logger="ufo"):
        assert await record_transcript_access(workspace_id, theirs, agent_id, member_id)
        assert await record_transcript_access(workspace_id, room, agent_id, member_id) is None
    disclosed = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "surface.transcript_disclosed"
    ]
    assert [
        (entry["reader_email"], entry["subject_email"], entry["conversation_id"])
        for entry in disclosed
    ] == [("boss@example.com", "m@example.com", str(theirs))]


async def test_a_stale_disclosure_closes_the_transcript_again(db: None, tmp_path) -> None:
    """The disclosure opens the conversation for a bounded window, so an admin who acknowledged
    once does not read silently forever — past the window the gate shuts and a fresh
    acknowledgement is another recorded access."""
    workspace_id, agent_id, member_id = await _seed(member_email="admin@example.com")
    assert member_id is not None
    other_id = await _seed_member_row(workspace_id, "n@example.com")
    theirs = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="theirs",
        audience=str(conversation_audience(other_id)),
        member_id=other_id,
    )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await record_transcript_access(workspace_id, theirs, agent_id, member_id) is not None
    assert await context.readable_conversation(theirs, agent_id, member_id, admin=True) is True

    stale = datetime.now(UTC) - TRANSCRIPT_ACCESS_WINDOW - timedelta(minutes=1)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.transcript_access)
            .where(tables.transcript_access.c.conversation_id == theirs)
            .values(created_at=stale)
        )

    assert await context.readable_conversation(theirs, agent_id, member_id, admin=True) is False
    assert await record_transcript_access(workspace_id, theirs, agent_id, member_id) is not None
    assert await context.readable_conversation(theirs, agent_id, member_id, admin=True) is True
    assert await _recorded_disclosures(workspace_id) == 2


async def test_one_disclosure_opens_exactly_its_own_conversation_for_its_own_reader(
    db: None, tmp_path
) -> None:
    """A disclosure is scoped to the pair it names. Acknowledging member M's conversation opens
    that conversation and no other private one of the same agent, and opens it for the admin who
    acknowledged and for no other admin — the two identity predicates the gate answers on."""
    workspace_id, agent_id, first_admin = await _seed(member_email="one@example.com")
    assert first_admin is not None
    second_admin = await _seed_member_row(workspace_id, "two@example.com")
    subject = await _seed_member_row(workspace_id, "m@example.com")
    other_subject = await _seed_member_row(workspace_id, "n@example.com")
    acknowledged = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="acknowledged",
        audience=str(conversation_audience(subject)),
        member_id=subject,
    )
    untouched = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="untouched",
        audience=str(conversation_audience(other_subject)),
        member_id=other_subject,
    )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert (
        await record_transcript_access(workspace_id, acknowledged, agent_id, first_admin)
        is not None
    )

    assert (
        await context.readable_conversation(acknowledged, agent_id, first_admin, admin=True) is True
    )
    assert (
        await context.readable_conversation(untouched, agent_id, first_admin, admin=True) is False
    )
    assert (
        await context.readable_conversation(acknowledged, agent_id, second_admin, admin=True)
        is False
    )


async def test_a_live_row_opens_nothing_for_a_non_admin_or_a_room(db: None, tmp_path) -> None:
    """The two clauses the disclosure lookup sits behind, each with a live row present so the
    lookup itself cannot be what refuses. A row naming a reader who is no longer an admin opens
    nothing — losing the role closes an open window immediately — and a row against a room or an
    externally-shared channel opens nothing for anyone, because participation there is the peer
    surface's live roster that no portal read can check."""
    workspace_id, agent_id, reader_id = await _seed(member_email="reader@example.com")
    assert reader_id is not None
    subject_id = await _seed_member_row(workspace_id, "m@example.com")
    private = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="private",
        audience=str(conversation_audience(subject_id)),
        member_id=subject_id,
    )
    room = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="room",
        audience=str(room_audience("slack", "C7")),
        member_id=None,
    )
    external = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="external",
        audience=str(foreign_room_audience("slack", "C8")),
        member_id=None,
    )
    async with workspace_tx() as connection:
        for conversation_id in (private, room, external):
            await connection.execute(
                sa.insert(tables.transcript_access).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    reader_member_id=reader_id,
                    subject_member_id=subject_id,
                    created_at=datetime.now(UTC),
                )
            )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))

    assert await context.readable_conversation(private, agent_id, reader_id, admin=True) is True
    assert await context.readable_conversation(private, agent_id, reader_id, admin=False) is False
    for conversation_id in (room, external):
        assert (
            await context.readable_conversation(conversation_id, agent_id, reader_id, admin=True)
            is False
        )


async def test_conversation_subagent_turns_nest_transitively(db: None, tmp_path) -> None:
    """Every turn spawned beneath the conversation's turns, however deep, oldest first, and nothing
    spawned beneath another conversation's."""
    workspace_id, agent_id, _ = await _seed()
    conversation_id = await _seed_conversation(
        workspace_id, agent_id, queue_key="root", audience=str(SHARED_AUDIENCE), member_id=None
    )
    sibling = await _seed_conversation(
        workspace_id, agent_id, queue_key="sibling", audience=str(SHARED_AUDIENCE), member_id=None
    )
    parent_turn = await _seed_conversation_turn(
        workspace_id, conversation_id, agent_id, seq=1, inbound="ask"
    )
    child_conversation = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="child",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
        surface=SUBAGENT_SURFACE,
    )
    child_turn = await _seed_conversation_turn(
        workspace_id,
        child_conversation,
        agent_id,
        seq=1,
        inbound="research",
        parent_turn_id=parent_turn,
        subagent_profile="researcher",
    )
    grandchild_conversation = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="grandchild",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
        surface=SUBAGENT_SURFACE,
    )
    grandchild_turn = await _seed_conversation_turn(
        workspace_id,
        grandchild_conversation,
        agent_id,
        seq=1,
        inbound="read",
        parent_turn_id=child_turn,
        subagent_profile="reader",
    )
    sibling_turn = await _seed_conversation_turn(
        workspace_id, sibling, agent_id, seq=1, inbound="other"
    )
    sibling_child = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="sibling-child",
        audience=str(SHARED_AUDIENCE),
        member_id=None,
        surface=SUBAGENT_SURFACE,
    )
    await _seed_conversation_turn(
        workspace_id,
        sibling_child,
        agent_id,
        seq=1,
        inbound="unrelated",
        parent_turn_id=sibling_turn,
        subagent_profile="researcher",
    )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))

    nested = await context.conversation_subagent_turns(conversation_id)
    assert [turn.id for turn in nested] == [child_turn, grandchild_turn]
    assert [turn.parent_turn_id for turn in nested] == [parent_turn, child_turn]
    assert [turn.subagent_profile for turn in nested] == ["researcher", "reader"]
    assert [turn.id for turn in await context.conversation_subagent_turns(child_conversation)] == [
        grandchild_turn
    ]
    assert await context.conversation_subagent_turns(grandchild_conversation) == ()


async def test_agent_origin_refs_names_only_the_machine_envelopes(db: None, tmp_path) -> None:
    """The seam the portal's no-bubble rule rests on. A firing and a delivered subagent result are
    envelopes the member cannot read; an extension's invoke sends prose it is meant to read, and a
    member's own words must never be named here — the one direction this must not fail in."""
    workspace_id, agent_id, member_id = await _seed()
    conversation_id = await _seed_conversation(
        workspace_id, agent_id, queue_key="root", audience=str(SHARED_AUDIENCE), member_id=None
    )
    spoke = await _seed_conversation_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=1,
        inbound="find the flaky test",
        speaker_member_id=member_id,
    )
    fired = await _seed_conversation_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=2,
        inbound="<scheduled_task>…</scheduled_task>\ndigest",
        admission_source="scheduled",
    )
    invoked = await _seed_conversation_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=3,
        inbound="Review this exact pull-request comparison.",
        admission_source="internal",
    )
    delivered = await _seed_conversation_turn(
        workspace_id,
        conversation_id,
        agent_id,
        seq=4,
        inbound='<subagent_result profile="x" subagent_id="y" status="done">…</subagent_result>',
        admission_source="internal",
        idempotency_key=f"{SUBAGENT_RESULT_KEY_PREFIX}{uuid4()}",
    )

    refs = await _context(
        workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path / "blobs")
    ).agent_origin_refs(conversation_id)

    assert str(fired) in refs
    assert str(delivered) in refs
    assert str(spoke) not in refs
    assert str(invoked) not in refs
