"""The core surface seam: the privileged SurfaceContext (admit + identity + workspace write) and the
durable WritebackPoller. A minimal recording surface stands in for a real extension surface — the
dependency, never the thing asserted; every assertion reads durable rows (turn, surface_identity,
writeback) and blob bytes that core wrote. The full end-to-end through a real registered surface is
proven by the sample-extension conformance probe and the Slack extension's own tests."""

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4
from zipfile import ZipFile

import httpx
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    EMPTY_TURN_STEPS,
    UNREACHED_AMBIENT_REPLY,
    UNREACHED_STOPPER,
    no_member_skills,
)

import ufo.runtime.ext.surface as surface_module
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.db import workspace_tx
from ufo.harness.auth.bearer import UFO_TOKEN_SECRET_ENV
from ufo.harness.containment import ContainmentError
from ufo.harness.models.interface import (
    AUTO_MODEL,
    Message,
    ModelRequest,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.ingress_host import parse_site_label, site_label
from ufo.harness.sandbox.ingress_token import (
    INGRESS_SESSION_KIND,
    INGRESS_VIEW_KIND,
    INGRESS_VIEW_PATH,
    INGRESS_VIEW_TTL_SECONDS,
    FramerClaim,
    IngressTokenError,
    ShippedClaim,
    verify_ingress_token,
)
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint
from ufo.runtime.access.connectors import ConnectorRegistry
from ufo.runtime.access.credentials import (
    CREDENTIAL_REQUEST_RENEWAL_TTL_SECONDS,
    CREDENTIAL_REQUEST_TTL_SECONDS,
    CredentialRequestInvalid,
    CredentialRequestState,
    CredentialSlotUnset,
    CredentialStore,
    DeclaredSlot,
    declared_slot_fingerprint,
    open_credential_request,
    seal_credential_request,
)
from ufo.runtime.billing.accounting import PARK, REJECT, OffTurnSpendRefused, SpendOutcome
from ufo.runtime.billing.balance import credit, set_reserve
from ufo.runtime.engine import INJECTED_CONTEXT, _context_tag
from ufo.runtime.ext.surface import (
    CONVERSATION_TITLE_CHARS,
    NOTHING_DELIVERED,
    OPERATOR_EMAIL_DOMAIN,
    PREVIEW_PAGES_MAX,
    SILENCE_LINE_BREAK,
    SILENCE_SENTINEL,
    WRITEBACK_CLAIMED,
    WRITEBACK_DELIVERED,
    WRITEBACK_FAILED,
    WRITEBACK_MAX_AGE_SECONDS,
    MidTurnReply,
    NothingDelivered,
    SharedArtifact,
    SurfaceAuth,
    SurfaceContext,
    SurfaceDeliveryError,
    SurfaceInstallationAccess,
    SurfaceListenerContext,
    SurfaceListenerRunner,
    SurfaceRoute,
    SurfaceSpec,
    TurnStep,
    Writeback,
    WritebackPoller,
    fence_member_message,
    inbox_name,
    is_silence_sentinel,
    member_message_ref,
    member_message_said,
    member_message_text,
    mint_marker,
    record_transcript_access,
    scheduled_runs,
    writeback_says_nothing,
    writeback_workspaces,
)
from ufo.runtime.hub import InProcessHub
from ufo.runtime.queue import _load_turn
from ufo.runtime.seats import signup_workspace_id
from ufo.runtime.sources.sync import feed_handle_for
from ufo.runtime.surfaces.admission import Admission, MemberAdmission
from ufo.runtime.surfaces.hub_tail import HubTailer
from ufo.runtime.turns.ambient_reply import (
    AMBIENT_MESSAGE_CHARS,
    AmbientMessage,
    AmbientReplyClassifier,
    MeteredModel,
)
from ufo.runtime.turns.audience import (
    SHARED_AUDIENCE,
    conversation_audience,
    foreign_room_audience,
    room_audience,
)
from ufo.runtime.turns.transcript import (
    Conversation,
    encode,
    transcript_key,
)
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import (
    MAIN_AGENT_ICON,
    SUBAGENT_SURFACE,
    WRITEBACK_PENDING,
    AskQuestion,
    AskUserInput,
    ConnectRequest,
    CredentialPrompt,
    CredentialRequest,
    TerminalFrame,
    ToolIntent,
    TurnContext,
)

SURFACE = "test_surface"
OPENING_PERMALINK = (
    "https://acme.slack.com/archives/C7/p1700000000000100?thread_ts=1700000000.000100&cid=C7"
)
WRITEBACK_READ_INTERVAL_SECONDS = 0.01
WEDGE_WATCHDOG_SECONDS = 30
LISTENER_POLL_SECONDS = 0.05
LISTENER_LEASE_SECONDS = LISTENER_POLL_SECONDS * 5


@dataclass
class StubDbos:
    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)


@dataclass
class RecordingTurnSteps:
    read_workflows: list[str] = field(default_factory=list)

    async def read(self, workflow_id: str) -> tuple[TurnStep, ...]:
        self.read_workflows.append(workflow_id)
        return ()


@dataclass
class RecordingSurface:
    """A surface stand-in for the poller: `post` returns a canned ref (or raises when told to),
    `attach` records what it was handed. The poller's claim/lease/retry logic is what the tests
    assert, read back off the durable writeback row — this fake is only the delivery dependency."""

    ref: str = "posted-ref"
    fail_post: bool = False
    fail_speak: bool = False
    fail_attach_attempts: int = 0
    posted: list[UUID] = field(default_factory=list)
    spoken: list[tuple[UUID, UUID | None, str]] = field(default_factory=list)
    targets: list[tuple[UUID, UUID]] = field(default_factory=list)
    attach_attempts: int = 0
    attached: list[tuple[UUID, str, tuple[str, ...]]] = field(default_factory=list)

    async def speak(self, ctx: SurfaceContext, reply: MidTurnReply) -> str:
        self.spoken.append((reply.id, reply.message_ref, reply.text))
        if self.fail_speak:
            raise RuntimeError("speak failed")
        return f"{self.ref}:{len(self.spoken)}"

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
    turn's whole answer says nothing."""

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


async def _seed(
    *, member_email: str | None = None, workspace_subject: str | None = None
) -> tuple[UUID, UUID, UUID | None]:
    workspace_id = signup_workspace_id(workspace_subject) if workspace_subject else uuid4()
    agent_id = uuid4()
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
                icon=MAIN_AGENT_ICON,
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
        blob=WorkspaceBlobStore(backend=blob),
        _sandboxes=sandboxes if sandboxes is not None else _sandboxes(blob.root / "workspaces"),
        _admitter=MemberAdmission(
            workspace_id=workspace_id,
            admission=Admission(dbos=dbos, durable_surfaces=frozenset({SURFACE})),
        ),
        _tailer=HubTailer(hub=InProcessHub()),
        _stopper=UNREACHED_STOPPER,
        _turn_steps=EMPTY_TURN_STEPS,
        _credentials=store,
        _declared_slots=(),
        _artifact_token_secret="artifact-token-secret",
        _connectors=ConnectorRegistry(entries={}),
        _skills=EMPTY_SKILL_REGISTRY,
        _member_skill_listing=no_member_skills,
        _public_base_url="https://ufo.example.test",
        _home_surface="web",
        _ingress_public_url="https://sites.example.test",
        _deploy_sandbox_internet=False,
        _models=("auto", "claude-opus-4-8", "claude-sonnet-5"),
        _ambient_reply=UNREACHED_AMBIENT_REPLY,
    )


async def _seed_turn(
    workspace_id: UUID,
    queue_key: str,
    status: str,
    text: str,
    artifacts: tuple[SharedArtifact, ...] = (),
    *,
    surface: str = SURFACE,
    member_id: UUID | None = None,
    audience: str | None = None,
    admission_source: str = "internal",
    idempotency_key: str | None = None,
    created_at: datetime | None = None,
    context: TurnContext | None = None,
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
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
                **({} if audience is None else {"audience": audience}),
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
                admission_source=admission_source,
                idempotency_key=idempotency_key,
                terminal=terminal,
                context=None if context is None else context.model_dump(mode="json"),
                created_at=created_at or sa.func.now(),
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
        artifact_created_at = datetime.now(UTC)
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
                    attached_by_member=artifact.attached_by_member,
                    preview_blob_key=artifact.preview_blob_key,
                    preview_media_type=artifact.preview_media_type,
                    preview_size_bytes=artifact.preview_size_bytes,
                    created_at=artifact_created_at,
                    updated_at=artifact_created_at,
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_writeback_due_index_is_installed(db: None) -> None:
    async with workspace_tx() as connection:
        dialect = connection.dialect.name
        indexes = await connection.run_sync(lambda sync: sa.inspect(sync).get_indexes("writeback"))
    due = next(index for index in indexes if index["name"] == "writeback_due")
    assert due["column_names"] == ["workspace_id", "created_at"]
    predicate = str(due["dialect_options"][f"{dialect}_where"])
    assert "pending" in predicate
    assert "claimed" in predicate


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    again = await context.admit(
        conversation_id,
        "hello",
        idempotency_key="C1:1.0",
        speaker_member_id=member_id,
    )
    assert again.turn_id == turn_id
    assert not again.opened_run


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_agent_model_reads_the_setting_of_an_archived_agent_too(db: None, tmp_path) -> None:
    """A surface drawing a settled turn back states the model its agent is set to, so it reads that
    setting by id: the agent listing drops an archived agent whose conversations a member still
    reads. An id this workspace holds no agent for states nothing."""
    workspace_id, agent_id, _ = await _seed()
    archived = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=archived,
                workspace_id=workspace_id,
                name="scout",
                prompt="be brief",
                model=AUTO_MODEL,
                archived_at=sa.func.now(),
                archived_name="scout",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))

    assert await context.agent_model(agent_id) == "claude-opus-4-8"
    assert await context.agent_model(archived) == AUTO_MODEL
    assert archived not in {agent.id for agent in await context.list_agents()}
    assert await context.agent_model(uuid4()) is None
    other_workspace, _, _ = await _seed()
    assert (
        await _context(other_workspace, StubDbos(), FilesystemBlobStore(root=tmp_path)).agent_model(
            agent_id
        )
        is None
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    workspace_id: UUID,
    agent_id: UUID | None,
    owner_member_id: UUID | None,
    provider: str,
    *,
    shared: bool,
    base_url: str | None = None,
    backfill_days: int | None = None,
) -> UUID:
    """One connection, and the grant edge that reaches it where an agent holds one. A workspace
    connection passes no owner: nobody consented to it, and the shared check is what keeps it
    readable. The id comes back because a source row hangs off it."""
    connection_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider=provider,
                account_id=f"{provider}-account",
                account_label=f"{provider} label",
                host="api.example.test",
                base_url=base_url,
                backfill_days=backfill_days,
                owner_member_id=owner_member_id,
                shared=shared,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        if agent_id is not None:
            await connection.execute(
                sa.insert(tables.connector_grant).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    connection_id=connection_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return connection_id


async def _seed_source(
    workspace_id: UUID, connection_id: UUID, backend: str, config: dict[str, str]
) -> UUID:
    source_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                uid=source_id,
                workspace_id=workspace_id,
                backend=backend,
                config=config,
                feed_handle=feed_handle_for(config, frozenset()),
                connection_id=connection_id,
                next_sync_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return source_id


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    assert owner_view[0].own
    assert owner_view[0].account_label == "github label"
    assert owner_view[1].owner_email == "peer@example.com"
    assert not owner_view[1].own
    peer_view = await context.list_agent_connections(agent_id, peer, admin=False)
    assert [view.provider for view in peer_view] == ["slack"]
    assert peer_view[0].owner_email == "peer@example.com"
    assert peer_view[0].own
    admin_view = await context.list_agent_connections(agent_id, peer, admin=True)
    assert [view.provider for view in admin_view] == ["github", "slack"]
    assert [
        view.provider
        for view in await context.list_agent_connections(other_agent, owner, admin=True)
    ] == ["asana"]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_pool_lists_the_workspace_connection_and_what_its_streams_read(
    db: None, tmp_path
) -> None:
    workspace_id, agent_id, owner = await _seed(member_email="owner@example.com")
    assert owner is not None
    held = await _seed_connection(
        workspace_id,
        agent_id,
        owner,
        "github",
        shared=False,
        base_url="https://acme.example.test",
        backfill_days=30,
    )
    workspace = await _seed_connection(workspace_id, None, None, "folder", shared=True)
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))

    listed = await context.list_connections(owner, admin=False)
    # The workspace's own connection is owned by nobody, so an inner owner join would drop exactly
    # the account whose streams every member reads.
    assert [(view.id, view.owner_email) for view in listed] == [
        (workspace, None),
        (held, "owner@example.com"),
    ]
    assert (listed[1].base_url, listed[1].backfill_days) == ("https://acme.example.test", 30)
    assert (listed[0].base_url, listed[0].backfill_days) == (None, None)
    assert [view.name for view in listed[1].agents] == ["assistant"]
    assert listed[0].agents == ()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_streams_hang_off_their_connection_and_take_its_visibility(
    db: None, tmp_path
) -> None:
    workspace_id, agent_id, owner = await _seed(member_email="owner@example.com")
    assert owner is not None
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
    private = await _seed_connection(workspace_id, agent_id, owner, "github", shared=False)
    workspace = await _seed_connection(workspace_id, None, None, "folder", shared=True)
    issues = await _seed_source(workspace_id, private, "github", {"stream": "issues"})
    root = await _seed_source(workspace_id, workspace, "folder", {"root": "/notes"})

    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    owner_view = await context.list_sources(owner)
    assert [(view.id, view.connection_id, view.stream) for view in owner_view] == [
        (root, workspace, None),
        (issues, private, "issues"),
    ]
    assert owner_view[0].backend == "folder"
    assert [view.id for view in await context.list_sources(peer)] == [root]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_github_coverage_reads_only_what_the_member_may_see(db: None, tmp_path) -> None:
    """Both legs of the GitHub card answer over the connections this member may see, which is the
    set the connections and streams beside it list. A workspace admin is not widened: admin
    authority governs acts on a connection, never the sight of one (#327), and a card that read a
    private account's stream would state a coverage nothing else on the screen accounts for."""
    workspace_id, agent_id, owner = await _seed(member_email="owner@example.com")
    assert owner is not None
    admin = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=admin,
                workspace_id=workspace_id,
                email="admin@example.com",
                is_admin=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    held = await _seed_connection(workspace_id, agent_id, owner, "github", shared=False)
    await _seed_source(workspace_id, held, "github", {"stream": "issues"})
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))

    mine = await context.github_coverage(owner)
    assert (mine.api, mine.sources) == (True, True)
    theirs = await context.github_coverage(admin)
    assert (theirs.api, theirs.sources) == (False, False)

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.connection).values(shared=True).where(tables.connection.c.id == held)
        )
    shared = await context.github_coverage(admin)
    assert (shared.api, shared.sources) == (True, True)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_list_installations_orders_by_surface(db: None, tmp_path) -> None:
    workspace_id, agent_id, _ = await _seed()
    async with workspace_tx() as connection:
        for surface, installation_id in (("slack", "team:T123"), ("chime", "room:R9")):
            await connection.execute(
                sa.insert(tables.surface_installation).values(
                    routes_ingress=True,
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_conversation_for_refuses_a_foreign_agent(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    _, foreign_agent, _ = await _seed()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    with pytest.raises(ValueError, match="not an agent of this workspace"):
        await context.conversation_for("stray-key", SHARED_AUDIENCE, agent_id=foreign_agent)
    assert await context.find_conversation("stray-key") is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
        input={"manifest": "kind: agent"},
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    assert turn.context == ambient.model_copy(update={"reply_reaches": "test_surface"})
    assert turn.speaker_member_id == member_id
    assert audience == SHARED_AUDIENCE
    assert turn.created_at.tzinfo is not None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_link_member_id_requires_a_member_of_the_workspace(db: None, tmp_path) -> None:
    workspace_id, _, member_id = await _seed(member_email="bee@example.com")
    assert member_id is not None
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await context.link_member_id("UBEE", member_id) == member_id
    assert await context.linked_member("UBEE") == member_id
    assert await context.link_member_id("UOTHER", uuid4()) is None
    assert await context.linked_member("UOTHER") is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_member_surfaces_counts_linked_identities_and_proved_addresses(
    db: None, tmp_path
) -> None:
    workspace_id, _, member_id = await _seed(member_email="bee@example.com")
    assert member_id is not None
    other_id = uuid4()
    live = datetime.now(UTC) + timedelta(minutes=10)
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
        await connection.execute(
            sa.insert(tables.surface_identity).values(
                workspace_id=workspace_id,
                member_id=other_id,
                surface="ufo",
                external_id="other@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        for surface, address, holder, proved_by in (
            ("imessage", "+15550000001", member_id, "msg-1"),
            ("imessage", "+15550000002", member_id, None),
            ("sms", "+15550000003", other_id, "msg-3"),
        ):
            await connection.execute(
                sa.insert(tables.surface_address).values(
                    surface=surface,
                    address=address,
                    workspace_id=workspace_id,
                    member_id=holder,
                    claim_expires_at=None if proved_by else live,
                    proved_by=proved_by,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await context.member_surfaces(member_id) == frozenset({"imessage"})
    assert await context.link_member("UBEE", "bee@example.com") == member_id
    assert await context.member_surfaces(member_id) == frozenset({"imessage", SURFACE})
    assert await context.member_surfaces(other_id) == frozenset({"ufo", "sms"})


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_surface_listener_resolves_only_a_bound_installation(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    surface_context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))

    async def owned() -> bool:
        return True

    listener = SurfaceListenerContext(
        surface=SURFACE,
        public_base_url=None,
        _auth=SurfaceAuth(_credentials=None, _declared=frozenset(), _surface=SURFACE),
        _context_for=lambda _workspace_id, _surface: surface_context,
        _owned=owned,
    )
    async with listener.workspace("installation") as unresolved:
        assert unresolved is None
    with ws(workspace_id):
        await SurfaceInstallationAccess(declared=frozenset({SURFACE})).bind(SURFACE, "installation")
    async with listener.workspace("installation") as resolved:
        assert resolved is surface_context


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_surface_listener_has_one_live_owner_and_parks_a_failure(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runners are symmetric, so starting both together leaves the lease to whichever upsert
    commits first and either listener may be the one that runs. `first` claims it alone, and
    `second` then meets a lease already held — the state this asserts about. A parked runner holds
    that lease by renewing it every poll, so dropping the runner is what hands the surface on: the
    claim lapses on its own and the next instance takes it.

    The lease is renewed on every poll, which makes the poll rate a write rate. Both are scaled down
    together so the runners keep the production ratio: scaling the poll alone spends a lease on a
    hundred writes, and against SQLite's one writer slot they then starve every other transaction
    past its busy timeout."""
    first_id = UUID(int=1)
    second_id = UUID(int=2)
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.runtime_instance),
            [
                {
                    "id": first_id,
                    "workspace_id": None,
                    "heartbeat_at": now,
                    "created_at": now,
                    "updated_at": now,
                },
                {
                    "id": second_id,
                    "workspace_id": None,
                    "heartbeat_at": now,
                    "created_at": now,
                    "updated_at": now,
                },
            ],
        )
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    parked = asyncio.Event()
    metrics: list[tuple[str, dict[str, str]]] = []

    def emit_metric(name: str, **dimensions: str) -> None:
        metrics.append((name, dimensions))
        parked.set()

    monkeypatch.setattr(surface_module, "emit_metric", emit_metric)

    async def fail(_context: SurfaceListenerContext) -> None:
        first_started.set()
        raise RuntimeError("event failed")

    async def wait(_context: SurfaceListenerContext) -> None:
        second_started.set()
        await asyncio.Event().wait()

    def context_for(_workspace_id: UUID, _surface: str) -> SurfaceContext:
        raise AssertionError("listener context was not requested")

    auth = SurfaceAuth(_credentials=None, _declared=frozenset(), _surface=SURFACE)
    first = SurfaceListenerRunner(
        surface=SURFACE,
        instance_id=first_id,
        listener=fail,
        public_base_url=None,
        _auth=auth,
        _context_for=context_for,
        poll_seconds=LISTENER_POLL_SECONDS,
        lease_seconds=LISTENER_LEASE_SECONDS,
    )
    second = SurfaceListenerRunner(
        surface=SURFACE,
        instance_id=second_id,
        listener=wait,
        public_base_url=None,
        _auth=auth,
        _context_for=context_for,
        poll_seconds=LISTENER_POLL_SECONDS,
        lease_seconds=LISTENER_LEASE_SECONDS,
    )
    running = [asyncio.create_task(first.run())]
    try:
        await asyncio.wait_for(first_started.wait(), timeout=1)
        await asyncio.wait_for(parked.wait(), timeout=1)
        assert metrics == [("surface_listener_parked_total", {"surface": SURFACE})]
        running.append(asyncio.create_task(second.run()))
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(second_started.wait(), timeout=LISTENER_LEASE_SECONDS)
        assert not running[0].done()
        stopped = running.pop(0)
        stopped.cancel()
        await asyncio.gather(stopped, return_exceptions=True)
        await asyncio.wait_for(second_started.wait(), timeout=1)
    finally:
        for task in running:
            task.cancel()
        await asyncio.gather(*running, return_exceptions=True)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_surface_listener_survives_one_failed_claim_tick(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance_id = UUID(int=1)
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.runtime_instance).values(
                id=instance_id,
                workspace_id=None,
                heartbeat_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    listener_started = asyncio.Event()
    claim_recovered = asyncio.Event()
    claim_ticks = 0
    owns = SurfaceListenerRunner._owns

    async def wait(_context: SurfaceListenerContext) -> None:
        listener_started.set()
        await asyncio.Event().wait()

    async def flaky_owns(runner: SurfaceListenerRunner) -> bool:
        nonlocal claim_ticks
        claim_ticks += 1
        if claim_ticks == 2:
            raise sa.exc.SQLAlchemyError("database unavailable")
        owned = await owns(runner)
        if claim_ticks >= 3:
            claim_recovered.set()
        return owned

    def context_for(_workspace_id: UUID, _surface: str) -> SurfaceContext:
        raise AssertionError("listener context was not requested")

    monkeypatch.setattr(SurfaceListenerRunner, "_owns", flaky_owns)
    runner = SurfaceListenerRunner(
        surface=SURFACE,
        instance_id=instance_id,
        listener=wait,
        public_base_url=None,
        _auth=SurfaceAuth(_credentials=None, _declared=frozenset(), _surface=SURFACE),
        _context_for=context_for,
        poll_seconds=LISTENER_POLL_SECONDS,
        lease_seconds=LISTENER_LEASE_SECONDS,
    )
    task = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(listener_started.wait(), timeout=1)
        await asyncio.wait_for(claim_recovered.wait(), timeout=1)
        assert not task.done()
        assert claim_ticks >= 3
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_surface_listener_restarts_after_a_database_failure(db: None) -> None:
    instance_id = UUID(int=1)
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.runtime_instance).values(
                id=instance_id,
                workspace_id=None,
                heartbeat_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    attempts = 0
    restarted = asyncio.Event()

    async def listen(_context: SurfaceListenerContext) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sa.exc.SQLAlchemyError("database unavailable")
        restarted.set()
        await asyncio.Event().wait()

    def context_for(_workspace_id: UUID, _surface: str) -> SurfaceContext:
        raise AssertionError("listener context was not requested")

    runner = SurfaceListenerRunner(
        surface=SURFACE,
        instance_id=instance_id,
        listener=listen,
        public_base_url=None,
        _auth=SurfaceAuth(_credentials=None, _declared=frozenset(), _surface=SURFACE),
        _context_for=context_for,
        poll_seconds=LISTENER_POLL_SECONDS,
        lease_seconds=LISTENER_LEASE_SECONDS,
    )
    task = asyncio.create_task(runner.run())
    try:
        await asyncio.wait_for(restarted.wait(), timeout=1)
        assert attempts == 2
        assert not task.done()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@dataclass
class _Decides:
    answer: str
    model: str = "decider"

    async def complete(self, request: ModelRequest) -> str:
        return self.answer


@dataclass
class _Raises:
    model: str = "decider"

    async def complete(self, request: ModelRequest) -> str:
        raise RuntimeError("provider is down")


@dataclass
class _SpendRefused:
    outcome: SpendOutcome = REJECT
    model: str = "decider"

    async def complete(self, request: ModelRequest) -> str:
        raise OffTurnSpendRefused(self.outcome, "out of credit", self.model)


@dataclass
class _Stalls:
    model: str = "decider"

    async def complete(self, request: ModelRequest) -> str:
        await asyncio.sleep(30)
        return "REPLY"


def _ambient_context(tmp_path: Path, model: MeteredModel) -> SurfaceContext:
    return replace(
        _context(uuid4(), StubDbos(), FilesystemBlobStore(root=tmp_path)),
        _ambient_reply=AmbientReplyClassifier(model=model),
    )


async def test_a_decided_ambient_reply_is_the_answer_the_surface_gets(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """Only a decision this seam actually read holds a message back, and the record says which model
    read it — the one place an operator can see what the deploy is spending its ambient decisions
    on."""
    message = AmbientMessage(speaker="U2", text="nice, thanks for chasing that")
    history = (AmbientMessage(speaker="UBOT", text="here it is", own=True),)

    with caplog.at_level(logging.INFO, logger="ufo"):
        assert not await _ambient_context(tmp_path, _Decides("NO_REPLY")).ambient_reply_wanted(
            message, history
        )
        assert await _ambient_context(tmp_path, _Decides("REPLY")).ambient_reply_wanted(
            message, history
        )

    decided = [r for r in caplog.records if r.getMessage() == "surface.ambient_reply"]
    assert [(r.__dict__["ufo"]["decision"], r.__dict__["ufo"]["history"]) for r in decided] == [
        ("NO_REPLY", 1),
        ("REPLY", 1),
    ]
    assert {r.__dict__["ufo"]["model"] for r in decided} == {"decider"}


async def test_an_undecided_ambient_reply_admits_the_turn(
    tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Fail open, every way the decision can fail to arrive: a provider that errors, one whose
    answer is unreadable, and one that never comes back inside the bound. The two outcomes are not
    symmetric — an unwanted line costs one line, while a dropped request costs the member their
    answer with nothing to show them — so an undecided message is admitted and the operator gets the
    record."""
    monkeypatch.setattr(surface_module, "AMBIENT_REPLY_TIMEOUT_SECONDS", 0.01)
    message = AmbientMessage(speaker="U2", text="how many rows did the others come back with?")

    with caplog.at_level(logging.WARNING, logger="ufo"):
        for model in (_Raises(), _Decides("maybe?"), _Stalls()):
            assert await _ambient_context(tmp_path, model).ambient_reply_wanted(message, ())

    undecided = [
        r.__dict__["ufo"]["error"]
        for r in caplog.records
        if r.getMessage() == "surface.ambient_reply_undecided"
    ]
    assert len(undecided) == 3
    assert "provider is down" in undecided[0]
    assert "unreadable" in undecided[1]
    assert "TimeoutError" in undecided[2]
    assert not [r for r in caplog.records if r.getMessage() == "surface.ambient_reply"]


@pytest.mark.parametrize(("outcome", "wanted"), ((PARK, True), (REJECT, False)))
async def test_a_spend_refused_ambient_reply_preserves_the_policy_outcome(
    tmp_path, caplog: pytest.LogCaptureFixture, outcome: SpendOutcome, wanted: bool
) -> None:
    message = AmbientMessage(speaker="U2", text="nice, thanks for chasing that")

    with caplog.at_level(logging.INFO, logger="ufo"):
        assert (
            await _ambient_context(tmp_path, _SpendRefused(outcome)).ambient_reply_wanted(
                message, ()
            )
            is wanted
        )

    refused = [
        r.__dict__["ufo"]["outcome"]
        for r in caplog.records
        if r.getMessage() == "surface.ambient_reply_spend_refused"
    ]
    assert refused == [outcome]
    assert not [r for r in caplog.records if r.getMessage() == "surface.ambient_reply"]


async def test_a_refused_deploy_classifier_retries_on_the_surface_agents_model(
    db: None, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    workspace_id, _, _ = await _seed()
    selected: list[str] = []

    def classifier_for(model: str) -> AmbientReplyClassifier:
        selected.append(model)
        return AmbientReplyClassifier(model=_Decides("NO_REPLY", model=model))

    context = replace(
        _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path)),
        _ambient_reply=AmbientReplyClassifier(model=_SpendRefused(REJECT, "openai-decider")),
        _ambient_reply_for=classifier_for,
    )
    message = AmbientMessage(speaker="U2", text="nice, thanks for chasing that")

    with ws(workspace_id), caplog.at_level(logging.INFO, logger="ufo"):
        assert not await context.ambient_reply_wanted(message, ())

    assert selected == ["claude-opus-4-8"]
    decided = [
        r.__dict__["ufo"] for r in caplog.records if r.getMessage() == "surface.ambient_reply"
    ]
    assert [(entry["model"], entry["decision"]) for entry in decided] == [
        ("claude-opus-4-8", "NO_REPLY")
    ]


async def test_an_oversized_ambient_message_is_admitted(tmp_path) -> None:
    message = AmbientMessage(speaker="U2", text="x" * (AMBIENT_MESSAGE_CHARS + 1))

    assert await _ambient_context(tmp_path, _Decides("NO_REPLY")).ambient_reply_wanted(message, ())


def test_the_silence_sentinel_is_the_whole_answer_or_it_is_not_silence() -> None:
    """The predicate is the only reader of the tokens, and it decides whether a member sees nothing
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


def test_a_bare_line_break_answer_says_nothing_in_every_form_a_model_writes_it() -> None:
    """A model with nothing to write sometimes answers with a line-break tag alone, which carries no
    words, so the same predicate reads it as silence: the bare tag, the self-closing form with and
    without a space, and any case. It is a real tag in ordinary content, so only the whole answer
    counts — a reply that mentions it or holds it between words is a normal reply."""
    assert is_silence_sentinel(SILENCE_LINE_BREAK)
    assert is_silence_sentinel(f"  {SILENCE_LINE_BREAK}\n")
    assert is_silence_sentinel("<br/>")
    assert is_silence_sentinel("<br />")
    assert is_silence_sentinel("<BR>")
    assert is_silence_sentinel("<Br />")
    assert not is_silence_sentinel("Nothing further from me. <br>")
    assert not is_silence_sentinel("<br><br>")
    assert not is_silence_sentinel("First line<br>second line")
    assert not is_silence_sentinel("Use `<br>` to break the line.")
    assert not is_silence_sentinel("<break>")
    assert not is_silence_sentinel("<br>done</br>")


def test_a_delivery_says_nothing_only_when_the_turn_owes_the_member_nothing_else() -> None:
    """Every durable surface reads this before it posts, so what it counts as silence is what a
    member never sees. The words are only part of it: a shared file, a question, a connect handoff
    and a credential prompt each reach the member through this one reply and nothing later re-asks,
    so the reply posts whatever the words say. The failed and cancelled lines are the surface's own
    words rather than the agent's, so they are never silence either."""
    silent = Writeback(
        turn_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        queue_key="CQUIET:1.0",
        terminal=TerminalFrame(status="done", text=SILENCE_SENTINEL),
        artifacts=(),
    )

    assert writeback_says_nothing(silent)
    assert writeback_says_nothing(
        replace(silent, terminal=TerminalFrame(status="done", text=SILENCE_LINE_BREAK))
    )
    assert not writeback_says_nothing(
        replace(silent, terminal=TerminalFrame(status="done", text="Filed it."))
    )
    assert not writeback_says_nothing(replace(silent, artifacts=(_shared_page("quiet"),)))
    assert not writeback_says_nothing(
        replace(silent, terminal=TerminalFrame(status="failed", text=SILENCE_SENTINEL))
    )
    assert not writeback_says_nothing(
        replace(
            silent,
            terminal=TerminalFrame(
                status="done",
                text=SILENCE_SENTINEL,
                question=AskUserInput(
                    title="Which one?", questions=(AskQuestion(question="Now?"),)
                ),
            ),
        )
    )
    assert not writeback_says_nothing(
        replace(
            silent,
            terminal=TerminalFrame(
                status="done",
                text=SILENCE_SENTINEL,
                connect_request=ConnectRequest(provider="github", requester_member_id=uuid4()),
            ),
        )
    )
    assert not writeback_says_nothing(
        replace(
            silent,
            terminal=TerminalFrame(
                status="done",
                text=SILENCE_SENTINEL,
                credential_request=CredentialRequest(
                    reason="the API key",
                    prompts=(CredentialPrompt(slot="api_key", prompt="The key"),),
                    sealed="sealed",
                ),
            ),
        )
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_surface_that_delivered_nothing_settles_the_writeback(
    db: None, tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """A silent turn is delivered, not retried and not failed: nothing was owed, so the row closes
    with no reply ref recorded and no attachment phase — there is no message to attach to. A `None`
    ref instead of the explicit outcome would read as "not posted yet" and re-post every drain."""
    workspace_id, _agent_id, _member_id = await _seed()
    turn_id = await _seed_turn(workspace_id, "CQUIET:1.0", "done", SILENCE_LINE_BREAK)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_join_member_creates_a_same_domain_member_and_links(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed(workspace_subject="example.com")
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
    assert await context.join_member("UNEW2", "new.joiner@example.com") == joined


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_join_member_refuses_without_a_domain_match(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed(member_email="owner@example.com")
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    for external_id, email in (
        ("UGIGI", "gigi@elsewhere.com"),
        ("UBARE", "example.com"),
        ("UEMPTY", "@example.com"),
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_join_member_does_not_treat_gmail_as_workspace_authority(db: None, tmp_path) -> None:
    workspace_id, _, owner_id = await _seed(
        member_email="owner@gmail.com", workspace_subject="owner@gmail.com"
    )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await context.join_member("UOTHER", "other@gmail.com") is None
    assert await context.linked_member("UOTHER") is None
    assert owner_id is not None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


def test_home_url_addresses_the_browser_portal_at_cores_own_mount_path(tmp_path) -> None:
    """The link a surface that cannot answer an act hands the member: core's `/surface/<name>`
    mount joined to the home surface, carrying the portal's own hash route untouched. A deploy
    missing either half has no address to give and says so with None, so no caller renders a link
    to nowhere."""
    context = _context(uuid4(), StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert context.home_url() == "https://ufo.example.test/surface/web"
    assert (
        context.home_url("#/workspace/credentials")
        == "https://ufo.example.test/surface/web#/workspace/credentials"
    )
    assert replace(context, _public_base_url="https://ufo.example.test/").home_url() == (
        "https://ufo.example.test/surface/web"
    )
    assert replace(context, _public_base_url=None).home_url("#/workspace/credentials") is None
    assert replace(context, _home_surface=None).home_url("#/workspace/credentials") is None


async def test_the_report_link_opens_only_a_conversation_the_portal_shows(
    db: None, tmp_path: Path
) -> None:
    """The link into the portal at a carried report is minted for a member's own conversation and
    withheld for a room, whose roster the portal cannot check — the surface then hands the download
    instead — and withheld everywhere when the deploy has no portal."""
    workspace_id, _agent_id, member_id = await _seed(member_email="owner@example.com")
    assert member_id is not None
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    own_turn = await _seed_turn(
        workspace_id,
        "own:1",
        "done",
        "hi",
        member_id=member_id,
        audience=str(conversation_audience(member_id)),
    )
    room_turn = await _seed_turn(
        workspace_id, "room:1", "done", "hi", audience=str(room_audience(SURFACE, "C1"))
    )
    async with workspace_tx() as connection:
        conversations = dict(
            (
                await connection.execute(
                    sa.select(tables.turn.c.id, tables.turn.c.conversation_id).where(
                        tables.turn.c.id.in_([own_turn, room_turn])
                    )
                )
            ).all()
        )
    report_id = uuid4()
    report = SharedArtifact(
        id=report_id,
        blob_key=f"artifacts/{report_id}/plan.md",
        filename="plan.md",
        subject=None,
        media_type="text/markdown",
        size_bytes=3,
        role="details",
    )
    with ws(workspace_id):
        own = await context.report_url(conversations[own_turn], report)
        room = await context.report_url(conversations[room_turn], report)
        unhosted = await replace(context, _home_surface=None).report_url(
            conversations[own_turn], report
        )
    assert own == (
        f"https://ufo.example.test/surface/web#/c/{conversations[own_turn]}?report={report_id}"
    )
    assert room is None
    assert unhosted is None


def test_ingress_url_addresses_the_site_the_ingress_resolves(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mint and the ingress agree by construction: the label the surface puts in the hostname
    parses back to the same `(conversation, port)`, and the view token in the path verifies to the
    workspace the context is bound to. It is a *view* token, never the session kind the ingress
    accepts as a cookie, so the link cannot be pasted into a jar to skip the handshake. The secret
    stays core-side — a surface holds neither end."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    workspace_id, conversation_id, framer_id = uuid4(), uuid4(), uuid4()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    framed_from = f"https://{site_label(framer_id, 3000)}.sites.example.test/records/one"
    url = context.ingress_url(conversation_id, 8000, "/", framed_from=framed_from)
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
    assert claims.framer == FramerClaim(conversation_id=framer_id, port=3000)
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


def test_ingress_url_carries_the_shipped_claim(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipped producer half: with both `shipped_slug` and `shipped_digest`, the minted view
    token carries the shipped reference the ingress reads to serve deploy-wide bytes, while the
    label still names the anchor `(conversation, port)` so the origin stays workspace-scoped. Absent
    either half, the token carries no shipped claim and the ingress serves the conversation's own
    bytes — the row-backed path is unchanged."""
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, "s3cret")
    workspace_id, anchor = uuid4(), uuid4()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    url = context.ingress_url(
        anchor, 20001, "/", shipped_slug="radar", shipped_digest="deadbeefdeadbeef"
    )
    assert url is not None
    label, _, _host = urlsplit(url).netloc.partition(".")
    assert parse_site_label(label) == (anchor, 20001)
    token = urlsplit(url).path.removeprefix(f"{INGRESS_VIEW_PATH}/")
    claims = verify_ingress_token(token, datetime.now(UTC), INGRESS_VIEW_KIND)
    assert claims.shipped == ShippedClaim(slug="radar", digest="deadbeefdeadbeef")
    plain = context.ingress_url(anchor, 20001, "/")
    assert plain is not None
    plain_token = urlsplit(plain).path.removeprefix(f"{INGRESS_VIEW_PATH}/")
    assert verify_ingress_token(plain_token, datetime.now(UTC), INGRESS_VIEW_KIND).shipped is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_poller_delivers_a_done_turn_and_attaches_its_files(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    blob = FilesystemBlobStore(root=tmp_path)
    await blob.put("artifacts/x/report.pdf", b"PDF")
    artifact = SharedArtifact(
        id=uuid4(),
        blob_key="artifacts/x/report.pdf",
        filename="report.pdf",
        subject=None,
        media_type="application/pdf",
        size_bytes=3,
        role="file",
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


async def test_poller_does_not_re_deliver_a_member_attachment(db: None, tmp_path) -> None:
    """A file a member attached is a shared artifact of the turn, so it lists on the shelf and
    downloads through the one route — but the durable-surface reply is the agent's, so the writeback
    carries only what the agent shared. Re-uploading the member's own file back into the thread is
    what the `attached_by_member` split keeps out of the reply."""
    workspace_id, _, _ = await _seed()
    blob = FilesystemBlobStore(root=tmp_path)
    await blob.put("artifacts/x/shared.pdf", b"PDF")
    await blob.put("artifacts/y/mine.pdf", b"PDF")
    shared = SharedArtifact(
        id=uuid4(),
        blob_key="artifacts/x/shared.pdf",
        filename="shared.pdf",
        subject=None,
        media_type="application/pdf",
        size_bytes=3,
        role="file",
    )
    mine = SharedArtifact(
        id=uuid4(),
        blob_key="artifacts/y/mine.pdf",
        filename="mine.pdf",
        subject=None,
        media_type="application/pdf",
        size_bytes=3,
        attached_by_member=True,
        role="file",
    )
    turn_id = await _seed_turn(workspace_id, "C5:2.0", "done", "hi there", artifacts=(shared, mine))
    poller, surface = _poller(workspace_id, RecordingSurface(ref="C5:9.9"), blob)
    await poller.drain()
    assert surface.attached == [(turn_id, "C5:9.9", ("shared.pdf",))]


def _shared_page(name: str) -> SharedArtifact:
    return SharedArtifact(
        id=uuid4(),
        blob_key=f"artifacts/{name}/report.md",
        filename="report.md",
        subject=None,
        media_type="text/markdown",
        size_bytes=6,
        role="file",
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_scheduled_runs_carry_output_and_files_and_hold_the_audience(db: None) -> None:
    """The runs page lists only terminal scheduled admissions whose conversation content the
    reader reads — the shared conversations' runs and their own. A run's reply is transcript
    content, so another member's private conversation and a room never list, whoever asks; a turn
    still going has no reply and never lists either — and each run carries its key, terminal text,
    and shared files whole, previews included. `turn_id` pins the read to one run under the
    same fence."""
    workspace_id, agent_id, _ = await _seed()
    member_id = await _seed_member_row(workspace_id, "m@example.com")
    other_id = await _seed_member_row(workspace_id, "n@example.com")
    chart = SharedArtifact(
        id=uuid4(),
        blob_key="artifacts/x/chart.png",
        filename="chart.png",
        subject="the chart",
        media_type="image/png",
        size_bytes=3,
        role="file",
    )
    brief = SharedArtifact(
        id=uuid4(),
        blob_key="artifacts/x/brief.pdf",
        filename="brief.pdf",
        subject=None,
        media_type="application/pdf",
        size_bytes=5,
        preview_blob_key="artifacts/x/brief.png",
        preview_media_type="image/png",
        preview_size_bytes=4,
        role="file",
    )
    shared_run = await _seed_turn(
        workspace_id,
        "R1",
        "done",
        "the digest",
        artifacts=(chart, brief),
        admission_source="scheduled",
        idempotency_key="11111111-1111-4111-8111-111111111111:2026-08-14T09:00:00+00:00",
    )
    private_run = await _seed_turn(
        workspace_id,
        "R2",
        "done",
        "private",
        (_shared_page("R2"),),
        member_id=other_id,
        admission_source="scheduled",
    )
    await _seed_turn(
        workspace_id,
        "R4",
        "done",
        "in a room",
        (_shared_page("R4"),),
        audience=str(room_audience("slack", "C123")),
        admission_source="scheduled",
    )
    await _seed_turn(
        workspace_id, "R5", "running", "", (_shared_page("R5"),), admission_source="scheduled"
    )
    await _seed_turn(workspace_id, "R3", "done", "typed", (_shared_page("R3"),))
    listed = await scheduled_runs(workspace_id, member_id, limit=10)
    assert [run.turn_id for run in listed] == [shared_run]
    run = listed[0]
    assert (run.status, run.text) == ("done", "the digest")
    assert run.idempotency_key is not None
    assert run.idempotency_key.startswith("11111111-1111-4111-8111-111111111111:")
    assert (run.surface, run.source) == (SURFACE, None)
    assert run.agent_id == agent_id
    assert [artifact.filename for artifact in run.artifacts] == ["brief.pdf", "chart.png"]
    assert run.artifacts[0].preview_blob_key == "artifacts/x/brief.png"
    own = await scheduled_runs(workspace_id, other_id, limit=10)
    assert {run.turn_id for run in own} == {shared_run, private_run}
    foreign = await scheduled_runs(workspace_id, other_id, limit=10, agent_id=uuid4())
    assert foreign == ()
    narrowed = await scheduled_runs(workspace_id, other_id, limit=10, agent_id=agent_id)
    assert {run.turn_id for run in narrowed} == {shared_run, private_run}
    pinned = await scheduled_runs(workspace_id, member_id, limit=1, turn_id=shared_run)
    assert [run.turn_id for run in pinned] == [shared_run]
    fenced = await scheduled_runs(workspace_id, member_id, limit=1, turn_id=private_run)
    assert fenced == ()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_scheduled_runs_bound_newest_first_and_list_only_the_runs_that_reported(
    db: None,
) -> None:
    """A run that ended well and shared no file reported nothing, so it is no row of this feed —
    quiet fires between two reports cost the reader nothing — and `limit` takes the newest rows
    of what remains."""
    workspace_id, _, _ = await _seed()
    member_id = await _seed_member_row(workspace_id, "m@example.com")
    midnight = datetime(2026, 8, 15, tzinfo=UTC)
    reported: list[UUID] = []
    for index, (status, files) in enumerate(
        (
            ("done", (_shared_page("Q0"),)),
            ("done", ()),
            ("done", ()),
            ("failed", ()),
            ("done", ()),
            ("done", ()),
            ("done", (_shared_page("Q6"),)),
        )
    ):
        turn_id = await _seed_turn(
            workspace_id,
            f"Q{index}",
            status,
            "the digest",
            files,
            admission_source="scheduled",
            created_at=midnight + timedelta(minutes=index),
        )
        if status != "done" or files:
            reported.append(turn_id)
    whole = await scheduled_runs(workspace_id, member_id, limit=10)
    assert [run.turn_id for run in whole] == list(reversed(reported))
    newest = await scheduled_runs(workspace_id, member_id, limit=2)
    assert [run.turn_id for run in newest] == list(reversed(reported))[:2]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_poller_leaves_a_non_terminal_turn_undelivered(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    turn_id = await _seed_turn(workspace_id, "C6:1.0", "queued", "")
    poller, surface = _poller(workspace_id, RecordingSurface(), FilesystemBlobStore(root=tmp_path))
    await poller.drain()
    assert surface.attached == []
    assert (await _writeback(turn_id)).status == WRITEBACK_PENDING


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_poller_resumes_attachments_from_a_recorded_ref_without_reposting(
    db: None, tmp_path
) -> None:
    workspace_id, _, _ = await _seed()
    artifact = SharedArtifact(
        id=uuid4(),
        blob_key="artifacts/x/resume.txt",
        filename="resume.txt",
        subject=None,
        media_type="text/plain",
        size_bytes=6,
        role="file",
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_attachment_failure_retries_from_the_recorded_reply(db: None, tmp_path) -> None:
    workspace_id, _, _ = await _seed()
    artifact = SharedArtifact(
        id=uuid4(),
        blob_key="artifacts/x/retry.txt",
        filename="retry.txt",
        subject=None,
        media_type="text/plain",
        size_bytes=5,
        role="file",
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_credential_prompts_gate_per_slot_on_seal_workspace_and_marker(
    db: None, tmp_path
) -> None:
    """The per-slot render gate over the real methods: every named prompt of a live seal is
    pending, garbage and a foreign workspace never are, and fulfilling one slot silences exactly
    that prompt — its sibling keeps asking, so a disconnect mid-entry or a rotation over
    already-stored slots never strands a prompt. Expiry is proven where the seal contract lives
    (test_credentials)."""
    workspace_id, _, member_id = await _seed(member_email="owner@example.com")
    assert member_id is not None
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member).values(is_admin=True).where(tables.member.c.id == member_id)
        )
    blob = FilesystemBlobStore(root=tmp_path)
    context = replace(
        _context(workspace_id, StubDbos(), blob),
        _declared_slots=(
            DeclaredSlot(name="a", description="", extension="sample"),
            DeclaredSlot(name="b", description="", extension="sample"),
        ),
    )
    sealed = seal_credential_request(
        context._credentials.fernet,
        CredentialRequestState(workspace_id=workspace_id, member_id=member_id, slots=("a", "b")),
    )
    with ws(workspace_id):
        assert await context.credential_prompt_pending(sealed, "a") is True
        assert await context.credential_prompt_pending(sealed, "b") is True
        assert await context.credential_prompt_pending(sealed, "unnamed") is False
        assert await context.credential_prompt_pending("garbage", "a") is False
        expired = context._credentials.fernet.encrypt_at_time(
            CredentialRequestState(workspace_id=workspace_id, member_id=member_id, slots=("a",))
            .model_dump_json()
            .encode(),
            int(time.time()) - CREDENTIAL_REQUEST_TTL_SECONDS - 1,
        ).decode()
        assert await context.credential_prompt_pending(expired, "a") is False
        foreign = seal_credential_request(
            context._credentials.fernet,
            CredentialRequestState(workspace_id=uuid4(), member_id=member_id, slots=("a",)),
        )
        assert await context.credential_prompt_pending(foreign, "a") is False
        await context.fulfill_credential_request(sealed, "a", "one", member_id)
        assert await context.credential_prompt_pending(sealed, "a") is False
        marker = hashlib.sha256(sealed.encode()).hexdigest()[:32]
        assert await context.blob.exists(f"credential_requests/{marker}/a")
        assert await context.credential_prompt_pending(sealed, "b") is True
        await context.fulfill_credential_request(sealed, "b", "two", member_id)
        assert await context.credential_prompt_pending(sealed, "b") is False
        rotation = seal_credential_request(
            context._credentials.fernet,
            CredentialRequestState(
                workspace_id=workspace_id, member_id=member_id, slots=("a", "b")
            ),
        )
        assert await context.credential_prompt_pending(rotation, "a") is True
        with pytest.raises(CredentialRequestInvalid, match="member"):
            await context.fulfill_credential_request(sealed, "a", "hijack", uuid4())
        with pytest.raises(CredentialRequestInvalid, match="slot"):
            await context.fulfill_credential_request(sealed, "c", "off-seal", member_id)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_credential_fulfillment_uses_the_declared_merge(db: None, tmp_path) -> None:
    workspace_id, _, member_id = await _seed(member_email="owner@example.com")
    assert member_id is not None
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member).values(is_admin=True).where(tables.member.c.id == member_id)
        )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))

    def merge(current: str | None, submitted: str) -> str:
        return submitted if current is None else f"{current},{submitted}"

    context = replace(
        context,
        _declared_slots=(
            DeclaredSlot(name="structured", description="", extension="sample", merge=merge),
        ),
    )
    first = seal_credential_request(
        context._credentials.fernet,
        CredentialRequestState(
            workspace_id=workspace_id, member_id=member_id, slots=("structured",)
        ),
    )
    second = seal_credential_request(
        context._credentials.fernet,
        CredentialRequestState(
            workspace_id=workspace_id, member_id=member_id, slots=("structured",)
        ),
    )

    with ws(workspace_id):
        await context.fulfill_credential_request(first, "structured", "one", member_id)
        with pytest.raises(CredentialRequestInvalid, match="already fulfilled"):
            await context.fulfill_credential_request(first, "structured", "reused", member_id)
        await context.fulfill_credential_request(second, "structured", "two", member_id)
        assert await context._credentials.get(workspace_id, "structured") == "one,two"
        undeclared = seal_credential_request(
            context._credentials.fernet,
            CredentialRequestState(
                workspace_id=workspace_id, member_id=member_id, slots=("removed",)
            ),
        )
        with pytest.raises(CredentialRequestInvalid, match="not declared"):
            await context.fulfill_credential_request(undeclared, "removed", "value", member_id)
        context = replace(
            context,
            _declared_slots=(DeclaredSlot(name="provider", description="", extension="sample"),),
        )
        authorization = seal_credential_request(
            context._credentials.fernet,
            CredentialRequestState(
                workspace_id=workspace_id,
                member_id=member_id,
                slots=("provider",),
                payload="provider-state",
            ),
        )
        await context.fulfill_credential_request(authorization, "provider", "one", member_id)
        await context.fulfill_credential_request(authorization, "provider", "two", member_id)
        assert await context._credentials.get(workspace_id, "provider") == "two"


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_credential_fulfillment_rejects_a_changed_workspace_declaration(
    db: None, tmp_path
) -> None:
    workspace_id, _, member_id = await _seed(member_email="owner@example.com")
    assert member_id is not None
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member).values(is_admin=True).where(tables.member.c.id == member_id)
        )
    original = DeclaredSlot(
        name="dynamic",
        description="API key",
        extension="workspace_credentials",
        host="api.original.example",
        env="DYNAMIC_API_KEY",
        header="Authorization",
    )
    changed = replace(original, host="api.changed.example")
    context = replace(
        _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path)),
        _declared_slots=(changed,),
    )
    sealed = seal_credential_request(
        context._credentials.fernet,
        CredentialRequestState(
            workspace_id=workspace_id,
            member_id=member_id,
            slots=(original.name,),
            workspace_declarations={
                original.name: declared_slot_fingerprint(original),
            },
        ),
    )

    with ws(workspace_id), pytest.raises(CredentialRequestInvalid, match="changed after"):
        await context.fulfill_credential_request(sealed, original.name, "secret", member_id)

    with pytest.raises(CredentialSlotUnset):
        await context._credentials.get(workspace_id, original.name)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_credential_fulfillment_rechecks_live_admin_authority(db: None, tmp_path) -> None:
    workspace_id, _, member_id = await _seed(member_email="owner@example.com")
    assert member_id is not None
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member).values(is_admin=True).where(tables.member.c.id == member_id)
        )
    context = replace(
        _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path)),
        _declared_slots=(DeclaredSlot(name="secret", description="", extension="sample"),),
    )
    sealed = seal_credential_request(
        context._credentials.fernet,
        CredentialRequestState(
            workspace_id=workspace_id,
            member_id=member_id,
            slots=("secret",),
            request_id=uuid4(),
        ),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member)
            .where(tables.member.c.id == member_id)
            .values(is_admin=False, updated_at=sa.func.now())
        )

    with ws(workspace_id), pytest.raises(CredentialRequestInvalid, match="seated workspace admin"):
        await context.fulfill_credential_request(sealed, "secret", "value", member_id)

    with pytest.raises(CredentialSlotUnset):
        await context._credentials.get(workspace_id, "secret")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_one_credential_request_cannot_race_two_values_into_a_slot(
    db: None, tmp_path
) -> None:
    workspace_id, _, member_id = await _seed(member_email="owner@example.com")
    assert member_id is not None
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member).values(is_admin=True).where(tables.member.c.id == member_id)
        )
    context = replace(
        _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path)),
        _declared_slots=(DeclaredSlot(name="secret", description="", extension="sample"),),
    )
    sealed = seal_credential_request(
        context._credentials.fernet,
        CredentialRequestState(
            workspace_id=workspace_id,
            member_id=member_id,
            slots=("secret",),
            request_id=uuid4(),
        ),
    )

    with ws(workspace_id):
        outcomes = await asyncio.gather(
            context.fulfill_credential_request(sealed, "secret", "first", member_id),
            context.fulfill_credential_request(sealed, "secret", "second", member_id),
            return_exceptions=True,
        )

    assert sum(outcome is None for outcome in outcomes) == 1
    [refused] = [outcome for outcome in outcomes if outcome is not None]
    assert isinstance(refused, CredentialRequestInvalid)
    assert "already fulfilled" in str(refused)
    assert await context._credentials.get(workspace_id, "secret") in {"first", "second"}


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_credential_request_renewal_requires_the_requesting_admin(db: None, tmp_path) -> None:
    workspace_id, _, member_id = await _seed(member_email="owner@example.com")
    assert member_id is not None
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member).values(is_admin=True).where(tables.member.c.id == member_id)
        )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    recently_expired = context._credentials.fernet.encrypt_at_time(
        CredentialRequestState(
            workspace_id=workspace_id, member_id=member_id, slots=("structured",)
        )
        .model_dump_json()
        .encode(),
        int(time.time()) - CREDENTIAL_REQUEST_TTL_SECONDS - 1,
    ).decode()
    stale = context._credentials.fernet.encrypt_at_time(
        CredentialRequestState(
            workspace_id=workspace_id, member_id=member_id, slots=("structured",)
        )
        .model_dump_json()
        .encode(),
        int(time.time()) - CREDENTIAL_REQUEST_RENEWAL_TTL_SECONDS - 1,
    ).decode()
    old_claim_in_fresh_seal = context._credentials.fernet.encrypt(
        CredentialRequestState(
            workspace_id=workspace_id,
            member_id=member_id,
            slots=("structured",),
            issued_at=int(time.time()) - CREDENTIAL_REQUEST_RENEWAL_TTL_SECONDS - 1,
        )
        .model_dump_json()
        .encode()
    ).decode()

    with ws(workspace_id):
        renewed = await context.renew_credential_request(recently_expired, member_id)
        assert renewed is not None
        assert await context.credential_prompt_pending(renewed, "structured")
        renewed_state = open_credential_request(context._credentials.fernet, renewed)
        assert renewed_state.issued_at == context._credentials.fernet.extract_timestamp(
            recently_expired.encode()
        )
        assert await context.renew_credential_request(recently_expired, uuid4()) is None
        assert await context.renew_credential_request(stale, member_id) is None
        assert await context.renew_credential_request(old_claim_in_fresh_seal, member_id) is None

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.member).values(is_admin=False).where(tables.member.c.id == member_id)
        )
    with ws(workspace_id):
        assert await context.renew_credential_request(recently_expired, member_id) is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_join_member_seats_every_teammate_it_creates(db: None, tmp_path) -> None:
    """Nothing bounds the members a workspace has, so a teammate joining on their first message is
    answered on that message. A join that left them unseated would state a revocation no admin
    made, and the agent would refuse the person it just created."""
    workspace_id, _, _ = await _seed()
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    early = datetime(2026, 1, 1, tzinfo=UTC)
    async with workspace_tx() as connection:
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
    joins = [
        await context.join_member(f"U{index}", f"teammate{index}@example.com") for index in range(3)
    ]
    assert all(join is not None for join in joins)
    assert await context.linked_member("U2") == joins[2]
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
    assert all(rows[join] is not None for join in joins)


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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    assert by_id[busy].opening_message == "ask 1"
    assert by_id[busy].model == "claude-opus-4-8"
    assert by_id[quiet].turn_count == 0
    assert by_id[quiet].last_turn_at is None
    assert by_id[quiet].member_email == "bee@example.com"
    assert by_id[quiet].opening_message is None
    assert by_id[quiet].model is None
    assert await context.list_conversations(limit=1) == (by_id[busy],)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    with ws(workspace_id):
        await context.blob.put(transcript_key(conversation_id), encode(stored))
        read = await context.read_transcript(conversation_id)
        assert read == stored
        empty = await _conversation_row(workspace_id, queue_key="empty")
        assert await context.read_transcript(empty) is None
    foreign_context = _context(foreign_workspace, StubDbos(), blob)
    with ws(foreign_workspace):
        assert await foreign_context.read_transcript(conversation_id) is None


async def _seed_conversation(
    workspace_id: UUID,
    agent_id: UUID,
    *,
    queue_key: str,
    audience: str,
    member_id: UUID | None,
    surface: str = SURFACE,
    surface_label: str | None = None,
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
                surface_label=surface_label,
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
        await connection.execute(
            sa.update(tables.conversation)
            .where(
                tables.conversation.c.id == conversation_id,
                tables.conversation.c.title.is_(None),
            )
            .values(title=member_message_text(inbound).strip()[:CONVERSATION_TITLE_CHARS])
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
        surface_label="#ops",
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
    by_id = {entry.summary.id: entry for entry in as_admin}
    assert by_id[mine].audience == str(conversation_audience(member_id))
    assert by_id[shared].audience == str(SHARED_AUDIENCE)
    assert by_id[room].audience == str(room_audience("slack", "C7"))
    assert by_id[room].surface_label == "#ops"
    assert by_id[shared].surface_label is None
    assert subagent not in {entry.summary.id for entry in as_admin}
    assert walled not in {entry.summary.id for entry in as_admin}

    other_agent = await context.list_agent_conversations(
        second_agent, member_id, admin=True, limit=50
    )
    assert {entry.summary.id for entry in other_agent} == {walled}

    lanes = await context.list_agent_conversations(
        agent_id, member_id, admin=False, limit=50, member_admitted=True
    )
    assert lanes == ()
    await _seed_conversation_turn(
        workspace_id, mine, agent_id, seq=1, inbound="hello", speaker_member_id=member_id
    )
    spoken = await context.list_agent_conversations(
        agent_id, member_id, admin=False, limit=50, member_admitted=True
    )
    assert [entry.summary.id for entry in spoken] == [mine]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_search_never_answers_for_a_conversation_the_member_may_not_read(
    db: None, tmp_path
) -> None:
    """What a conversation holds — what it is called, who spoke in it — is matched only where this
    member may read it. An admin searching another member's private thread by its words would be
    told whether those words stand in it, which is the content the acknowledgement audits. What the
    row states about itself either way — whose it is, where it came in — still matches."""
    workspace_id, agent_id, admin_id = await _seed(member_email="boss@example.com")
    assert admin_id is not None
    owner_id = await _seed_member_row(workspace_id, "owner@example.com")
    theirs = await _seed_conversation(
        workspace_id,
        agent_id,
        queue_key="theirs",
        audience=str(conversation_audience(owner_id)),
        member_id=owner_id,
        surface_label="DM",
    )
    await _seed_conversation_turn(
        workspace_id,
        theirs,
        agent_id,
        seq=1,
        inbound="the salary review spreadsheet",
        speaker_member_id=owner_id,
    )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))

    by_words = await context.list_agent_conversations(
        agent_id, admin_id, admin=True, limit=50, search="salary"
    )
    by_speaker = await context.list_agent_conversations(
        agent_id, admin_id, admin=True, limit=50, search="owner@example.com"
    )
    by_origin = await context.list_agent_conversations(
        agent_id, admin_id, admin=True, limit=50, search="DM"
    )

    assert by_words == ()
    assert [entry.summary.id for entry in by_speaker] == [theirs]
    assert [entry.summary.id for entry in by_origin] == [theirs]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_queued_arrivals_carry_the_source_and_the_wait_that_shape_each_bubble(
    db: None, tmp_path
) -> None:
    """The queue holds two kinds of row and a projection must tell them apart: a member's own
    words, folded in while a turn runs, and an agent's prompt an extension invoked into the same
    live turn. Nothing in the id or the body says which, so the row's admission source rides along —
    ordered by admission. A row another read's turn consumed stays out unless the caller names that
    turn as the one still draining, since only then is the row's transcript unwritten — and such a
    row arrives with its wait already over."""
    workspace_id, agent_id, member_id = await _seed()
    conversation_id = await _seed_conversation(
        workspace_id, agent_id, queue_key="root", audience=str(SHARED_AUDIENCE), member_id=None
    )
    running = await _seed_conversation_turn(
        workspace_id, conversation_id, agent_id, seq=1, inbound="find the flaky test"
    )
    spoke = await _seed_pending_arrival(
        workspace_id,
        conversation_id,
        running,
        seq=1,
        body="also check the tests",
        admission_source="member",
        speaker_member_id=member_id,
        context=TurnContext(sender="Mel Okafor (m@example.com)"),
    )
    invoked = await _seed_pending_arrival(
        workspace_id,
        conversation_id,
        running,
        seq=2,
        body="[seat approval request] ask an admin to decide.",
        admission_source="internal",
    )
    folded = await _seed_pending_arrival(
        workspace_id,
        conversation_id,
        running,
        seq=3,
        body="already folded in",
        admission_source="member",
        speaker_member_id=member_id,
        consumed_turn_id=running,
        context=TurnContext(sender="Mel Okafor (m@example.com)"),
    )
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path / "blobs"))

    pending = await context.queued_arrivals(conversation_id, None)
    draining = await context.queued_arrivals(conversation_id, running)

    assert [(arrival.id, arrival.admission_source, arrival.waiting) for arrival in pending] == [
        (spoke, "member", True),
        (invoked, "internal", True),
    ]
    assert [(arrival.id, arrival.waiting) for arrival in draining] == [
        (spoke, True),
        (invoked, True),
        (folded, False),
    ]

    spoken = await context.arrival_speakers(conversation_id)

    assert {(row.id, row.sender, row.speaker_member_id) for row in spoken} == {
        (spoke, "Mel Okafor (m@example.com)", member_id),
        (folded, "Mel Okafor (m@example.com)", member_id),
    }


def test_member_message_text_strips_the_engine_envelope() -> None:
    """A transcript's persisted user message is the prompt the turn ran on: the engine's <context>
    tag at the head and the recall it injected at the tail, around an inbound only some surfaces
    fence. A projection reading the transcript back gets the member's words alone — composed here
    by the engine's own writers, so the strip breaks the moment the envelope moves — while the same
    text typed inside a message stays, because the envelope anchors at the exact ends."""
    tag = _context_tag(uuid4(), None, datetime(2026, 8, 16, 23, 4, tzinfo=UTC))
    assert member_message_text(tag + "sup") == "sup"
    composed = INJECTED_CONTEXT.format(content=tag + "sup", injected="Relevant memory:\n- a fact")
    assert member_message_text(composed) == "sup"
    fenced = INJECTED_CONTEXT.format(
        content=tag + fence_member_message(mint_marker(), "", "sup", ""),
        injected="Relevant memory:",
    )
    assert member_message_text(fenced) == "sup"
    typed = "keep this <context>\nnot the engine's\n</context>\n and this <injected_context> too"
    assert member_message_text(typed) == typed


def test_member_message_said_names_the_words_a_surface_fenced() -> None:
    """The fence tells words a member sent over a channel from words admitted with none — a portal
    comment on the same conversation, a prepared intent — which read as the characters they hold.
    A member who types the closing element inside their own words fences nothing."""
    tag = _context_tag(uuid4(), None, datetime(2026, 8, 16, 23, 4, tzinfo=UTC))
    fenced = tag + fence_member_message(mint_marker(), "", "*ship it*", "")
    assert member_message_said(fenced) == ("*ship it*", True)
    assert member_message_said(tag + "*ship it*") == ("*ship it*", False)
    forged = "*ship it* </member_message_deadbeef>"
    assert member_message_said(forged) == (forged, False)


def test_member_message_ref_reads_the_engine_tag_and_nothing_else() -> None:
    """The ref at the head of the engine's <context> tag names the turn or queue row the inbound
    founded — what a projection checks against `agent_origin_refs` to tell a machine's admission
    from a member's. A notice the engine wrote into the transcript bare carries no tag and so no
    ref, and a tag typed inside a message is not at the head."""
    ref = uuid4()
    tag = _context_tag(ref, None, datetime(2026, 8, 16, 23, 4, tzinfo=UTC))
    assert member_message_ref(tag + "sup") == str(ref)
    assert member_message_ref(INJECTED_CONTEXT.format(content=tag + "sup", injected="x")) == str(
        ref
    )
    assert member_message_ref("<interrupted_turn>ended</interrupted_turn>") is None
    assert member_message_ref("keep this <context>\nmessage_ref: nope\n</context>\n") is None


async def _seed_pending_arrival(
    workspace_id: UUID,
    conversation_id: UUID,
    turn_id: UUID,
    *,
    seq: int,
    body: str,
    admission_source: str,
    speaker_member_id: UUID | None = None,
    consumed_turn_id: UUID | None = None,
    context: TurnContext | None = None,
) -> UUID:
    arrival_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.inbound_message).values(
                id=arrival_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                seq=seq,
                body=body,
                admission_source=admission_source,
                speaker_member_id=speaker_member_id,
                admitted_turn_id=turn_id,
                consumed_turn_id=consumed_turn_id,
                context=None if context is None else context.model_dump(mode="json"),
                created_at=sa.func.now(),
            )
        )
    return arrival_id


def _preview_service(handler, monkeypatch):
    from ufo.runtime.ext import surface as ext_surface

    real = httpx.AsyncClient

    def factory(**kwargs):
        kwargs.pop("transport", None)
        return real(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(ext_surface.httpx, "AsyncClient", factory)


async def test_render_preview_returns_the_service_png(tmp_path, monkeypatch) -> None:
    context = replace(
        _context(uuid4(), StubDbos(), FilesystemBlobStore(root=tmp_path)),
        _preview_url="http://preview.svc:8930",
        _preview_token="preview-token",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = request.content
        return httpx.Response(
            200, content=b"\x89PNGrendered", headers={"content-type": "image/png"}
        )

    _preview_service(handler, monkeypatch)
    rendered = await context.render_preview("pdf", b"%PDF-1.7")
    assert rendered is not None
    assert rendered.pages == (b"\x89PNGrendered",)
    assert rendered.start_page == 1
    assert rendered.page_count == 1
    assert captured["auth"] == "Bearer preview-token"
    assert str(captured["url"]).endswith("/render")
    assert b'"inline": true' in captured["body"]  # type: ignore[operator]
    assert b'"kind": "pdf"' in captured["body"]  # type: ignore[operator]


async def test_render_preview_unpacks_a_page_range_from_the_services_zip(
    tmp_path, monkeypatch
) -> None:
    """A range comes back as a zip of `page-01.png…`, and the pages read out of it in page order.
    The whole file's length rides the service's own header, so a caller knows there is more to
    ask for."""
    context = replace(
        _context(uuid4(), StubDbos(), FilesystemBlobStore(root=tmp_path)),
        _preview_url="http://preview.svc:8930",
        _preview_token="preview-token",
    )
    bundle = BytesIO()
    with ZipFile(bundle, "w") as pages:
        pages.writestr("page-02.png", b"\x89PNGfourth")
        pages.writestr("page-01.png", b"\x89PNGthird")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(
            200,
            content=bundle.getvalue(),
            headers={"content-type": "application/zip", "x-preview-page-count": "12"},
        )

    _preview_service(handler, monkeypatch)
    rendered = await context.render_preview("pdf", b"%PDF-1.7", start_page=3, pages=8)
    assert rendered is not None
    assert rendered.pages == (b"\x89PNGthird", b"\x89PNGfourth")
    assert rendered.start_page == 3
    assert rendered.page_count == 12
    assert b'"start_page": 3' in captured["body"]  # type: ignore[operator]
    assert b'"pages": 8' in captured["body"]  # type: ignore[operator]


async def test_render_preview_clamps_a_range_to_what_the_service_renders(
    tmp_path, monkeypatch
) -> None:
    context = replace(
        _context(uuid4(), StubDbos(), FilesystemBlobStore(root=tmp_path)),
        _preview_url="http://preview.svc:8930",
        _preview_token="preview-token",
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(
            200, content=b"\x89PNGrendered", headers={"content-type": "image/png"}
        )

    _preview_service(handler, monkeypatch)
    assert await context.render_preview("pdf", b"%PDF-1.7", start_page=0, pages=99) is not None
    assert b'"start_page": 1' in captured["body"]  # type: ignore[operator]
    assert f'"pages": {PREVIEW_PAGES_MAX}'.encode() in captured["body"]  # type: ignore[operator]


async def test_render_preview_is_none_on_a_malformed_bundle(tmp_path, monkeypatch) -> None:
    context = replace(
        _context(uuid4(), StubDbos(), FilesystemBlobStore(root=tmp_path)),
        _preview_url="http://preview.svc:8930",
        _preview_token="preview-token",
    )
    _preview_service(
        lambda request: httpx.Response(
            200, content=b"not a zip", headers={"content-type": "application/zip"}
        ),
        monkeypatch,
    )
    assert await context.render_preview("pdf", b"%PDF-1.7", pages=8) is None


async def test_render_preview_is_none_when_unconfigured(tmp_path) -> None:
    context = _context(uuid4(), StubDbos(), FilesystemBlobStore(root=tmp_path))
    assert await context.render_preview("pdf", b"%PDF-1.7") is None


async def test_render_preview_is_none_on_service_refusal(tmp_path, monkeypatch) -> None:
    context = replace(
        _context(uuid4(), StubDbos(), FilesystemBlobStore(root=tmp_path)),
        _preview_url="http://preview.svc:8930",
        _preview_token="preview-token",
    )
    _preview_service(
        lambda request: httpx.Response(415, json={"error": "unsupported_type"}), monkeypatch
    )
    assert await context.render_preview("pdf", b"not a pdf") is None


async def test_a_turn_the_balance_holds_absorbs_a_member_message(db: None, tmp_path: Path) -> None:
    """Slack asks this before its ambient gate; None hands a thread reply to the classifier, which
    may leave it silent. Admission folds a member's message into a turn the balance holds, so the
    read says so — and once credit lands the resume sweep, not a fold, wakes the turn."""
    workspace_id, _, _ = await _seed()
    held = await _seed_turn(workspace_id, "C9:1.0", "parked", "")
    dollar = 1_000_000
    async with workspace_tx() as connection:
        conversation_id = (
            await connection.execute(
                sa.select(tables.turn.c.conversation_id).where(tables.turn.c.id == held)
            )
        ).scalar_one()
        await credit(connection, workspace_id, dollar, dollar, "seed")
        await set_reserve(connection, workspace_id, 5 * dollar)
    context = _context(workspace_id, StubDbos(), FilesystemBlobStore(root=tmp_path))
    with ws(workspace_id):
        assert await context.absorbing_turn(conversation_id) == held
        async with workspace_tx() as connection:
            await credit(connection, workspace_id, 20 * dollar, 20 * dollar, "top-up")
        assert await context.absorbing_turn(conversation_id) is None
