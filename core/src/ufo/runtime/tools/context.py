"""The capability-scoped view a tool handler receives, and the result it returns.

`ToolResult` is what a handler answers with; `ToolFailure` is the one shape it answers with when
it fails — a nonempty summary, what was being attempted, the effects that already landed, and the
bounded diagnostics of the shell step or third party that refused. A failure that carries none of
that reads to the model as a failure that did nothing, which is the one thing it never means.

A handler reaches the outside world only through the fields here: the sandbox for filesystem and
shell, the blob store for artifacts, the turn/agent it runs under, `spawn` to delegate a typed
subtask to a child turn, the message requester who gates private authorization, the exact
conversation audience that scopes disclosure, and `artifact_token_secret` with which
`share_file` mints the signed download URLs the web surface verifies. `touched_paths` is the
working set that lets `edit` refuse to touch a file the turn has neither read nor named in a bash
command. `connector_account` hands
a connector tool the broker's connected-account id it passes to the broker's server-side execute
API, resolved from the turn-agent's grants admitted to the requester (their own plus shared)
so a tool reaches only the accounts its requester may use. `skills` is the loadable
skill set for the deploy (core plus the active packs') that `load_skill` resolves against; it
defaults to the core floor so a context built without the loader still resolves the core three.
`loaded_skills` is the turn's live record of which of those workflows the context already holds, so
a repeat `load_skill` re-mounts the files without injecting the instructions twice.
`cdp_provider` is the turn's selected browser transport and `find` its host-side element-ranking
hook — the browser tools build one per-turn surface from them on first use and register its `aclose`
on `cleanup`, the per-turn registry the loop drains at turn end so a CDP connection never outlives
its turn. `search_provider` is the deploy's selected web-search backend (None when no research
extension is active) — the research tools call it host-side, so the provider reads its key in the
serve process and the sandbox never sees it. `idempotency_key` is `{turn}/{call}/{call_id}` where
`call` is the semantic identity — a tool's name, or a bound action's canonical
`action:<kind>:<name>` — folded
on only for a `side_effecting` tool: its dedup key against a cross-attempt resume — an external
write's header, a spawned child's identity — so the effect applies at most once; a read tool gets
`None`. `target` is the resolved `ObjectActionTarget` a dispatched object action acts on, folded on
exactly as the idempotency key is; a global call carries `None`. `granted_actions` is the set of
canonical action ids this turn's agent or profile holds — what the object verbs filter discovery
views by.
`meter_images` and `meter_videos` book what a paid image or video generation cost onto this
turn's ledger: metering is core's, so a provider extension prices its own call and writes it through
here. `store_preview` takes a picture a tool rendered inside the sandbox into the artifact
namespace, which core alone names; `render_site_preview` instead gives the preview service a
core-minted view of one hosted port and the resulting picture's one-key store capability. An
extension tool also gets `ext`, its owning extension's workspace-scoped ExtensionContext; a builtin
tool gets `ext=None`."""

import asyncio
import json
import shlex
from base64 import b64encode
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import sqlalchemy as sa
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.blob import FilesystemBlobStore, S3BlobStore, WorkspaceBlobStore
from ufo.browser import CdpProvider, FindCompleter
from ufo.db import workspace_tx
from ufo.harness.context import ContextRemaining
from ufo.harness.models.interface import AUTO_MODEL
from ufo.harness.models.pricing import ModelPrice, pricing_from
from ufo.harness.models.spec import ModelSpec
from ufo.harness.o11y import log
from ufo.harness.sandbox.session import Sandbox, shell_path
from ufo.runtime.access.connectors import ConnectorRegistry
from ufo.runtime.access.credentials import CredentialRequests
from ufo.runtime.access.grants import (
    ConnectUnavailable,
    Grant,
    GrantStore,
)
from ufo.runtime.access.member_authorization import AuthorizationBinding
from ufo.runtime.billing.accounting import UNGATED_LEDGER, Ledger
from ufo.runtime.ext.source_reader import SourceReader
from ufo.runtime.hub import SourceRef
from ufo.runtime.media.artifact_url import ARTIFACT_KEY_PREFIX, artifact_media_type
from ufo.runtime.media.image_previews import IMAGE_PREVIEW_MAX_BYTES, raster_image_media_type
from ufo.runtime.media.previews import StoredPreview
from ufo.runtime.media.site_previewer import SitePreviewer
from ufo.runtime.object_name import ObjectRef
from ufo.runtime.object_scope import ObjectActionTarget
from ufo.runtime.search import SearchProvider
from ufo.runtime.seats import member_is_admin
from ufo.runtime.skills.runtime import CORE_SKILL_REGISTRY, LoadedSkills, SkillRegistry
from ufo.runtime.turns.audience import (
    FOREIGN_AUDIENCE_PREFIX,
    Audience,
    audience_subjects,
)
from ufo.runtime.turns.contracts import ValidatedJson
from ufo.runtime.turns.subjects import member_subject
from ufo.schema import tables
from ufo.schema.records import Agent, AgentVisibility, TerminalFrame, Turn, Usage

if TYPE_CHECKING:
    from ufo.runtime.access.workspace_slots import WorkspaceSlots
    from ufo.runtime.ext.context import ExtensionContext

SHARED_BYTES_LIMIT = 256 * 1024
SHARED_STREAM_LIMIT = 1024 * 1024 * 1024
SHARE_PREFLIGHT_TIMEOUT_SECONDS = 300
SHA256_DIGEST_PREFIX = "sha256:"
ARTIFACT_PUT_MAX_BYTES = 5 * 1024 * 1024 * 1024
ARTIFACT_PUT_TTL_SECONDS = 900
ARTIFACT_PUT_TIMEOUT_SECONDS = 900
SHARE_PREFLIGHT_CMD = (
    "p={path}\n"
    '[ -f "$p" ] || {{ printf %s "$p is not a regular file" >&2; exit 1; }}\n'
    'size=$(wc -c < "$p" | tr -d " ") || exit 1\n'
    'digest=$(openssl dgst -sha256 "$p") || exit 1\n'
    "digest=${{digest##* }}\n"
    'kept=$(head -c 4096 "$p" | tr -d "\\000" | wc -c | tr -d " ")\n'
    'seen=$(head -c 4096 "$p" | wc -c | tr -d " ")\n'
    'text=true; [ "$kept" = "$seen" ] || text=false\n'
    'printf \'{{"size":%d,"digest":"sha256:%s","is_text":%s}}\' "$size" "$digest" "$text"\n'
)
"""Measure a produced file's size, sha256 and text-ness with tools every carrier has — `wc`,
`openssl`, `head`, `tr` — so the same one command runs in the container and on a member's own
machine, where no baked `ufo` client or usable `python3` exists. The size and digest bind the S3
presigned PUT (§`store_artifact`), so a file changing between the measure and the upload fails at
S3 rather than landing as a self-consistent lie."""
PREVIEW_SIZE_TIMEOUT_SECONDS = 30
PREVIEW_PUT_TTL_SECONDS = 900
PREVIEW_PUT_TIMEOUT_SECONDS = 300
PREVIEW_DETAIL_CHARS = 500


class TextContent(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageContent(BaseModel):
    """A tool's visual output — the model sees the image, not a base64 string. `data` is base64;
    the engine folds it into the tool_result's ImageBlock the model client puts on the wire."""

    type: Literal["image"] = "image"
    media_type: str
    data: str


ContentBlock = Annotated[TextContent | ImageContent, Field(discriminator="type")]


class UntrustedContentError(Exception):
    """An error whose message embeds untrusted (page-derived, third-party) content — a subagent
    with `untrusted_output` returning output that fails validation. The engine walls the message
    exactly as it walls an untrusted result, so the content never reaches the model as
    instructions."""


class UnknownSubagentProfile(Exception):
    """A registry lookup named a profile it does not hold. Its message names the bad profile and
    lists the registered profile names, so the spawning tool surfaces an error the model retries
    against a valid name instead of dead-ending on a bare KeyError. The same two facts ride as
    fields, so a failed setup names which profile failed in telemetry that carries no exception
    message."""

    def __init__(self, requested: str, registered: tuple[str, ...]) -> None:
        super().__init__(
            f"unknown subagent profile {requested!r}; valid profiles are: {', '.join(registered)}"
        )
        self.requested = requested
        self.registered = registered


class SpawnPayloadRejected(Exception):
    """A spawn named a valid target and a payload its contract refuses. Its message names the
    target, the keys that target takes, and what was wrong, so the model repairs the call it made
    instead of reading the child contract's own error — which names a type the caller never sees,
    reports the value the tool defaulted to rather than the one the caller sent, and so reads as a
    fault in the deploy. This is the payload half of the decision `UnknownSpawnTarget` already
    makes for the target: a shape mistake is recoverable, and the refusal carries what to fix."""

    def __init__(self, target: str, keys: str, detail: str) -> None:
        super().__init__(f"spawn payload refused by {target!r}, which takes {keys}: {detail}")
        self.target = target
        self.keys = keys
        self.detail = detail


class SpawnModelRejected(Exception):
    """A spawn asked for a model its child cannot be run on. The pin reaches the child turn's
    runtime config, which the child's every setup reads, so it is decided where the caller can
    still repair the call: an id the registry does not serve names the ids it does serve, and a
    turn tree the member already pinned says whose choice it keeps — a pin silently dropped is what
    makes a caller believe it ran a model it never ran."""

    def __init__(self, message: str, requested: str) -> None:
        super().__init__(message)
        self.requested = requested

    @classmethod
    def unknown(cls, requested: str, models: tuple[str, ...]) -> "SpawnModelRejected":
        return cls(
            f"unknown model {requested!r} for a spawn; this deploy serves: "
            f"{', '.join(sorted(models)) or 'none'}",
            requested,
        )

    @classmethod
    def pinned_tree(cls, requested: str, pinned: str) -> "SpawnModelRejected":
        return cls(
            f"this turn tree is pinned to model {pinned!r}, which every agent and profile in it "
            f"runs on — a spawn cannot move a child to {requested!r}; spawn it without a model",
            requested,
        )


class UnknownSpawnTarget(Exception):
    """A spawn named a target neither namespace holds. Its message lists what is spawnable — the
    registered profiles and the workspace's agents — so the model retries against a valid name."""

    def __init__(self, requested: str, profiles: tuple[str, ...], agents: tuple[str, ...]) -> None:
        super().__init__(
            f"unknown spawn target {requested!r}; profiles: {', '.join(profiles) or 'none'}; "
            f"agents: {', '.join(agents) or 'none'}"
        )
        self.requested = requested
        self.profiles = profiles
        self.agents = agents


class AmbiguousSpawnTarget(Exception):
    """A bare spawn target that both a profile and a workspace agent hold. The message names the
    qualified forms so the caller picks one."""

    def __init__(self, requested: str) -> None:
        super().__init__(
            f"spawn target {requested!r} names both a profile and an agent; "
            f"use 'profile:{requested}' or 'agent:{requested}'"
        )
        self.requested = requested


TOOL_COMPLETION_MAX_CHARS = 20_000


class ToolResult(BaseModel):
    """A handler result. Successful completion supplies the member-facing final reply;
    structured profiles keep their output contract. `created` names every object the call brought
    into being, whichever tool ran the verb, so the turn records the creation from the result
    itself rather than from the text one tool happens to print."""

    completion: str | None = Field(default=None, min_length=1, max_length=TOOL_COMPLETION_MAX_CHARS)
    content: tuple[ContentBlock, ...]
    is_error: bool = False
    untrusted: bool = False
    sources: tuple[SourceRef, ...] = ()
    created: tuple[ObjectRef, ...] = ()


RESULT_CUT_MARKER = "\n…["
TRUNCATION_NOTICE = RESULT_CUT_MARKER + "truncated {dropped} of {total} chars]"
FAILURE_SUMMARY_MAX_CHARS = 2_000
FAILURE_STREAM_MAX_CHARS = 4_000
FAILURE_PROVIDER_MAX_CHARS = 4_000
FAILURE_APPLIED_MAX = 64
NO_REASON_NOTICE = "the tool failed and recorded no reason"


def clipped(value: str, limit: int) -> str:
    """One announcement of a cut, wherever the model meets one: the whole result's bound in the
    engine and each diagnostic field's bound here, so a cut always reads the same and the count it
    names is always the count of what was dropped."""
    if len(value) <= limit:
        return value
    return value[:limit] + TRUNCATION_NOTICE.format(dropped=len(value) - limit, total=len(value))


class CommandDiagnostics(BaseModel):
    """What a command that failed actually said. The exit code alone cannot separate broken work
    from an expired budget — code running `timeout` exits 124 exactly as a carrier-stopped run
    does — so `timed_out_after_s` rides beside it. Both streams are kept because a build writes
    its reason to stdout as readily as to stderr, and each is clipped here rather than by its
    caller: a handler holding the bytes hands them over whole and one rule decides what the model
    can afford to read."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out_after_s: int | None = None

    @field_validator("stdout", "stderr")
    @classmethod
    def _bound_stream(cls, value: str) -> str:
        return clipped(value, FAILURE_STREAM_MAX_CHARS)


class AppliedEffect(BaseModel):
    """One effect a failing call had already applied when it failed. `identity` is what the agent
    addresses that effect by — a spawned child's turn id, a created tab's index, an action's place
    in the batch it was taking — and `state` is what became of it. Without both, an agent reading a
    mid-flight failure cannot tell a retry that resumes from one that duplicates."""

    kind: str
    identity: str
    state: str


class ToolFailure(BaseModel):
    """The one shape a tool returns when it fails.

    `summary` says what went wrong in the agent's terms and is never empty — an empty one becomes
    a notice saying so, because "" and "no reason was recorded" read identically to the model and
    only the second is true. `operation` names what was attempted, which the tool name alone stops
    saying once a handler runs several steps. `applied` carries the effects that already landed, so
    a failure part-way through a batch, a fan-out, or a deploy hands back what a retry must not
    repeat. `command` preserves a shell step's exit code, both streams and its timeout state, and
    `provider` carries a third party's own structured error, serialized by its caller and
    clipped here — text, because a nested structure has no length to bound by, and read as
    data, since the words are theirs and not ours."""

    operation: str
    summary: str
    applied: tuple[AppliedEffect, ...] = ()
    command: CommandDiagnostics | None = None
    provider: str | None = None

    @field_validator("summary")
    @classmethod
    def _nonempty_summary(cls, value: str) -> str:
        return clipped(value.strip() or NO_REASON_NOTICE, FAILURE_SUMMARY_MAX_CHARS)

    @field_validator("applied")
    @classmethod
    def _bound_applied(cls, value: tuple[AppliedEffect, ...]) -> tuple[AppliedEffect, ...]:
        return value[:FAILURE_APPLIED_MAX]

    @field_validator("provider")
    @classmethod
    def _bound_provider(cls, value: str | None) -> str | None:
        return None if value is None else clipped(value, FAILURE_PROVIDER_MAX_CHARS)

    def result(self, *, untrusted: bool = False) -> ToolResult:
        return ToolResult(
            content=(TextContent(text=self.model_dump_json(exclude_none=True)),),
            is_error=True,
            untrusted=untrusted,
        )


class SpeakerRequired(ValueError):
    """A handler refused because no member is bound to the call."""


ADMIN_GATE_NEEDS_A_SPEAKER = (
    "this call names no member, so name the member who is asking with `requested_by` — the act "
    "then answers to its own gate: {gate}"
)

CREDENTIAL_AUTHORIZATION_GATE = "a workspace admin authorizes a credential slot"

CALL_NEEDS_A_SPEAKER = (
    "this call names no member, so name the member who is asking with `requested_by`"
)


@dataclass(frozen=True)
class SpawnResult:
    """The exact child and, once finished, its terminal and validated output — a profile child's
    pydantic model, or an agent child's contract-validated JSON. A background spawn has neither
    terminal nor output yet; a child that ended asking has a terminal carrying its question and no
    output. `untrusted` carries a profile's `untrusted_output` declaration, so the returning tool
    result is walled as data.

    `detached_on_arrival` marks a wait a member message ended: the child was moved to the
    background and keeps running, so this result carries its identity in place of the output the
    caller waited for, and the child delivers that output to the conversation itself."""

    turn_id: UUID
    conversation_id: UUID
    output: BaseModel | ValidatedJson | None
    terminal: TerminalFrame | None = None
    untrusted: bool = False
    detached_on_arrival: bool = False


@dataclass(frozen=True)
class SubagentStatus:
    """The terminal state of one already-spawned child turn as the lifecycle tools report it: its
    turn id, terminal status, and its final answer text (the profile's JSON output when it ended
    `done`, otherwise the terminal message). `untrusted` carries the profile's `untrusted_output`
    declaration."""

    turn_id: UUID
    status: str
    text: str
    untrusted: bool = False


class Spawn(Protocol):
    """Delegate a subtask to a named target — a subagent profile or a workspace agent: validate
    the payload against the target's input contract, run a child turn, and (foreground) return its
    contract-validated output. A bare target name is resolved across both namespaces; the
    `profile:`/`agent:` qualified forms are exact, and host-side callers use them.

    `dedup_key` makes the child's identity deterministic from the parent turn and the key rather
    than random, so a caller that re-runs on crash recovery (a dispatch step dying mid-await, a
    fanned-out `wide_*` step re-executing) reconnects to the child it already spawned instead of
    respawning it — the same key yields the same child turn, its admit is idempotent, and a child
    that already finished is awaited, not recomputed. Every tool-step caller derives its key from
    `ctx.idempotency_key`: the recorded round freezes call ids, so distinct model calls still get
    distinct children while a re-executed step reconnects. A keyless spawn mints a fresh child per
    execution — on recovery that is a duplicate doing the same work.

    `delivers_result` says nobody will await this child: it hands its own output to the parent's
    conversation when it finishes. A caller that awaits — foreground, or background bounded by its
    own timeout — leaves it false, or the parent reads the same answer twice.

    `detach_on_arrival` makes a foreground wait interruptible by a member message arriving on the
    parent's conversation: the child moves to the background and the result says so instead of
    carrying an output. A caller that assembles its own answer out of the output leaves it false —
    it has no way to represent a child that is still running.

    `model` pins the child turn onto one model id, over the target's own model and the deploy's
    default. An id the registry does not serve is refused as `SpawnModelRejected`, and so is any
    pin under a turn tree the member's own admission already pinned: that selection holds for every
    agent and profile in the tree."""

    async def __call__(
        self,
        target: str,
        payload: dict[str, Any],
        background: bool = False,
        dedup_key: str | None = None,
        delivers_result: bool = False,
        name: str = "",
        detach_on_arrival: bool = False,
        model: str | None = None,
        *,
        requester_member_id: UUID | None = None,
        requesting_message_ref: UUID | None = None,
    ) -> SpawnResult: ...


class SubagentControl(Protocol):
    """Operations on already-spawned background subagents, keyed by child turn id. `result` reads
    a finished child's exact terminal and validated output; `wait` bounds a hold inside one tool;
    `cancel` stops a running child; `message` hands a live child an idempotent follow-up at its
    next round, or admits it as an idle child's next turn. A child's output
    otherwise arrives on the parent's conversation when the child ends. Threaded onto ToolContext
    from the same Subagents workflow that backs `spawn`."""

    async def result(self, turn_id: UUID) -> SpawnResult: ...

    async def wait(self, turn_ids: tuple[UUID, ...]) -> tuple[SubagentStatus, ...]: ...

    async def cancel(self, turn_id: UUID) -> SubagentStatus: ...

    async def message(
        self,
        turn_id: UUID,
        text: str,
        dedup_key: str,
        *,
        requesting_message_ref: UUID | None = None,
    ) -> SubagentStatus: ...


class ContextControl(Protocol):
    """The turn's context window, as the context tools reach it: where the window stands against
    its rollover line and the model's hard limit, and the cap a handoff carried across the boundary
    is trimmed to. Backed by whichever strategy the deploy selected — a Protocol here so a tool the
    boundary's own extension ships reaches the live boundary without importing the loop, and so the
    tool layer imports no strategy at all."""

    def remaining(self) -> ContextRemaining: ...

    def handoff_cap(self) -> int: ...

    def checklist_cap(self) -> int: ...


@dataclass(eq=False)
class TurnCleanup:
    """Per-turn async cleanup registry: a tool registers an `aclose` here on first use of a resource
    it opens for the turn (the browser surface's CDP connection, a hosted-session lease), and the
    loop drains it once at turn end — closing in reverse order of registration — so the turn never
    leaks a connection whether it ended done, failed, or cancelled. Identity-keyed (`eq=False`), so
    a tool building a once-per-turn resource can cache it against this registry across the per-call
    context copies dispatch hands it."""

    _closers: list[Callable[[], Awaitable[None]]] = field(default_factory=list)

    def register(self, aclose: Callable[[], Awaitable[None]]) -> None:
        self._closers.append(aclose)

    async def drain(self) -> None:
        while self._closers:
            closer = self._closers.pop()
            try:
                await closer()
            except Exception as error:
                log("turn.cleanup.failed", error=repr(error))


@dataclass(frozen=True)
class ConnectorConnection:
    id: UUID
    grant_id: UUID
    provider: str
    account_id: str
    owner_member_id: UUID | None


@dataclass(frozen=True)
class ConnectorAccount:
    connection_id: UUID
    provider: str
    account_id: str
    owner_email: str | None
    shared: bool


def _speaker_required(
    subject: str, withheld: Sequence[Grant], audience: Audience
) -> SpeakerRequired:
    """Names no owner in an externally-shared channel: the refusal is read back into a room
    another organization sits in."""
    owners = (
        []
        if audience.startswith(FOREIGN_AUDIENCE_PREFIX)
        else sorted({grant.owner_email for grant in withheld if grant.owner_email})
    )
    note = ""
    if owners:
        label = "owner" if len(owners) == 1 else "owners"
        note = f" ({label}: {', '.join(owners)})"
    return SpeakerRequired(
        f"{subject} is a member's private account{note}, and this call does not carry that member"
    )


@dataclass(frozen=True)
class MeasuredFile:
    """A produced file as the sandbox measured it: the size and sha256 an upload is bound to, and
    whether its first window reads as text."""

    size_bytes: int
    digest: str
    is_text: bool


async def measure_file(sandbox: Sandbox, scoped: str) -> MeasuredFile:
    """Measure one workspace file in the sandbox with `SHARE_PREFLIGHT_CMD`. A path with no regular
    file behind it fails loud with the sandbox's own words."""
    preflight = await sandbox.bash(
        SHARE_PREFLIGHT_CMD.format(path=shell_path(scoped)),
        timeout_s=SHARE_PREFLIGHT_TIMEOUT_SECONDS,
    )
    if preflight.exit_code != 0:
        raise RuntimeError(preflight.stderr.strip() or f"artifact preflight failed for {scoped}")
    stat = json.loads(preflight.stdout)
    return MeasuredFile(
        size_bytes=int(stat["size"]), digest=str(stat["digest"]), is_text=bool(stat["is_text"])
    )


async def store_artifact(
    sandbox: Sandbox, blob: WorkspaceBlobStore, scoped: str, key: str, size_bytes: int, digest: str
) -> None:
    """Put the measured file under `key`, by the one route the store offers — the route a shared
    file and a report the closing reply carries by path both take.

    S3: serve mints a presigned PUT bound to `size_bytes` and `digest`, and the sandbox uploads to
    it over the egress proxy — the bytes go sandbox → S3 and never cross this process, and S3
    refuses any body that is not the measured one, so a file still being written between the
    preflight and the upload fails loudly instead of landing as a self-consistent lie. The URL is an
    argv element of one `curl`, which is what the sandbox already does to stage a connector's file
    inputs; binding it to those measurements is what makes holding it worth nothing beyond this one
    upload. A non-2xx carries S3's own error document on stdout, so a failure names its cause.

    Filesystem: there is no URL to sign, so the bytes stream out of the container through the
    carrier and into the store in bounded chunks."""
    match blob.backend:
        case S3BlobStore():
            if size_bytes > ARTIFACT_PUT_MAX_BYTES:
                raise ValueError(
                    f"{scoped} is {size_bytes} bytes; a shared file is capped at "
                    f"{ARTIFACT_PUT_MAX_BYTES} bytes"
                )
            checksum = b64encode(bytes.fromhex(digest.removeprefix(SHA256_DIGEST_PREFIX))).decode()
            url = await blob.presigned_put(key, size_bytes, checksum, ARTIFACT_PUT_TTL_SECONDS)
            put = await sandbox.bash(
                f"curl -sS --fail-with-body -T {shell_path(scoped)} "
                f"-H {shlex.quote(f'x-amz-checksum-sha256: {checksum}')} "
                f"--url {shlex.quote(url)}",
                timeout_s=ARTIFACT_PUT_TIMEOUT_SECONDS,
            )
            if put.exit_code != 0:
                detail = put.stdout.strip() or put.stderr.strip()
                raise RuntimeError(detail or f"uploading {scoped} to the artifact store failed")
        case FilesystemBlobStore():
            await blob.put_stream(key, sandbox.read_file(scoped))


async def _sized_artifact_stream(
    chunks: AsyncIterator[bytes], expected: int
) -> AsyncIterator[bytes]:
    size = 0
    async for chunk in chunks:
        size += len(chunk)
        if size > expected:
            raise ValueError("artifact stream exceeds its declared size")
        yield chunk
    if size != expected:
        raise ValueError("artifact stream does not match its declared size")


@dataclass(frozen=True)
class ToolContext:
    sandbox: Sandbox
    blob: WorkspaceBlobStore
    turn: Turn
    agent: Agent
    spawn: Spawn
    speaker_member_id: UUID | None
    audience: Audience
    artifact_token_secret: str
    requesting_message_ref: UUID | None = None
    other_members_active: bool = False
    """Whether more than one member holds an active message this round — the one fact that says a
    miss on a private account could be corrected by naming another member."""
    member_messages_active: bool = False
    """Whether a member-admitted message is active, including one with no workspace identity."""
    grants: GrantStore | None = None
    subagents: SubagentControl | None = None
    touched_paths: set[str] = field(default_factory=set)
    idempotency_key: str | None = None
    target: ObjectActionTarget | None = None
    granted_actions: frozenset[str] = frozenset()
    skills: SkillRegistry = CORE_SKILL_REGISTRY
    loaded_skills: LoadedSkills = field(default_factory=LoadedSkills)
    ext: "ExtensionContext | None" = None
    cdp_provider: CdpProvider | None = None
    search_provider: SearchProvider | None = None
    connectors: ConnectorRegistry | None = None
    connector_selection: ConnectorConnection | None = None
    connector_binding: AuthorizationBinding | None = None
    connector_read_only: bool = False
    find: FindCompleter | None = None
    requestable_credentials: CredentialRequests | None = None
    workspace_slots: "WorkspaceSlots | None" = None
    """The slots an extension resolves for this workspace alone, which no manifest names — None
    where no installed extension resolves any. A fill seals against the deploy's declarations and
    these together, read live, so a slot declared earlier in this turn is fillable in it."""
    models: tuple[str, ...] = ()
    """The model ids this deploy serves, `auto` first — the closed set a write that stores a
    model must hold to, since an id the registry cannot answer fails at every later turn's
    setup and leaves no member-reachable repair."""
    model_specs: Mapping[str, ModelSpec] = field(default_factory=dict)
    auto_model: str = AUTO_MODEL
    public_base_url: str | None = None
    sign_in_path: str | None = None
    """`[serve] sign_in_path`, or None on a deploy that signs no browser in."""
    page_kit: Path | None = None
    """`[sites] page_kit`, the app-page kit archive, or None on a deploy that builds no app page."""
    site_previewer: SitePreviewer | None = None
    ledger: Ledger = UNGATED_LEDGER
    publish_artifacts: Callable[[], Awaitable[None]] | None = None
    cleanup: TurnCleanup = field(default_factory=TurnCleanup)
    context: ContextControl | None = None
    """The turn's own context window — how much is left and the handoff cap a deliberate reset
    trims to. None outside a turn loop that owns a window."""

    @property
    def effective_audience(self) -> Audience:
        """The exact audience a write belongs to: the conversation's own. What is said in a
        conversation is that conversation's to remember — a workspace conversation remembers for
        the workspace, a private room or Slack Connect channel for itself — and stamping a write
        with the member who happened to be bound would carry it into every other conversation they
        speak in. A member who wants a private note makes it in their own conversation."""
        return self.audience

    @property
    def read_subjects(self) -> frozenset[str]:
        """What the conversation and the member a call acts for may jointly read: the
        conversation's own subjects plus that member's private one. The member contributes only
        their own subject, never the workspace-shared atom their private audience also reads — a
        Slack Connect audience is sealed against internal content, and speaking there does not
        unseal it."""
        subjects = audience_subjects(self.audience)
        acting = self.acting_member_id
        if acting is None:
            return subjects
        return subjects | {member_subject(acting)}

    async def store_preview(
        self, sandbox_path: str, name: str, *, extension: str = "png"
    ) -> StoredPreview | None:
        """Store a picture a tool rendered inside the sandbox as a preview blob, and answer the key
        and size the tool's own row records. None when there is nothing readable at that path.

        The key is core's to name: the signed preview route serves the artifact namespace alone, so
        a picture addressable through it is one core placed there. `extension` is the raster type
        the render actually is, because the key's own suffix is what every reader of it types the
        bytes by. The bytes go straight from the
        sandbox to the store — an S3 store takes them on a presigned PUT core mints for this one
        key, curled from inside the container, and a filesystem dev store takes the same file as a
        stream — so a render never crosses this process. The size is measured in the sandbox before
        either, which is also what proves the render landed.

        A picture is decoration, so an unreadable render is reported and answered with None rather
        than raised: the caller's own work has already succeeded by the time it renders one."""
        sized = await self.sandbox.bash(
            f"wc -c < {shell_path(sandbox_path)}", timeout_s=PREVIEW_SIZE_TIMEOUT_SECONDS
        )
        measured = sized.stdout.strip()
        if sized.exit_code != 0 or not measured.isdigit():
            log(
                "preview.unsized",
                path=sandbox_path,
                detail=(sized.stderr.strip() or measured)[:PREVIEW_DETAIL_CHARS],
            )
            return None
        key = f"{ARTIFACT_KEY_PREFIX}{uuid4()}/{name}.{extension}"
        match self.blob.backend:
            case S3BlobStore():
                put_url = await self.blob.presigned_put_unmeasured(key, PREVIEW_PUT_TTL_SECONDS)
                put = await self.sandbox.bash(
                    f"curl -sS --fail-with-body -T {shell_path(sandbox_path)} "
                    f"--url {shlex.quote(put_url)}",
                    timeout_s=PREVIEW_PUT_TIMEOUT_SECONDS,
                )
                if put.exit_code != 0:
                    log(
                        "preview.put_failed",
                        path=sandbox_path,
                        detail=(put.stdout.strip() or put.stderr.strip())[:PREVIEW_DETAIL_CHARS],
                    )
                    return None
            case _:
                await self.blob.put_stream(key, self.sandbox.read_file(sandbox_path))
        return StoredPreview(blob_key=key, size_bytes=int(measured))

    async def render_site_preview(
        self, name: str, port: int, width: int, height: int
    ) -> StoredPreview | None:
        """Capture this turn's hosted sandbox port through the configured preview service."""
        if self.site_previewer is None:
            return None
        conversation_id = self.turn.sandbox_conversation_id or self.turn.conversation_id
        return await self.site_previewer.render(conversation_id, port, name, width, height)

    def source_reader(self) -> SourceReader:
        """Who is asking for a source's synced pages: this turn's agent, the member the call acts
        for, and what the two may jointly read."""
        return SourceReader(
            agent_id=self.turn.agent_id,
            requesting_member_id=self.acting_member_id,
            subjects=self.read_subjects,
        )

    @property
    def acting_member_id(self) -> UUID | None:
        """The member this call acts for: the speaker bound to it; on a turn nobody spoke in, the
        member the turn acts for; on a turn a member founded, nobody — the engine left the call
        unbound because more than one member is active and none was named."""
        if self.speaker_member_id is not None:
            return self.speaker_member_id
        return self.turn.member_id if self.turn.speaker_member_id is None else None

    async def meter_tokens(
        self,
        call_id: UUID,
        model: str,
        usage: Usage,
        price: ModelPrice,
        *,
        byok: bool,
    ) -> None:
        """Record one provider call with its stable ID, token price, and funding source.

        Repeated writes of the same call do not charge twice. Each actual provider call has a
        distinct ID, including a charged retry.
        """
        async with workspace_tx() as connection:
            await self.ledger.record_turn_usage(
                connection,
                self.turn.workspace_id,
                self.turn.id,
                model,
                usage,
                attempt=f"provider:{call_id}",
                pricing=pricing_from({model: price}),
                byok=byok,
            )

    async def meter_images(self, model: str, images: int, micro_usd: int) -> None:
        """Book a generated image's provider charge onto this turn under the ledger's `images`
        dimension. An image model is priced per image rather than per token and is not in the
        `ModelRegistry`, so the extension that called the provider reads the charge off its own
        response; the ledger write is core's, and doing it here binds the spend to this turn's
        workspace, member and agent exactly as a token burn is bound."""
        async with workspace_tx() as connection:
            await self.ledger.record_image_usage(
                connection, self.turn.workspace_id, self.turn.id, model, images, micro_usd
            )

    async def meter_videos(self, model: str, videos: int, micro_usd: int) -> None:
        """Book a generated video's provider charge onto this turn under the ledger's `videos`
        dimension, on the same terms as `meter_images`: a video model is priced per output second
        rather than per token and is not in the `ModelRegistry`, so the extension that called the
        provider reads the charge off its own response and core binds the spend to this turn."""
        async with workspace_tx() as connection:
            await self.ledger.record_video_usage(
                connection, self.turn.workspace_id, self.turn.id, model, videos, micro_usd
            )

    async def share_artifact(
        self,
        filename: str,
        data: bytes,
        subject: str | None = None,
        *,
        preview: StoredPreview | None = None,
    ) -> None:
        """Hand the member one file this call rendered in-process, as a shared artifact of this
        turn — the same rows `share_file` writes for a produced workspace file, so every surface
        delivers it by the path it already uploads artifacts through. For bytes a tool computed
        itself: nothing runs in the sandbox and nothing is measured there, so the size is bounded
        here at the call. An optional preview is a raster this context already stored from the
        sandbox; the member sees those validated bytes and downloads the original file."""
        if len(data) > SHARED_BYTES_LIMIT:
            raise ValueError(f"shared artifact {filename!r} exceeds {SHARED_BYTES_LIMIT} bytes")
        preview_media_type = None if preview is None else raster_image_media_type(preview.blob_key)
        if preview is not None and (
            preview.size_bytes < 0 or preview.size_bytes > IMAGE_PREVIEW_MAX_BYTES
        ):
            raise ValueError(f"shared artifact preview exceeds {IMAGE_PREVIEW_MAX_BYTES} bytes")
        if preview is not None and (
            not preview.blob_key.startswith(ARTIFACT_KEY_PREFIX) or preview_media_type is None
        ):
            raise ValueError("shared artifact preview is not a bounded stored raster")
        await self._share_artifact(filename, data, len(data), subject, preview)

    async def share_artifact_stream(
        self,
        filename: str,
        chunks: AsyncIterator[bytes],
        size_bytes: int,
        subject: str,
        *,
        is_workspace_export: bool = False,
    ) -> None:
        """Publish a bounded generated file through the artifact delivery path."""
        await self._share_artifact(
            filename, chunks, size_bytes, subject, None, is_workspace_export=is_workspace_export
        )

    async def _share_artifact(
        self,
        filename: str,
        data: bytes | AsyncIterator[bytes],
        size_bytes: int,
        subject: str | None,
        preview: StoredPreview | None,
        *,
        is_workspace_export: bool = False,
    ) -> None:
        preview_media_type = None if preview is None else raster_image_media_type(preview.blob_key)
        artifact_id = (
            uuid5(NAMESPACE_URL, f"{self.idempotency_key}/{filename}")
            if self.idempotency_key is not None
            else uuid4()
        )
        key = f"{ARTIFACT_KEY_PREFIX}{artifact_id}/{filename}"
        async with workspace_tx() as connection:
            recorded = (
                await connection.execute(
                    sa.select(tables.shared_artifact.c.id).where(
                        tables.shared_artifact.c.turn_id == self.turn.id,
                        tables.shared_artifact.c.blob_key == key,
                    )
                )
            ).scalar_one_or_none()
        match data:
            case bytes():
                await self.blob.put(key, data)
            case _:
                if recorded is not None:
                    if self.publish_artifacts is not None:
                        await self.publish_artifacts()
                    return
                if not 0 <= size_bytes <= SHARED_STREAM_LIMIT:
                    raise ValueError(f"shared artifact exceeds {SHARED_STREAM_LIMIT} bytes")
                await self.blob.put_stream(key, _sized_artifact_stream(data, size_bytes))
        now = datetime.now(UTC)
        try:
            async with workspace_tx() as connection:
                insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
                await connection.execute(
                    insert(tables.shared_artifact)
                    .values(
                        id=uuid5(NAMESPACE_URL, key),
                        turn_id=self.turn.id,
                        member_id=self.speaker_member_id,
                        blob_key=key,
                        workspace_id=self.turn.workspace_id,
                        filename=filename,
                        subject=subject,
                        media_type=artifact_media_type(filename),
                        size_bytes=size_bytes,
                        role="file",
                        is_workspace_export=is_workspace_export,
                        preview_blob_key=None if preview is None else preview.blob_key,
                        preview_media_type=preview_media_type,
                        preview_size_bytes=None if preview is None else preview.size_bytes,
                        created_at=now,
                        updated_at=now,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            tables.shared_artifact.c.turn_id,
                            tables.shared_artifact.c.blob_key,
                        ]
                    )
                )
        except asyncio.CancelledError:
            raise
        except BaseException:
            if recorded is None:
                try:
                    await self.blob.delete(key)
                except Exception as error:
                    log(
                        "share_artifact.cleanup_failed",
                        blob_key=key,
                        error_class=type(error).__name__,
                    )
            raise
        if self.publish_artifacts is not None:
            await self.publish_artifacts()

    async def speaker_is_admin(self) -> bool:
        """Whether this call's requesting member is a workspace admin. Workspace-wide acts gate on
        the requester, so a background call cannot exercise admin authority."""
        if self.speaker_member_id is None:
            return False
        async with workspace_tx() as connection:
            return await member_is_admin(
                connection,
                self.turn.workspace_id,
                self.speaker_member_id,
            )

    def require_speaker(self, gate: str = "") -> UUID:
        """The member this call is bound to, or the refusal that names the repair. A handler that
        needs a speaker asks for one thing and receives the id, so the gate is written once here
        rather than as a null test and a hand-raised error in every extension that has one. Naming
        a `gate` states what the member is being asked for; omitted, the refusal asks for the ref
        alone.

        The class is the point: `SpeakerRequired` is what tells the engine to name the active
        member messages, so the model can resubmit the same call carrying `requested_by`."""
        if self.speaker_member_id is None:
            raise SpeakerRequired(
                ADMIN_GATE_NEEDS_A_SPEAKER.format(gate=gate) if gate else CALL_NEEDS_A_SPEAKER
            )
        return self.speaker_member_id

    def require_requesting_message(self) -> UUID:
        """The authenticated message this member-bound call selected."""
        if self.requesting_message_ref is None:
            raise RuntimeError("member-bound call has no requesting message")
        return self.requesting_message_ref

    async def require_speaking_admin(self, gate: str) -> bool:
        """The same answer, with the speaker question asked first. `speaker_is_admin` answers False
        for a call nobody is bound to, so a gate that reads it alone tells a call that merely
        omitted `requested_by` that only an admin may act — a refusal naming a repair the model
        cannot make. Who is speaking is answered before what they may do, the order the object
        kinds' gate keeps, and each gate still raises its own admin refusal on a False.

        `require_speaker` raises that first refusal, so an extension gating on a speaker alone and
        one gating on an admin ask the same helper and read the same words back."""
        self.require_speaker(gate)
        return await self.speaker_is_admin()

    async def agent_is_main(self) -> bool:
        async with workspace_tx() as connection:
            return bool(
                (
                    await connection.execute(
                        sa.select(tables.agent.c.is_main).where(
                            tables.agent.c.id == self.turn.agent_id,
                            tables.agent.c.workspace_id == self.turn.workspace_id,
                        )
                    )
                ).scalar_one_or_none()
            )

    async def agent_visibility(self) -> AgentVisibility:
        async with workspace_tx() as connection:
            stored = (
                await connection.execute(
                    sa.select(tables.agent.c.visibility).where(
                        tables.agent.c.id == self.turn.agent_id,
                        tables.agent.c.workspace_id == self.turn.workspace_id,
                    )
                )
            ).scalar_one()
        if stored not in ("private", "workspace"):
            raise RuntimeError(f"agent visibility {stored!r} is not a level the column serves")
        return stored

    async def begin_credential_authorization(self, slot: str, payload: str) -> str:
        requests, member_id = await self._credential_authorization(slot)
        return requests.authorize(self.turn.workspace_id, member_id, slot, payload)

    async def open_credential_authorization(self, slot: str, sealed: str) -> str:
        requests, member_id = await self._credential_authorization(slot)
        return requests.open_authorization(sealed, self.turn.workspace_id, member_id, slot)

    async def _credential_authorization(self, slot: str) -> tuple[CredentialRequests, UUID]:
        speaker = self.require_speaker(CREDENTIAL_AUTHORIZATION_GATE)
        if self.ext is None or slot not in self.ext.credentials.declared:
            raise ValueError(f"this extension does not declare credential slot {slot!r}")
        if self.requestable_credentials is None:
            raise ValueError("no credential key is configured — this deploy cannot store secrets")
        if not await self.speaker_is_admin():
            raise ValueError("only a workspace admin can authorize credential slots")
        return self.requestable_credentials, speaker

    async def connector_account(self, provider: str, account_id: str | None = None) -> str:
        """The broker's connected-account id a connector tool passes to the broker's server-side
        execute API (the broker holds the account's token and injects it itself, so no sentinel and
        no egress proxy). Resolved strictly from the turn's own workspace and agent, so a tool
        executes only against the turn-agent's accounts, never another agent's. `account_id`
        targets any account this turn may use; omitted, the requested member's own private grants
        are preferred and agent-shared ones are the fallback. With no requested member, only
        agent-shared grants are admitted. An exact connection scope admits precisely its listed
        capabilities, independent of ownership, while preserving the private-before-shared tiers.
        Exactly one account must exist in the winning tier. Fails loud when no grant subsystem is
        configured or the selection is absent or ambiguous. A call
        that misses only because the accounts are other members' private ones raises
        `SpeakerRequired` wherever another member can still be named — a speakerless call, and any
        call in a shared-audience conversation — so the engine can name the member refs a retry may
        carry."""
        return (await self.connector_connection(provider, account_id)).account_id

    async def connector_connection(
        self, provider: str, account_id: str | None = None
    ) -> ConnectorConnection:
        """The exact member-owned connection generation this turn may use. Source registration
        persists its id so disconnecting and reconnecting the same external account cannot revive a
        prior member's sync."""
        if self.connector_selection is not None:
            selected = self.connector_selection
            if selected.provider != provider or account_id not in (None, selected.account_id):
                raise ValueError("the connector call changed after member authorization")
            await self.require_connector_connection(selected)
            return selected
        private, shared, withheld = await self._connector_account_tiers(provider)
        if account_id is not None:
            match = next(
                (grant for grant in (*private, *shared) if grant.account_id == account_id),
                None,
            )
            if match is not None:
                return ConnectorConnection(
                    id=match.connection_id,
                    grant_id=match.id,
                    provider=match.provider,
                    account_id=match.account_id,
                    owner_member_id=match.owner_member_id,
                )
            targeted = [grant for grant in withheld if grant.account_id == account_id]
            if targeted:
                raise _speaker_required(
                    f"{provider!r} account {account_id!r}", targeted, self.audience
                )
            raise ValueError(
                f"no active {provider!r} account {account_id!r} is available to this turn"
            )
        preferred = private or shared
        if not preferred:
            if withheld:
                raise _speaker_required(f"every {provider!r} account", withheld, self.audience)
            raise ValueError(
                f"no {provider!r} account is available to this turn — connect one with "
                "connect_account"
            )
        if len(preferred) > 1:
            raise ValueError(
                f"multiple active {provider!r} accounts; pass account_id as one of "
                f"{[grant.account_id for grant in preferred]!r}"
            )
        match = preferred[0]
        return ConnectorConnection(
            id=match.connection_id,
            grant_id=match.id,
            provider=match.provider,
            account_id=match.account_id,
            owner_member_id=match.owner_member_id,
        )

    async def require_connector_connection(self, selected: ConnectorConnection) -> None:
        """Refuse a connection selection whose exact agent-grant generation is no longer usable by
        this call. A connector call can stage files after selecting its account; this
        last-mile read keeps a revoke, disconnect, regrant, or sharing change during that work from
        reaching the broker as an external side effect."""
        private, shared, _ = await self._connector_account_tiers(selected.provider)
        current = next(
            (
                grant
                for grant in (*private, *shared)
                if grant.id == selected.grant_id
                and grant.connection_id == selected.id
                and grant.account_id == selected.account_id
                and grant.owner_member_id == selected.owner_member_id
            ),
            None,
        )
        if current is None:
            raise ValueError(
                f"the selected {selected.provider!r} connector grant is no longer active"
            )

    async def connector_accounts(self, provider: str) -> tuple[str, ...]:
        """The connected-account ids this call may use for one provider. A requested member's own
        grants and grants shared with the agent are admitted; without one, only shared grants
        are. An exact connection scope instead admits only its listed capabilities."""
        return tuple(
            account.account_id
            for account in await self.usable_connector_accounts()
            if account.provider == provider
        )

    async def usable_connector_accounts(self) -> tuple[ConnectorAccount, ...]:
        """Every connected account this call may discover or select for the member it acts for.
        Owner and sharing metadata describe only those usable accounts; an empty tuple discloses
        none."""
        if self.grants is None:
            return ()
        acting = self.acting_member_id
        granted = [
            grant
            for grant in await self.grants.active_grants()
            if grant.connection_shared or grant.owner_member_id == acting
        ]
        return tuple(
            ConnectorAccount(
                connection_id=grant.connection_id,
                provider=grant.provider,
                account_id=grant.account_id,
                owner_email=grant.owner_email,
                shared=grant.connection_shared,
            )
            for grant in sorted(
                granted,
                key=lambda grant: (grant.provider, grant.account_id, str(grant.connection_id)),
            )
        )

    async def _connector_account_tiers(
        self, provider: str
    ) -> tuple[list[Grant], list[Grant], list[Grant]]:
        if self.grants is None:
            raise ConnectUnavailable("grants unavailable: no credential key configured")
        acting = self.acting_member_id
        granted = [
            grant for grant in await self.grants.active_grants() if grant.provider == provider
        ]
        private = sorted(
            (
                grant
                for grant in granted
                if not grant.connection_shared and grant.owner_member_id == acting
            ),
            key=lambda grant: grant.account_id,
        )
        shared = sorted(
            (grant for grant in granted if grant.connection_shared),
            key=lambda grant: grant.account_id,
        )
        if acting is not None and not self.other_members_active:
            return private, shared, []
        return (
            private,
            shared,
            [
                grant
                for grant in granted
                if not grant.connection_shared and grant.owner_member_id != acting
            ],
        )
