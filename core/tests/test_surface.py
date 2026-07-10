"""The core surface seam: the privileged SurfaceContext (admit + identity + workspace write) and the
durable WritebackPoller. A minimal recording surface stands in for a real extension surface — the
dependency, never the thing asserted; every assertion reads durable rows (turn, surface_identity,
writeback) and blob bytes that core wrote. The full end-to-end through a real registered surface is
proven by the sample-extension conformance probe and the Slack extension's own tests."""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

from ufo.blob import FilesystemBlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.surface import (
    WRITEBACK_CLAIMED,
    WRITEBACK_DELIVERED,
    WRITEBACK_FAILED,
    WRITEBACK_MAX_AGE_SECONDS,
    SharedArtifact,
    SurfaceContext,
    SurfaceRoute,
    SurfaceSpec,
    Writeback,
    WritebackPoller,
    workspace_key,
)
from ufo.hub import InProcessHub
from ufo.loop.queue import _load_turn
from ufo.schema import tables
from ufo.schema.records import WRITEBACK_PENDING, TerminalFrame, TurnContext
from ufo.surfaces.admission import Admission, AdmissionInvoker
from ufo.surfaces.hub_tail import HubTailer

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
    attached: list[tuple[UUID, str, tuple[str, ...]]] = field(default_factory=list)

    async def post(self, ctx: SurfaceContext, writeback: Writeback) -> str:
        if self.fail_post:
            raise RuntimeError("post failed")
        return self.ref

    async def attach(self, ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None:
        self.attached.append(
            (writeback.turn_id, reply_ref, tuple(a.filename for a in writeback.artifacts))
        )


async def _unused_ingest(ctx: SurfaceContext, request: object) -> object:
    raise AssertionError("ingest is not exercised by the poller tests")


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


def _context(workspace_id: UUID, dbos: StubDbos, blob: FilesystemBlobStore) -> SurfaceContext:
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    return SurfaceContext(
        workspace_id=workspace_id,
        surface=SURFACE,
        blob=blob,
        _invoker=AdmissionInvoker(
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
                surface=SURFACE,
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
    spec = SurfaceSpec(
        name=SURFACE,
        routes=(SurfaceRoute(method="POST", path="", handler=_unused_ingest),),
        post=surface.post,
        attach=surface.attach,
    )
    context = _context(workspace_id, StubDbos(), blob)
    poller = WritebackPoller(
        workspace_id=workspace_id, worker_id="worker-1", surfaces={SURFACE: (spec, context)}
    )
    return poller, surface


def test_workspace_key_scopes_under_the_conversation_workspace() -> None:
    cid = uuid4()
    assert workspace_key(cid, "slack-inbox/a.txt") == (
        f"conversations/{cid}/workspace/slack-inbox/a.txt"
    )
    for bad in ("../escape", "/etc/passwd", "a/../../b", ""):
        with pytest.raises(ValueError):
            workspace_key(cid, bad)


async def test_admit_queues_a_turn_and_registers_a_writeback(db: None, tmp_path) -> None:
    workspace_id, agent_id, _ = await _seed()
    dbos = StubDbos()
    context = _context(workspace_id, dbos, FilesystemBlobStore(root=tmp_path))
    conversation_id = await context.conversation_for("C1:1.0", None)
    turn_id = await context.admit(conversation_id, agent_id, "hello", idempotency_key="C1:1.0")
    assert dbos.enqueued == [str(turn_id)]
    async with workspace_tx() as connection:
        turn = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.inbound).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).one()
    assert turn.status == "queued"
    assert turn.inbound == "hello"
    assert (await _writeback(turn_id)).status == WRITEBACK_PENDING
    # A redelivery of the same message joins the one turn and one writeback.
    again = await context.admit(conversation_id, agent_id, "hello", idempotency_key="C1:1.0")
    assert again == turn_id


async def test_admitted_context_round_trips_to_the_loaded_turn(db: None, tmp_path) -> None:
    """Both ends of the turn.context column: the surface admits its ambient TurnContext, and the
    queue loader — the engine's one read path — validates the same record back off the row, with
    the admission stamp the engine renders from."""
    workspace_id, agent_id, _ = await _seed()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    conversation_id = await context.conversation_for("C1:1.0", None)
    ambient = TurnContext(sender="Bee Jones (bee@example.com)", timezone="America/New_York")
    turn_id = await context.admit(
        conversation_id, agent_id, "hello", idempotency_key="C1:2.0", context=ambient
    )
    turn, _, _ = await _load_turn(turn_id)
    assert turn.context == ambient
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


async def test_write_workspace_file_streams_into_the_workspace_subtree(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    blob = FilesystemBlobStore(root=tmp_path)
    context = _context(workspace_id, StubDbos(), blob)
    conversation_id = uuid4()

    async def _chunks():
        yield b"hello "
        yield b"world"

    await context.write_workspace_file(conversation_id, "slack-inbox/note.txt", _chunks())
    stored = await blob.get(workspace_key(conversation_id, "slack-inbox/note.txt"))
    assert stored == b"hello world"


async def test_is_workspace_owner_is_the_earliest_member(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    early = datetime(2026, 1, 1, tzinfo=UTC)
    async with workspace_tx() as connection:
        for offset, email in ((0, "owner@example.com"), (5, "joiner@example.com")):
            await connection.execute(
                sa.insert(tables.member).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    email=email,
                    created_at=early + timedelta(minutes=offset),
                    updated_at=early + timedelta(minutes=offset),
                )
            )
    assert await context.is_workspace_owner("OWNER@example.com") is True
    assert await context.is_workspace_owner("joiner@example.com") is False
    assert await context.is_workspace_owner("stranger@example.com") is False


async def test_put_credential_round_trips_through_the_store(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    await context.put_credential("slack_bot_token", "xoxb-secret")
    assert await context.credential("slack_bot_token") == "xoxb-secret"


async def test_put_credential_fails_loud_without_a_store(tmp_path) -> None:
    context = replace(
        _context(uuid4(), StubDbos(), FilesystemBlobStore(root=tmp_path)), _credentials=None
    )
    with pytest.raises(RuntimeError, match="writes a credential but holds no store"):
        await context.put_credential("slot", "value")


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


async def test_poller_leaves_a_non_terminal_turn_undelivered(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    turn_id = await _seed_turn(workspace_id, "C6:1.0", "queued", "")
    poller, surface = _poller(workspace_id, RecordingSurface(), FilesystemBlobStore(root=tmp_path))
    await poller.drain()
    assert surface.attached == []
    assert (await _writeback(turn_id)).status == WRITEBACK_PENDING


async def test_poller_finalizes_a_recorded_ref_without_reposting(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    turn_id = await _seed_turn(workspace_id, "C7:1.0", "done", "already sent")
    await _set_writeback(
        turn_id,
        status=WRITEBACK_CLAIMED,
        claimed_by="dead-worker",
        reply_ref="C7:5.5",
        claim_expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    poller, surface = _poller(workspace_id, RecordingSurface(), FilesystemBlobStore(root=tmp_path))
    await poller.drain()
    assert surface.attached == []
    row = await _writeback(turn_id)
    assert row.status == WRITEBACK_DELIVERED
    assert row.reply_ref == "C7:5.5"


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
