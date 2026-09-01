"""The surface seam: the privileged capabilities a surface extension reaches into core for.

A surface is trusted infrastructure — it asserts a member's identity and admits turns as that member
— so unlike the scoped `ExtensionContext` (a ScopedStore, declared credential slots, a read-only
trajectory corpus), a surface context carries privileged capabilities a scoped extension may not
hold: admit a member turn onto the durable queue, resolve an external id to a member and a
conversation (linking a `surface_identity` on first contact — and joining a channel-verified email
whose domain is the workspace's own as a new member), and read the workspace's credential slots
in-process. Only this seam admits a turn as a member; the `invoke` capability held by scheduled
tasks and evals admits internal turns and can never name a speaker.

One `SurfaceSpec`/`SurfaceContext` expresses both shapes of surface, differing only in how the reply
gets back and thus in how much of the one context each uses:

- A **durable** surface (Slack, iMessage) is delivered to — its member is elsewhere. Its two-phase
  delivery (`post` then best-effort `attach`) marks it durable; core runs the
  `WritebackPoller` that delivers at-least-once from the durable terminal frame (the hub is lossy,
  so never from a live frame). It declares provider ingress as routes or a listener and never
  tails. A surface that admits ambient traffic asks `ambient_reply_wanted` first, and a message the
  agent is not wanted in founds no turn at all.
- A **live** surface (web; core's built-in CLI is the twin) holds the member's connection open and
  tails the turn's frames off the hub as they publish, so no poller row is written for its
  conversations. It declares its own routes (page, admit, SSE tail, spend) and reaches the hub
  through the injected `TurnTailer`.

The poller only ever processes turns that registered a writeback — admission registers one for
every turn entering a durable-surface conversation, whoever admits it (a surface ingest, a
scheduled fire, an extension invoke) — so it is a no-op for a live surface, the efficient
downgrade, not a second seam."""

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from secrets import token_hex
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import sqlalchemy as sa
from pydantic import BaseModel, Field, JsonValue, ValidationError, field_validator
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection
from starlette.requests import Request
from starlette.responses import Response

from ufo.blob import BlobNotFound, FleetBlobStore, WorkspaceBlobStore
from ufo.db import owner_tx, workspace_tx
from ufo.harness.containment import contained_leaf
from ufo.harness.models.interface import Message, ModelRequest
from ufo.harness.o11y import emit_metric, log, warn
from ufo.harness.sandbox.conversation import (
    WORKSPACE_WRITE_MAX_BYTES,
    ConversationSandbox,
    WorkspaceFile,
)
from ufo.harness.sandbox.ingress_url import mint_ingress_view_url
from ufo.harness.sandbox.terminal import TerminalOp
from ufo.runtime.access.connectors import DIRECT_ACCOUNT, CatalogPage, ConnectorRegistry
from ufo.runtime.access.credentials import (
    CREDENTIAL_REQUEST_PURPOSE,
    CREDENTIAL_REQUEST_RENEWAL_TTL_SECONDS,
    CredentialRequestInvalid,
    CredentialRequestState,
    CredentialSlotUnset,
    CredentialStore,
    DeclaredSlot,
    member_slot,
    named_slots,
    open_credential_request,
    seal_credential_request,
)
from ufo.runtime.access.grants import (
    ConnectHandoff,
    ConnectRequestInvalid,
    ConnectUnavailable,
    account_object_name,
    installed_connect_flow,
)
from ufo.runtime.agent_scope import agent as bind_agent
from ufo.runtime.authority import MemberAuthority
from ufo.runtime.billing.accounting import (
    ALLOW,
    PARK,
    AgentSpendReport,
    BalanceGate,
    MemberSpendReport,
    OffTurnSpendRefused,
    SpendEvaluator,
    SpendReport,
    SpendRollup,
)
from ufo.runtime.candidates import WorkspaceCandidates, owner_candidates
from ufo.runtime.hub import Activity, LiveFrame
from ufo.runtime.kinds.agent_setup import (
    AgentSetup,
    ArmedOrder,
    SetupState,
    pending_setup,
    setup_state,
)
from ufo.runtime.kinds.governance import prompt_digest
from ufo.runtime.media.artifact_url import (
    artifact_url_expiry,
    mint_artifact_url,
    mint_image_preview_url,
)
from ufo.runtime.media.image_previews import raster_image_media_type
from ufo.runtime.object_views import ActionView, presented_action_views
from ufo.runtime.seats import (
    SeatEntry,
    Seats,
    create_member,
    email_domain,
    member_is_admin,
    workspace_domain,
)
from ufo.runtime.skills.runtime import RuntimeSkill, SkillRegistry, SystemSkillBundle
from ufo.runtime.sources.backend import ConnectorSourceConfig, binding_name
from ufo.runtime.turns.ambient_reply import NO_REPLY, AmbientMessage, AmbientReplyClassifier
from ufo.runtime.turns.audience import (
    Audience,
    audience_member,
    conversation_audience,
    narrow_audience,
    parse_audience,
    readable_audiences,
)
from ufo.runtime.turns.subjects import SHARED_SUBJECT
from ufo.runtime.turns.transcript import (
    CompactionRecord,
    Conversation,
    decode,
    read_compaction_after,
    read_compaction_record,
    transcript_key,
)
from ufo.runtime.turns.workspace_changes import (
    NOTHING_CHANGED,
    WorkspaceChanges,
    recorded_workspace_changes,
)
from ufo.runtime.workspace import MEMBER_ROUTED_SLOTS, ws, ws_current
from ufo.schema import tables
from ufo.schema.records import (
    EXTENSION_SURFACE_PREFIX,
    MEMBER_ADMISSION,
    NON_TERMINAL_STATUSES,
    PARKED,
    PORTAL_SURFACE,
    SCHEDULED_ADMISSION,
    SPAWN_RESULT_KEY_PREFIX,
    SUBAGENT_SURFACE,
    SURFACE_COMMENT_ROUND_INDEX,
    WRITEBACK_CLAIMED,
    WRITEBACK_DELIVERED,
    WRITEBACK_FAILED,
    WRITEBACK_PENDING,
    AgentVisibility,
    ReasoningEffort,
    RuntimeIdentity,
    SandboxSize,
    TablerIcon,
    TerminalFrame,
    ToolIntent,
    Turn,
    TurnAdmissionSource,
    TurnContext,
    TurnRuntimeConfig,
)
from ufo.sdk.http import cookie_secure

if TYPE_CHECKING:
    from ufo.runtime.ext.context import SourceReader
    from ufo.runtime.ext.conversation_slots import (
        BoundConversationSlot,
        ConversationSlotContext,
        ConversationSlotPayload,
    )
    from ufo.runtime.listings import ListingCursor, ListingPage
    from ufo.runtime.memory import MemoryMatch, MemorySearch
    from ufo.runtime.objects import (
        BoundAction,
        BoundKind,
        ConversationObjectGrant,
        MemberObject,
        ObjectListQuery,
        ObjectPage,
    )
    from ufo.runtime.tools.registry import ActionBinding

OPERATOR_EMAIL_DOMAIN = "metalcraft.ai"

PREVIEW_THUMBNAIL_MAX_WIDTH = 600
PREVIEW_THUMBNAIL_MAX_HEIGHT = 800
PREVIEW_RENDER_TIMEOUT_SECONDS = 330.0


AMBIENT_CONTEXT_ELEMENT = "channel_context"
MEMBER_MESSAGE_ELEMENT = "member_message"
ATTACHMENTS_ELEMENT = "attachments"
MARKER_BYTES = 4
INBOX_NAME_MAX_CHARS = 80
INBOX_FALLBACK_NAME = "file"
INBOX_UNSAFE_NAME_CHARS = re.compile(r"[^\w.-]")
_MEMBER_MESSAGE_RE = re.compile(
    rf"<{MEMBER_MESSAGE_ELEMENT}_(?P<marker>[0-9a-f]{{{MARKER_BYTES * 2}}})>\n"
    rf"(?P<said>.*)\n</{MEMBER_MESSAGE_ELEMENT}_(?P=marker)>",
    re.DOTALL,
)
_CONTEXT_TAG_RE = re.compile(r"\A<context>\n.*?\n</context>\n", re.DOTALL)
_INJECTED_CONTEXT_RE = re.compile(r"\n\n<injected_context>\n.*\n</injected_context>\Z", re.DOTALL)

SILENCE_SENTINEL = "<response></response>"
"""The whole delivery of a turn that has nothing to say: an empty response element, which cannot
occur in prose the way a bare word can."""
SILENCE_LINE_BREAK = "<br>"
"""The other whole answer that says nothing: a model with nothing to write sometimes emits a bare
line-break tag and nothing else, which carries no words a member is owed."""
_SILENCE_NAME = SILENCE_SENTINEL.removeprefix("<").partition(">")[0]
_BREAK_NAME = SILENCE_LINE_BREAK.removeprefix("<").partition(">")[0]
_SILENCE_RE = re.compile(
    rf"<{_SILENCE_NAME}>\s*</{_SILENCE_NAME}>"
    rf"|<{_SILENCE_NAME}\s*/>"
    rf"|(?i:<{_BREAK_NAME}\s*/?>)"
)


def is_silence_sentinel(answer: str) -> bool:
    """Whether a final answer says nothing and nothing else.

    Strict about the whole answer: whitespace-stripped, it is the empty response element — the
    paired form with any whitespace between the tags, or the self-closing one, since the model
    produces both — or a bare line-break tag in any of its forms, whatever its case. An answer that
    merely contains one of them among other text is a normal reply and is delivered as written,
    because silence is only ever the whole delivery."""
    return _SILENCE_RE.fullmatch(answer.strip()) is not None


def mint_marker() -> str:
    """The token one member message's elements are named with.

    Minted per message, so no text the prompt carries can name one: a bystander's words were already
    frozen in the ambient digest when it did not exist, and the member's own text is their own
    message anyway. That is what makes the elements a boundary rather than a convention, and why
    core escapes nobody's words: an inequality and a tag a member typed on purpose reach the model
    as written. What a surface renders before it hands the text over is its own — Slack resolves the
    ids in `<@U…>` and `<#C…|…>` to names, since nobody can act on an id — and core neither
    inspects nor undoes it."""
    return token_hex(MARKER_BYTES)


def fence_member_message(marker: str, ambient: str, body: str, attachments: str) -> str:
    """The inbound text one member message becomes: already-rendered ambient context, the member's
    own words, then what their attachments delivered — each in its own element, named with `marker`.

    Run together as plain text these are one transcript whose last line is the member's message, so
    a message that trails off — an attachment that never arrived, a sentence ending on a colon —
    reads as a log with more to come and the turn answers by writing the member's next message
    instead of its own."""
    member = f"{MEMBER_MESSAGE_ELEMENT}_{marker}"
    fenced = f"{ambient}<{member}>\n{body}\n</{member}>"
    if not attachments:
        return fenced
    delivered = f"{ATTACHMENTS_ELEMENT}_{marker}"
    return f"{fenced}\n<{delivered}>\n{attachments}\n</{delivered}>"


def inbox_name(raw: str, used: set[str]) -> str:
    """A safe workspace leaf for a filename an inbound surface was handed, and the name it landed
    under: path components dropped, separators and everything outside word characters, dot and dash
    collapsed, length capped, dots-only and empty names falling back, and a name already used in
    this batch numbered so no delivery overwrites another. A word charset rather than an ASCII one,
    since a member who attaches `отчёт.pdf` reads the name back in the note this returns, and a
    script is not a path separator.

    The cap falls on the stem, so the suffix survives it. A file op routes on the suffix — a `.pdf`
    is converted to text and page images, a `.png` is read as bytes and typed — so a cap that ate
    the extension off an ordinary long attachment name turned a document the member sent into a
    binary the read refuses. A suffix with no room left under the cap is not an extension, and gets
    cut with everything else.

    One implementation for every surface. A Slack attachment name, a browser's content-disposition,
    and whatever a future surface is handed are the same untrusted input, and the CVE-2026-56692
    follow-on is what a per-surface copy of this costs: the second ingress kept its own weaker idea
    of what was safe. `used` is updated with the returned name."""
    leaf = INBOX_UNSAFE_NAME_CHARS.sub("-", contained_leaf(raw, INBOX_FALLBACK_NAME))
    stem, dot, suffix = leaf.partition(".")
    stem = stem[: max(INBOX_NAME_MAX_CHARS - len(dot) - len(suffix), 0)]
    leaf = f"{stem}{dot}{suffix}"[:INBOX_NAME_MAX_CHARS].strip(".") or INBOX_FALLBACK_NAME
    stem, dot, suffix = leaf.partition(".")
    name = leaf
    index = 1
    while name in used:
        name = f"{stem}-{index}{dot}{suffix}"
        index += 1
    used.add(name)
    return name


def member_message_text(inbound: str) -> str:
    """The member's own words back out of an inbound, for a projection that renders what the member
    said rather than the prompt the turn ran on.

    Two wrappers come off. The engine's envelope — the <context> tag it writes at the head of every
    member turn and the <injected_context> recall it appends at the tail — persists into the
    transcript, so a projection reading messages back (the terminal's resume replay) strips it by
    its anchors: the head of the text and the tail, positions no member's own words can hold. The
    surface fence is exact rather than anchored: the element is named with a marker minted for that
    one message, so the only text that can close it is text `fence_member_message` wrote, and a
    member who types `</member_message>` closes nothing. An inbound wearing neither — a prepared
    intent, a subagent payload — is its own text."""
    said = _INJECTED_CONTEXT_RE.sub("", _CONTEXT_TAG_RE.sub("", inbound))
    found = _MEMBER_MESSAGE_RE.search(said)
    return said if found is None else found.group("said")


@dataclass(frozen=True)
class Stopped:
    """What one member stop did: whether this call ended the turn, and the turn the stop founded
    on a follow-up the member had already sent — None when nothing was pending. A surface holding
    the member's screen switches its tail to the founded turn; the one that stays on the stopped
    turn shows an idle chat while the follow-up runs unseen."""

    ended: bool
    founded_turn_id: UUID | None


@dataclass(frozen=True)
class Admitted:
    """What one member admission did: the turn the message belongs to, and whether this admission is
    the one that put that turn's current run on the queue. Founding a turn, taking over a queued
    timer turn, and resuming a parked one each open a run; a message folded into a live turn, or
    deduped to a turn already admitted, joins a run another admission opened. The call is made under
    the conversation-row lock, so exactly one admission opens any run however many deliveries and
    replicas race for it — the line a surface starts a per-turn reporter on, since a reporter posts
    messages and a second one doubles the member's updates for the turn's whole life.

    `arrival_id` names the `inbound_message` row when the message joined a turn already live rather
    than founding one — the id the turn's `Absorbed` frame carries when it folds that row in, so a
    surface tailing the turn knows which of its own messages the agent has taken up, and its absence
    says the returned turn is the message's own."""

    turn_id: UUID
    opened_run: bool
    arrival_id: UUID | None = None
    comment_id: UUID | None = None


@dataclass(frozen=True)
class ObjectChange:
    """One row of the object-change journal, as the admin audit read renders it: the verb, the
    caller that ran it (`member:<id>` or `turn:<id>`), the agent whose namespace the object landed
    in, the kind and name, the before/after specs, and when."""

    kind: str
    name: str
    verb: str
    caller: str
    agent_id: UUID
    spec_before: str | None
    spec_after: str | None
    created_at: datetime


class MemberAdmitter(Protocol):
    """Admit a member message as the member who spoke it."""

    async def admit(
        self,
        conversation_id: UUID,
        message: str,
        idempotency_key: str | None = None,
        context: TurnContext | None = None,
        *,
        speaker_member_id: UUID | None,
        intent: ToolIntent | None = None,
        comment: str | None = None,
        runtime_config: TurnRuntimeConfig | None = None,
    ) -> Admitted: ...


class TurnTailer(Protocol):
    """Tail one turn's live frames until it ends, each frame with its replay cursor — the live
    surface's read half, mirroring `MemberAdmitter`'s write half. The concrete tailer binds the
    process hub and ends the stream on the durable terminal-or-parked state; a live surface never
    touches the hub directly, it reaches it through this one primitive.

    A tail is a scope: it holds a hub subscription and the tasks feeding it, and the block's exit
    releases them — a surface that renders one frame and answers included. `latest_activity` is
    the tail's peek half: the newest Activity the hub retains for a turn, no
    subscription held."""

    def tail(
        self, turn_id: UUID, since: str = ""
    ) -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]: ...

    async def latest_activity(self, turn_id: UUID) -> Activity | None: ...


class TurnStopper(Protocol):
    """End a running turn a member asked to stop — the member's write-half counterpart to
    `TurnTailer`. The concrete stopper cancels the turn's durable workflow, commits its cancelled
    terminal, publishes that terminal so live tails end immediately, and founds the next turn on a
    follow-up the member had already sent; descendants are the cancel reconciler's. Refuses a turn
    that is not the named conversation's."""

    async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> "Stopped": ...


class TurnStepSource(Protocol):
    """Read one turn's durable workflow steps after the surface has gated its workspace."""

    async def read(self, workflow_id: str) -> tuple["TurnStep", ...]: ...


TERMINAL_TURN_STATUSES: tuple[str, ...] = ("done", "failed", "cancelled")
MAX_WRITEBACK_ERROR_CHARS = 2_048
WRITEBACK_POLL_SECONDS = 1.0
WRITEBACK_CLAIM_SECONDS = 300
WRITEBACK_CLAIM_REFRESH_SECONDS = 60
WRITEBACK_RETRY_BACKOFF_SECONDS = 60
WRITEBACK_MAX_AGE_SECONDS = 3600
WRITEBACK_CLAIM_BATCH = 16
WRITEBACK_WORKSPACE_BATCH = 16
WRITEBACK_WORKSPACE_CONCURRENCY = 4
WRITEBACK_WORKSPACE_IN_FLIGHT = WRITEBACK_WORKSPACE_BATCH * 2


SURFACE_MODEL_JOB_PREFIX = "surface:"


class SurfaceModel(Protocol):
    """The metered one-shot model a surface route reaches: `ModelAccess` pinned to the deploy's
    background-jobs model and labelled `surface:<name>`, so a route's own call is keyed, billed, and
    attributed exactly as a job's is — one label per installed surface, a bounded set decided at
    boot. Held as a Protocol because `ModelAccess` lives in `ext.context`, which imports this
    module.

    A route reaches this only for work it can answer without it: a call here runs while a member
    waits, so the handler owns the failure and answers from what it already holds."""

    @property
    def model(self) -> str: ...

    async def turn(self, request: ModelRequest) -> Message: ...


@dataclass(frozen=True)
class ListedArtifact:
    """One row of the portal's artifacts view with its owner, origin, and ways back: the turn and
    conversation that shared it, its surface, and — where the opening turn reported one — the
    thread it came in on, the same string the conversations listing links by.

    `id` is the row's own identity, which is what a reader names one file by: a filename is free
    text an agent chose, so nothing read off it tells two rows apart or bounds what a link to one
    costs to carry."""

    id: UUID
    artifact: "SharedArtifact"
    created_at: datetime
    owner_email: str | None
    origin: str | None
    turn_id: UUID
    conversation_id: UUID
    surface: str
    source: str | None


@dataclass(frozen=True)
class ScheduledRun:
    """One turn that fired on its own — a scheduled task's cron fire or a durable pause's timer
    resume — as the portal's feed reads it: where and when it ran, how it ended, the reply it
    closed with, the files it shared, and the admission key that names what fired it. `source` is
    the reporting conversation's way back out — the permalink its opening turn's surface reported,
    the same string the conversations listing links by."""

    turn_id: UUID
    conversation_id: UUID
    agent_id: UUID
    fired_at: datetime
    status: str
    text: str
    idempotency_key: str | None
    surface: str
    source: str | None
    artifacts: tuple["SharedArtifact", ...]


LIVE_TURN_PRIORITY: tuple[Literal["running", "queued", "parked"], ...] = (
    "running",
    "queued",
    "parked",
)


@dataclass(frozen=True)
class AgentTurnStatus:
    """One agent's turn aggregate as a portal status read draws it: the liveest non-terminal turn
    it holds (`LIVE_TURN_PRIORITY` order) with that turn's id when it is running — the id a hub
    activity peek reads — when any turn of its last moved, and whether its most recent terminal
    turn failed."""

    agent_id: UUID
    live: Literal["running", "queued", "parked"] | None
    running_turn_id: UUID | None
    last_active_at: datetime | None
    last_failed: bool


@dataclass(frozen=True)
class SharedArtifact:
    """A file a turn shared, as the writeback poller hands it to a surface's `attach`: the blob key
    to stream from, the download name, an optional human caption (`subject`), and its media type and
    size — the size lets a chunked-upload API reserve the exact length up front.

    `preview_*` names a second blob holding the rendered picture of a file that is not itself one —
    a document's first page, rasterized in the sandbox at share time. A file that is already an
    image carries none: it is its own preview, minted off `blob_key`."""

    blob_key: str
    filename: str
    subject: str | None
    media_type: str
    size_bytes: int
    preview_blob_key: str | None = None
    preview_media_type: str | None = None
    preview_size_bytes: int | None = None


def shared_artifact_link(
    secret: str, public_base_url: str | None, workspace_id: UUID, artifact: SharedArtifact
) -> str | None:
    """A TTL download link for a shared file, or None when artifact delivery is unconfigured (no
    token secret or no public base URL) — the caller then names the file without a link. Mints the
    same signed URL the web download route verifies: the link opens for anyone holding it until it
    expires, and after that only for a signed-in member of the workspace that shared it."""
    if not secret or not public_base_url:
        return None
    expires_at = artifact_url_expiry(datetime.now(UTC))
    path = mint_artifact_url(secret, artifact.blob_key, expires_at, workspace_id=workspace_id)
    return f"{public_base_url.rstrip('/')}{path}"


def shared_artifact_preview_link(
    secret: str, public_base_url: str | None, workspace_id: UUID, artifact: SharedArtifact
) -> str | None:
    """A signed raster-preview link, or None when its type, size, or delivery is ineligible.

    Two files reach this: one that is already an image, previewed off its own bytes, and one the
    sandbox rasterized a first page for at share time, previewed off that second blob. Either way
    the grant names a raster type and an exact size, so the route serves the bytes inline only
    after they prove to be that picture. The row's own declared type has to agree with the key it
    names, so a document row whose filename says `pdf` never grants a picture."""
    if artifact.preview_blob_key is not None:
        blob_key = artifact.preview_blob_key
        declared = artifact.preview_media_type
        size_bytes = artifact.preview_size_bytes
    else:
        blob_key = artifact.blob_key
        declared = artifact.media_type
        size_bytes = artifact.size_bytes
    if raster_image_media_type(blob_key) != declared:
        return None
    return mint_image_preview_url(
        secret, public_base_url, blob_key, size_bytes, workspace_id=workspace_id
    )


def _scheduled_runs_query(
    workspace_id: UUID, member_id: UUID, agent_id: UUID | None
) -> sa.Select[Any]:
    reported = (
        sa.select(tables.shared_artifact.c.turn_id)
        .where(
            tables.shared_artifact.c.workspace_id == workspace_id,
            tables.shared_artifact.c.turn_id == tables.turn.c.id,
        )
        .exists()
    )
    query = (
        sa.select(
            tables.turn.c.id,
            tables.turn.c.conversation_id,
            tables.turn.c.agent_id,
            tables.turn.c.status,
            tables.turn.c.idempotency_key,
            tables.turn.c.terminal,
            tables.turn.c.created_at,
            tables.conversation.c.surface,
        )
        .select_from(
            tables.turn.join(
                tables.conversation,
                tables.turn.c.conversation_id == tables.conversation.c.id,
            )
        )
        .where(
            tables.turn.c.workspace_id == workspace_id,
            tables.turn.c.admission_source == SCHEDULED_ADMISSION,
            tables.turn.c.terminal.is_not(None),
            sa.or_(tables.turn.c.status != "done", reported),
            tables.conversation.c.audience.in_(readable_audiences(member_id)),
        )
    )
    return query if agent_id is None else query.where(tables.turn.c.agent_id == agent_id)


async def scheduled_runs(
    workspace_id: UUID,
    member_id: UUID,
    *,
    limit: int,
    agent_id: UUID | None = None,
    turn_id: UUID | None = None,
    subjects: frozenset[str] | None = None,
) -> tuple[ScheduledRun, ...]:
    """The newest turns that fired on their own — scheduled admissions — reporting into
    conversations whose content this reader reads: the workspace-shared ones and their own. A
    run's reply is transcript content, so the read never widens for an admin the way
    `readable_conversation` never answers them a private conversation without a recorded
    disclosure, or a room at all — a feed aggregates, and an aggregate of what each row would
    refuse is still refused. A turn still going is not yet a run — it has no reply to report — so
    only terminal turns list, and each carries its terminal reply and the files it shared, so a
    feed renders output and previews without a second walk.

    A run reports by sharing a file — the fire asks for exactly that act — so a run that ended
    well and shared none published nothing and is no row here; its conversation holds it. A
    failure is always a row, because how a fire went wrong is itself the report. `limit` therefore
    counts rows a feed draws, newest first. `turn_id` narrows the read to one run — a permalink —
    under the same fence, so a run outside the audience reads as no rows. `subjects` narrows the
    fence further to the conversation subjects a turn's own room reads — the turn path's fence, so
    an externally shared room is never handed workspace content its audience does not carry."""
    query = _scheduled_runs_query(workspace_id, member_id, agent_id)
    if turn_id is not None:
        query = query.where(tables.turn.c.id == turn_id)
    if subjects is not None:
        query = query.where(tables.conversation.c.audience.in_(subjects))
    query = query.order_by(tables.turn.c.created_at.desc(), tables.turn.c.id.desc()).limit(limit)
    async with workspace_tx() as connection:
        rows = (await connection.execute(query)).all()
        files = (
            await connection.execute(
                sa.select(tables.shared_artifact)
                .where(
                    tables.shared_artifact.c.workspace_id == workspace_id,
                    tables.shared_artifact.c.turn_id.in_(tuple(row.id for row in rows)),
                )
                .order_by(tables.shared_artifact.c.created_at, tables.shared_artifact.c.blob_key)
            )
        ).all()
    shared: dict[UUID, list[SharedArtifact]] = {}
    for file in files:
        shared.setdefault(file.turn_id, []).append(
            SharedArtifact(
                blob_key=file.blob_key,
                filename=file.filename,
                subject=file.subject,
                media_type=file.media_type,
                size_bytes=file.size_bytes,
                preview_blob_key=file.preview_blob_key,
                preview_media_type=file.preview_media_type,
                preview_size_bytes=file.preview_size_bytes,
            )
        )
    sources = await ConversationDirectory(workspace_id).sources(
        tuple({row.conversation_id for row in rows})
    )
    return tuple(
        ScheduledRun(
            turn_id=row.id,
            conversation_id=row.conversation_id,
            agent_id=row.agent_id,
            fired_at=(
                row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC)
            ),
            status=row.status,
            text=TerminalFrame.model_validate(row.terminal).text,
            idempotency_key=row.idempotency_key,
            surface=row.surface,
            source=sources.get(row.conversation_id),
            artifacts=tuple(shared.get(row.id, ())),
        )
        for row in rows
    )


@dataclass(frozen=True)
class Writeback:
    """One terminal turn ready for delivery: its agent and conversation, the conversation's
    `queue_key` (the surface decodes its own channel/thread from it), the turn's whole terminal
    frame, and the files the turn shared. A surface renders the reply and metadata off `terminal`,
    uploads `artifacts`, renders `terminal.question` as its own answer affordance, collects
    credentials privately, or exposes `terminal.connect_request` only through its authenticated
    member channel."""

    turn_id: UUID
    conversation_id: UUID
    agent_id: UUID
    queue_key: str
    terminal: TerminalFrame
    artifacts: tuple[SharedArtifact, ...]


@dataclass(frozen=True)
class MidTurnReply:
    """One reply delivered while a turn runs: words the model marked for delivery or a notice that a
    member commented from another surface. `message_ref` names what it answers, and `id` is its
    durable identity, so a surface that keys its own idempotency record on it posts it once however
    often it is handed the row.

    It carries no terminal frame: a mid-turn reply is not the turn's outcome, so it has no settled
    accounting to footer, no question to attach buttons for, and no shared files. Those ride the
    terminal writeback that follows it."""

    id: UUID
    turn_id: UUID
    conversation_id: UUID
    agent_id: UUID
    queue_key: str
    message_ref: UUID | None
    text: str
    is_comment: bool = False


AMBIENT_REPLY_TIMEOUT_SECONDS = 5.0
LIST_CONVERSATIONS_LIMIT = 200
LIST_TURNS_LIMIT = 500
TRANSCRIPT_ACCESS_WINDOW = timedelta(hours=1)
CONVERSATION_TITLE_CHARS = 240
MAX_CONVERSATION_SPEAKERS = 8


def conversation_name(inbound: str) -> str:
    """What a conversation is called when the turn that opens it names it: the member's own words
    out of the fence — the ambient digest a channel surface wraps them in is not what the
    conversation is about — bounded. Every path that opens a conversation names it through this,
    so a run spawned by an agent and a thread opened by a member are named the same way."""
    return member_message_text(inbound).strip()[:CONVERSATION_TITLE_CHARS]


async def retitle_conversation(workspace_id: UUID, conversation_id: UUID, title: str) -> None:
    """Name a conversation what the surface holding it now calls it — the string every listing row,
    rail row and record header states, and the one a search narrows on. The turn that opens a
    conversation names it from the words it opened with; this is how a surface that has since
    written a better name says so. A blank name is not one and leaves the conversation called what
    it was; an id naming no conversation of this workspace writes nothing."""
    named = title.strip()[:CONVERSATION_TITLE_CHARS]
    if not named:
        return
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.conversation)
            .where(
                tables.conversation.c.workspace_id == workspace_id,
                tables.conversation.c.id == conversation_id,
            )
            .values(title=named)
        )


async def summarize_conversation_title(
    workspace_id: UUID, conversation_id: UUID, title: str
) -> None:
    """Name a conversation what the titling job's summary of its opening exchange calls it, and
    record on the row that the summary has run. The two are one write: the record is what takes the
    conversation out of the job's candidate set, and a summary written without it would be paid for
    again on the next tick.

    A blank summary is not a name and leaves the conversation called what its opening words called
    it — the record still lands, so an exchange no model can name costs one summary rather than one
    every tick."""
    named = title.strip()[:CONVERSATION_TITLE_CHARS]
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.conversation)
            .where(
                tables.conversation.c.workspace_id == workspace_id,
                tables.conversation.c.id == conversation_id,
            )
            .values(title=named or tables.conversation.c.title, title_summarized=True)
        )


class AgentSummary(BaseModel):
    """One workspace agent as a surface lists it — the read a surface whose member picks an agent
    (the web portal's switcher) filters through its own audience authority.
    `internet_access_allowed` is the agent's narrowing of the deploy's sandbox public-internet
    capability, carried for the administration view. `owner_member_id` is the member who created
    the row (None for the main agent and provisioned rows), so an audience can give an owner
    their own agent without a separate grant. `visibility` is the agent's own audience floor:
    `workspace` answers every member, `private` its owner and admins plus per-surface grants.
    `icon` is the slug the surface draws the agent with. `provisioned_by` names the extension
    whose provision created the row (None for member-created rows and main), so a surface can
    tell a shipped app from an agent a member built."""

    id: UUID
    name: str
    main: bool
    model: str
    internet_access_allowed: bool
    visibility: AgentVisibility
    icon: TablerIcon
    purpose: str | None = None
    """The one sentence the agent states about what it is for, or None for a row written before
    it had one. A surface states it wherever a member meets the agent before opening it."""
    owner_member_id: UUID | None = None
    provisioned_by: str | None = None


class ArchivedAgent(BaseModel):
    """One archived app as a surface lists it for restore: the name it held, and `object_name`,
    the durable name its agent object answers to — what a restore targets."""

    id: UUID
    name: str
    object_name: str
    icon: TablerIcon
    archived_at: datetime
    owner_member_id: UUID | None = None


class InstallationSummary(BaseModel):
    """One surface installation of the workspace — which surface is bound and the agent its
    conversations land on. The workspace-administration read behind the portal's agents view;
    binding stays each surface's own act."""

    surface: str
    agent_id: UUID


class AgentDetail(BaseModel):
    """One agent as a portal settings read states it: the row's configuration beside its prompt
    digest, the chat surfaces whose installations bind to it, and what it still needs from a
    member. `icon` is the slug the settings page draws the agent with."""

    name: str
    main: bool
    model: str
    internet_access_allowed: bool
    use_workspace_skills: bool
    reasoning: ReasoningEffort
    sandbox_size: SandboxSize
    visibility: AgentVisibility
    icon: TablerIcon
    prompt: str
    prompt_digest: str
    surfaces: tuple[str, ...]
    setup: AgentSetup | None
    """The grants an extension shipped this agent expecting that no grant covers yet, or None once
    it is wired and for an agent no extension shipped. The portal offers the setup from this, and
    stops offering it as the grants land — the offer is derived, never a flag to clear."""
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class PortalKind:
    """One object kind as the portal's index and detail pages read it around its rows: the fields
    it declared for filtering and ordering, and its spec schema (None for a kind this deploy
    registers without one). A value object — the web surface renders it and nothing persists
    it."""

    kind: str
    list_fields: tuple[str, ...]
    spec_schema: dict[str, Any] | None


@dataclass(frozen=True)
class PortalSkill:
    """One skill as the portal lists it: a member-authored skill of the workspace
    (`origin="member"`) or a deploy-provided loadable skill (`origin="deploy"`), carrying the
    workflow body the agent loads so the portal can show the whole record. `depends` and `agents`
    are the frontmatter's routing metadata — an edit regenerates `SKILL.md`, so it writes them
    back rather than dropping them."""

    name: str
    description: str
    origin: Literal["member", "deploy"]
    instructions: str
    depends: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()


class ConnectionView(BaseModel):
    """One connector account reaching one agent, as the portal's connections panel lists it: the
    provider identity, the `connector_grant` object name a prepared intent mutates it by, the
    consenting owner, whether this viewer may manage it, the edge's disclosure, and when the grant
    landed."""

    provider: str
    account_id: str
    account_label: str | None
    grant: str
    owner_email: str | None
    own: bool
    shared: bool
    connected_at: datetime

    @field_validator("connected_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class AttachedAgentView(BaseModel):
    id: UUID
    name: str


class ConnectionPoolView(BaseModel):
    """One connected account as the workspace's connector library lists it, with every agent
    holding an edge to it. `own` is whether this viewer may manage the account itself — its owner
    or a workspace admin, the same rule the disconnect gate re-checks — so the library draws a
    remove control exactly where the act would be admitted."""

    provider: str
    account_id: str
    grant: str
    account_label: str | None
    owner_email: str | None
    own: bool
    shared: bool
    connected_at: datetime
    agents: tuple[AttachedAgentView, ...]

    @field_validator("connected_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class GithubCoverageView(BaseModel):
    api: bool
    git_push: bool
    sources: bool


class SpendCapView(BaseModel):
    """One spend cap as the administration view lists it: the scope, its subject named for the
    reader (an agent's name, a member's email, nothing for the workspace's own cap), the window,
    the limit, and what a breach does."""

    scope: str
    subject: str | None
    window_seconds: int
    limit_micro_usd: int
    on_breach: str


class DeployExtensionView(BaseModel):
    """One installed extension as the administration view lists it: the manifest's name and
    version, and whether its sandbox tools require metered public egress — deploy shape only,
    never an agent's resources or secrets."""

    name: str
    version: str
    sandbox_internet: bool


class CredentialSlotView(BaseModel):
    """One declared BYOK slot and whether the workspace holds a value for it — never the value.
    Slots come from installed manifests, the same declarations the `credential` object kind
    projects, minus the slots only the deploy's own code writes (`member_filled=False`, a provider
    callback's seal): those are machinery a member can neither fill nor rotate, so the panel
    leaves them out. Deploy config (model keys, signing secrets) is not a slot at all and cannot
    appear. `name` is the `credential` object kind's name for the slot — the address a prepared
    intent mutates."""

    slot: str
    name: str
    extension: str
    description: str
    filled: bool


class _BindingFields(TypedDict):
    """The projection `_binding_fields` returns, typed so its `**` expansion into `SourceView`
    is checked field by field rather than collapsed to one union — which is what lets the
    projection carry a non-string identity field at all."""

    name: str | None
    stream: str | None
    account_id: str | None
    base_url: str | None
    backfill_days: int | Literal["all"] | None


def _binding_fields(backend: str, config: dict[str, JsonValue]) -> _BindingFields:
    """The `source` kind's identity for one row, from the stored connector config — the binding
    name plus the spec fields a panel act echoes back. A row whose config is not a connector's
    (a config-registered folder, a feed) is not kind-managed and carries None throughout.

    Every field the object's own identity is built from has to be here, not just the ones a column
    displays: a panel act submits `{...row.apply, <the one thing it changes>}`, so a field missing
    from this projection arrives at the verb as its default and reads as an edit nobody made. That
    is what refuses the act — a resync whose submitted spec must equal the binding's, a share-flip
    that reaches the identity check first. `backfill_days` is carried for exactly that reason and
    for no display purpose; `test_the_portals_binding_projection_carries_every_identity_field`
    holds the set complete as `SourceSpec` grows."""
    try:
        parsed = ConnectorSourceConfig.model_validate(config)
    except ValidationError:
        return {
            "name": None,
            "stream": None,
            "account_id": None,
            "base_url": None,
            "backfill_days": None,
        }
    return {
        "name": binding_name(backend, parsed.account, parsed.base_url),
        "stream": parsed.stream,
        "account_id": "" if parsed.account == DIRECT_ACCOUNT else parsed.account,
        "base_url": parsed.base_url or "",
        "backfill_days": parsed.backfill_days,
    }


class SourceView(BaseModel):
    """One live source stream as the portal lists it: the backend, its disclosure subject
    (member-private pages stay gated to their member; `shared` means the agent's audience), the
    registering owner, whether this viewer may manage it, sync health, and — for a
    connector-registered row — the `source` kind's binding name plus the spec fields that
    reconstruct the binding, so the panel's per-binding acts (resync, share, remove) submit the
    same object the chat verbs mutate. A config- or feed-registered row is not kind-managed and
    carries None.

    `parked_reason` is what a refused stream reads as, and carries the park: it is set exactly on a
    row the driver slowed to an hour. Without it the panel shows a row the provider has stopped
    answering as healthy — no errors, next sync a minute out — while it syncs nothing. The text is
    the backend's own, and names the scope to re-grant to have it back inside the minute."""

    backend: str
    shared: bool
    owner_email: str | None
    own: bool
    consecutive_errors: int
    next_sync_at: datetime
    parked_reason: str | None = None
    name: str | None = None
    stream: str | None = None
    account_id: str | None = None
    base_url: str | None = None
    backfill_days: int | Literal["all"] | None = None

    @field_validator("next_sync_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class ConversationSummary(BaseModel):
    """One conversation as a read view lists it: identity and keying, the owning member's email,
    and its activity aggregates — deliberately spanning every surface in the workspace (the
    context's own `surface` scopes keying and admission, never these reads)."""

    id: UUID
    surface: str
    queue_key: str
    member_email: str | None
    created_at: datetime
    turn_count: int
    last_turn_at: datetime | None

    @field_validator("created_at", "last_turn_at")
    @classmethod
    def _aware_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class TranscriptAccess(BaseModel):
    """One recorded disclosure: the moment an admin acknowledged that another member's private
    transcript may hold private information and read it. Written by `record_transcript_access`
    before any content is served; the row it names is the operator's record, read by `ufoctl
    transcript-reads`, and the subject it names is what the acknowledging admin is told back."""

    reader_email: str
    subject_email: str


async def record_transcript_access(
    workspace_id: UUID, conversation_id: UUID, agent_id: UUID, member_id: UUID
) -> TranscriptAccess | None:
    """Record that an admin acknowledged another member's private transcript may hold private
    information and is reading it, and open that conversation's content to them for
    `TRANSCRIPT_ACCESS_WINDOW`. This is the disclosure's one writer, and it is a tool the portal
    reaches through the prepared-intent lane rather than a route of its own: the row is a grant —
    it is what `readable_conversation` answers on — and a granting act is speaker-gated and rides
    a turn, so the turn is its audit record and this row is the gate it opens. The same act emits
    `surface.transcript_disclosed`, which is the operator's read of who opened whose conversation;
    no member surface lists the rows.

    Each acknowledgement writes its own row, so a second visit is a second access rather than a
    silent re-read. None — the caller's refusal — for a conversation this agent does not hold, for
    a room or externally-shared channel (content nobody reads here), and for the reader's own or
    the workspace-shared one, which need no disclosure. The caller establishes that the member is
    an admin."""
    async with workspace_tx() as connection:
        found = (
            await connection.execute(
                sa.select(tables.conversation.c.audience).where(
                    tables.conversation.c.workspace_id == workspace_id,
                    tables.conversation.c.id == conversation_id,
                    tables.conversation.c.agent_id == agent_id,
                )
            )
        ).one_or_none()
        if found is None:
            return None
        subject_id = audience_member(parse_audience(found.audience))
        if subject_id is None or subject_id == member_id:
            return None
        named = (
            await connection.execute(
                sa.select(tables.member.c.id, tables.member.c.email).where(
                    tables.member.c.workspace_id == workspace_id,
                    tables.member.c.id.in_((member_id, subject_id)),
                )
            )
        ).all()
        emails: dict[UUID, str] = {row.id: row.email for row in named}
        recorded_at = datetime.now(UTC)
        await connection.execute(
            sa.insert(tables.transcript_access).values(
                id=uuid4(),
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                reader_member_id=member_id,
                subject_member_id=subject_id,
                created_at=recorded_at,
            )
        )
    log(
        "surface.transcript_disclosed",
        conversation_id=str(conversation_id),
        reader_email=emails[member_id],
        subject_email=emails[subject_id],
    )
    return TranscriptAccess(
        reader_email=emails[member_id],
        subject_email=emails[subject_id],
    )


class ConversationSpeaker(BaseModel):
    """One member who has spoken in a conversation: the workspace email their turns are attributed
    to, and `sender` — the line the admitting surface reported them under on the first of those
    turns, which Slack writes as `Real Name (email)`. A `member` row carries no display name and a
    Slack handle is stored nowhere, so that line is the only name there is; a surface that reports
    none leaves it None and the address is the whole answer."""

    email: str
    sender: str | None


class ListedConversation(BaseModel):
    """One conversation as the portal's per-agent conversations view lists it: `ConversationSummary`
    plus whether this viewer may read its content now and whether they may disclose it to
    themselves by acknowledging (an admin, another member's private conversation — never a
    room's), the words that opened it, where they were said, and who has spoken in it. Disclosures
    recorded against it are not here and no portal read lists them: the record is the operator's,
    kept in `transcript_access` and reported by `surface.transcript_disclosed`.

    `audience` and `surface_label` travel as the conversation row stores them — the portal maps
    them to member words.

    `title` is what the conversation is called: the member's own opening words as the turn that
    opened it stored them — the ambient digest a channel surface renders around them is not what
    the conversation is about — or whatever a surface has since named it. `source` is the first
    turn's `TurnContext.source`: the link the admitting surface reported for the message that
    opened the conversation, which for Slack is the permalink of its first message, so a screen
    reading the conversation leads back to the thread it came in on. It is None where the surface
    reported none and surface-defined where it did — a portal chat's own source is not a link out.
    `speakers` runs in order of first appearance and stops at `MAX_CONVERSATION_SPEAKERS`.

    All are content of the conversation and all answer empty unless `readable`: a row listed to
    an admin as administration metadata states whose it is and how busy, never a word of it and
    never who else is in it. Reading it is the acknowledgement's act, and the acknowledgement is
    what `record_transcript_access` audits — so the gate is here, where `readable` is decided, and
    no read surface can carry past it."""

    summary: ConversationSummary
    audience: str
    surface_label: str | None
    readable: bool
    disclosable: bool
    title: str
    source: str | None
    speakers: tuple[ConversationSpeaker, ...]


@dataclass(frozen=True)
class ConversationDirectory:
    """One workspace's conversation listings: the single implementation behind the portal's
    per-agent views, the permalink resolves, and the `conversation` kind's member listing, so a
    conversation listed anywhere is listed by exactly one query."""

    workspace_id: UUID

    async def list(
        self,
        agent_id: UUID,
        member_id: UUID,
        *,
        admin: bool,
        limit: int,
        surface: str | None = None,
        portal: bool | None = None,
        conversation_id: UUID | None = None,
        participation: Literal["mine", "others"] | None = None,
        search: str | None = None,
        member_admitted: bool = False,
    ) -> tuple[ListedConversation, ...]:
        """One agent's conversations as the portal lists them, newest activity first and bounded:
        the member's own plus the workspace-shared ones, every one of the agent's for an admin.
        `surface` narrows to one surface's conversations in the query, before the bound, so a
        member's rows are never displaced by another surface's newer traffic under the cap.
        `portal` narrows the same way to the surfaces the portal's own chat transport carries —
        its own and the extension-opened ones its composer answers — or, false, to the rest.
        `participation` narrows the same way to one side of the member: `mine` is the ones they
        are in — `_participated` defines that — and `others` the readable ones somebody else spoke
        and they did not. A rail reads both, one bound each; an agent's directory reads neither.
        `member_admitted` narrows to conversations a member's own message ever opened a turn in —
        what separates a conversation from a machine lane sharing its surface (a homepage seed, the
        portal's prepared-intent queue). Each entry carries `readable` (content this viewer reads
        now) and `disclosable` (an admin may acknowledge and read another member's private one —
        `record_transcript_access` is the act). Subagent conversations are absent: they are the
        agent's own work on a request, listed nested under the turn that spawned them, never beside
        it. `conversation_id` selects one exact row before the bound for a durable permalink.

        Newest activity is the last turn, and creation only where no turn has landed yet, so the
        top of the page is what moved most recently rather than what was opened most recently.
        Creation breaks a tie under it, which is what two conversations opened together and never
        spoken in are.

        The opening words and the speakers are two further reads over the page's ids, never one
        per row: what a conversation is about and who is in it are facts of its turns, and only a
        turn read can answer them. Both reads are narrowed to the rows this viewer may read, so an
        unreadable row's content is never fetched, let alone carried."""
        activity = (
            sa.select(
                tables.turn.c.conversation_id,
                sa.func.count().label("turn_count"),
                sa.func.max(tables.turn.c.updated_at).label("last_turn_at"),
            )
            .where(tables.turn.c.workspace_id == self.workspace_id)
            .group_by(tables.turn.c.conversation_id)
            .subquery()
        )
        query = (
            sa.select(
                tables.conversation.c.id,
                tables.conversation.c.surface,
                tables.conversation.c.queue_key,
                tables.conversation.c.audience,
                tables.conversation.c.surface_label,
                tables.conversation.c.title,
                tables.member.c.email,
                tables.conversation.c.created_at,
                activity.c.turn_count,
                activity.c.last_turn_at,
            )
            .select_from(
                tables.conversation.outerjoin(
                    tables.member, tables.member.c.id == tables.conversation.c.member_id
                ).outerjoin(activity, activity.c.conversation_id == tables.conversation.c.id)
            )
            .where(
                tables.conversation.c.workspace_id == self.workspace_id,
                tables.conversation.c.agent_id == agent_id,
                tables.conversation.c.surface != SUBAGENT_SURFACE,
            )
            .order_by(
                sa.func.coalesce(activity.c.last_turn_at, tables.conversation.c.created_at).desc(),
                tables.conversation.c.created_at.desc(),
            )
            .limit(limit)
        )
        if surface is not None:
            query = query.where(tables.conversation.c.surface == surface)
        if portal is not None:
            carried = sa.or_(
                tables.conversation.c.surface == PORTAL_SURFACE,
                tables.conversation.c.surface.startswith(EXTENSION_SURFACE_PREFIX),
            )
            query = query.where(carried if portal else sa.not_(carried))
        if conversation_id is not None:
            query = query.where(tables.conversation.c.id == conversation_id)
        match participation:
            case "mine":
                query = query.where(self._participated(member_id))
            case "others":
                query = query.where(self._others(member_id))
            case None:
                pass
        if member_admitted:
            query = query.where(self._member_admitted())
        if not admin:
            query = query.where(tables.conversation.c.audience.in_(readable_audiences(member_id)))
        if search:
            query = query.where(self._matches(search, member_id))
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        if not rows:
            return ()
        readable = readable_audiences(member_id)
        content = [row.id for row in rows if row.audience in readable]
        sources = await self.sources(content)
        speakers = await self.speakers(content)
        mine = str(conversation_audience(member_id))
        return tuple(
            ListedConversation(
                summary=ConversationSummary(
                    id=row.id,
                    surface=row.surface,
                    queue_key=row.queue_key,
                    member_email=row.email,
                    created_at=row.created_at,
                    turn_count=row.turn_count or 0,
                    last_turn_at=row.last_turn_at,
                ),
                audience=row.audience,
                surface_label=row.surface_label,
                readable=row.audience in readable,
                disclosable=admin
                and row.audience != mine
                and audience_member(parse_audience(row.audience)) is not None,
                title=row.title or "" if row.audience in readable else "",
                source=sources.get(row.id),
                speakers=speakers.get(row.id, ()),
            )
            for row in rows
        )

    async def sources(self, listed: Sequence[UUID]) -> dict[UUID, str | None]:
        """Each listed conversation's opening `TurnContext.source` — the link the admitting surface
        reported for the message that opened it."""
        if not listed:
            return {}
        opening = (
            sa.select(
                tables.turn.c.conversation_id,
                sa.func.min(tables.turn.c.seq).label("seq"),
            )
            .where(
                tables.turn.c.workspace_id == self.workspace_id,
                tables.turn.c.conversation_id.in_(listed),
            )
            .group_by(tables.turn.c.conversation_id)
            .subquery()
        )
        query = (
            sa.select(tables.turn.c.conversation_id, tables.turn.c.context)
            .select_from(
                tables.turn.join(
                    opening,
                    sa.and_(
                        tables.turn.c.conversation_id == opening.c.conversation_id,
                        tables.turn.c.seq == opening.c.seq,
                    ),
                )
            )
            .where(tables.turn.c.workspace_id == self.workspace_id)
        )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        return {
            row.conversation_id: (
                None if row.context is None else TurnContext.model_validate(row.context).source
            )
            for row in rows
        }

    async def speakers(self, listed: Sequence[UUID]) -> dict[UUID, tuple[ConversationSpeaker, ...]]:
        """Each listed conversation's speakers in order of first appearance, stopping at
        `MAX_CONVERSATION_SPEAKERS`."""
        if not listed:
            return {}
        said = (
            sa.select(
                tables.turn.c.conversation_id,
                tables.turn.c.seq,
                tables.turn.c.context,
                tables.member.c.email,
                sa.func.row_number()
                .over(
                    partition_by=(tables.turn.c.conversation_id, tables.turn.c.speaker_member_id),
                    order_by=tables.turn.c.seq,
                )
                .label("said_rank"),
            )
            .select_from(
                tables.turn.join(
                    tables.member, tables.member.c.id == tables.turn.c.speaker_member_id
                )
            )
            .where(
                tables.turn.c.workspace_id == self.workspace_id,
                tables.turn.c.conversation_id.in_(listed),
            )
            .subquery()
        )
        first = (
            sa.select(
                said.c.conversation_id,
                said.c.context,
                said.c.email,
                sa.func.row_number()
                .over(partition_by=said.c.conversation_id, order_by=said.c.seq)
                .label("speaker_rank"),
            )
            .where(said.c.said_rank == 1)
            .subquery()
        )
        query = (
            sa.select(first.c.conversation_id, first.c.context, first.c.email)
            .where(first.c.speaker_rank <= MAX_CONVERSATION_SPEAKERS)
            .order_by(first.c.conversation_id, first.c.speaker_rank)
        )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        spoke: dict[UUID, list[ConversationSpeaker]] = {}
        for row in rows:
            context = None if row.context is None else TurnContext.model_validate(row.context)
            spoke.setdefault(row.conversation_id, []).append(
                ConversationSpeaker(
                    email=row.email, sender=None if context is None else context.sender
                )
            )
        return {conversation_id: tuple(who) for conversation_id, who in spoke.items()}

    def _member_admitted(self) -> sa.ColumnElement[bool]:
        """Whether a member's own message ever opened a turn here. An intent turn carries the
        member as speaker but is admitted as `intent`, and a seed's turn is `scheduled`, so the
        admission source is the one column that tells a conversation from a machine lane."""
        return (
            sa.select(sa.literal(1))
            .where(
                tables.turn.c.workspace_id == self.workspace_id,
                tables.turn.c.conversation_id == tables.conversation.c.id,
                tables.turn.c.admission_source == MEMBER_ADMISSION,
            )
            .correlate(tables.conversation)
            .exists()
        )

    def _spoken(self, member_id: UUID | None) -> sa.ColumnElement[bool]:
        """Whether the conversation holds a member turn — this member's where one is named, any
        member's where none is.

        Correlated on the row being listed rather than grouped over the workspace: `turn_spoken`
        indexes exactly this lookup, so each candidate costs one seek instead of every turn in the
        workspace being reduced to a speaker table the bound then throws most of away."""
        speaker = (
            tables.turn.c.speaker_member_id.is_not(None)
            if member_id is None
            else tables.turn.c.speaker_member_id == member_id
        )
        return (
            sa.select(sa.literal(1))
            .where(
                tables.turn.c.workspace_id == self.workspace_id,
                tables.turn.c.conversation_id == tables.conversation.c.id,
                speaker,
            )
            .correlate(tables.conversation)
            .exists()
        )

    def _participated(self, member_id: UUID) -> sa.ColumnElement[bool]:
        """Whether this member is in the conversation: it is bound to them, or they spoke a turn of
        it. Answering in another member's thread counts — the member was there, and a rail that
        drops it hides work they did. A conversation an extension opened — a trigger run, a review,
        an agent's own errand — carries no member and holds no member turn, so it is in nobody's."""
        return sa.or_(
            tables.conversation.c.member_id == member_id,
            self._spoken(member_id),
        )

    def _others(self, member_id: UUID) -> sa.ColumnElement[bool]:
        """The complement, over the conversations a member turn stands in: somebody spoke, and it
        was not this member, and the row is not bound to them either.

        `is_distinct_from` carries the binding test because `member_id` is null on exactly the rows
        this group is made of — a shared thread belongs to no member — and `member_id <> :me` is
        null there, which a `where` reads as false. Negating the participation predicate whole
        would empty the group in silence."""
        return sa.and_(
            tables.conversation.c.member_id.is_distinct_from(member_id),
            sa.not_(self._spoken(member_id)),
            self._spoken(None),
        )

    def _matches(self, search: str, member_id: UUID) -> sa.ColumnElement[bool]:
        """Whether a conversation answers to `search`. It narrows in the query, ahead of the bound,
        because a term applied after one is a filter over the page the bound already cut — the
        conversation the member is looking for is the one that fell off it.

        What a row states about itself is matched for whoever may list it: the origin the surface
        named, and the member it belongs to. What the conversation holds — what it is called, and
        who has spoken in it — is matched only where this member may read that content, the same
        gate `readable` puts on carrying it. Otherwise an admin's search would answer which words
        stand in another member's private thread, which reading it would have audited."""
        speaker = tables.member.alias("search_speaker")
        spoke = (
            sa.select(sa.literal(1))
            .select_from(tables.turn.join(speaker, speaker.c.id == tables.turn.c.speaker_member_id))
            .where(
                tables.turn.c.workspace_id == self.workspace_id,
                tables.turn.c.conversation_id == tables.conversation.c.id,
                speaker.c.email.icontains(search, autoescape=True),
            )
            .correlate(tables.conversation)
            .exists()
        )
        return sa.or_(
            tables.conversation.c.surface_label.icontains(search, autoescape=True),
            tables.member.c.email.icontains(search, autoescape=True),
            sa.and_(
                tables.conversation.c.audience.in_(readable_audiences(member_id)),
                sa.or_(tables.conversation.c.title.icontains(search, autoescape=True), spoke),
            ),
        )


class LedgerEntry(BaseModel):
    """One accounting row of a turn — a dimension's metered amount and its priced cost."""

    dimension: str
    amount: int
    priced_micro_usd: int
    model: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class TurnStep(BaseModel):
    """One durable workflow step with its recorded identity and wall-clock interval. `messages` is
    what the step's recorded output rebuilds — a model round as the assistant message it produced,
    a tool dispatch as its result — so a reader sees the turn's actual trajectory, uncompacted, and
    not only the timeline. A step whose output carries no window (a compaction, a claimed arrival, a
    round that only errored) contributes none."""

    number: int = Field(ge=1)
    kind: Literal["model", "tool", "workflow"]
    name: str
    function_name: str
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None = Field(ge=0)
    messages: tuple[Message, ...] = ()

    @field_validator("started_at", "completed_at")
    @classmethod
    def _aware_utc(cls, value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)


class TurnDetail(BaseModel):
    """One turn with everything durable that hangs off it: the row itself (terminal outcome and
    context included), its accounting, and the subagent turns it spawned (`parent_turn_id`
    children, each living in its own conversation)."""

    turn: Turn
    ledger: tuple[LedgerEntry, ...]
    children: tuple[Turn, ...]


class QueuedArrival(BaseModel):
    """One message admitted to a conversation that its written transcript does not hold yet: the
    queue row a member admission returns as its `arrival_id`, and the inbound it carries. A
    projection reads these so a conversation reloaded mid-turn still shows the message, drained or
    not — it is admitted and durable, and only the written transcript is still without it.

    `waiting` is whether a turn has yet to take it up — the state the turn's `Absorbed` frame ends.
    `admission_source` is the only fact that says whose message it is: the queue holds a member's
    own words folded into a running turn and an agent's prompt invoked into that same turn, and
    neither the id nor the body tells them apart. A projection stating a wait of the member's reads
    both — an agent-origin row is nothing the member sent and nothing they are waiting on."""

    id: UUID
    inbound: str
    waiting: bool
    admission_source: TurnAdmissionSource


class SpokenArrival(BaseModel):
    """One member-admitted queue row's attribution: the display line the admitting surface
    reported, the member the words are attributed to, and the question the words answered when the
    surface knew one — exactly what the turn it joined carries for its own founding message.
    Drained rows included: the engine names a folded message by its queue-row id in the
    transcript's `<context>` tag, so a projection labelling who spoke reads these beside the turn
    rows, which only name the messages that founded turns."""

    id: UUID
    sender: str | None
    question: str | None
    speaker_member_id: UUID | None


class KeyedAdmission(BaseModel):
    """One message a surface admitted under an idempotency key of its own: the `message_ref` the
    transcript names it by, the key it landed under, and the words that landed. Both id spaces
    answer, because a turn's founding message is referenced by the turn and one folded into a
    running turn by its queue row."""

    ref: UUID
    idempotency_key: str
    inbound: str


def _fulfilled_marker_key(request_id: UUID, slot: str) -> str:
    return f"credential_requests/{request_id.hex}/{slot}"


def _credential_request_id(state: CredentialRequestState, sealed: str) -> UUID:
    if state.request_id is not None:
        return state.request_id
    return UUID(bytes=hashlib.sha256(sealed.encode()).digest()[:16])


async def _main_agent(workspace_id: UUID) -> UUID:
    """The workspace's main agent, used when no surface binding names an agent."""
    async with workspace_tx() as connection:
        agent = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(
                    tables.agent.c.workspace_id == workspace_id,
                    tables.agent.c.is_main,
                )
            )
        ).one_or_none()
    if agent is None:
        raise RuntimeError(f"workspace {workspace_id} has no main agent")
    return agent.id


async def _bind_surface_installation(
    workspace_id: UUID, surface: str, installation_id: str, *, routes_ingress: bool
) -> None:
    """Upsert one surface's installation binding for a workspace, replacing any prior binding for
    that (workspace, surface). A new binding lands on the workspace's main agent; rebinding
    replaces the installation identity and keeps the binding's agent. `routes_ingress` says the
    installation is what selects the tenant — a customer's own account, so the partial unique index
    on (surface, installation_id) raises `SurfaceInstallationConflict` when another workspace holds
    it. An addressed surface's installation is the deploy's own and routes nothing, so every
    workspace binds the same one and its members are told apart by `surface_address`. The one place
    the binding is written — a tool (`SurfaceInstallationAccess.bind`) and a surface's own OAuth
    callback (`SurfaceContext.bind_installation`) both land it here."""
    if not installation_id:
        raise ValueError("surface installation id is empty")
    agent_id = await _main_agent(workspace_id)
    try:
        async with workspace_tx() as connection:
            insert = postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(tables.surface_installation)
                .values(
                    workspace_id=workspace_id,
                    surface=surface,
                    installation_id=installation_id,
                    agent_id=agent_id,
                    routes_ingress=routes_ingress,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_update(
                    index_elements=("workspace_id", "surface"),
                    set_={
                        "installation_id": installation_id,
                        "routes_ingress": routes_ingress,
                        "updated_at": sa.func.now(),
                    },
                )
            )
    except sa.exc.IntegrityError as error:
        raise SurfaceInstallationConflict(surface) from error


@dataclass(frozen=True)
class AddressClaim:
    """One address an addressed surface routes to a member. `claim_expires_at` is set while the
    sender has not proved the address yet and cleared once they have, and `proved_by` is the inbound
    message that proved it."""

    member_id: UUID
    claim_expires_at: datetime | None
    proved_by: str | None


@dataclass(frozen=True)
class SurfaceContext:
    """The privileged handle a surface's route handlers receive — one context spanning both delivery
    modes. `blob` and the admit/identity reach are deliberately unscoped for a workspace's trusted
    surface (the distinction from a scoped extension context, which never admits a turn or asserts
    identity). A **durable** surface (Slack, iMessage) delivers through the poller; a **live**
    surface (web; core's CLI is the built-in twin) delivers by `tail`-ing the turn's frames off the
    hub in its own SSE route, reading `turn_owner` to gate a tail, `spend_rollup` for a workspace
    spend view and `member_spend` for the reader's own, and the per-agent projections a portal
    renders —
    `list_member_objects`/`member_object`/`object_kind`, `agent_skills`, `agent_spend`,
    `connector_catalog`, and `memory_available`/`search_memory` —
    and either mode renders a turn's
    shared files, the poller handing them to `attach` while a live surface reads
    `shared_artifacts` and links each through `artifact_link`. Each calls only what it needs.
    `credential` reads the surface workspace's slots in-process (never through the sandbox proxy),
    and the sealed member handoff (`credential_prompt_pending`, `fulfill_credential_request`)
    rides the same store — a surface with neither declared slots nor a member credential handoff
    never calls it."""

    workspace_id: UUID
    surface: str
    blob: WorkspaceBlobStore
    _sandboxes: ConversationSandbox
    _admitter: MemberAdmitter
    _tailer: TurnTailer
    _stopper: TurnStopper
    _turn_steps: TurnStepSource
    _credentials: CredentialStore | None
    _artifact_token_secret: str
    _public_base_url: str | None
    _home_surface: str | None
    _ingress_public_url: str | None
    _deploy_sandbox_internet: bool
    _models: tuple[str, ...]
    _skills: SkillRegistry
    _member_skill_listing: Callable[[], Awaitable[tuple[RuntimeSkill, ...]]]
    _declared_slots: tuple[DeclaredSlot, ...]
    _ambient_reply: AmbientReplyClassifier
    _connectors: ConnectorRegistry
    _runtime: RuntimeIdentity | None = None
    _ambient_reply_for: Callable[[str], AmbientReplyClassifier] | None = None
    _system_skill_bundle: SystemSkillBundle = field(
        default_factory=lambda: SystemSkillBundle.from_skills(())
    )
    _key_slot_for: Callable[[str], str | None] | None = None
    _object_schemas: Mapping[str, dict[str, Any]] = field(default_factory=dict)
    _deploy_extensions: tuple[DeployExtensionView, ...] = ()
    _sandbox_sizes: tuple[str, ...] = ()
    _memory: "MemorySearch | None" = None
    _model: "SurfaceModel | None" = None
    _store_environment_document: Callable[[WorkspaceBlobStore, bytes], Awaitable[str]] | None = None
    _store_environment_file: Callable[[WorkspaceBlobStore, bytes], Awaitable[str]] | None = None
    _objects: "Mapping[str, BoundKind]" = MappingProxyType({})
    _actions: "Mapping[str, Mapping[str, BoundAction]]" = MappingProxyType({})
    _frame_admissible: frozenset[str] = frozenset()
    _conversation_slots: tuple["BoundConversationSlot", ...] = ()
    _preview_url: str | None = None
    _preview_token: str | None = None

    @property
    def fleet_blob(self) -> FleetBlobStore:
        """The deploy-owned view of the same store `blob` scopes: closed to the fleet namespaces
        (static assets), for data every workspace shares."""
        return FleetBlobStore(backend=self.blob.backend)

    @property
    def conversation_slots(self) -> tuple["BoundConversationSlot", ...]:
        """The deploy's extension-provided conversation slots, fixed and validated at boot."""
        return self._conversation_slots

    async def read_conversation_slot(
        self, bound: "BoundConversationSlot", context: "ConversationSlotContext"
    ) -> "ConversationSlotPayload":
        """Run one slot read in the authorized conversation's agent namespace."""
        with bind_agent(context.agent_id):
            return await bound.provider.read(context)

    async def summarize_conversation_slot(
        self, bound: "BoundConversationSlot", context: "ConversationSlotContext"
    ) -> int | None:
        """Run one slot summary in the authorized conversation's agent namespace."""
        with bind_agent(context.agent_id):
            return await bound.provider.summarize(context)

    @property
    def deploy_extensions(self) -> tuple[DeployExtensionView, ...]:
        """The deploy's installed extensions — the administration view's deploy-status read,
        fixed at boot from the manifest set the process loaded."""
        return self._deploy_extensions

    @property
    def runtime(self) -> RuntimeIdentity | None:
        """The service and sandbox identity supplied by the composition root."""
        return self._runtime

    @property
    def deploy_sandbox_internet(self) -> bool:
        """Whether this deploy's active extensions grant sandbox public internet at all — the
        ceiling a portal shows an agent's `internet_access_allowed` narrowing."""
        return self._deploy_sandbox_internet

    @property
    def deploy_skills(self) -> tuple[tuple[str, str], ...]:
        """The deploy's loadable-skill index — the floor of what any child can load. A spawn merges
        the spawning agent's member-authored skills onto it before rendering the child's
        `{{skill_index}}`, and a profile is deploy shape reached by every agent, so those belong to
        `agent_skills` and this names the shared part."""
        return self._skills.index()

    @property
    def system_skill_bundle(self) -> SystemSkillBundle:
        """The immutable deploy-skill archive a terminal caches before it executes a turn."""
        return self._system_skill_bundle

    @property
    def models(self) -> tuple[str, ...]:
        """The model ids this deploy's registry serves, `auto` first — the closed set a portal
        offers where an agent's model is chosen, the same records the runtime routes and bills
        on."""
        return self._models

    def validate_runtime_config(self, runtime_config: TurnRuntimeConfig) -> None:
        """Refuse a turn model selection this deployed runtime cannot execute, and an environment
        document on a deployment that does not allow one. The document store callable stands in
        for the deployment gate: the composition root wires it exactly where documents are
        allowed, so runtime never learns what a document is."""
        if runtime_config.model is not None and runtime_config.model not in self._models:
            raise ValueError(f"unknown model: {runtime_config.model}")
        if runtime_config.environment is not None and self._store_environment_document is None:
            raise ValueError("this deployment does not allow environment documents")

    async def store_environment_document(self, body: bytes) -> str:
        """Store one environment document (JSON or YAML) content-addressed in the workspace's blob
        store and return the digest a turn pins as its environment. Refused where the deployment
        does not allow the rail — the same gate that refuses the pin at admission."""
        if self._store_environment_document is None:
            raise ValueError("this deployment does not allow environment documents")
        return await self._store_environment_document(self.blob, body)

    async def store_environment_file(self, body: bytes) -> str:
        """Store one file an environment document references, content-addressed in the
        workspace's blob store, and return the digest the document's `files` entry pins."""
        if self._store_environment_file is None:
            raise ValueError("this deployment does not allow environment documents")
        return await self._store_environment_file(self.blob, body)

    @property
    def sandbox_sizes(self) -> tuple[str, ...]:
        """The sandbox sizes this deploy's selected carrier provisions, empty for a single-shape
        backend — what decides whether a portal offers the agent's `sandbox_size` setting."""
        return self._sandbox_sizes

    async def credential(self, slot: str) -> str:
        if self._credentials is None:
            raise RuntimeError(f"surface {self.surface!r} reads a credential but holds no store")
        return await self._credentials.get(self.workspace_id, slot)

    async def put_member_credential(self, member_id: UUID, slot: str, value: str) -> None:
        """Store one member's own value for `slot`, keyed to them. The sign-in leg that collected it
        has already proven which member is speaking, so the value lands directly rather than through
        a sealed chat handoff."""
        if self._credentials is None:
            raise RuntimeError(f"surface {self.surface!r} stores a credential but holds no store")
        await self._credentials.put(self.workspace_id, member_slot(slot, member_id), value)

    async def member_credential_stored(self, member_id: UUID, slot: str) -> bool:
        """Whether this member holds their own value for `slot`. Asked per provider, so a screen can
        say which account is connected rather than only that one is."""
        if self._credentials is None:
            return False
        try:
            await self._credentials.get(self.workspace_id, member_slot(slot, member_id))
        except CredentialSlotUnset:
            return False
        return True

    async def clear_member_credential(self, member_id: UUID, slot: str) -> None:
        """Drop this member's own value for `slot`, so they can connect a different account or
        replace one that was revoked. Only their row goes — an admin's key and the workspace's own
        are not a member's to clear."""
        if self._credentials is None:
            raise RuntimeError(f"surface {self.surface!r} clears a credential but holds no store")
        await self._credentials.clear(self.workspace_id, member_slot(slot, member_id))

    async def member_holds_own_model_key(self, member_id: UUID) -> bool:
        """Whether this member signed in with a provider account of their own — the same question
        the subagent gate asks, over the same slot set, so a screen that offers to connect one and
        the capability that turns on when they do can never disagree about which slots count. The
        store is this surface's own; only the rule is shared."""
        if self._credentials is None:
            return False
        for slot in MEMBER_ROUTED_SLOTS:
            try:
                await self._credentials.get(self.workspace_id, member_slot(slot, member_id))
            except CredentialSlotUnset:
                continue
            return True
        return False

    async def credential_prompt_pending(self, sealed: str, slot: str) -> bool:
        """Whether one prompt of a sealed credential request still awaits its value."""
        if self._credentials is None:
            return False
        try:
            state = open_credential_request(
                self._credentials.fernet,
                sealed,
                purpose=CREDENTIAL_REQUEST_PURPOSE,
            )
        except CredentialRequestInvalid:
            return False
        if state.workspace_id != self.workspace_id or slot not in state.slots:
            return False
        request_id = _credential_request_id(state, sealed)
        async with workspace_tx() as connection:
            fulfilled = (
                await connection.execute(
                    sa.select(tables.credential_fulfillment.c.fulfilled_at).where(
                        tables.credential_fulfillment.c.workspace_id == self.workspace_id,
                        tables.credential_fulfillment.c.request_id == request_id,
                        tables.credential_fulfillment.c.slot == slot,
                    )
                )
            ).scalar_one_or_none()
        return fulfilled is None and not await self.blob.exists(
            _fulfilled_marker_key(request_id, slot)
        )

    async def renew_credential_request(self, sealed: str, member_id: UUID) -> str | None:
        """Renew one authenticated member's pending request for a page reload."""
        if self._credentials is None:
            return None
        try:
            state = open_credential_request(
                self._credentials.fernet,
                sealed,
                purpose=CREDENTIAL_REQUEST_PURPOSE,
                ttl=CREDENTIAL_REQUEST_RENEWAL_TTL_SECONDS,
            )
        except CredentialRequestInvalid:
            return None
        if state.workspace_id != self.workspace_id or state.member_id != member_id:
            return None
        issued_at = state.issued_at
        if issued_at is None:
            issued_at = self._credentials.fernet.extract_timestamp(sealed.encode())
        if int(datetime.now(UTC).timestamp()) - issued_at > CREDENTIAL_REQUEST_RENEWAL_TTL_SECONDS:
            return None
        async with workspace_tx() as connection:
            if not await member_is_admin(connection, self.workspace_id, member_id):
                return None
        state = state.model_copy(
            update={
                "request_id": state.request_id or _credential_request_id(state, sealed),
                "issued_at": issued_at,
            }
        )
        return seal_credential_request(self._credentials.fernet, state)

    def open_credential_authorization(self, sealed: str) -> CredentialRequestState:
        """Open a sealed credential-authorization handoff, returning its claims (workspace, member,
        slot, provider state). The Fernet's authenticity and TTL are the trust — a surface that
        completes a provider handoff at a browser callback (a Slack OAuth install) recovers the
        sealed member and slot to fulfill against, having no turn to bind them from. Raises
        `CredentialRequestInvalid` on a tampered or expired seal."""
        if self._credentials is None:
            raise RuntimeError(f"surface {self.surface!r} opens a seal but holds no store")
        return open_credential_request(
            self._credentials.fernet, sealed, purpose=CREDENTIAL_REQUEST_PURPOSE
        )

    async def fulfill_credential_request(
        self, sealed: str, slot: str, value: str, member_id: UUID | None
    ) -> None:
        """Verify the seal and store one requested slot: the fulfiller must be the member the
        request was sealed for, the slot one it named, and the seal fresh — anything else raises
        `CredentialRequestInvalid` before a byte is written. The value lands through the same
        encrypted store `ufoctl credential set` writes, and the slot's own marker stops its prompt
        from rendering again."""
        if self._credentials is None:
            raise RuntimeError(f"surface {self.surface!r} stores a credential but holds no store")
        state = open_credential_request(
            self._credentials.fernet, sealed, purpose=CREDENTIAL_REQUEST_PURPOSE
        )
        if state.workspace_id != self.workspace_id:
            raise CredentialRequestInvalid("credential request was sealed for another workspace")
        if member_id is None or member_id != state.member_id:
            raise CredentialRequestInvalid(
                "credential request can only be fulfilled by the member who asked"
            )
        if slot not in state.slots:
            raise CredentialRequestInvalid(f"credential request does not name slot {slot!r}")
        declared = next((entry for entry in self._declared_slots if entry.name == slot), None)
        if declared is None:
            raise CredentialRequestInvalid(f"credential slot {slot!r} is not declared")
        private_prompt = state.payload is None
        if private_prompt and not declared.member_filled:
            raise CredentialRequestInvalid(f"credential slot {slot!r} is never entered by a member")
        marker = (
            _fulfilled_marker_key(_credential_request_id(state, sealed), slot)
            if private_prompt
            else None
        )
        if marker is not None and await self.blob.exists(marker):
            raise CredentialRequestInvalid("credential request was already fulfilled")
        await self._credentials.fulfill(
            self.workspace_id,
            slot,
            value,
            _credential_request_id(state, sealed) if private_prompt else None,
            member_id,
            declared.merge,
        )
        if marker is not None:
            try:
                await self.blob.put(
                    marker,
                    json.dumps({"at": datetime.now(UTC).timestamp()}).encode(),
                )
            except Exception as error:
                warn(
                    "credential.fulfillment_marker_failed",
                    slot=slot,
                    error_class=type(error).__name__,
                )

    async def bind_installation(self, installation_id: str) -> None:
        """Bind this surface's external installation identity (a Slack team) to this workspace,
        replacing any prior binding for this workspace's surface. A surface completing its own OAuth
        install at a callback records the team→workspace mapping shared ingress later resolves by;
        the fleet-wide uniqueness on (surface, installation_id) rejects a team already bound to
        another workspace with `SurfaceInstallationConflict`."""
        await _bind_surface_installation(
            self.workspace_id, self.surface, installation_id, routes_ingress=True
        )

    async def address_claim(self, address: str) -> AddressClaim | None:
        """This workspace's claim on one address the surface routes by, or None when it holds
        none. A claim still carrying `claim_expires_at` is a reservation the sender has not proved
        yet; a proved one names the message that proved it."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.surface_address.c.member_id,
                        tables.surface_address.c.claim_expires_at,
                        tables.surface_address.c.proved_by,
                    ).where(
                        tables.surface_address.c.surface == self.surface,
                        tables.surface_address.c.address == address,
                        tables.surface_address.c.workspace_id == self.workspace_id,
                    )
                )
            ).one_or_none()
        if row is None:
            return None
        expires_at = row.claim_expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return AddressClaim(
            member_id=row.member_id, claim_expires_at=expires_at, proved_by=row.proved_by
        )

    async def member_surface_claim(self, surface: str, member_id: UUID) -> AddressClaim | None:
        """This workspace's claim on one address of `surface` that names `member_id` — the claim a
        member holds rather than the one an address names, so a portal can read its own member's
        reservation without knowing the address the member stated. A member may hold several rows
        of one surface, since the keyspace is (surface, address) and a reservation lapses in place:
        the strongest one answers, a proved claim ahead of a reservation and the latest reservation
        ahead of an older one. None when the member holds no claim on this surface."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.surface_address.c.member_id,
                        tables.surface_address.c.claim_expires_at,
                        tables.surface_address.c.proved_by,
                    )
                    .where(
                        tables.surface_address.c.surface == surface,
                        tables.surface_address.c.workspace_id == self.workspace_id,
                        tables.surface_address.c.member_id == member_id,
                    )
                    .order_by(
                        tables.surface_address.c.proved_by.is_(None),
                        tables.surface_address.c.claim_expires_at.desc(),
                    )
                    .limit(1)
                )
            ).one_or_none()
        if row is None:
            return None
        expires_at = row.claim_expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        return AddressClaim(
            member_id=row.member_id, claim_expires_at=expires_at, proved_by=row.proved_by
        )

    async def confirm_address(self, address: str, proved_by: str) -> None:
        """Link one address this surface routes by, against the inbound message that proved it. The
        reservation stops lapsing and the proof id is what a replayed stream recognises, so the
        proving message links the address once and is never admitted as a turn."""
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.surface_address)
                .where(
                    tables.surface_address.c.surface == self.surface,
                    tables.surface_address.c.address == address,
                    tables.surface_address.c.workspace_id == self.workspace_id,
                )
                .values(claim_expires_at=None, proved_by=proved_by, updated_at=sa.func.now())
            )

    async def release_address(self, address: str) -> None:
        """Drop one address's claim, so the phone is free to be claimed again."""
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.surface_address).where(
                    tables.surface_address.c.surface == self.surface,
                    tables.surface_address.c.address == address,
                    tables.surface_address.c.workspace_id == self.workspace_id,
                )
            )

    @property
    def public_base_url(self) -> str | None:
        """The deploy's public base (`[connect] public_base_url`), or None when unset — a
        channel's callback URL (Slack's Events request URL) renders from it."""
        return self._public_base_url

    @property
    def cookie_secure(self) -> bool:
        """Whether a surface of this deploy may mark its session cookie `Secure`. Read off the
        published base rather than the request every surface handler holds, so one deploy-wide
        fact answers every surface that binds a session."""
        return cookie_secure(urlsplit(self._public_base_url or "").scheme)

    def home_url(self, fragment: str = "") -> str | None:
        """A link into the deploy's browser portal, or None when this deploy has no public base or
        installs no browser surface — a surface that cannot answer an act itself renders one so the
        member reaches a surface that can.

        Core owns both halves, so no surface has to know another's routes: `/surface/<name>` is
        core's own mount path and `home` is the manifest flag naming the one surface a browser
        belongs on. `fragment` is the portal's own hash route, which core does not interpret."""
        if not self._public_base_url or self._home_surface is None:
            return None
        return f"{self._public_base_url.rstrip('/')}/surface/{self._home_surface}{fragment}"

    async def shared_artifacts(self, turn_id: UUID) -> tuple[SharedArtifact, ...]:
        """The files a turn shared, in share order (key-tiebroken within one timestamp) — the rows
        the writeback poller hands a durable surface's `attach`, read directly by a live surface
        that renders each as a download link (`artifact_link`) on its own stream."""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.shared_artifact.c.blob_key,
                        tables.shared_artifact.c.filename,
                        tables.shared_artifact.c.subject,
                        tables.shared_artifact.c.media_type,
                        tables.shared_artifact.c.size_bytes,
                        tables.shared_artifact.c.preview_blob_key,
                        tables.shared_artifact.c.preview_media_type,
                        tables.shared_artifact.c.preview_size_bytes,
                    )
                    .where(
                        tables.shared_artifact.c.workspace_id == self.workspace_id,
                        tables.shared_artifact.c.turn_id == turn_id,
                    )
                    .order_by(
                        tables.shared_artifact.c.created_at, tables.shared_artifact.c.blob_key
                    )
                )
            ).all()
        return tuple(
            SharedArtifact(
                blob_key=row.blob_key,
                filename=row.filename,
                subject=row.subject,
                media_type=row.media_type,
                size_bytes=row.size_bytes,
                preview_blob_key=row.preview_blob_key,
                preview_media_type=row.preview_media_type,
                preview_size_bytes=row.preview_size_bytes,
            )
            for row in rows
        )

    def artifact_link(self, artifact: SharedArtifact) -> str | None:
        """A TTL download link for a shared file the surface cannot upload inline, or None when
        artifact delivery is unconfigured (no token secret or no public base URL) — the surface then
        names the file without a link. Mints the same signed URL the web download route verifies:
        the link opens for anyone holding it until it expires, and after that only for a signed-in
        member of the workspace that shared it."""
        return shared_artifact_link(
            self._artifact_token_secret, self._public_base_url, self.workspace_id, artifact
        )

    def artifact_preview_link(self, artifact: SharedArtifact) -> str | None:
        """A signed raster-preview link, or None when its type, size, or delivery is ineligible.

        Two files reach this: one that is already an image, previewed off its own bytes, and one the
        sandbox rasterized a first page for at share time, previewed off that second blob. Either
        way the grant names a raster type and an exact size, so the route serves the bytes inline
        only after they prove to be that picture. The row's own declared type has to agree with the
        key it names, so a document row whose filename says `pdf` never grants a picture."""
        return shared_artifact_preview_link(
            self._artifact_token_secret, self._public_base_url, self.workspace_id, artifact
        )

    def ingress_url(
        self,
        conversation_id: UUID,
        port: int,
        entry_path: str,
        *,
        framed_from: str | None = None,
        shipped_slug: str | None = None,
        shipped_digest: str | None = None,
    ) -> str | None:
        """The URL that opens one conversation's sandbox port in a browser at `entry_path`, or None
        when the ingress is unconfigured (no `[sandbox] ingress_public_url`) — the surface then
        serves no site. The port gets its own signed origin, and the view token the ingress trades
        for that origin's session cookie. The origin is stable per `(conversation, port)`, so a
        bookmark and the site's stored state survive a redeploy, while the token expires, so a
        leaked URL stops opening new sessions. Mints the view token the ingress verifies — never a
        session token, the other kind — so no surface holds the deploy secret or a credential a site
        accepts.

        `entry_path` is site-rooted and rides after the token, which the ingress can still read off
        because `sign_token` emits `base64url.base64url` and neither half holds a `/`. The root
        appends nothing, so the link a site is ordinarily handed out as keeps its shape. It is
        quoted rather than trusted: this caller decodes it out of its own URL, so re-encoding is
        what round-trips a space or a literal `?` in a filename instead of splitting the URL.

        `framed_from` carries the enclosing sibling site's browser URL when this surface is itself
        framed there. Core reduces it to that signed site's address, and the ingress admits it only
        when the address belongs to this workspace, so the response names one exact extra
        `frame-ancestors` origin instead of every site the workspace has ever hosted.

        `shipped_slug`/`shipped_digest`, when both given, redirect the ingress from the
        conversation's own stored/dialed bytes to a deploy-wide precompiled app bundle in the fleet
        store under `apps/<digest>/`, of which the slug names one app's subtree. The conversation
        and port stay the page's synthetic per-workspace anchor — they scope the origin and its
        session cookie to the workspace — while the shipped claim names the row-less deploy-wide
        bytes: this is the producer half of the ingress's shipped-serving path, whose bytes are
        workspace-independent."""
        return mint_ingress_view_url(
            self._ingress_public_url,
            self.workspace_id,
            conversation_id,
            port,
            entry_path,
            framed_from=framed_from,
            shipped_slug=shipped_slug,
            shipped_digest=shipped_digest,
        )

    async def _identity_member(self, surface: str, external_id: str) -> UUID | None:
        """The member a surface's external id is linked to, or None. `linked_member` reads this
        surface's own identity; `adopt_identity` reads a peer surface's."""
        async with workspace_tx() as connection:
            linked = (
                await connection.execute(
                    sa.select(tables.surface_identity.c.member_id).where(
                        tables.surface_identity.c.workspace_id == self.workspace_id,
                        tables.surface_identity.c.surface == surface,
                        tables.surface_identity.c.external_id == external_id,
                    )
                )
            ).one_or_none()
        return None if linked is None else linked.member_id

    async def linked_member(self, external_id: str) -> UUID | None:
        return await self._identity_member(self.surface, external_id)

    async def member_has_access(self, member_id: UUID) -> bool:
        async with workspace_tx() as connection:
            return await Seats(self.workspace_id).admits(connection, MemberAuthority(member_id))

    async def is_operator_workspace(self) -> bool:
        """Whether this workspace is the fleet operator's own — the workspace whose own domain
        (its initial member's vetted email domain, the same resolution hosted onboarding joins by)
        is `OPERATOR_EMAIL_DOMAIN`. Gates renderings meant for the operator alone, like Slack's
        accounting footer and its debugger link — never a tenant-facing capability; an
        unidentified workspace is never the operator's, so internals render nowhere rather than
        in a customer's thread."""
        return await self.workspace_domain() == OPERATOR_EMAIL_DOMAIN

    async def adopt_identity(self, peer_surface: str, external_id: str) -> UUID | None:
        """Link this surface's external id to the member a peer surface already knows it by, so one
        human spans both surfaces under one member and one memory subject — web's session token,
        whose canonical home is the CLI, adopts the member the same digest already names there. A
        peer with no such identity is unknown and leaves this surface unlinked; a lost race
        collapses on the identity's primary key."""
        peer_member = await self._identity_member(peer_surface, external_id)
        if peer_member is None:
            return None
        try:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.surface_identity).values(
                        workspace_id=self.workspace_id,
                        member_id=peer_member,
                        surface=self.surface,
                        external_id=external_id,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        except sa.exc.IntegrityError:
            log("surface.identity_adopt_race", surface=self.surface, external_id=external_id)
        return peer_member

    async def link_member(self, external_id: str, email: str) -> UUID | None:
        """Link this surface's external id to the workspace member whose email matches, the first
        time they speak. No matching member leaves the external id unlinked (a shared conversation
        with no memory subject); a lost race collapses on the identity's primary key.

        Member uniqueness compares bytes, so one address can carry two rows differing only in
        casing. This answers the oldest of them rather than raising on the ambiguity, so every
        request for that address serves under one member."""
        async with workspace_tx() as connection:
            member = (
                await connection.execute(
                    sa.select(tables.member.c.id)
                    .where(
                        tables.member.c.workspace_id == self.workspace_id,
                        sa.func.lower(tables.member.c.email) == email.lower(),
                    )
                    .order_by(tables.member.c.created_at, tables.member.c.id)
                    .limit(1)
                )
            ).first()
        if member is None:
            return None
        return await self.link_member_id(external_id, member.id)

    async def link_member_id(self, external_id: str, member_id: UUID) -> UUID | None:
        """Link this surface's external id to one member after the surface proves that exact
        member requested the link. A missing member leaves the identity unlinked; a lost race
        collapses on the identity's primary key."""
        async with workspace_tx() as connection:
            member = (
                await connection.execute(
                    sa.select(tables.member.c.id).where(
                        tables.member.c.workspace_id == self.workspace_id,
                        tables.member.c.id == member_id,
                    )
                )
            ).one_or_none()
        if member is None:
            return None
        try:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.surface_identity).values(
                        workspace_id=self.workspace_id,
                        member_id=member_id,
                        surface=self.surface,
                        external_id=external_id,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        except sa.exc.IntegrityError:
            log("surface.identity_link_race", surface=self.surface, external_id=external_id)
        return member_id

    async def join_member(self, external_id: str, email: str) -> UUID | None:
        """`link_member`, plus the domain-match join: an email with no member row whose domain is
        the workspace's own — the initial member's vetted email domain — creates the member and
        links it in one step, so a teammate becomes a member on first contact and only the initial
        member onboards through provisioning. The surface asserting the email is the trust anchor:
        it calls this only with an email its channel verified. A foreign-domain email stays
        unlinked; a lost creation race collapses on the member's (workspace_id, email) uniqueness
        and links the surviving row."""
        linked = await self.link_member(external_id, email)
        if linked is not None:
            return linked
        own = await self.workspace_domain()
        domain = email_domain(email)
        if own is None or not domain or domain != own:
            return None
        async with workspace_tx() as connection:
            await create_member(connection, self.workspace_id, email.strip().lower())
        return await self.link_member(external_id, email)

    def _conversation_lookup(self, queue_key: str) -> sa.Select:
        return sa.select(
            tables.conversation.c.id,
            tables.conversation.c.member_id,
            tables.conversation.c.audience,
            tables.conversation.c.surface_label,
        ).where(
            tables.conversation.c.workspace_id == self.workspace_id,
            tables.conversation.c.surface == self.surface,
            tables.conversation.c.queue_key == queue_key,
        )

    async def find_conversation(self, queue_key: str) -> UUID | None:
        """The conversation this surface keys by `queue_key`, or None before its first turn — how a
        surface reads participation without creating a conversation as a side effect (Slack admits
        an un-mentioned reply only in a thread whose key already names a conversation)."""
        async with workspace_tx() as connection:
            found = (await connection.execute(self._conversation_lookup(queue_key))).one_or_none()
        return None if found is None else found.id

    async def conversation_agent(self, conversation_id: UUID) -> UUID | None:
        """The agent this workspace's conversation is permanently bound to, or None when the id
        names no conversation here — how a surface resolves an opaque conversation permalink to
        the agent wall before reading its content."""
        async with workspace_tx() as connection:
            found = (
                await connection.execute(
                    sa.select(tables.conversation.c.agent_id).where(
                        tables.conversation.c.workspace_id == self.workspace_id,
                        tables.conversation.c.id == conversation_id,
                    )
                )
            ).one_or_none()
        return None if found is None else found.agent_id

    async def retitle_conversation(self, conversation_id: UUID, title: str) -> None:
        """Name a conversation this surface holds — what it calls the conversation on its own rows,
        replacing the words the opening turn named it with."""
        await retitle_conversation(self.workspace_id, conversation_id, title)

    async def conversation_for(
        self,
        queue_key: str,
        audience: Audience,
        agent_id: UUID | None = None,
        conversation_id: UUID | None = None,
        label: str | None = None,
    ) -> UUID:
        """Get-or-create the conversation this surface keys by `queue_key`, outside any admission
        transaction; a lost creation race re-reads the surviving row. A caller may name the new
        conversation's id so state it keys by that id can be written before the conversation
        exists — a crash between the two leaves inert keyed state, never a conversation missing
        its state; an existing conversation keeps its own id regardless. A new conversation binds
        permanently to the surface's agent — an explicit `agent_id` when the surface's member
        picks the agent (the web portal, after its own audience check), else the surface's
        installation binding when one exists, else the workspace's main agent — and admission
        derives every turn's agent from that binding. A shared conversation is narrowed when the
        surface learns its exact member or room; an audience is never widened, and a room becoming
        externally shared seals as foreign.

        `label` is what a member calls this conversation's origin — the channel a Slack thread runs
        in — in the surface's own grammar, which core stores and renders but never reads. It is
        rewritten whenever the surface names a different one, so a renamed origin corrects itself
        on the next message; a surface that does not know one passes None and leaves the stored
        label standing."""
        audience = parse_audience(audience)
        member_id = audience_member(audience)
        async with workspace_tx() as connection:
            found = (await connection.execute(self._conversation_lookup(queue_key))).one_or_none()
        if found is not None:
            narrowed = narrow_audience(parse_audience(found.audience), audience)
            if narrowed != found.audience:
                async with workspace_tx() as connection:
                    await connection.execute(
                        sa.update(tables.conversation)
                        .where(
                            tables.conversation.c.id == found.id,
                            tables.conversation.c.audience == found.audience,
                        )
                        .values(
                            member_id=audience_member(narrowed),
                            audience=str(narrowed),
                            updated_at=sa.func.now(),
                        )
                    )
                return await self.conversation_for(queue_key, audience, label=label)
            if label is not None and label != found.surface_label:
                async with workspace_tx() as connection:
                    await connection.execute(
                        sa.update(tables.conversation)
                        .where(tables.conversation.c.id == found.id)
                        .values(surface_label=label, updated_at=sa.func.now())
                    )
            return found.id
        conversation_id = conversation_id or uuid4()
        if agent_id is None:
            agent_id = await self._surface_agent()
        else:
            async with workspace_tx() as connection:
                known = (
                    await connection.execute(
                        sa.select(tables.agent.c.id).where(
                            tables.agent.c.workspace_id == self.workspace_id,
                            tables.agent.c.id == agent_id,
                        )
                    )
                ).one_or_none()
            if known is None:
                raise ValueError(f"agent {agent_id} is not an agent of this workspace")
        try:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.conversation).values(
                        id=conversation_id,
                        workspace_id=self.workspace_id,
                        agent_id=agent_id,
                        surface=self.surface,
                        queue_key=queue_key,
                        surface_label=label,
                        member_id=member_id,
                        audience=str(audience),
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        except sa.exc.IntegrityError:
            log("surface.conversation_create_lost_race", surface=self.surface, queue_key=queue_key)
            async with workspace_tx() as connection:
                found = (
                    await connection.execute(self._conversation_lookup(queue_key))
                ).one_or_none()
            if found is None:
                raise
            return await self.conversation_for(queue_key, audience, label=label)
        return conversation_id

    async def _surface_agent(self) -> UUID:
        async with workspace_tx() as connection:
            bound = (
                await connection.execute(
                    sa.select(tables.surface_installation.c.agent_id).where(
                        tables.surface_installation.c.workspace_id == self.workspace_id,
                        tables.surface_installation.c.surface == self.surface,
                    )
                )
            ).one_or_none()
        if bound is not None:
            return bound.agent_id
        return await _main_agent(self.workspace_id)

    async def ambient_reply_wanted(
        self, message: AmbientMessage, history: tuple[AmbientMessage, ...]
    ) -> bool:
        """Whether one un-addressed message in a thread the agent converses in earns a turn — the
        decision a durable surface makes before it admits ambient traffic, on the deploy's ambient
        reply model with the thread's recent messages as its evidence.

        Fails open, bounded by `AMBIENT_REPLY_TIMEOUT_SECONDS`: a provider that errors, stalls, or
        answers something unreadable admits the turn. The two outcomes are not symmetric — an
        unwanted reply costs one line, while a decision this seam gets wrong in the other direction
        drops a member's request with nothing to show them — so an actual classifier failure takes
        the expensive outcome. A spend refusal is a policy decision instead: retry on the surface
        agent's model, which may run on the workspace's own provider key; if that model is refused
        too, a park reaches admission and a reject stays silent. Only a decision this seam actually
        read holds a message back."""
        classifier = self._ambient_reply
        retried = False
        while True:
            try:
                decision = await asyncio.wait_for(
                    classifier.decide(message, history), AMBIENT_REPLY_TIMEOUT_SECONDS
                )
            except OffTurnSpendRefused as refusal:
                if not retried and self._ambient_reply_for is not None:
                    retried = True
                    try:
                        fallback = self._ambient_reply_for(await self._surface_agent_model())
                    except Exception as error:
                        warn(
                            "surface.ambient_reply_undecided",
                            surface=self.surface,
                            model=classifier.model.model,
                            error=repr(error),
                        )
                        return True
                    if fallback.model.model != classifier.model.model:
                        classifier = fallback
                        continue
                log(
                    "surface.ambient_reply_spend_refused",
                    surface=self.surface,
                    model=classifier.model.model,
                    outcome=refusal.outcome,
                )
                return refusal.outcome == PARK
            except Exception as error:
                warn(
                    "surface.ambient_reply_undecided",
                    surface=self.surface,
                    model=classifier.model.model,
                    error=repr(error),
                )
                return True
            log(
                "surface.ambient_reply",
                surface=self.surface,
                model=classifier.model.model,
                decision=decision,
                history=len(history),
            )
            return decision != NO_REPLY

    async def _surface_agent_model(self) -> str:
        agent_id = await self._surface_agent()
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(tables.agent.c.model).where(
                        tables.agent.c.id == agent_id,
                        tables.agent.c.workspace_id == self.workspace_id,
                    )
                )
            ).scalar_one()

    async def admit(
        self,
        conversation_id: UUID,
        body: str,
        idempotency_key: str | None = None,
        context: TurnContext | None = None,
        *,
        speaker_member_id: UUID | None,
        intent: ToolIntent | None = None,
        comment: str | None = None,
        runtime_config: TurnRuntimeConfig | None = None,
    ) -> Admitted:
        """Admit an inbound message onto the durable turn queue and return its turn, with whether
        this delivery opened that turn's run. The turn executes as the conversation's bound agent —
        a surface never names one. Delivery is admission's concern, derived from the conversation's
        surface: a durable-surface turn registers for the poller atomically with its row, a live
        surface's turn registers nothing and its member tails the hub — the surface supplies only
        the message, its idempotency key, and the ambient `TurnContext` (sender, timezone, and the
        source — where the member said it, in whatever form this surface can name) the engine
        renders before the inbound. A redelivery deduped to the turn already admitted joins it, as
        does a follow-up folded into a live one. A prepared `intent` (a panel's form submit) admits
        a turn that dispatches that one tool call verbatim instead of running model rounds — it
        requires the speaking member, its admission never folds into a live turn (each submit
        founds its own queued turn on the member's durable intent conversation with the agent, and
        the per-conversation partition runs them in order), and its `body` must equal the
        envelope's serialization, which is the turn's inbound."""
        return await self._admitter.admit(
            conversation_id,
            body,
            idempotency_key=idempotency_key,
            context=context,
            speaker_member_id=speaker_member_id,
            intent=intent,
            comment=comment,
            runtime_config=runtime_config,
        )

    async def connect_url(self, turn_id: UUID, member_id: UUID) -> str:
        """Open a terminal connect request as its speaking member."""
        try:
            flow = installed_connect_flow()
        except ConnectUnavailable as error:
            raise ConnectRequestInvalid("connect flow is unavailable") from error
        return await ConnectHandoff(flow).authorize(self.workspace_id, turn_id, member_id)

    async def held_accounts(self, owner_member_id: UUID) -> dict[str, str]:
        """What this member's own connections are held under, keyed by provider: the account a
        settled connect control names. Which request settled is the turn's own stamp, so this read
        only supplies the name — the newest account where the member holds more than one on a
        provider, which is the one their last connect made."""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.connection.c.provider,
                        tables.connection.c.account_label,
                        tables.connection.c.account_id,
                    )
                    .where(
                        tables.connection.c.workspace_id == self.workspace_id,
                        tables.connection.c.owner_member_id == owner_member_id,
                    )
                    .order_by(tables.connection.c.updated_at)
                )
            ).all()
        return {row.provider: row.account_label or row.account_id for row in rows}

    def connect_available(self) -> bool:
        """Whether this deploy holds the connect machinery at all — no credential key means no flow,
        and a surface that drew a connect act anyway would offer a press with nowhere to go."""
        try:
            installed_connect_flow()
        except ConnectUnavailable:
            return False
        return True

    def connect_label(self, provider: str) -> str:
        """The member-facing name of a connect provider, as the connect flow declares it."""
        return installed_connect_flow().label_for(provider)

    async def connector_catalog(self, query: str, limit: int, after: str | None) -> CatalogPage:
        """Read one page of the deploy's connectable provider catalog."""
        return await self._connectors.catalog(query, limit, after)

    async def admitted_body(self, idempotency_key: str) -> str | None:
        """The message body an idempotency key admitted — the founding inbound of the turn the key
        opened, or the queue row it landed as — or None when the key admitted nothing. How a
        surface whose answer affordance raced (an idempotent admit joins whatever the first click
        won) confirms which answer landed: only the click whose body was stored may rewrite the
        affordance into its answer."""
        async with workspace_tx() as connection:
            turn_row = (
                await connection.execute(
                    sa.select(tables.turn.c.inbound).where(
                        tables.turn.c.workspace_id == self.workspace_id,
                        tables.turn.c.idempotency_key == idempotency_key,
                    )
                )
            ).one_or_none()
            if turn_row is not None:
                return turn_row.inbound
            message_row = (
                await connection.execute(
                    sa.select(tables.inbound_message.c.body).where(
                        tables.inbound_message.c.workspace_id == self.workspace_id,
                        tables.inbound_message.c.idempotency_key == idempotency_key,
                    )
                )
            ).one_or_none()
        return None if message_row is None else message_row.body

    async def turn_owner(self, turn_id: UUID) -> UUID | None:
        """The member whose conversation owns a turn, or None when no such turn exists — the check a
        live surface gates its per-turn tail on, so a member cannot tail another member's turn."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.conversation.c.member_id)
                    .select_from(tables.turn.join(tables.conversation))
                    .where(
                        tables.turn.c.id == turn_id,
                        tables.turn.c.workspace_id == self.workspace_id,
                    )
                )
            ).one_or_none()
        return None if row is None else row.member_id

    async def stop_turn(self, conversation_id: UUID, turn_id: UUID) -> Stopped:
        """End a running turn of a conversation this surface already authorized for the acting
        member — the surface's identity assertion is the gate, exactly as it is for `admit` and
        `tail`. Idempotent: `ended` iff this call ended the turn, False for one already terminal.
        `founded_turn_id` names the run the stop opened on a follow-up the member had already
        sent, so the surface can move the member's screen onto it."""
        return await self._stopper.stop(self.workspace_id, conversation_id, turn_id)

    async def retract_arrival(
        self, conversation_id: UUID, arrival_id: UUID, member_id: UUID
    ) -> bool:
        """Take back a message the member sent that no turn has taken up: the pending
        `inbound_message` row is deleted iff it is still unconsumed and `member_id` spoke it — a
        member unqueues only their own words. True iff this call retracted it; False for a row
        already folded into a turn, already retracted, or another speaker's — the message has left
        the member's hands either way."""
        async with workspace_tx() as connection:
            retracted = await connection.execute(
                sa.delete(tables.inbound_message).where(
                    tables.inbound_message.c.id == arrival_id,
                    tables.inbound_message.c.workspace_id == self.workspace_id,
                    tables.inbound_message.c.conversation_id == conversation_id,
                    tables.inbound_message.c.speaker_member_id == member_id,
                    tables.inbound_message.c.consumed_turn_id.is_(None),
                )
            )
        return retracted.rowcount == 1

    async def turn_is_terminal(self, turn_id: UUID) -> bool:
        """Whether a turn has committed its terminal state, read from the row rather than the hub.

        A side-channel writer that posts on a timer needs this: the tail learns a turn ended by
        polling, so between the commit and the frame there is a window in which the poller has
        already delivered the turn's reply. A checkpoint firing inside that window would post an
        update *after* the answer it was reporting progress towards, which the member sees in their
        thread. A missing turn reads as terminal — there is nothing left to report on."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.status).where(
                        tables.turn.c.id == turn_id,
                        tables.turn.c.workspace_id == self.workspace_id,
                    )
                )
            ).one_or_none()
        return row is None or row.status in TERMINAL_TURN_STATUSES

    async def latest_turn(self, conversation_id: UUID) -> UUID | None:
        """The most recent turn admitted to a conversation, or None when it holds none — the
        turn a live surface resumes tailing when a held stream reconnects to drain an answer that
        outran the hold, and the turn whose open handoffs (an unanswered question, pending
        credential prompts, shared files) a reload re-renders."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.id)
                    .where(
                        tables.turn.c.workspace_id == self.workspace_id,
                        tables.turn.c.conversation_id == conversation_id,
                    )
                    .order_by(tables.turn.c.seq.desc())
                    .limit(1)
                )
            ).one_or_none()
        return None if row is None else row.id

    async def absorbing_turn(self, conversation_id: UUID) -> UUID | None:
        """The turn a message admitted to this conversation now would fold into, or None when it
        would found a turn of its own instead — what a surface asks before running a gate that
        governs only the founding of a turn.

        Admission takes the same decision again under the conversation lock, so this is a read of
        the moment and never an authority. It answers the founding cases it must not miss: the
        conversation's oldest live turn is the one a fold lands on, a parked turn absorbs nothing
        because it is held rather than running, and the fold carries both gates the arrival is
        admitted under — the caps and the prepaid balance — so a spent balance or a breached cap
        answers None here rather than promising a fold admission then refuses.

        The balance is read with the same model resolution admission uses, so a workspace serving
        its turns with its own provider key is answered the same way in both places. Without it this
        read hides a live turn admission does fold into, and a surface that takes None as "no live
        turn" — Slack hands the reply to its ambient classifier — can drop the member's message in
        silence."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.id,
                        tables.turn.c.status,
                        tables.turn.c.agent_id,
                        tables.conversation.c.member_id,
                    )
                    .select_from(tables.turn.join(tables.conversation))
                    .where(
                        tables.turn.c.workspace_id == self.workspace_id,
                        tables.turn.c.conversation_id == conversation_id,
                        tables.turn.c.status.in_(NON_TERMINAL_STATUSES),
                    )
                    .order_by(tables.turn.c.seq)
                    .limit(1)
                )
            ).one_or_none()
            if row is None or row.status == PARKED:
                return None
            decision = await SpendEvaluator(self.workspace_id, row.member_id, row.agent_id).decide(
                connection, 0
            )
            balance = await BalanceGate(self.workspace_id).admits(
                connection, row.agent_id, self._key_slot_for
            )
        return row.id if ALLOW == decision.outcome == balance.outcome else None

    def tail(
        self, turn_id: UUID, since: str = ""
    ) -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]:
        """Tail a turn's live frames off the hub until it ends — a live surface streams these to the
        member's held connection (SSE), reaching the hub only through the injected tailer. Leaving
        the scope ends the subscription and the tasks behind it, however the block ends."""
        return self._tailer.tail(turn_id, since)

    async def latest_activity(self, turn_id: UUID) -> Activity | None:
        """The newest activity frame the hub retains for a turn — what a running turn is doing
        right now — or None when it retains none. A peek through the same injected tailer `tail`
        rides, holding no subscription."""
        return await self._tailer.latest_activity(turn_id)

    async def spend_rollup(self, window_seconds: int | None) -> SpendReport:
        """The workspace usage report for a selected range or all time."""
        async with workspace_tx() as connection:
            return await SpendRollup(self.workspace_id).read(connection, window_seconds)

    async def write_workspace_file(
        self, conversation_id: UUID, rel: str, chunks: AsyncIterator[bytes]
    ) -> None:
        """Land a file in the conversation's `/workspace` before its turn runs, so the agent's file
        tools find it already present. The workspace lives in the sandbox, so the write goes through
        the carrier, which takes the whole body at once — bounded while it accumulates, so a stream
        with no cap of its own is refused at the limit instead of first sitting whole in memory."""
        body = bytearray()
        async for chunk in chunks:
            body += chunk
            if len(body) > WORKSPACE_WRITE_MAX_BYTES:
                raise ValueError(
                    f"{rel} exceeds the {WORKSPACE_WRITE_MAX_BYTES}-byte limit for a "
                    "workspace write"
                )
        await self._sandboxes.write(conversation_id, rel, bytes(body))

    async def render_preview(self, kind: str, data: bytes) -> bytes | None:
        """Render a document `data` of `kind` to a preview PNG through the preview service, so a
        surface can show a member the file they are about to send. The bytes go to the service's
        `inline` sink and the PNG comes straight back — nothing is stored and this process never
        rasterizes. Returns None when the service is unconfigured or refuses the file, so a surface
        shows a named card rather than failing the compose."""
        if self._preview_url is None or self._preview_token is None:
            return None
        request = json.dumps(
            {
                "kind": kind,
                "max_width": PREVIEW_THUMBNAIL_MAX_WIDTH,
                "max_height": PREVIEW_THUMBNAIL_MAX_HEIGHT,
                "pages": 1,
                "sink": {"inline": True},
            }
        )
        try:
            async with httpx.AsyncClient(timeout=PREVIEW_RENDER_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{self._preview_url}/render",
                    headers={"Authorization": f"Bearer {self._preview_token}"},
                    files={"request": (None, request), "file": ("upload", data)},
                )
        except httpx.HTTPError:
            return None
        if response.status_code != 200 or response.headers.get("content-type") != "image/png":
            return None
        return response.content

    async def list_agents(self) -> tuple[AgentSummary, ...]:
        """Every agent of this workspace, main first then by name — the read a surface whose
        member picks an agent filters through its own audience authority before showing."""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.id,
                        tables.agent.c.name,
                        tables.agent.c.is_main,
                        tables.agent.c.model,
                        tables.agent.c.internet_access_allowed,
                        tables.agent.c.visibility,
                        tables.agent.c.icon,
                        tables.agent.c.purpose,
                        tables.agent.c.owner_member_id,
                        tables.agent.c.provisioned_by,
                    )
                    .where(
                        tables.agent.c.workspace_id == self.workspace_id,
                        tables.agent.c.archived_at.is_(None),
                    )
                    .order_by(tables.agent.c.is_main.desc(), tables.agent.c.name)
                )
            ).all()
        return tuple(
            AgentSummary(
                id=row.id,
                name=row.name,
                main=row.is_main,
                model=row.model,
                internet_access_allowed=row.internet_access_allowed,
                visibility=row.visibility,
                icon=row.icon,
                purpose=row.purpose,
                owner_member_id=row.owner_member_id,
                provisioned_by=row.provisioned_by,
            )
            for row in rows
        )

    async def list_archived_agents(self) -> tuple[ArchivedAgent, ...]:
        """The archived apps of this workspace, most recently archived first."""
        member_name = sa.func.coalesce(tables.agent.c.archived_name, tables.agent.c.name)
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.id,
                        member_name.label("name"),
                        tables.agent.c.name.label("object_name"),
                        tables.agent.c.icon,
                        tables.agent.c.archived_at,
                        tables.agent.c.owner_member_id,
                    )
                    .where(
                        tables.agent.c.workspace_id == self.workspace_id,
                        tables.agent.c.archived_at.is_not(None),
                    )
                    .order_by(tables.agent.c.archived_at.desc(), member_name)
                )
            ).all()
        return tuple(
            ArchivedAgent(
                id=row.id,
                name=row.name,
                object_name=row.object_name,
                icon=row.icon,
                archived_at=row.archived_at,
                owner_member_id=row.owner_member_id,
            )
            for row in rows
        )

    async def member_extension_agent_ids(self, member_id: UUID) -> frozenset[UUID]:
        """Agents with a private extension conversation bound to this member."""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.conversation.c.agent_id)
                    .where(
                        tables.conversation.c.workspace_id == self.workspace_id,
                        tables.conversation.c.member_id == member_id,
                        tables.conversation.c.audience == str(conversation_audience(member_id)),
                        tables.conversation.c.surface.startswith("extension:"),
                    )
                    .distinct()
                )
            ).scalars()
        return frozenset(rows)

    async def agent_detail(self, agent_id: UUID, member_id: UUID) -> AgentDetail | None:
        """One agent's configuration for a portal settings read — the row beside its prompt digest
        and bound surfaces, or None when no such agent exists in this workspace. The surface's own
        audience authority gates who may read it, exactly as `list_agents`.

        The outstanding setup is read for `member_id`: an account granted privately by somebody
        else is one this member's turns cannot use, so it settles nothing for them."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.name,
                        tables.agent.c.is_main,
                        tables.agent.c.model,
                        tables.agent.c.internet_access_allowed,
                        tables.agent.c.use_workspace_skills,
                        tables.agent.c.reasoning,
                        tables.agent.c.sandbox_size,
                        tables.agent.c.visibility,
                        tables.agent.c.icon,
                        tables.agent.c.prompt,
                        tables.agent.c.updated_at,
                    ).where(
                        tables.agent.c.workspace_id == self.workspace_id,
                        tables.agent.c.id == agent_id,
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            surfaces = (
                (
                    await connection.execute(
                        sa.select(tables.surface_installation.c.surface)
                        .where(
                            tables.surface_installation.c.workspace_id == self.workspace_id,
                            tables.surface_installation.c.agent_id == agent_id,
                        )
                        .order_by(tables.surface_installation.c.surface)
                    )
                )
                .scalars()
                .all()
            )
        return AgentDetail(
            name=row.name,
            main=row.is_main,
            model=row.model,
            internet_access_allowed=row.internet_access_allowed,
            use_workspace_skills=row.use_workspace_skills,
            reasoning=row.reasoning,
            sandbox_size=row.sandbox_size,
            visibility=row.visibility,
            icon=row.icon,
            prompt=row.prompt,
            prompt_digest=prompt_digest(row.prompt),
            surfaces=tuple(surfaces),
            setup=dict(
                (agent, missing) for agent, _name, missing in await pending_setup(member_id)
            ).get(agent_id),
            updated_at=row.updated_at,
        )

    def object_kind(self, kind: str) -> "PortalKind | None":
        """What the portal's object pages render around one kind's rows, or None when this deploy
        registers no such kind: its declared filter and order vocabulary, and the spec schema a
        form renders its fields from, the same schema `object_explain` reports. The kind's own
        description and guidance are the agent's tool prose and stay out of the portal."""
        bound = self._objects.get(kind)
        if bound is None:
            return None
        return PortalKind(
            kind=kind,
            list_fields=tuple(sorted(bound.kind.list_fields)),
            spec_schema=self._object_schemas.get(kind),
        )

    def object_actions(
        self,
        kind: str,
        binding: "ActionBinding",
        *,
        name: str | None = None,
        generation: UUID | None = None,
    ) -> tuple[ActionView, ...]:
        """The controls the portal draws for one target of `kind`: the presented actions of one
        binding, built from the declarations and pre-bound to the name the route holds. No member
        read gates them — a target the portal offers no row for (an archived app, the workspace,
        another member's private conversation) still projects its acts, and dispatch's recheck and
        the handler decide; `presented_action_views` states the rest of the rule."""
        return presented_action_views(
            self._actions, kind, binding, name=name, generation=generation
        )

    def frame_admits(self, callable_id: str) -> bool:
        """Whether an embedded app page may post this callable — a global by its tool name, a bound
        action by its canonical id — as its declaration says with `frame`."""
        return callable_id in self._frame_admissible

    async def agent_skills(self, agent_id: UUID) -> tuple[PortalSkill, ...]:
        """The loadable skills of this workspace — the composition a turn of an agent that uses them
        loads (the deploy registry with the workspace's saved skills as its member tier, deploy
        winning on a name collision): top-level deploy skills in registration order,
        member-authored ones appended last. The member tier arrives whole from one provider read
        (`materialize_all`) — a corrupt row is the provider's to skip with a log, and a saved name a
        deploy skill shadows drops here exactly as `with_member` refuses it on a turn. The read
        binds an agent because a provider runs inside an agent boundary, not because the set is
        that agent's."""
        with bind_agent(agent_id):
            saved = await self._member_skill_listing()
        member_skills = []
        for skill in saved:
            if skill.name in self._skills.by_name:
                log("skill.member_shadow_refused", skill=skill.name)
                continue
            member_skills.append(skill)
        return (
            *(
                PortalSkill(
                    name=skill.name,
                    description=skill.description,
                    origin="deploy",
                    instructions=skill.instructions,
                )
                for skill in self._skills.by_name.values()
                if skill.parent is None
            ),
            *(
                PortalSkill(
                    name=skill.name,
                    description=skill.description,
                    origin="member",
                    instructions=skill.instructions,
                    depends=skill.depends,
                    agents=skill.agents,
                )
                for skill in member_skills
            ),
        )

    @property
    def model(self) -> "SurfaceModel | None":
        """The metered model this route may call, or None where the deploy wired none. A route that
        needs it gates on it and answers without it when absent, the way `memory_available` gates
        the memory view — a deploy missing a model must still serve every page it can."""
        return self._model

    @property
    def memory_available(self) -> bool:
        """Whether this deploy resolved a memory-search provider — the surface's render gate for
        its memory view, answered the same on every path so a provider-less deploy never claims
        otherwise."""
        return self._memory is not None

    async def search_memory(
        self, reader: "SourceReader", queries: tuple[str, ...]
    ) -> "tuple[MemoryMatch, ...]":
        """Search the memory a reader may see: the selected agent, the requesting member, and
        exactly the readable subjects — the same reader shape a turn's tools search under, so
        source-gated pages answer the portal and the agent identically. Gates on
        `memory_available` first: searching a deploy that resolved no provider is a programming
        error, not an empty result."""
        if self._memory is None:
            raise RuntimeError("no memory-search provider is installed — gate on memory_available")
        return await self._memory.search(reader, queries)

    async def recent_memory(
        self,
        subjects: frozenset[str],
        limit: int,
        kinds: "frozenset[str] | None" = None,
        cursor: "ListingCursor | None" = None,
    ) -> "ListingPage[MemoryMatch]":
        """One page of the live memory items the subjects may read, newest first — the browse half
        of the memory seam, for the portal's listing: no query, no similarity, source pages stay
        search-only. Keyset-paged through `cursor` and narrowable to item classes through `kinds`.
        Gates on `memory_available` like `search_memory`."""
        if self._memory is None:
            raise RuntimeError("no memory-search provider is installed — gate on memory_available")
        return await self._memory.list_recent(subjects, limit, kinds, cursor)

    @property
    def memory_kinds(self) -> tuple[str, ...]:
        """The item classes the installed provider writes — the closed set the portal's filter
        offers, so a class the provider adds cannot go missing from it."""
        if self._memory is None:
            raise RuntimeError("no memory-search provider is installed — gate on memory_available")
        return self._memory.listable_kinds()

    async def agent_spend(self, agent_id: UUID, window_seconds: int | None) -> AgentSpendReport:
        """One agent's usage for a selected range or all time, plus its caps."""
        async with workspace_tx() as connection:
            return await SpendRollup(workspace_id=self.workspace_id).read_agent(
                connection, agent_id, window_seconds
            )

    async def member_spend(self, member_id: UUID, window_seconds: int | None) -> MemberSpendReport:
        """One member's usage for a selected range or all time, plus their caps."""
        async with workspace_tx() as connection:
            return await SpendRollup(workspace_id=self.workspace_id).read_member(
                connection, member_id, window_seconds
            )

    async def list_agent_connections(
        self, agent_id: UUID, member_id: UUID, *, admin: bool
    ) -> tuple[ConnectionView, ...]:
        """The connector accounts granted to one agent that this member may see — the member gate
        in the query, never the caller: an admin sees every edge, everyone else their own private
        grants plus workspace-shared ones (#645's resolution rule, read-side). The wall stays the
        query's `agent_id`; another agent's edges are simply absent. A shared connection names its
        owner to every member who can use it — sharing is the disclosure; a private connection
        names its owner only to the owner or an admin."""
        query = (
            sa.select(
                tables.connection.c.provider,
                tables.connection.c.account_id,
                tables.connection.c.account_label,
                tables.member.c.email,
                tables.connection.c.owner_member_id,
                tables.connection.c.shared,
                tables.connector_grant.c.created_at,
            )
            .select_from(
                tables.connector_grant.join(
                    tables.connection,
                    tables.connector_grant.c.connection_id == tables.connection.c.id,
                ).join(tables.member, tables.connection.c.owner_member_id == tables.member.c.id)
            )
            .where(
                tables.connector_grant.c.workspace_id == self.workspace_id,
                tables.connector_grant.c.agent_id == agent_id,
            )
            .order_by(tables.connection.c.provider, tables.connection.c.account_id)
        )
        if not admin:
            query = query.where(
                sa.or_(
                    tables.connection.c.shared,
                    tables.connection.c.owner_member_id == member_id,
                )
            )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        return tuple(
            ConnectionView(
                provider=row.provider,
                account_id=row.account_id,
                grant=account_object_name(row.provider, row.account_id),
                account_label=row.account_label,
                owner_email=row.email,
                own=admin or row.owner_member_id == member_id,
                shared=row.shared,
                connected_at=row.created_at,
            )
            for row in rows
        )

    async def list_connections(
        self, member_id: UUID, *, admin: bool
    ) -> tuple[ConnectionPoolView, ...]:
        """This workspace's connections with the live apps each is attached to. An archived holder
        is not one: the panel's Revoke posts a detach on every holder it lists, and an archived app
        refuses the turn that would carry it, which stops the revoke before the live holders after
        it. The filter rides the join, so a connection whose only holder is archived still lists —
        held by nobody until a restore."""
        member_name = sa.func.coalesce(tables.agent.c.archived_name, tables.agent.c.name)
        query = (
            sa.select(
                tables.connection.c.provider,
                tables.connection.c.account_id,
                tables.connection.c.account_label,
                tables.member.c.email,
                tables.connection.c.owner_member_id,
                tables.connection.c.shared,
                tables.connection.c.created_at,
                tables.agent.c.id.label("agent_id"),
                member_name.label("agent_name"),
            )
            .select_from(
                tables.connection.join(
                    tables.member, tables.connection.c.owner_member_id == tables.member.c.id
                )
                .outerjoin(
                    tables.connector_grant,
                    tables.connector_grant.c.connection_id == tables.connection.c.id,
                )
                .outerjoin(
                    tables.agent,
                    sa.and_(
                        tables.connector_grant.c.agent_id == tables.agent.c.id,
                        tables.agent.c.archived_at.is_(None),
                    ),
                )
            )
            .where(tables.connection.c.workspace_id == self.workspace_id)
            .order_by(
                tables.connection.c.provider,
                tables.connection.c.account_id,
                member_name,
            )
        )
        if not admin:
            query = query.where(
                sa.or_(
                    tables.connection.c.shared,
                    tables.connection.c.owner_member_id == member_id,
                )
            )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        grouped: dict[tuple[str, str], list[AttachedAgentView]] = {}
        records: dict[tuple[str, str], ConnectionPoolView] = {}
        for row in rows:
            key = (row.provider, row.account_id)
            records.setdefault(
                key,
                ConnectionPoolView(
                    provider=row.provider,
                    account_id=row.account_id,
                    grant=account_object_name(row.provider, row.account_id),
                    account_label=row.account_label,
                    owner_email=(
                        row.email
                        if admin or row.shared or row.owner_member_id == member_id
                        else None
                    ),
                    own=admin or row.owner_member_id == member_id,
                    shared=row.shared,
                    connected_at=row.created_at,
                    agents=(),
                ),
            )
            if row.agent_id is not None:
                grouped.setdefault(key, []).append(
                    AttachedAgentView(id=row.agent_id, name=row.agent_name)
                )
        return tuple(
            records[key].model_copy(update={"agents": tuple(grouped.get(key, ()))})
            for key in records
        )

    async def github_coverage(self, member_id: UUID, *, admin: bool) -> GithubCoverageView:
        connection_visibility = (
            sa.true()
            if admin
            else sa.or_(
                tables.connection.c.shared,
                tables.connection.c.owner_member_id == member_id,
            )
        )
        source_visibility = (
            sa.true()
            if admin
            else sa.or_(
                tables.source.c.subject == SHARED_SUBJECT,
                tables.source.c.owner_member_id == member_id,
            )
        )
        async with workspace_tx() as connection:
            api = await connection.scalar(
                sa.select(
                    sa.exists().where(
                        tables.connection.c.workspace_id == self.workspace_id,
                        tables.connection.c.provider == "github",
                        connection_visibility,
                    )
                )
            )
            sources = await connection.scalar(
                sa.select(
                    sa.exists().where(
                        tables.source.c.workspace_id == self.workspace_id,
                        tables.source.c.backend == "github",
                        tables.source.c.removed_at.is_(None),
                        source_visibility,
                    )
                )
            )
            slots = await connection.scalars(
                sa.select(tables.credential.c.slot).where(
                    tables.credential.c.workspace_id == self.workspace_id,
                    tables.credential.c.slot.in_(("github_app_installation", "github_git_token")),
                )
            )
        filled_slots = set(slots.all())
        return GithubCoverageView(
            api=bool(api),
            git_push=bool({"github_app_installation", "github_git_token"} & filled_slots),
            sources=bool(sources),
        )

    async def recent_object_changes(self, limit: int) -> tuple[ObjectChange, ...]:
        """The workspace's most recent object-change journal rows, newest first — the admin audit
        read of every create/update/delete the object verbs recorded. Reads the core journal
        directly; the surface that calls it admin-gates the read, since the journal is the
        operator's record rather than a member surface."""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.object_change.c.kind,
                        tables.object_change.c.name,
                        tables.object_change.c.verb,
                        tables.object_change.c.caller,
                        tables.object_change.c.agent_id,
                        tables.object_change.c.spec_before,
                        tables.object_change.c.spec_after,
                        tables.object_change.c.created_at,
                    )
                    .where(tables.object_change.c.workspace_id == self.workspace_id)
                    .order_by(tables.object_change.c.created_at.desc())
                    .limit(limit)
                )
            ).all()
        return tuple(
            ObjectChange(
                kind=row.kind,
                name=row.name,
                verb=row.verb,
                caller=row.caller,
                agent_id=row.agent_id,
                spec_before=row.spec_before,
                spec_after=row.spec_after,
                created_at=(
                    row.created_at
                    if row.created_at.tzinfo is not None
                    else row.created_at.replace(tzinfo=UTC)
                ),
            )
            for row in rows
        )

    async def list_conversation_artifacts(
        self, conversation_id: UUID, *, limit: int
    ) -> tuple[ListedArtifact, ...]:
        """A bounded newest-first projection of the durable files shared by one conversation.
        Authorization remains the calling surface's responsibility; this seam fixes the workspace
        and conversation predicates and never widens to another conversation's rows."""
        query = (
            sa.select(
                tables.shared_artifact.c.id,
                tables.shared_artifact.c.blob_key,
                tables.shared_artifact.c.filename,
                tables.shared_artifact.c.subject,
                tables.shared_artifact.c.media_type,
                tables.shared_artifact.c.size_bytes,
                tables.shared_artifact.c.preview_blob_key,
                tables.shared_artifact.c.preview_media_type,
                tables.shared_artifact.c.preview_size_bytes,
                tables.shared_artifact.c.created_at,
                tables.shared_artifact.c.turn_id,
                tables.member.c.email,
                tables.conversation.c.surface,
            )
            .select_from(
                tables.shared_artifact.join(
                    tables.turn, tables.shared_artifact.c.turn_id == tables.turn.c.id
                )
                .join(
                    tables.conversation,
                    tables.turn.c.conversation_id == tables.conversation.c.id,
                )
                .outerjoin(tables.member, tables.conversation.c.member_id == tables.member.c.id)
            )
            .where(
                tables.shared_artifact.c.workspace_id == self.workspace_id,
                tables.turn.c.workspace_id == self.workspace_id,
                tables.turn.c.conversation_id == conversation_id,
            )
            .order_by(
                tables.shared_artifact.c.created_at.desc(),
                tables.shared_artifact.c.id.desc(),
            )
            .limit(limit)
        )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        found = await ConversationDirectory(self.workspace_id).sources((conversation_id,))
        source = found.get(conversation_id)
        return tuple(
            ListedArtifact(
                id=row.id,
                artifact=SharedArtifact(
                    blob_key=row.blob_key,
                    filename=row.filename,
                    subject=row.subject,
                    media_type=row.media_type,
                    size_bytes=row.size_bytes,
                    preview_blob_key=row.preview_blob_key,
                    preview_media_type=row.preview_media_type,
                    preview_size_bytes=row.preview_size_bytes,
                ),
                created_at=row.created_at,
                owner_email=row.email,
                origin=None,
                turn_id=row.turn_id,
                conversation_id=conversation_id,
                surface=row.surface,
                source=source,
            )
            for row in rows
        )

    async def agent_turn_statuses(
        self, agent_ids: Sequence[UUID], member_id: UUID
    ) -> tuple[AgentTurnStatus, ...]:
        """Each named agent's turn aggregate, one row per id in the given order: the liveest
        non-terminal turn it holds (its id too when it is running, so the caller can peek that
        turn's hub activity), the newest `updated_at` across those turns, and whether its most
        recent terminal turn ended `failed`. The caller's audience authority picks the ids — this
        read never widens them.

        An agent is reached by many members and its turns are not: every part of the aggregate is
        computed only from turns in conversations this reader reads — the workspace-shared ones and
        their own — so one member's private turn is never what another member's screen reports, and
        the running turn id this hands back can only open a hub the reader may already tail. An
        agent whose readable turns are none is still a row, all nulls.

        A screen polls this every few seconds for the life of a tab, so no part of it may grow with
        an agent's history. `last_active_at` and the last terminal status are each the newest turn
        matching a predicate, asked as a correlated `order by updated_at desc limit 1` per agent —
        an index walk from the newest end that stops at the first readable row, where a `group by`
        or a window over the same rows must first read every turn the agent ever took. The live read
        ranks instead, because it needs two columns of one row and its partial index already bounds
        it to the turns actually in flight."""
        if not agent_ids:
            return ()
        liveness = sa.case(
            *(
                (tables.turn.c.status == status, rank)
                for rank, status in enumerate(LIVE_TURN_PRIORITY)
            )
        )
        readable = tables.turn.join(
            tables.conversation, tables.turn.c.conversation_id == tables.conversation.c.id
        )
        audiences = readable_audiences(member_id)
        live_ranked = (
            sa.select(
                tables.turn.c.agent_id,
                tables.turn.c.id,
                tables.turn.c.status,
                sa.func.row_number()
                .over(
                    partition_by=tables.turn.c.agent_id,
                    order_by=(
                        liveness.asc(),
                        tables.turn.c.updated_at.desc(),
                        tables.turn.c.id.desc(),
                    ),
                )
                .label("recency"),
            )
            .select_from(readable)
            .where(
                tables.turn.c.workspace_id == self.workspace_id,
                tables.turn.c.agent_id.in_(agent_ids),
                tables.conversation.c.audience.in_(audiences),
                tables.turn.c.terminal.is_(None),
            )
            .subquery()
        )
        moved_at = (
            sa.select(tables.turn.c.updated_at)
            .select_from(readable)
            .where(
                tables.turn.c.workspace_id == self.workspace_id,
                tables.turn.c.agent_id == tables.agent.c.id,
                tables.conversation.c.audience.in_(audiences),
            )
            .order_by(tables.turn.c.updated_at.desc(), tables.turn.c.id.desc())
            .limit(1)
            .correlate(tables.agent)
            .scalar_subquery()
        )
        ended_status = (
            sa.select(tables.turn.c.status)
            .select_from(readable)
            .where(
                tables.turn.c.workspace_id == self.workspace_id,
                tables.turn.c.agent_id == tables.agent.c.id,
                tables.conversation.c.audience.in_(audiences),
                tables.turn.c.terminal.is_not(None),
            )
            .order_by(tables.turn.c.updated_at.desc(), tables.turn.c.id.desc())
            .limit(1)
            .correlate(tables.agent)
            .scalar_subquery()
        )
        async with workspace_tx() as connection:
            live_rows = (
                await connection.execute(
                    sa.select(live_ranked.c.agent_id, live_ranked.c.id, live_ranked.c.status).where(
                        live_ranked.c.recency == 1
                    )
                )
            ).all()
            latest_rows = (
                await connection.execute(
                    sa.select(
                        tables.agent.c.id.label("agent_id"),
                        moved_at.label("moved_at"),
                        ended_status.label("ended_status"),
                    ).where(
                        tables.agent.c.workspace_id == self.workspace_id,
                        tables.agent.c.id.in_(agent_ids),
                    )
                )
            ).all()
        live = {row.agent_id: row for row in live_rows}
        latest = {row.agent_id: row for row in latest_rows}
        statuses = []
        for agent_id in agent_ids:
            liveest = live.get(agent_id)
            newest = latest.get(agent_id)
            moved = None if newest is None else newest.moved_at
            statuses.append(
                AgentTurnStatus(
                    agent_id=agent_id,
                    live=None if liveest is None else liveest.status,
                    running_turn_id=(
                        liveest.id if liveest is not None and liveest.status == "running" else None
                    ),
                    last_active_at=(
                        None
                        if moved is None
                        else (moved if moved.tzinfo else moved.replace(tzinfo=UTC))
                    ),
                    last_failed=newest is not None and newest.ended_status == "failed",
                )
            )
        return tuple(statuses)

    async def agent_setup(self, agent_id: UUID, member_id: UUID) -> SetupState:
        """What one agent still needs before it works: the accounts its provision declared, which
        of them this workspace has granted it, whether the credentials it cannot run without are
        filled, whether it holds the standing order that gives it an occasion to run, and what to
        do about the rest.

        Declaration, installs and standing orders are workspace shape — the presence of an
        order, never whose. The accounts are read for `member_id`, because a grant made privately
        works for the member who made it and for nobody else: a workspace-wide count told the
        second member their app was connected and then refused every call it made. Which account
        it is still stays behind `list_agent_connections`.

        The standing half reads through the object registry rather than off a core table, because
        the kinds that arm an agent are extensions': `member_id` names the reader the registry's
        own gate wants, and the read runs as an admin because whether an app is armed is a fact
        about the app, not about who is looking at it.

        A named order is read as the one row it is, never looked for in a listing, because a
        listing is paged: an agent whose other orders of that kind fill a page would read its own
        as missing, and re-applying the same name only moves the row that is already there."""
        from ufo.runtime.objects import ObjectListQuery

        async def armed(kind: str, name: str | None) -> ArmedOrder:
            if name is not None:
                held = await self.member_object(kind, name, agent_id, member_id, admin=True)
                if held is None:
                    return ArmedOrder(held=False)
                # The cron the order fires on, read off the kind's own spec: a screen that offered
                # the cadences reads it back into the offer it took, so the answer stays on the
                # step. The spec is the extension's, so it is read as the record it serialises to
                # rather than by reaching for an attribute core cannot name.
                cron = held.detail.spec.model_dump(mode="json").get("schedule")
                return ArmedOrder(held=True, schedule=cron if isinstance(cron, str) else None)
            page = await self.list_member_objects(
                kind, agent_id, member_id, admin=True, query=ObjectListQuery()
            )
            return ArmedOrder(held=page is not None and bool(page.rows))

        with ws(self.workspace_id):
            return await setup_state(agent_id, member_id, armed=armed)

    async def list_member_objects(
        self,
        kind: str,
        agent_id: UUID,
        member_id: UUID,
        *,
        admin: bool,
        query: "ObjectListQuery",
    ) -> "ObjectPage | None":
        """One object kind's page for a signed-in member — the portal's index projection over the
        deploy's registry, answering through the kind's own visibility gate (`member_page`) and
        its own declared filter and order vocabulary, which this stamps onto the query so a
        caller cannot widen it. None when this deploy registers no such kind, or the kind reads
        for a member but does not list. The agent is bound here, so an agent-scoped kind reads
        behind the same wall every portal route answers on."""
        from ufo.runtime.objects import MemberListable

        bound = self._objects.get(kind)
        if bound is None or not isinstance(bound.kind.store, MemberListable):
            return None
        with bind_agent(agent_id):
            return await bound.kind.store.member_page(
                bound.context,
                member_id=member_id,
                admin=admin,
                query=replace(query, supported_fields=bound.kind.list_fields),
            )

    async def member_object(
        self, kind: str, name: str, agent_id: UUID, member_id: UUID, *, admin: bool
    ) -> "MemberObject | None":
        """One object as a signed-in member reads it — the portal's detail projection, answering
        through the kind's own visibility gate (`member_detail`). None when this deploy registers
        no such kind, the kind does not read for a member, or the member may not see that row: a
        detail page cannot tell a hidden row from an absent one, and neither may its caller."""
        from ufo.runtime.objects import MemberReadable

        bound = self._objects.get(kind)
        if bound is None or not isinstance(bound.kind.store, MemberReadable):
            return None
        with bind_agent(agent_id):
            return await bound.kind.store.member_detail(
                bound.context, name, member_id=member_id, admin=admin
            )

    async def list_conversation_member_objects(
        self,
        kind: str,
        agent_id: UUID,
        conversation_id: UUID,
        member_id: UUID,
        *,
        admin: bool,
        limit: int,
    ) -> tuple["ConversationObjectGrant", ...] | None:
        from ufo.runtime.objects import ConversationMemberListable

        bound = self._objects.get(kind)
        if bound is None or not isinstance(bound.kind.store, ConversationMemberListable):
            return None
        with bind_agent(agent_id):
            return await bound.kind.store.member_conversation_rows(
                bound.context,
                conversation_id,
                member_id=member_id,
                admin=admin,
                limit=limit,
            )

    async def list_credential_slots(self) -> tuple[CredentialSlotView, ...]:
        """The member-fillable declared slots with their fill state — never a value. The
        `credential` object kind lists every slot; this read drops `member_filled=False` ones
        (deploy-written seals a member can neither fill nor rotate), because the panel exists to
        show a member what they can act on."""
        async with workspace_tx() as connection:
            filled = set(
                (
                    await connection.execute(
                        sa.select(tables.credential.c.slot).where(
                            tables.credential.c.workspace_id == self.workspace_id
                        )
                    )
                )
                .scalars()
                .all()
            )
        names = {
            slot.extension + "/" + slot.name: name
            for name, slot in named_slots(self._declared_slots).items()
        }
        return tuple(
            CredentialSlotView(
                slot=slot.name,
                name=names[slot.extension + "/" + slot.name],
                extension=slot.extension,
                description=slot.description,
                filled=slot.name in filled,
            )
            for slot in sorted(self._declared_slots, key=lambda slot: (slot.extension, slot.name))
            if slot.member_filled
        )

    async def workspace_domain(self) -> str | None:
        """The workspace's domain signup subject, which a chat-surface join may match.

        Personal-mail workspaces are keyed by the founder's exact address and answer None, so a
        shared provider domain grants nobody access."""
        async with workspace_tx() as connection:
            return await workspace_domain(connection, self.workspace_id)

    async def list_members(self) -> tuple[SeatEntry, ...]:
        """The workspace roster a portal session reads, ordered by email so the panel's rows are
        stable — the same rows the `member` kind lists to a member asking the main agent in an
        internal conversation, which a portal session always is (its audience is the signed-in
        member's own, never a shared room). Adding a member and changing a role or seat stay
        admin-gated in their own verbs."""
        async with workspace_tx() as connection:
            snapshot = await Seats(self.workspace_id).snapshot(connection)
        return tuple(sorted(snapshot.members, key=lambda entry: entry.email))

    async def list_sources(self, member_id: UUID, *, admin: bool) -> tuple[SourceView, ...]:
        """The live source bindings this member may see — an admin all of them, everyone else
        their own registrations plus shared ones. Removed sources stay gone; a member-subject
        source's pages remain gated to that member wherever they land."""
        query = (
            sa.select(
                tables.source.c.backend,
                tables.source.c.subject,
                tables.member.c.email,
                tables.source.c.owner_member_id,
                tables.source.c.consecutive_errors,
                tables.source.c.next_sync_at,
                tables.source.c.parked_reason,
                tables.source.c.config,
            )
            .select_from(
                tables.source.outerjoin(
                    tables.member, tables.source.c.owner_member_id == tables.member.c.id
                )
            )
            .where(
                tables.source.c.workspace_id == self.workspace_id,
                tables.source.c.removed_at.is_(None),
            )
            .order_by(tables.source.c.backend, tables.source.c.created_at)
        )
        if not admin:
            query = query.where(
                sa.or_(
                    tables.source.c.subject == SHARED_SUBJECT,
                    tables.source.c.owner_member_id == member_id,
                )
            )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        return tuple(
            SourceView(
                backend=row.backend,
                shared=row.subject == SHARED_SUBJECT,
                owner_email=row.email,
                own=admin or row.owner_member_id == member_id,
                consecutive_errors=row.consecutive_errors,
                next_sync_at=row.next_sync_at,
                parked_reason=row.parked_reason,
                **_binding_fields(row.backend, row.config),
            )
            for row in rows
        )

    async def spend_caps(self) -> tuple[SpendCapView, ...]:
        """Every spend cap of this workspace with its subject named for the reader — the
        workspace-administration read behind the portal's billing view. Caps are set by the
        deploy's operators today; no object kind owns them, so this stays a read."""
        member_name = sa.func.coalesce(tables.agent.c.archived_name, tables.agent.c.name)
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.spend_cap.c.scope,
                        tables.spend_cap.c.subject_id,
                        tables.spend_cap.c.window_seconds,
                        tables.spend_cap.c.limit_micro_usd,
                        tables.spend_cap.c.on_breach,
                        member_name.label("agent_name"),
                        tables.member.c.email.label("member_email"),
                    )
                    .select_from(
                        tables.spend_cap.outerjoin(
                            tables.agent,
                            sa.and_(
                                tables.spend_cap.c.scope == "agent",
                                tables.spend_cap.c.subject_id == tables.agent.c.id,
                            ),
                        ).outerjoin(
                            tables.member,
                            sa.and_(
                                tables.spend_cap.c.scope == "member",
                                tables.spend_cap.c.subject_id == tables.member.c.id,
                            ),
                        )
                    )
                    .where(tables.spend_cap.c.workspace_id == self.workspace_id)
                    .order_by(
                        tables.spend_cap.c.scope,
                        tables.spend_cap.c.subject_id,
                        tables.spend_cap.c.window_seconds,
                    )
                )
            ).all()
        return tuple(
            SpendCapView(
                scope=row.scope,
                subject=row.agent_name if row.scope == "agent" else row.member_email,
                window_seconds=row.window_seconds,
                limit_micro_usd=row.limit_micro_usd,
                on_breach=row.on_breach,
            )
            for row in rows
        )

    async def list_installations(self) -> tuple[InstallationSummary, ...]:
        """Every surface installation of this workspace with the agent it binds, ordered by
        surface — the workspace-administration read behind the portal's agents view."""
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.surface_installation.c.surface,
                        tables.surface_installation.c.agent_id,
                    )
                    .where(tables.surface_installation.c.workspace_id == self.workspace_id)
                    .order_by(tables.surface_installation.c.surface)
                )
            ).all()
        return tuple(
            InstallationSummary(surface=row.surface, agent_id=row.agent_id) for row in rows
        )

    async def list_conversations(
        self, limit: int = LIST_CONVERSATIONS_LIMIT
    ) -> tuple[ConversationSummary, ...]:
        """The workspace's conversations, newest activity first, across every surface — the read
        half a debug view lists. Bounded, and RLS-scoped like every read on this context."""
        activity = (
            sa.select(
                tables.turn.c.conversation_id,
                sa.func.count().label("turn_count"),
                sa.func.max(tables.turn.c.updated_at).label("last_turn_at"),
            )
            .where(tables.turn.c.workspace_id == self.workspace_id)
            .group_by(tables.turn.c.conversation_id)
            .subquery()
        )
        query = (
            sa.select(
                tables.conversation.c.id,
                tables.conversation.c.surface,
                tables.conversation.c.queue_key,
                tables.conversation.c.created_at,
                tables.member.c.email,
                activity.c.turn_count,
                activity.c.last_turn_at,
            )
            .select_from(
                tables.conversation.outerjoin(
                    tables.member, tables.member.c.id == tables.conversation.c.member_id
                ).outerjoin(activity, activity.c.conversation_id == tables.conversation.c.id)
            )
            .where(tables.conversation.c.workspace_id == self.workspace_id)
            .order_by(
                sa.func.coalesce(activity.c.last_turn_at, tables.conversation.c.created_at).desc()
            )
            .limit(limit)
        )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        return tuple(
            ConversationSummary(
                id=row.id,
                surface=row.surface,
                queue_key=row.queue_key,
                member_email=row.email,
                created_at=row.created_at,
                turn_count=row.turn_count or 0,
                last_turn_at=row.last_turn_at,
            )
            for row in rows
        )

    async def list_agent_conversations(
        self,
        agent_id: UUID,
        member_id: UUID,
        *,
        admin: bool,
        limit: int,
        surface: str | None = None,
        conversation_id: UUID | None = None,
        participation: Literal["mine", "others"] | None = None,
        search: str | None = None,
    ) -> tuple[ListedConversation, ...]:
        """One agent's conversations as the portal lists them — `ConversationDirectory.list`,
        bound to this surface's workspace."""
        return await ConversationDirectory(self.workspace_id).list(
            agent_id,
            member_id,
            admin=admin,
            limit=limit,
            surface=surface,
            conversation_id=conversation_id,
            participation=participation,
            search=search,
        )

    async def readable_conversation(
        self, conversation_id: UUID, agent_id: UUID, member_id: UUID, *, admin: bool = False
    ) -> bool:
        """Whether this member may read that conversation's content — its turns, the subagent turns
        it spawned, and its workspace files. True for their own conversations and the
        workspace-shared ones. Another member's private one answers true for an admin who has
        recorded a disclosure against it inside `TRANSCRIPT_ACCESS_WINDOW`
        (`record_transcript_access` — the acknowledgement is the act, and the row is the audit
        record), and false for that same admin until they do. A room (a private channel or group
        DM) and an externally-shared channel stay false for everyone, admin included, because
        participation there is the peer surface's live roster and no portal read can check it
        (#645 — live reachability is the audience authority). The agent is the wall: another
        agent's conversation is unreadable even by id, so every content route fails closed on this
        one answer."""
        async with workspace_tx() as connection:
            found = (
                await connection.execute(
                    sa.select(tables.conversation.c.audience).where(
                        tables.conversation.c.workspace_id == self.workspace_id,
                        tables.conversation.c.id == conversation_id,
                        tables.conversation.c.agent_id == agent_id,
                    )
                )
            ).one_or_none()
            if found is None:
                return False
            if found.audience in readable_audiences(member_id):
                return True
            if not admin or audience_member(parse_audience(found.audience)) is None:
                return False
            disclosed = (
                await connection.execute(
                    sa.select(tables.transcript_access.c.id).where(
                        tables.transcript_access.c.workspace_id == self.workspace_id,
                        tables.transcript_access.c.conversation_id == conversation_id,
                        tables.transcript_access.c.reader_member_id == member_id,
                        tables.transcript_access.c.created_at
                        >= datetime.now(UTC) - TRANSCRIPT_ACCESS_WINDOW,
                    )
                )
            ).first()
        return disclosed is not None

    async def conversation_audience(self, conversation_id: UUID, agent_id: UUID) -> Audience | None:
        """The audience bound to one conversation on this workspace and agent, or None."""
        async with workspace_tx() as connection:
            value = await connection.scalar(
                sa.select(tables.conversation.c.audience).where(
                    tables.conversation.c.workspace_id == self.workspace_id,
                    tables.conversation.c.id == conversation_id,
                    tables.conversation.c.agent_id == agent_id,
                )
            )
        return None if value is None else parse_audience(value)

    async def conversation_subagent_turns(
        self, conversation_id: UUID, limit: int = LIST_TURNS_LIMIT
    ) -> tuple[Turn, ...]:
        """Every turn spawned beneath this conversation's turns, transitively — a subagent that
        spawns its own is nested again. Each runs in its own conversation carrying the parent's
        audience and agent, so a caller authorized for the parent conversation is
        authorized for these; the tree a view nests is `parent_turn_id` over this set. Breadth
        first, so a parent always precedes the turns it spawned however the timestamps tie, and
        bounded."""
        roots = sa.select(tables.turn.c.id).where(
            tables.turn.c.workspace_id == self.workspace_id,
            tables.turn.c.conversation_id == conversation_id,
        )
        descendants = (
            sa.select(tables.turn.c.id, sa.literal(1).label("depth"))
            .where(
                tables.turn.c.workspace_id == self.workspace_id,
                tables.turn.c.parent_turn_id.in_(roots),
            )
            .cte("subagent_turns", recursive=True)
        )
        descendants = descendants.union_all(
            sa.select(tables.turn.c.id, (descendants.c.depth + 1).label("depth")).where(
                tables.turn.c.workspace_id == self.workspace_id,
                tables.turn.c.parent_turn_id == descendants.c.id,
            )
        )
        query = (
            self._turn_query()
            .add_columns(descendants.c.depth)
            .join(descendants, descendants.c.id == tables.turn.c.id)
            .where(tables.turn.c.workspace_id == self.workspace_id)
            .order_by(descendants.c.depth, tables.turn.c.created_at, tables.turn.c.id)
            .limit(limit)
        )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        return tuple(self._turn_record(row) for row in rows)

    async def list_turns(
        self, conversation_id: UUID, limit: int = LIST_TURNS_LIMIT
    ) -> tuple[Turn, ...]:
        """The conversation's turns in admission order, oldest first, bounded to the `limit` most
        recent — each the full durable row (terminal outcome, context, subagent parentage)."""
        query = (
            self._turn_query()
            .where(
                tables.turn.c.workspace_id == self.workspace_id,
                tables.turn.c.conversation_id == conversation_id,
            )
            .order_by(tables.turn.c.seq.desc())
            .limit(limit)
        )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).all()
        return tuple(self._turn_record(row) for row in reversed(rows))

    async def agent_origin_refs(self, conversation_id: UUID) -> frozenset[str]:
        """The `message_ref`s in this conversation whose inbound is a machine envelope rather than
        words: a scheduled task's firing, wrapped in its cron element, and a subagent result,
        wrapped in the element naming the child that answered. A transcript projection renders a
        member's own words as their bubble, and neither of these is words.

        Both are named by what produced them — the firing by its admission source, the delivery by
        the key it admits under — never by reading the body. An extension invoking a turn sends the
        member prose they are meant to read (which sources changed, which pull request to review),
        so it is deliberately not here: `internal` alone would take those prompts away and leave an
        answer to nothing. Both id spaces answer, because a turn's founding inbound is referenced by
        the turn and a drained arrival by its queue row."""
        machine = sa.or_(
            tables.turn.c.admission_source == SCHEDULED_ADMISSION,
            tables.turn.c.idempotency_key.startswith(SPAWN_RESULT_KEY_PREFIX),
        )
        async with workspace_tx() as connection:
            refs = (
                await connection.execute(
                    sa.union_all(
                        sa.select(tables.turn.c.id).where(
                            tables.turn.c.workspace_id == self.workspace_id,
                            tables.turn.c.conversation_id == conversation_id,
                            machine,
                        ),
                        sa.select(tables.inbound_message.c.id).where(
                            tables.inbound_message.c.workspace_id == self.workspace_id,
                            tables.inbound_message.c.conversation_id == conversation_id,
                            tables.inbound_message.c.idempotency_key.startswith(
                                SPAWN_RESULT_KEY_PREFIX
                            ),
                        ),
                    )
                )
            ).scalars()
        return frozenset(str(ref) for ref in refs)

    async def turn_detail(self, turn_id: UUID) -> TurnDetail | None:
        """One turn with its accounting rows and the subagent turns it spawned, or None when no
        such turn exists in this workspace."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    self._turn_query().where(
                        tables.turn.c.id == turn_id,
                        tables.turn.c.workspace_id == self.workspace_id,
                    )
                )
            ).one_or_none()
            if row is None:
                return None
            children = (
                await connection.execute(
                    self._turn_query()
                    .where(
                        tables.turn.c.parent_turn_id == turn_id,
                        tables.turn.c.workspace_id == self.workspace_id,
                    )
                    .order_by(tables.turn.c.created_at)
                )
            ).all()
            ledger = (
                await connection.execute(
                    sa.select(
                        tables.ledger.c.dimension,
                        tables.ledger.c.amount,
                        tables.ledger.c.priced_micro_usd,
                        tables.ledger.c.model,
                        tables.ledger.c.created_at,
                    )
                    .where(tables.ledger.c.turn_id == turn_id)
                    .order_by(tables.ledger.c.created_at)
                )
            ).all()
        return TurnDetail(
            turn=self._turn_record(row),
            ledger=tuple(
                LedgerEntry(
                    dimension=entry.dimension,
                    amount=entry.amount,
                    priced_micro_usd=entry.priced_micro_usd,
                    model=entry.model,
                    created_at=entry.created_at,
                )
                for entry in ledger
            ),
            children=tuple(self._turn_record(child) for child in children),
        )

    async def turn_steps(self, turn_id: UUID) -> tuple[TurnStep, ...] | None:
        """One owned turn's durable DBOS steps, or None when it belongs to another workspace."""
        async with workspace_tx() as connection:
            turn = (
                await connection.execute(
                    sa.select(tables.turn.c.running_attempt).where(
                        tables.turn.c.id == turn_id,
                        tables.turn.c.workspace_id == self.workspace_id,
                    )
                )
            ).one_or_none()
        if turn is None:
            return None
        return await self._turn_steps.read(turn.running_attempt or str(turn_id))

    async def queued_arrivals(
        self, conversation_id: UUID, draining_turn_id: UUID | None
    ) -> tuple[QueuedArrival, ...]:
        """The conversation's admitted messages its written transcript does not hold, in admission
        order: the rows no turn has drained, and — given the live turn as `draining_turn_id` — the
        rows that turn folded in, since it writes the transcript only when it ends. A settled
        turn's rows are already written, which is why the caller names the turn rather than this
        read taking any consumer: the projection appends these after the running turn's own
        inbound, so a reload between admission and the turn's end still shows the message."""
        if not await self._owned_conversation(conversation_id):
            return ()
        taken = (
            sa.false()
            if draining_turn_id is None
            else tables.inbound_message.c.consumed_turn_id == draining_turn_id
        )
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.inbound_message.c.id,
                        tables.inbound_message.c.body,
                        tables.inbound_message.c.consumed_turn_id,
                        tables.inbound_message.c.admission_source,
                    )
                    .where(
                        tables.inbound_message.c.workspace_id == self.workspace_id,
                        tables.inbound_message.c.conversation_id == conversation_id,
                        sa.or_(tables.inbound_message.c.consumed_turn_id.is_(None), taken),
                    )
                    .order_by(tables.inbound_message.c.seq)
                )
            ).all()
        return tuple(
            QueuedArrival(
                id=row.id,
                inbound=row.body,
                waiting=row.consumed_turn_id is None,
                admission_source=row.admission_source,
            )
            for row in rows
        )

    async def arrival_speakers(self, conversation_id: UUID) -> tuple[SpokenArrival, ...]:
        """Attribution for every member-admitted row of the conversation, waiting or drained. The
        engine's `<context>` tag names a folded message by its queue-row id rather than a turn id,
        so the projection that labels who spoke reads these beside `list_turns` — a turn row only
        names the message that founded it."""
        if not await self._owned_conversation(conversation_id):
            return ()
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.inbound_message.c.id,
                        tables.inbound_message.c.context,
                        tables.inbound_message.c.speaker_member_id,
                    ).where(
                        tables.inbound_message.c.workspace_id == self.workspace_id,
                        tables.inbound_message.c.conversation_id == conversation_id,
                        tables.inbound_message.c.admission_source == MEMBER_ADMISSION,
                    )
                )
            ).all()
        spoken = []
        for row in rows:
            context = None if row.context is None else TurnContext(**row.context)
            spoken.append(
                SpokenArrival(
                    id=row.id,
                    sender=None if context is None else context.sender,
                    question=None if context is None else context.question,
                    speaker_member_id=row.speaker_member_id,
                )
            )
        return tuple(spoken)

    async def keyed_admissions(self, conversation_id: UUID) -> tuple[KeyedAdmission, ...]:
        """Every message of one conversation that admitted under an idempotency key. `admitted_body`
        answers one key the surface still holds; this answers the whole conversation, for a
        projection that has to recognize its own admissions in a transcript it reads back — the key
        says what a message was admitted as, and the ref says which message the transcript names.
        Both id spaces answer, as `agent_origin_refs` does, because either shape an admission takes
        carries the key it landed under."""
        if not await self._owned_conversation(conversation_id):
            return ()
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.union_all(
                        sa.select(
                            tables.turn.c.id,
                            tables.turn.c.idempotency_key,
                            tables.turn.c.inbound,
                        ).where(
                            tables.turn.c.workspace_id == self.workspace_id,
                            tables.turn.c.conversation_id == conversation_id,
                            tables.turn.c.idempotency_key.is_not(None),
                        ),
                        sa.select(
                            tables.inbound_message.c.id,
                            tables.inbound_message.c.idempotency_key,
                            tables.inbound_message.c.body,
                        ).where(
                            tables.inbound_message.c.workspace_id == self.workspace_id,
                            tables.inbound_message.c.conversation_id == conversation_id,
                            tables.inbound_message.c.idempotency_key.is_not(None),
                        ),
                    )
                )
            ).all()
        return tuple(
            KeyedAdmission(ref=row[0], idempotency_key=row[1], inbound=row[2]) for row in rows
        )

    async def read_transcript(self, conversation_id: UUID) -> Conversation | None:
        """The conversation's durable message transcript — assistant text and tool_use/tool_result
        blocks — or None when the conversation is not this workspace's or has no transcript yet.
        The ownership gate runs first because the blob store is unscoped: a foreign conversation id
        must yield nothing, never another tenant's transcript."""
        if not await self._owned_conversation(conversation_id):
            return None
        try:
            body = await self.blob.get(transcript_key(conversation_id))
        except BlobNotFound:
            return None
        return decode(body)

    async def list_compactions(self, conversation_id: UUID) -> tuple[int, ...]:
        """The indices of the conversation's persisted compaction records, ascending — each one
        readable via `read_compaction`."""
        if not await self._owned_conversation(conversation_id):
            return ()
        entries = await self.blob.list(f"conversations/{conversation_id}/compactions/")
        indices = {
            int(parts[3])
            for entry in entries
            if len(parts := entry.key.split("/")) == 5 and parts[3].isdigit()
        }
        return tuple(sorted(indices))

    async def read_compaction(self, conversation_id: UUID, index: int) -> CompactionRecord | None:
        """One compaction's durable record — the pre-compaction window, its replacement, and the
        typed summary — or None when the conversation is not this workspace's or the index holds
        no record."""
        if not await self._owned_conversation(conversation_id):
            return None
        return await read_compaction_record(self.blob, conversation_id, index)

    async def read_compaction_after(
        self, conversation_id: UUID, index: int
    ) -> tuple[Message, ...] | None:
        """One compaction's `after` window alone — the summary plus the kept tail, the light half a
        reader compares a later window's opening against — or None when the conversation is not
        this workspace's or the index holds no record."""
        if not await self._owned_conversation(conversation_id):
            return None
        return await read_compaction_after(self.blob, conversation_id, index)

    async def list_workspace_files(self, conversation_id: UUID) -> tuple[WorkspaceFile, ...]:
        """Member-visible files in the conversation's `/workspace` — the sandbox's live state with
        Git metadata omitted before the in-container walk's cap — as sorted workspace-relative
        paths. Empty for a conversation that is not this workspace's or has no sandbox yet."""
        if not await self._owned_conversation(conversation_id):
            return ()
        return await self._sandboxes.entries(conversation_id)

    async def conversation_changes(self, conversation_id: UUID) -> WorkspaceChanges:
        """What git last reported changed in the conversation's checkouts, recorded when a turn of
        its own — or of a subagent sharing its sandbox — committed. Nothing for a conversation that
        is not this workspace's."""
        if not await self._owned_conversation(conversation_id):
            return NOTHING_CHANGED
        return await recorded_workspace_changes(conversation_id)

    async def read_workspace_file(
        self, conversation_id: UUID, rel: str
    ) -> AsyncIterator[bytes] | None:
        """Stream one workspace file's bytes out of the live sandbox, or None when the
        conversation is not this workspace's, has no sandbox, or the path names nothing. The path
        is workspace-scoped in the session, so it escapes neither the workspace nor the
        container."""
        if not await self._owned_conversation(conversation_id):
            return None
        return await self._sandboxes.read(conversation_id, rel)

    def terminal_connect(
        self, conversation_id: UUID, cwd: str, member_id: UUID | None, runtime_id: str
    ) -> None:
        """Publish the terminal this surface's held connection stands for, so the conversation's
        sandbox can be that terminal. Paired with `terminal_disconnect` around the connection's
        life; the rendezvous survives the reconnects a held stream's cap makes routine."""
        self._sandboxes.terminals.connect(conversation_id, cwd, member_id, runtime_id)

    def terminal_disconnect(self, conversation_id: UUID) -> None:
        self._sandboxes.terminals.disconnect(conversation_id)

    async def claim_terminal(self, conversation_id: UUID, cwd: str) -> bool:
        """Bind a fresh conversation to the connected terminal at admission, so a turn whose first
        sandbox open lands between the client's held streams still opens on the terminal rather
        than silently provisioning the deploy's workspace. A compare-and-swap over an empty handle:
        a conversation already bound anywhere keeps its binding, and the claim reports whether this
        call made it — the one moment the surface tells the member where the agent works."""
        return await self._sandboxes.claim_terminal(conversation_id, cwd)

    async def next_terminal_op(
        self, conversation_id: UUID, exclude_op_id: str | None = None
    ) -> TerminalOp:
        """The next op the conversation's turn asks of its terminal — what a held stream races
        against the turn's own frames, rendering each op as one `run` directive. `exclude_op_id` is
        the op this same request just answered, so a reply POST that also resumes the tail never
        re-renders the op it is the answer to."""
        return await self._sandboxes.terminals.next_op(conversation_id, exclude_op_id)

    def terminal_resolve(
        self,
        conversation_id: UUID,
        op_id: str,
        reply: bytes,
        failed: str | None,
        member_id: UUID | None,
    ) -> bool:
        """Answer the in-flight op with what the member's client posted back. Gated twice: the op
        id is unguessable and single-use, and the reply must come from the member whose terminal
        the binding named — another member's bearer on the same channel resolves nothing. The
        member gate lives in the transport, which holds the binding: the reply POST can land on a
        pod that never held the connection, so a workspace read here would answer for the wrong
        pod."""
        return self._sandboxes.terminals.resolve(conversation_id, op_id, reply, failed, member_id)

    async def terminal_op_body(
        self, queue_key: str, op_id: str, member_id: UUID | None
    ) -> bytes | None:
        """The bytes the in-flight op sends down to the terminal — the read projection a client
        `curl`s a write's body from, gated exactly as `terminal_resolve` and never creating a
        conversation: an op in flight implies one exists. The transport holds the binding and the
        staged bytes, so the member gate and the read land together on whichever pod staged the op
        rather than reading a binding this pod may never have held."""
        async with workspace_tx() as connection:
            found = (await connection.execute(self._conversation_lookup(queue_key))).one_or_none()
        if found is None:
            return None
        return await self._sandboxes.terminals.staged(found.id, op_id, member_id)

    async def installation(self, peer_surface: str) -> str | None:
        """The workspace's installation identity on a peer surface (e.g. Slack's `team:<id>`), or
        None when that surface holds no installation here — how a read view renders deep links
        into the surface a conversation actually lives on."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.surface_installation.c.installation_id).where(
                        tables.surface_installation.c.workspace_id == self.workspace_id,
                        tables.surface_installation.c.surface == peer_surface,
                    )
                )
            ).one_or_none()
        return None if row is None else row.installation_id

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """A transaction over the tables the surface's own extension shipped a migration for — the
        same raw whole-database connection `ExtensionContext.transaction` yields, under the
        workspace this request already resolved. A surface that renders its extension's rows (the
        sites frame reading a `hosted_site`) has no turn and so no `ExtensionContext` to reach them
        through. Scoping every query to `workspace_id` is the surface's responsibility exactly as
        it is a tool handler's: the SDK import boundary is a static gate over imports, never over
        runtime SQL. Commits on exit, rolls back on error."""
        async with workspace_tx() as connection:
            yield connection

    async def _owned_conversation(self, conversation_id: UUID) -> bool:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.conversation.c.id).where(
                        tables.conversation.c.id == conversation_id,
                        tables.conversation.c.workspace_id == self.workspace_id,
                    )
                )
            ).one_or_none()
        return row is not None

    def _turn_query(self) -> sa.Select:
        return sa.select(
            tables.turn.c.id,
            tables.turn.c.workspace_id,
            tables.turn.c.conversation_id,
            tables.turn.c.agent_id,
            tables.turn.c.seq,
            tables.turn.c.status,
            tables.turn.c.inbound,
            tables.turn.c.admission_source,
            tables.turn.c.speaker_member_id,
            tables.turn.c.created_at,
            tables.turn.c.updated_at,
            tables.turn.c.context,
            tables.turn.c.terminal,
            tables.turn.c.parent_turn_id,
            tables.turn.c.subagent_profile,
            tables.turn.c.subagent_name,
            tables.turn.c.traceparent,
            tables.turn.c.connect_landed_at,
        )

    def _turn_record(self, row: sa.Row) -> Turn:
        return Turn(
            id=row.id,
            workspace_id=row.workspace_id,
            conversation_id=row.conversation_id,
            agent_id=row.agent_id,
            seq=row.seq,
            status=row.status,
            inbound=row.inbound,
            admission_source=row.admission_source,
            speaker_member_id=row.speaker_member_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            context=None if row.context is None else TurnContext.model_validate(row.context),
            terminal=None if row.terminal is None else TerminalFrame.model_validate(row.terminal),
            parent_turn_id=row.parent_turn_id,
            subagent_profile=row.subagent_profile,
            subagent_name=row.subagent_name,
            traceparent=row.traceparent,
            connect_landed_at=row.connect_landed_at,
        )


class SurfaceWorkspaceUnknown(LookupError):
    """A shared surface request names no workspace this deployment serves."""


class SurfaceInstallationConflict(LookupError):
    """A shared surface installation is already bound to another workspace."""


class UndeclaredSurface(KeyError):
    """A tool tried to register an installation for a surface its manifest does not declare."""


class AddressClaimState(StrEnum):
    """What a workspace member's claim on one address came to."""

    RESERVED = "reserved"
    LINKED = "linked"
    TAKEN = "taken"


@dataclass(frozen=True)
class SurfaceInstallationAccess:
    """A tool's manifest-scoped view of its declared surfaces under the ambient workspace: the
    installation this workspace is bound to, and the addresses an addressed surface routes to its
    members."""

    declared: frozenset[str]
    addressed: frozenset[str] = frozenset()

    async def reserve_address(
        self, surface: str, address: str, member_id: UUID, claim_expires_at: datetime
    ) -> AddressClaimState:
        """Claim one address for this workspace's member until `claim_expires_at`, so the surface's
        shared ingress routes it here once the sender proves it. The claim is fleet-wide because the
        address is the identity: `RESERVED` is this member's to prove, `LINKED` is already proved,
        and `TAKEN` is another member's — whichever workspace holds it, which is why the answer
        names no more than that. A lapsed reservation nobody proved is taken over."""
        if surface not in self.addressed:
            raise UndeclaredSurface(surface)
        workspace_id = ws_current().workspace_id
        now = datetime.now(UTC)
        async with owner_tx() as connection:
            insert = postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert
            reserved = await connection.scalar(
                insert(tables.surface_address)
                .values(
                    surface=surface,
                    address=address,
                    workspace_id=workspace_id,
                    member_id=member_id,
                    claim_expires_at=claim_expires_at,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_update(
                    index_elements=("surface", "address"),
                    set_={
                        "workspace_id": workspace_id,
                        "member_id": member_id,
                        "claim_expires_at": claim_expires_at,
                        "proved_by": None,
                        "updated_at": sa.func.now(),
                    },
                    where=sa.or_(
                        tables.surface_address.c.claim_expires_at <= now,
                        sa.and_(
                            tables.surface_address.c.workspace_id == workspace_id,
                            tables.surface_address.c.member_id == member_id,
                            tables.surface_address.c.claim_expires_at.is_not(None),
                        ),
                    ),
                )
                .returning(tables.surface_address.c.member_id)
            )
            if reserved is not None:
                return AddressClaimState.RESERVED
            held = (
                await connection.execute(
                    sa.select(
                        tables.surface_address.c.workspace_id,
                        tables.surface_address.c.member_id,
                    ).where(
                        tables.surface_address.c.surface == surface,
                        tables.surface_address.c.address == address,
                    )
                )
            ).one()
        if held.workspace_id == workspace_id and held.member_id == member_id:
            return AddressClaimState.LINKED
        return AddressClaimState.TAKEN

    async def installation(self, surface: str) -> str | None:
        """Return this workspace's installation identity for one declared surface."""
        if surface not in self.declared:
            raise UndeclaredSurface(surface)
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.surface_installation.c.installation_id).where(
                        tables.surface_installation.c.workspace_id == ws_current().workspace_id,
                        tables.surface_installation.c.surface == surface,
                    )
                )
            ).one_or_none()
        return None if row is None else row.installation_id

    async def bind(self, surface: str, installation_id: str) -> None:
        """Bind one declared surface's installation to this workspace. Reconfiguration replaces
        this workspace's binding. An installation that routes ingress belongs to one workspace, so
        the fleet-wide identity constraint rejects another; an addressed surface's installation is
        the deploy's own and every workspace binds it."""
        if surface not in self.declared:
            raise UndeclaredSurface(surface)
        await _bind_surface_installation(
            ws_current().workspace_id,
            surface,
            installation_id,
            routes_ingress=surface not in self.addressed,
        )


@dataclass(frozen=True)
class SurfaceAuth:
    """The pre-binding gate for a shared surface resolver. Fleet lookup returns only the workspace
    owning an exact installation identity; credential reads remain manifest-slot gated."""

    _credentials: CredentialStore | None
    _declared: frozenset[str]
    _surface: str

    async def workspace(self, installation_id: str) -> UUID | None:
        async with owner_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.surface_installation.c.workspace_id).where(
                        tables.surface_installation.c.surface == self._surface,
                        tables.surface_installation.c.installation_id == installation_id,
                        tables.surface_installation.c.routes_ingress,
                    )
                )
            ).one_or_none()
        return None if row is None else row.workspace_id

    async def addressed_workspace(self, address: str) -> UUID | None:
        """The workspace one address reaches, or None when no workspace claims it. The fleet lookup
        an addressed surface resolves by, in place of an installation identity: the address is
        unique across the fleet, so one row answers it."""
        async with owner_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.surface_address.c.workspace_id).where(
                        tables.surface_address.c.surface == self._surface,
                        tables.surface_address.c.address == address,
                    )
                )
            ).one_or_none()
        return None if row is None else row.workspace_id

    def open_credential_authorization(self, sealed: str) -> CredentialRequestState | None:
        """Recover a sealed credential-authorization handoff's claims before any workspace is bound,
        or None when the seal is tampered or expired. A shared resolver completing a surface's own
        OAuth install reads the sealed workspace from the `state` the browser carries — the Fernet's
        authenticity is the trust, exactly as the connect callback verifies its own sealed state."""
        if self._credentials is None:
            return None
        try:
            return open_credential_request(
                self._credentials.fernet, sealed, purpose=CREDENTIAL_REQUEST_PURPOSE
            )
        except CredentialRequestInvalid:
            return None

    async def credential(self, workspace_id: UUID, slot: str) -> str:
        if slot not in self._declared:
            raise ValueError(f"surface resolver reads undeclared credential slot {slot!r}")
        if self._credentials is None:
            raise RuntimeError("surface resolver reads a credential but no store is configured")
        with ws(workspace_id):
            async with workspace_tx() as connection:
                exists = (
                    await connection.execute(
                        sa.select(tables.workspace.c.id).where(
                            tables.workspace.c.id == workspace_id
                        )
                    )
                ).one_or_none()
            if exists is None:
                raise SurfaceWorkspaceUnknown(str(workspace_id))
            return await self._credentials.get(workspace_id, slot)


@dataclass(frozen=True)
class NothingDelivered:
    """What a durable surface's `post` returns when the delivery was to send nothing at all — a turn
    whose answer is the silence sentinel. There is no message to reference, so no `reply_ref` is
    recorded and `attach` never runs; the writeback is still marked delivered, because nothing is
    what the turn owed. An explicit outcome rather than a `None` reply ref, which the poller reads
    as "not posted yet" and would re-post forever."""


NOTHING_DELIVERED = NothingDelivered()

RouteHandler = Callable[[SurfaceContext, Request], Awaitable[Response]]
PostHandler = Callable[[SurfaceContext, Writeback], Awaitable[str | NothingDelivered]]
AttachHandler = Callable[[SurfaceContext, Writeback, str], Awaitable[None]]
SpeakHandler = Callable[[SurfaceContext, MidTurnReply], Awaitable[str]]
WorkspaceResolver = Callable[[Request, SurfaceAuth], Awaitable[UUID | Response | None]]
SurfaceContextFactory = Callable[[UUID, str], SurfaceContext]
SurfaceListenerOwner = Callable[[], Awaitable[bool]]


@dataclass(frozen=True)
class SurfaceIdentityContext:
    """The live workspace dependencies a surface uses to resolve the external user it speaks as."""

    workspace_id: UUID
    blob: WorkspaceBlobStore
    credential: Callable[[str], Awaitable[str]]


SurfaceIdentityResolver = Callable[[SurfaceIdentityContext], Awaitable[str | None]]


@dataclass(frozen=True)
class SurfaceListenerContext:
    """The fleet gate a persistent surface listener uses to bind one provider installation to its
    workspace before it reads or admits anything. The returned context manager holds the workspace
    scope for the complete event and releases it when delivery ends."""

    surface: str
    _auth: SurfaceAuth
    _context_for: SurfaceContextFactory
    _owned: SurfaceListenerOwner

    @asynccontextmanager
    async def workspace(self, installation_id: str) -> AsyncIterator[SurfaceContext | None]:
        if not await self._owned():
            raise RuntimeError(f"surface listener {self.surface!r} lost fleet ownership")
        workspace_id = await self._auth.workspace(installation_id)
        if workspace_id is None:
            yield None
            return
        with ws(workspace_id):
            yield self._context_for(workspace_id, self.surface)

    @asynccontextmanager
    async def addressed(self, address: str) -> AsyncIterator[SurfaceContext | None]:
        """Bind the workspace one sender's address reaches, or yield None when no workspace claims
        it. One shared provider serves the whole fleet, so the address is what selects the tenant —
        every workspace binds the same installation and none of them owns the stream."""
        if not await self._owned():
            raise RuntimeError(f"surface listener {self.surface!r} lost fleet ownership")
        workspace_id = await self._auth.addressed_workspace(address)
        if workspace_id is None:
            yield None
            return
        with ws(workspace_id):
            yield self._context_for(workspace_id, self.surface)

    async def cursor(self, installation_id: str) -> int | None:
        """Where this listener's stream stands, or None when it has never read one — and when the
        stored position belongs to a different installation, since a deploy pointed at a new
        provider project starts from that project's own head."""
        async with owner_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.surface_stream_cursor.c.installation_id,
                        tables.surface_stream_cursor.c.sequence,
                    ).where(tables.surface_stream_cursor.c.surface == self.surface)
                )
            ).one_or_none()
        if row is None or row.installation_id != installation_id:
            return None
        return row.sequence

    async def store_cursor(self, installation_id: str, sequence: int) -> None:
        """Record where the stream stands. One position serves one stream, so it lives beside the
        listener rather than in any workspace the stream happens to deliver to."""
        async with owner_tx() as connection:
            insert = postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert
            await connection.execute(
                insert(tables.surface_stream_cursor)
                .values(
                    surface=self.surface,
                    workspace_id=None,
                    installation_id=installation_id,
                    sequence=sequence,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_update(
                    index_elements=("surface",),
                    set_={
                        "installation_id": installation_id,
                        "sequence": sequence,
                        "updated_at": sa.func.now(),
                    },
                )
            )

    async def clear_cursor(self) -> None:
        """Forget the stream position, so the next read starts from the provider's head."""
        async with owner_tx() as connection:
            await connection.execute(
                sa.delete(tables.surface_stream_cursor).where(
                    tables.surface_stream_cursor.c.surface == self.surface
                )
            )


SurfaceListener = Callable[[SurfaceListenerContext], Awaitable[None]]

SURFACE_LISTENER_OWNER_POLL_SECONDS = 2.0
SURFACE_LISTENER_LEASE_SECONDS = 10.0


@dataclass(frozen=True)
class SurfaceListenerRunner:
    """Run a persistent listener under one fleet-wide fenced lease. Core owns this assignment
    because an extension can see only its process, while core owns the fleet seats and listener
    lifecycle. A database availability error restarts at the ownership poll interval. Any other
    listener failure parks its cursor and keeps the lease until shutdown or ownership loss, so it
    does not stop unrelated server work or hot-loop the failed event."""

    surface: str
    instance_id: UUID
    listener: SurfaceListener
    _auth: SurfaceAuth
    _context_for: SurfaceContextFactory
    poll_seconds: float = SURFACE_LISTENER_OWNER_POLL_SECONDS
    lease_seconds: float = SURFACE_LISTENER_LEASE_SECONDS
    _token: UUID = field(default_factory=uuid4)

    async def run(self) -> None:
        while True:
            await self._wait_until_owned()
            listener: asyncio.Future[None] = asyncio.ensure_future(
                self.listener(
                    SurfaceListenerContext(
                        surface=self.surface,
                        _auth=self._auth,
                        _context_for=self._context_for,
                        _owned=self._owns,
                    )
                )
            )
            ownership = asyncio.create_task(self._wait_until_not_owned())
            try:
                done, _ = await asyncio.wait(
                    (listener, ownership), return_when=asyncio.FIRST_COMPLETED
                )
                if ownership in done:
                    ownership.result()
                    continue
                error = asyncio.CancelledError() if listener.cancelled() else listener.exception()
                error_class = type(error).__name__ if error is not None else "ListenerEnded"
                log(
                    "surface.listener_failed",
                    surface=self.surface,
                    error_class=error_class,
                )
                match error:
                    case sa.exc.SQLAlchemyError():
                        await asyncio.sleep(self.poll_seconds)
                    case _:
                        emit_metric("surface_listener_parked_total", surface=self.surface)
                        await ownership
            finally:
                for task in (listener, ownership):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(listener, ownership, return_exceptions=True)

    async def _wait_until_not_owned(self) -> None:
        while True:
            owned = await self._owned_on_tick()
            if owned is False:
                return
            await asyncio.sleep(self.poll_seconds)

    async def _wait_until_owned(self) -> None:
        while True:
            if await self._owned_on_tick():
                return
            await asyncio.sleep(self.poll_seconds)

    async def _owned_on_tick(self) -> bool | None:
        try:
            return await self._owns()
        except sa.exc.SQLAlchemyError as error:
            log(
                "surface.listener_claim_failed",
                surface=self.surface,
                error_class=type(error).__name__,
            )
            return None

    async def _owns(self) -> bool:
        """The claim transaction runs to completion even when this task is cancelled meanwhile: an
        abort mid-driver-call invalidates the connection, and on sqlite the hard-closed handle
        keeps its open write transaction — a lock every later writer waits out. The cancellation
        is re-raised once the transaction has committed or rolled back."""
        claim: asyncio.Future[bool] = asyncio.ensure_future(self._claim())
        cancelled: asyncio.CancelledError | None = None
        while not claim.done():
            try:
                await asyncio.shield(claim)
            except asyncio.CancelledError as cancel:
                cancelled = cancel
            except Exception:
                continue
        if cancelled is not None:
            if not claim.cancelled():
                claim.exception()
            raise cancelled
        return claim.result()

    async def _claim(self) -> bool:
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self.lease_seconds)
        async with owner_tx() as connection:
            claim: Any
            match connection.dialect.name:
                case "postgresql":
                    claim = postgres_insert(tables.surface_listener_claim)
                case "sqlite":
                    claim = sqlite_insert(tables.surface_listener_claim)
                case dialect:
                    raise RuntimeError(f"unsupported database dialect {dialect!r}")
            owner = await connection.scalar(
                claim.values(
                    surface=self.surface,
                    workspace_id=None,
                    owner_id=self.instance_id,
                    owner_token=self._token,
                    claim_expires_at=expires_at,
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_update(
                    index_elements=[tables.surface_listener_claim.c.surface],
                    set_={
                        "owner_id": self.instance_id,
                        "owner_token": self._token,
                        "claim_expires_at": expires_at,
                        "updated_at": now,
                    },
                    where=sa.or_(
                        tables.surface_listener_claim.c.claim_expires_at <= now,
                        sa.and_(
                            tables.surface_listener_claim.c.owner_id == self.instance_id,
                            tables.surface_listener_claim.c.owner_token == self._token,
                        ),
                    ),
                )
                .returning(tables.surface_listener_claim.c.owner_token)
            )
        return owner == self._token


class SurfaceDeliveryError(RuntimeError):
    """A durable surface failure carrying the provider's Retry-After, so the poller schedules the
    next attempt when the provider asked instead of on the fixed backoff."""

    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        if retry_after_seconds is not None and retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be nonnegative")
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


@dataclass(frozen=True)
class SurfaceRoute:
    """One HTTP route a surface serves. Core mounts `handler` for `method` at
    `/surface/<name>/<path>` bound to the surface's `SurfaceContext` (the handler reads path and
    query params off the Request and returns the Response)."""

    method: Literal["GET", "POST", "PUT"]
    path: str
    handler: RouteHandler


@dataclass(frozen=True)
class SurfaceSpec:
    """One surface an extension registers. Core mounts each of `routes` under `/surface/<name>`
    bound to the surface's `SurfaceContext`. A **durable** surface also declares its two-phase
    writeback delivery: `post` sends the reply and returns its durable reference (recorded before
    any upload, so recovery skips the re-post), then `attach` uploads the turn's shared files into
    that reply. A `post` returning `NOTHING_DELIVERED` sent no message at all: no reference is
    recorded and `attach` never runs, and the turn is delivered rather than retried.
    Recovery repeats `attach`: attachment delivery is at-least-once because a crash
    after upload but before the delivered commit cannot distinguish the completed upload. A
    surface may make individual files best effort so one rejection does not block its siblings.
    `speak` is the same contract for a reply delivered before the turn ends: one message per marked
    span, plus a source-surface notice when a member comments from another surface, returning that
    message's durable reference. A durable surface that declares none delivers at the terminal
    alone, so its members read a long turn's replies only once it ends.
    The poller drives these for every turn its ingest admitted with writeback. A **live**
    surface omits them (`post=attach=None`): it admits without writeback and delivers by tailing the
    hub in its own route, so the poller never sees its turns. `self_user_id` resolves the surface's
    current external speaker for a same-named source before each fetch, preventing its own output
    from becoming source pages without coupling the two extensions."""

    name: str
    routes: tuple[SurfaceRoute, ...] = ()
    identify: WorkspaceResolver | None = None
    """How the shared fleet resolves a request's workspace before binding it. The async resolver
    uses `SurfaceAuth` to map an installation and verify that workspace's credential. A UUID binds
    that workspace, None rejects the request, and a Response completes a bounded side-effect-free
    pre-binding handshake. Required because the shared fleet is the only runtime and every request
    must resolve its workspace before touching any data — a surface cannot mount without it."""
    post: PostHandler | None = None
    attach: AttachHandler | None = None
    speak: SpeakHandler | None = None
    self_user_id: SurfaceIdentityResolver | None = None
    listen: SurfaceListener | None = None
    """A persistent provider stream owned by this surface. Core starts it with the app and cancels
    it during app shutdown because only core owns the process lifecycle and privileged workspace
    binding. HTTP-only surfaces leave it unset."""
    addressed: bool = False
    """Whether inbound traffic names its member by the sender's own address rather than by an
    installation. A shared provider the deploy owns — one iMessage project, one line — serves every
    workspace, so `surface_address` resolves the tenant and the installation identity routes
    nothing; an installation-routed surface's identity is one customer's account and stays unique
    across the fleet."""
    home: bool = False
    """Whether a browser arriving at the deploy's root belongs on this surface. Core answers `GET /`
    with a redirect to `/surface/<name>`, so the bare host is a door rather than a 404. At most one
    installed surface may claim it — two homes is a pack error, refused at boot."""


def _writeback_due(now: datetime) -> sa.ColumnElement[bool]:
    """A terminal turn whose delivery row is claimable and whose mid-turn replies have all left, so
    the closing reply lands after the replies the turn already spoke rather than over them. A span
    that aged out to `failed` releases the terminal: one undeliverable reply must not silence the
    turn."""
    return sa.and_(
        tables.turn.c.status.in_(TERMINAL_TURN_STATUSES),
        ~sa.exists().where(
            tables.mid_turn_reply.c.turn_id == tables.turn.c.id,
            tables.mid_turn_reply.c.status.in_((WRITEBACK_PENDING, WRITEBACK_CLAIMED)),
        ),
        sa.or_(
            sa.and_(
                tables.writeback.c.status == WRITEBACK_PENDING,
                sa.or_(
                    tables.writeback.c.claim_expires_at.is_(None),
                    tables.writeback.c.claim_expires_at <= now,
                ),
            ),
            sa.and_(
                tables.writeback.c.status == WRITEBACK_CLAIMED,
                tables.writeback.c.claim_expires_at <= now,
            ),
        ),
    )


def writeback_workspaces() -> WorkspaceCandidates:
    """A rotating bounded page of workspace ids holding deliverable writebacks. This is the
    poller's only owner read; every claim, build, credential read, post, attachment, and state
    transition happens after the returned id is bound through the normal workspace boundary."""

    cursor: UUID | None = None

    def due() -> sa.Select[tuple[UUID]]:
        now = datetime.now(UTC)
        query = (
            sa.select(tables.writeback.c.workspace_id)
            .select_from(
                tables.writeback.join(tables.turn, tables.turn.c.id == tables.writeback.c.turn_id)
            )
            .where(_writeback_due(now))
            .group_by(tables.writeback.c.workspace_id)
            .order_by(tables.writeback.c.workspace_id)
            .limit(WRITEBACK_WORKSPACE_BATCH)
        )
        if cursor is not None:
            query = query.where(tables.writeback.c.workspace_id > cursor)
        return query

    read_due = owner_candidates(due)

    async def candidates() -> tuple[UUID, ...]:
        nonlocal cursor
        workspace_ids = await read_due()
        if not workspace_ids and cursor is not None:
            cursor = None
            workspace_ids = await read_due()
        if workspace_ids:
            cursor = workspace_ids[-1]
        return workspace_ids

    return candidates


class _WritebackClaimLost(RuntimeError):
    pass


class _WritebackDeliveryFailed(RuntimeError):
    def __init__(self, phase: Literal["post", "attach"], error: Exception) -> None:
        self.phase = phase
        self.error = error
        super().__init__(str(error) or type(error).__name__)


@dataclass(frozen=True)
class WritebackPoller:
    """Durable, at-least-once delivery across every registered surface. The hub is lossy, so a reply
    is never posted from a live frame: this poller claims writebacks whose turn reached a terminal
    state, dispatches each to its surface (by the conversation's surface), records the reply ref in
    its own commit before marking delivered, and retries a failed post with backoff until it ages
    out and is terminally failed — so an undeliverable reply neither hot-loops nor lingers. A claim
    (a worker id plus an expiry) is safe under concurrent instances: Postgres skips a peer's locked
    rows, SQLite's single writer serializes them, and a compare-and-swap on the owner means only the
    worker still holding the claim advances it. The worker refreshes its lease while external
    delivery is live; a crash after the ref is recorded resumes attachment delivery without
    re-posting. Attachments are at-least-once and can repeat after a crash between upload and the
    delivered commit. A reply can repeat only after a crash between a successful post and its ref
    commit — the trade is guaranteed delivery over a never-doubled one."""

    worker_id: str
    surfaces: Mapping[str, SurfaceSpec]
    context_for: SurfaceContextFactory
    candidates: WorkspaceCandidates

    async def run(self) -> None:
        semaphore = asyncio.Semaphore(WRITEBACK_WORKSPACE_CONCURRENCY)
        in_flight: dict[UUID, asyncio.Task[None]] = {}
        try:
            while True:
                for workspace_id, task in tuple(in_flight.items()):
                    if not task.done():
                        continue
                    del in_flight[workspace_id]
                    if task.cancelled():
                        continue
                    error = task.exception()
                    if error is not None:
                        log(
                            "surface.writeback_workspace_failed",
                            workspace_id=str(workspace_id),
                            error_class=type(error).__name__,
                        )
                if len(in_flight) <= WRITEBACK_WORKSPACE_IN_FLIGHT - WRITEBACK_WORKSPACE_BATCH:
                    try:
                        workspace_ids = await self.candidates()
                    except Exception as error:
                        log("surface.writeback_drain_failed", error_class=type(error).__name__)
                    else:
                        for workspace_id in workspace_ids:
                            if workspace_id not in in_flight:
                                in_flight[workspace_id] = asyncio.create_task(
                                    self._drain_workspace(workspace_id, semaphore)
                                )
                await asyncio.sleep(WRITEBACK_POLL_SECONDS)
        finally:
            for task in in_flight.values():
                task.cancel()
            await asyncio.gather(*in_flight.values(), return_exceptions=True)

    async def drain(self) -> None:
        workspace_ids = await self.candidates()
        semaphore = asyncio.Semaphore(WRITEBACK_WORKSPACE_CONCURRENCY)
        results = await asyncio.gather(
            *(self._drain_workspace(workspace_id, semaphore) for workspace_id in workspace_ids),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, Exception)]
        if errors:
            raise ExceptionGroup("writeback workspace drains failed", errors)

    async def _drain_workspace(self, workspace_id: UUID, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            with ws(workspace_id):
                rows = await self._claim(workspace_id)
                renewals = [asyncio.create_task(self._renew_claim(row.turn_id)) for row in rows]
                try:
                    for row, renewal in zip(rows, renewals, strict=True):
                        if row.last_error is not None:
                            log(
                                "surface.writeback_retry",
                                turn_id=str(row.turn_id),
                                last_error=row.last_error,
                            )
                        await self._deliver(workspace_id, row.turn_id, row.reply_ref, renewal)
                finally:
                    for renewal in renewals:
                        if not renewal.done():
                            renewal.cancel()
                    await asyncio.gather(*renewals, return_exceptions=True)

    async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]:
        now = datetime.now(UTC)
        claimable = (
            sa.select(tables.writeback.c.turn_id)
            .select_from(
                tables.writeback.join(tables.turn, tables.turn.c.id == tables.writeback.c.turn_id)
            )
            .where(
                tables.writeback.c.workspace_id == workspace_id,
                _writeback_due(now),
            )
            .order_by(tables.writeback.c.created_at)
            .limit(WRITEBACK_CLAIM_BATCH)
            .with_for_update(skip_locked=True, of=tables.writeback)
            .cte("claimable")
        )
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.update(tables.writeback)
                    .where(tables.writeback.c.turn_id == claimable.c.turn_id)
                    .values(
                        status=WRITEBACK_CLAIMED,
                        claimed_by=self.worker_id,
                        claim_expires_at=now + timedelta(seconds=WRITEBACK_CLAIM_SECONDS),
                        updated_at=sa.func.now(),
                    )
                    .returning(
                        tables.writeback.c.turn_id,
                        tables.writeback.c.reply_ref,
                        tables.writeback.c.last_error,
                    )
                )
            ).all()

    async def _deliver(
        self,
        workspace_id: UUID,
        turn_id: UUID,
        reply_ref: str | None,
        renewal: asyncio.Task[None],
    ) -> None:
        started_at = datetime.now(UTC)
        try:
            await self._deliver_with_lease(workspace_id, turn_id, reply_ref, renewal)
        except _WritebackClaimLost:
            log("surface.writeback_claim_lost", turn_id=str(turn_id))
        except _WritebackDeliveryFailed as error:
            outcome, last_error, next_attempt_at = await self._fail_or_retry(turn_id, error)
            if outcome == "claim_lost":
                log("surface.writeback_claim_lost", turn_id=str(turn_id))
                return
            log(
                "surface.writeback_failed",
                turn_id=str(turn_id),
                phase=error.phase,
                outcome=outcome,
                error_class=type(error.error).__name__,
                last_error=last_error,
                next_attempt_at=next_attempt_at,
                elapsed_ms=int((datetime.now(UTC) - started_at).total_seconds() * 1_000),
            )
        else:
            log(
                "surface.writeback_delivered",
                turn_id=str(turn_id),
                elapsed_ms=int((datetime.now(UTC) - started_at).total_seconds() * 1_000),
            )

    async def _deliver_with_lease(
        self,
        workspace_id: UUID,
        turn_id: UUID,
        reply_ref: str | None,
        renewal: asyncio.Task[None],
    ) -> None:
        """The lease covers exactly the external delivery, and ends before the commit that closes
        it. The renewal and `_mark_delivered` write the same writeback row, so a refresh still in
        flight holds the row lock the commit needs: the lease is stopped and awaited first, and only
        then does the terminal compare-and-swap run — never against this row's own refresher."""
        delivery = asyncio.create_task(self._deliver_claimed(workspace_id, turn_id, reply_ref))
        try:
            done, _pending = await asyncio.wait(
                (delivery, renewal), return_when=asyncio.FIRST_COMPLETED
            )
            if delivery not in done:
                if renewal.cancelled():
                    raise asyncio.CancelledError
                error = renewal.exception()
                if error is None:
                    raise RuntimeError("writeback claim renewal stopped")
                raise error
            await delivery
        finally:
            for task in (delivery, renewal):
                if not task.done():
                    task.cancel()
            await asyncio.gather(delivery, renewal, return_exceptions=True)
        await self._mark_delivered(turn_id)

    async def _deliver_claimed(
        self, workspace_id: UUID, turn_id: UUID, reply_ref: str | None
    ) -> None:
        writeback, surface_name = await self._build(turn_id)
        entry = self.surfaces.get(surface_name)
        if entry is None:
            log("surface.writeback_no_surface", turn_id=str(turn_id), surface=surface_name)
            return
        spec = entry
        if spec.post is None or spec.attach is None:
            log("surface.writeback_no_delivery", turn_id=str(turn_id), surface=surface_name)
            return
        context = self.context_for(workspace_id, surface_name)
        if reply_ref is None:
            try:
                posted = await spec.post(context, writeback)
            except Exception as error:
                raise _WritebackDeliveryFailed("post", error) from error
            if isinstance(posted, NothingDelivered):
                log(
                    "surface.writeback_nothing_delivered",
                    turn_id=str(turn_id),
                    surface=surface_name,
                )
                return
            reply_ref = posted
            await self._record_ref(turn_id, reply_ref)
        try:
            await spec.attach(context, writeback, reply_ref)
        except Exception as error:
            raise _WritebackDeliveryFailed("attach", error) from error

    async def _renew_claim(self, turn_id: UUID) -> None:
        while True:
            await asyncio.sleep(WRITEBACK_CLAIM_REFRESH_SECONDS)
            await self._refresh_claim(turn_id)

    async def _refresh_claim(self, turn_id: UUID) -> None:
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            renewed = await connection.execute(
                sa.update(tables.writeback)
                .where(
                    tables.writeback.c.turn_id == turn_id,
                    tables.writeback.c.status == WRITEBACK_CLAIMED,
                    tables.writeback.c.claimed_by == self.worker_id,
                )
                .values(
                    claim_expires_at=now + timedelta(seconds=WRITEBACK_CLAIM_SECONDS),
                    updated_at=sa.func.now(),
                )
            )
        if renewed.rowcount != 1:
            raise _WritebackClaimLost(str(turn_id))

    async def _build(self, turn_id: UUID) -> tuple[Writeback, str]:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.terminal,
                        tables.turn.c.conversation_id,
                        tables.turn.c.agent_id,
                        tables.conversation.c.queue_key,
                        tables.conversation.c.surface,
                    )
                    .select_from(
                        tables.turn.join(
                            tables.conversation,
                            tables.conversation.c.id == tables.turn.c.conversation_id,
                        )
                    )
                    .where(tables.turn.c.id == turn_id)
                )
            ).one()
            artifacts = (
                await connection.execute(
                    sa.select(
                        tables.shared_artifact.c.blob_key,
                        tables.shared_artifact.c.filename,
                        tables.shared_artifact.c.subject,
                        tables.shared_artifact.c.media_type,
                        tables.shared_artifact.c.size_bytes,
                    )
                    .where(tables.shared_artifact.c.turn_id == turn_id)
                    .order_by(
                        tables.shared_artifact.c.created_at, tables.shared_artifact.c.blob_key
                    )
                )
            ).all()
        writeback = Writeback(
            turn_id=turn_id,
            conversation_id=row.conversation_id,
            agent_id=row.agent_id,
            queue_key=row.queue_key,
            terminal=TerminalFrame.model_validate(row.terminal),
            artifacts=tuple(
                SharedArtifact(
                    blob_key=artifact.blob_key,
                    filename=artifact.filename,
                    subject=artifact.subject,
                    media_type=artifact.media_type,
                    size_bytes=artifact.size_bytes,
                )
                for artifact in artifacts
            ),
        )
        return writeback, row.surface

    async def _record_ref(self, turn_id: UUID, reply_ref: str) -> None:
        async with workspace_tx() as connection:
            updated = await connection.execute(
                sa.update(tables.writeback)
                .where(
                    tables.writeback.c.turn_id == turn_id,
                    tables.writeback.c.status == WRITEBACK_CLAIMED,
                    tables.writeback.c.claimed_by == self.worker_id,
                )
                .values(reply_ref=reply_ref, updated_at=sa.func.now())
            )
        if updated.rowcount != 1:
            raise _WritebackClaimLost(str(turn_id))

    async def _mark_delivered(self, turn_id: UUID) -> None:
        async with workspace_tx() as connection:
            updated = await connection.execute(
                sa.update(tables.writeback)
                .where(
                    tables.writeback.c.turn_id == turn_id,
                    tables.writeback.c.status == WRITEBACK_CLAIMED,
                    tables.writeback.c.claimed_by == self.worker_id,
                )
                .values(
                    status=WRITEBACK_DELIVERED,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
            )
        if updated.rowcount != 1:
            raise _WritebackClaimLost(str(turn_id))

    async def _fail_or_retry(
        self, turn_id: UUID, error: _WritebackDeliveryFailed
    ) -> tuple[str, str, datetime | None]:
        """Release the row for its next attempt — at the provider's Retry-After when the failure
        carried one, else the fixed backoff — or mark it failed once it outlives the writeback
        window. Both delays are bounded by that window, so a hostile Retry-After cannot park a
        row forever."""
        now = datetime.now(UTC)
        give_up_before = now - timedelta(seconds=WRITEBACK_MAX_AGE_SECONDS)
        match error.error:
            case SurfaceDeliveryError() as delivery_error:
                retry_after_seconds = delivery_error.retry_after_seconds
            case _:
                retry_after_seconds = None
        retry_seconds = min(
            retry_after_seconds
            if retry_after_seconds is not None
            else WRITEBACK_RETRY_BACKOFF_SECONDS,
            WRITEBACK_MAX_AGE_SECONDS,
        )
        retry_detail = (
            f"; retry_after_seconds={retry_after_seconds}"
            if retry_after_seconds is not None
            else ""
        )
        last_error = f"{error}{retry_detail}"[:MAX_WRITEBACK_ERROR_CHARS]
        retry_at = now + timedelta(seconds=retry_seconds)
        terminal = tables.writeback.c.created_at <= give_up_before
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.update(tables.writeback)
                    .where(
                        tables.writeback.c.turn_id == turn_id,
                        tables.writeback.c.claimed_by == self.worker_id,
                    )
                    .values(
                        status=sa.case((terminal, WRITEBACK_FAILED), else_=WRITEBACK_PENDING),
                        claim_expires_at=sa.case((terminal, None), else_=retry_at),
                        claimed_by=None,
                        last_error=last_error,
                        updated_at=sa.func.now(),
                    )
                    .returning(tables.writeback.c.status, tables.writeback.c.claim_expires_at)
                )
            ).one_or_none()
        outcome = (
            "claim_lost" if row is None else "failed" if row.status == WRITEBACK_FAILED else "retry"
        )
        return outcome, last_error, None if row is None else row.claim_expires_at


def mid_turn_reply_workspaces() -> WorkspaceCandidates:
    """A rotating bounded page of workspace ids holding deliverable mid-turn replies. Nothing here
    waits on a terminal status: the turn is still running, which is the whole point of the row."""

    cursor: UUID | None = None

    def due() -> sa.Select[tuple[UUID]]:
        now = datetime.now(UTC)
        query = (
            sa.select(tables.mid_turn_reply.c.workspace_id)
            .where(_mid_turn_reply_due(now))
            .group_by(tables.mid_turn_reply.c.workspace_id)
            .order_by(tables.mid_turn_reply.c.workspace_id)
            .limit(WRITEBACK_WORKSPACE_BATCH)
        )
        if cursor is not None:
            query = query.where(tables.mid_turn_reply.c.workspace_id > cursor)
        return query

    read_due = owner_candidates(due)

    async def candidates() -> tuple[UUID, ...]:
        nonlocal cursor
        workspace_ids = await read_due()
        if not workspace_ids and cursor is not None:
            cursor = None
            workspace_ids = await read_due()
        if workspace_ids:
            cursor = workspace_ids[-1]
        return workspace_ids

    return candidates


def _mid_turn_reply_due(now: datetime) -> sa.ColumnElement[bool]:
    return sa.or_(
        sa.and_(
            tables.mid_turn_reply.c.status == WRITEBACK_PENDING,
            sa.or_(
                tables.mid_turn_reply.c.claim_expires_at.is_(None),
                tables.mid_turn_reply.c.claim_expires_at <= now,
            ),
        ),
        sa.and_(
            tables.mid_turn_reply.c.status == WRITEBACK_CLAIMED,
            tables.mid_turn_reply.c.claim_expires_at <= now,
        ),
    )


@dataclass(frozen=True)
class MidTurnReplyPoller:
    """Exactly-once delivery of replies and source-surface comment notices before a turn ends.

    Three independent guards, because all three failures are real. The engine writes one row per
    span under the span's own identity; admission does the same for one portal comment, so a replay
    or request redelivery inserts nothing. This poller claims a row with its worker id and an expiry
    and renews every row in the batch from the moment it is claimed, including rows waiting behind
    an earlier send. It advances a row only while it still holds the claim, so a second replica
    never delivers the row this one has. The surface keys its own delivery record on `reply.id`,
    which closes the one window where two workers can both call out — a claim lost while a post is
    in flight.

    Order is the model's: rows are claimed and delivered oldest first and, within one moment, in
    span order — a resumed run counts its rounds from one again, so the round and the span alone
    would rank its spans against a parked attempt's by nothing at all. A turn's terminal writeback
    waits behind every span of its own. A surface that declares no `speak` marks its rows delivered
    untouched, because a live surface's member read the reply off the hub as the round produced it
    and there is nothing left to send."""

    worker_id: str
    surfaces: Mapping[str, SurfaceSpec]
    context_for: SurfaceContextFactory
    candidates: WorkspaceCandidates

    async def run(self) -> None:
        while True:
            try:
                await self.drain()
            except Exception as error:
                log("surface.mid_turn_reply_drain_failed", error_class=type(error).__name__)
            await asyncio.sleep(WRITEBACK_POLL_SECONDS)

    async def drain(self) -> None:
        for workspace_id in await self.candidates():
            with ws(workspace_id):
                rows = await self._claim(workspace_id)
                renewals = [asyncio.create_task(self._renew_claim(row.id)) for row in rows]
                try:
                    for row, renewal in zip(rows, renewals, strict=True):
                        if row.last_error is not None:
                            log(
                                "surface.mid_turn_reply_retry",
                                reply_id=str(row.id),
                                last_error=row.last_error,
                            )
                        await self._deliver(workspace_id, row, renewal)
                finally:
                    for renewal in renewals:
                        if not renewal.done():
                            renewal.cancel()
                    await asyncio.gather(*renewals, return_exceptions=True)

    async def _claim(self, workspace_id: UUID) -> Sequence[sa.Row]:
        now = datetime.now(UTC)
        claimable = (
            sa.select(tables.mid_turn_reply.c.id)
            .where(
                tables.mid_turn_reply.c.workspace_id == workspace_id,
                _mid_turn_reply_due(now),
            )
            .order_by(
                tables.mid_turn_reply.c.created_at,
                tables.mid_turn_reply.c.round_index,
                tables.mid_turn_reply.c.span_index,
            )
            .limit(WRITEBACK_CLAIM_BATCH)
            .with_for_update(skip_locked=True, of=tables.mid_turn_reply)
            .cte("claimable")
        )
        async with workspace_tx() as connection:
            claimed = (
                await connection.execute(
                    sa.update(tables.mid_turn_reply)
                    .where(tables.mid_turn_reply.c.id == claimable.c.id)
                    .values(
                        status=WRITEBACK_CLAIMED,
                        claimed_by=self.worker_id,
                        claim_expires_at=now + timedelta(seconds=WRITEBACK_CLAIM_SECONDS),
                        updated_at=sa.func.now(),
                    )
                    .returning(
                        tables.mid_turn_reply.c.id,
                        tables.mid_turn_reply.c.turn_id,
                        tables.mid_turn_reply.c.round_index,
                        tables.mid_turn_reply.c.span_index,
                        tables.mid_turn_reply.c.message_ref,
                        tables.mid_turn_reply.c.text,
                        tables.mid_turn_reply.c.reply_ref,
                        tables.mid_turn_reply.c.last_error,
                        tables.mid_turn_reply.c.created_at,
                    )
                )
            ).all()
        return sorted(claimed, key=lambda row: (row.created_at, row.round_index, row.span_index))

    async def _deliver(self, workspace_id: UUID, row: sa.Row, renewal: asyncio.Task[None]) -> None:
        started_at = datetime.now(UTC)
        try:
            await self._deliver_with_lease(workspace_id, row, renewal)
        except _WritebackClaimLost:
            log("surface.mid_turn_reply_claim_lost", reply_id=str(row.id))
            return
        except Exception as error:
            outcome, last_error, next_attempt_at = await self._fail_or_retry(row.id, error)
            log(
                "surface.mid_turn_reply_failed",
                reply_id=str(row.id),
                turn_id=str(row.turn_id),
                outcome=outcome,
                error_class=type(error).__name__,
                last_error=last_error,
                next_attempt_at=next_attempt_at,
            )
            return
        log(
            "surface.mid_turn_reply_delivered",
            reply_id=str(row.id),
            turn_id=str(row.turn_id),
            message_ref=str(row.message_ref or ""),
            elapsed_ms=int((datetime.now(UTC) - started_at).total_seconds() * 1_000),
        )

    async def _deliver_with_lease(
        self, workspace_id: UUID, row: sa.Row, renewal: asyncio.Task[None]
    ) -> None:
        delivery = asyncio.create_task(self._speak(workspace_id, row))
        try:
            done, _pending = await asyncio.wait(
                (delivery, renewal), return_when=asyncio.FIRST_COMPLETED
            )
            if delivery not in done:
                if renewal.cancelled():
                    raise asyncio.CancelledError
                error = renewal.exception()
                if error is None:
                    raise RuntimeError("mid-turn reply claim renewal stopped")
                raise error
            reply_ref = await delivery
        finally:
            for task in (delivery, renewal):
                if not task.done():
                    task.cancel()
            await asyncio.gather(delivery, renewal, return_exceptions=True)
        await self._mark_delivered(row.id, reply_ref)

    async def _speak(self, workspace_id: UUID, row: sa.Row) -> str | None:
        """The surface's own send, or None when this deploy has nothing to send it with. A recorded
        `reply_ref` means an earlier attempt's post landed and only the commit closing it was lost,
        so the row completes on that reference rather than posting a second message."""
        if row.reply_ref is not None:
            return str(row.reply_ref)
        async with workspace_tx() as connection:
            turn = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.conversation_id,
                        tables.turn.c.agent_id,
                        tables.conversation.c.queue_key,
                        tables.conversation.c.surface,
                    )
                    .select_from(
                        tables.turn.join(
                            tables.conversation,
                            tables.conversation.c.id == tables.turn.c.conversation_id,
                        )
                    )
                    .where(tables.turn.c.id == row.turn_id)
                )
            ).one()
        spec = self.surfaces.get(turn.surface)
        if spec is None:
            log("surface.mid_turn_reply_no_surface", reply_id=str(row.id), surface=turn.surface)
            return None
        if spec.speak is None:
            return None
        return await spec.speak(
            self.context_for(workspace_id, turn.surface),
            MidTurnReply(
                id=row.id,
                turn_id=row.turn_id,
                conversation_id=turn.conversation_id,
                agent_id=turn.agent_id,
                queue_key=turn.queue_key,
                message_ref=row.message_ref,
                text=row.text,
                is_comment=row.round_index == SURFACE_COMMENT_ROUND_INDEX,
            ),
        )

    async def _renew_claim(self, reply_id: UUID) -> None:
        while True:
            await asyncio.sleep(WRITEBACK_CLAIM_REFRESH_SECONDS)
            await self._refresh_claim(reply_id)

    async def _refresh_claim(self, reply_id: UUID) -> None:
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            renewed = await connection.execute(
                sa.update(tables.mid_turn_reply)
                .where(
                    tables.mid_turn_reply.c.id == reply_id,
                    tables.mid_turn_reply.c.status == WRITEBACK_CLAIMED,
                    tables.mid_turn_reply.c.claimed_by == self.worker_id,
                )
                .values(
                    claim_expires_at=now + timedelta(seconds=WRITEBACK_CLAIM_SECONDS),
                    updated_at=sa.func.now(),
                )
            )
        if renewed.rowcount != 1:
            raise _WritebackClaimLost

    async def _mark_delivered(self, reply_id: UUID, reply_ref: str | None) -> None:
        async with workspace_tx() as connection:
            updated = await connection.execute(
                sa.update(tables.mid_turn_reply)
                .where(
                    tables.mid_turn_reply.c.id == reply_id,
                    tables.mid_turn_reply.c.status == WRITEBACK_CLAIMED,
                    tables.mid_turn_reply.c.claimed_by == self.worker_id,
                )
                .values(
                    status=WRITEBACK_DELIVERED,
                    reply_ref=reply_ref,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
            )
        if updated.rowcount != 1:
            raise _WritebackClaimLost

    async def _fail_or_retry(
        self, reply_id: UUID, error: Exception
    ) -> tuple[str, str, datetime | None]:
        """Release the row for its next attempt — at the provider's Retry-After when the failure
        carried one, else the fixed backoff — or fail it once it outlives the delivery window. A
        failed span stops holding the turn's terminal reply back."""
        now = datetime.now(UTC)
        give_up_before = now - timedelta(seconds=WRITEBACK_MAX_AGE_SECONDS)
        match error:
            case SurfaceDeliveryError() as delivery_error:
                retry_after_seconds = delivery_error.retry_after_seconds
            case _:
                retry_after_seconds = None
        retry_seconds = min(
            retry_after_seconds
            if retry_after_seconds is not None
            else WRITEBACK_RETRY_BACKOFF_SECONDS,
            WRITEBACK_MAX_AGE_SECONDS,
        )
        last_error = (str(error) or type(error).__name__)[:MAX_WRITEBACK_ERROR_CHARS]
        aged_out = tables.mid_turn_reply.c.created_at <= give_up_before
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.update(tables.mid_turn_reply)
                    .where(
                        tables.mid_turn_reply.c.id == reply_id,
                        tables.mid_turn_reply.c.claimed_by == self.worker_id,
                    )
                    .values(
                        status=sa.case((aged_out, WRITEBACK_FAILED), else_=WRITEBACK_PENDING),
                        claim_expires_at=sa.case(
                            (aged_out, None), else_=now + timedelta(seconds=retry_seconds)
                        ),
                        claimed_by=None,
                        last_error=last_error,
                        updated_at=sa.func.now(),
                    )
                    .returning(
                        tables.mid_turn_reply.c.status,
                        tables.mid_turn_reply.c.claim_expires_at,
                    )
                )
            ).one_or_none()
        outcome = (
            "claim_lost" if row is None else "failed" if row.status == WRITEBACK_FAILED else "retry"
        )
        return outcome, last_error, None if row is None else row.claim_expires_at
