"""The core surface seam: the privileged SurfaceContext (admit + identity + workspace write) and the
durable WritebackPoller. A minimal recording surface stands in for a real extension surface — the
dependency, never the thing asserted; every assertion reads durable rows (turn, surface_identity,
writeback) and blob bytes that core wrote. The full end-to-end through a real registered surface is
proven by the sample-extension conformance probe and the Slack extension's own tests."""

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet

import ufo.ext.surface as surface_module
from ufo.blob import FilesystemBlobStore
from ufo.credentials import (
    CredentialRequestInvalid,
    CredentialRequestState,
    CredentialStore,
    seal_credential_request,
)
from ufo.db import workspace_tx
from ufo.ext.surface import (
    WRITEBACK_CLAIMED,
    WRITEBACK_DELIVERED,
    WRITEBACK_FAILED,
    WRITEBACK_MAX_AGE_SECONDS,
    WRITEBACK_WORKSPACE_BATCH,
    SharedArtifact,
    SurfaceContext,
    SurfaceRoute,
    SurfaceSpec,
    Writeback,
    WritebackPoller,
    workspace_key,
    writeback_workspaces,
)
from ufo.hub import InProcessHub
from ufo.loop.queue import _load_turn
from ufo.schema import tables
from ufo.schema.records import WRITEBACK_PENDING, TerminalFrame, TurnContext
from ufo.surfaces.admission import Admission, MemberAdmission
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
        post=surface.post,
        attach=surface.attach,
    )
    return WritebackPoller(
        worker_id=worker_id,
        surfaces={SURFACE: spec},
        context_for=lambda workspace_id, _name: contexts[workspace_id],
        candidates=writeback_workspaces(),
    )


def test_workspace_key_scopes_under_the_conversation_workspace() -> None:
    cid = uuid4()
    assert workspace_key(cid, "slack-inbox/a.txt") == (
        f"conversations/{cid}/workspace/slack-inbox/a.txt"
    )
    for bad in ("../escape", "/etc/passwd", "a/../../b", ""):
        with pytest.raises(ValueError):
            workspace_key(cid, bad)


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


async def test_find_conversation_reads_without_creating(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await context.find_conversation("C1:1.0") is None
    async with workspace_tx() as connection:
        created = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.conversation))
        ).scalar_one()
    assert created == 0
    conversation_id = await context.conversation_for("C1:1.0", None)
    assert await context.find_conversation("C1:1.0") == conversation_id
    assert await replace(context, surface="other").find_conversation("C1:1.0") is None


async def test_conversation_for_claims_a_memberless_conversation(db: None, tmp_path) -> None:
    """A conversation created before its speaker could resolve is claimed by the first resolving
    turn, and never re-claimed from the member who owns it."""
    workspace_id, _, member_id = await _seed(member_email="bee@example.com")
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    conversation_id = await context.conversation_for("D9", None)

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
    assert await context.conversation_for("D9", member_id) == conversation_id
    assert await _owner() == member_id
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
    assert await context.conversation_for("D9", other_id) == conversation_id
    assert await _owner() == member_id


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
    monkeypatch.setattr(surface_module, "WRITEBACK_CLAIM_REFRESH_SECONDS", 0.01)
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
        await asyncio.wait_for(surface.blocked.wait(), timeout=1)
        expired = datetime.now(UTC) - timedelta(seconds=1)
        for turn_id in turn_ids:
            await _set_writeback(turn_id, claim_expires_at=expired)
        forced_expiries = {
            turn_id: (await _writeback(turn_id)).claim_expires_at for turn_id in turn_ids
        }
        deadline = asyncio.get_running_loop().time() + 1
        while True:
            current = {
                turn_id: (await _writeback(turn_id)).claim_expires_at for turn_id in turn_ids
            }
            if all(current[turn_id] != forced_expiries[turn_id] for turn_id in turn_ids):
                break
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)
        for turn_id in turn_ids:
            renewed = await _writeback(turn_id)
            assert renewed.status == WRITEBACK_CLAIMED
            assert renewed.claimed_by == "worker-1"
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
