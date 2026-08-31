"""One turn, top to bottom: mark running, load context, model rounds absorbing queued arrivals,
terminal commit.

`run()` is the body of the `turn_workflow` DBOS workflow. Its non-deterministic, side-effecting
units are DBOS steps — each model round (`_stream_once`), each tool dispatch (`_dispatch_step`),
each arrival drain (`_claim_arrivals`), and each compaction (`Compaction._compact`). On a crash the
workflow re-dispatches under the same `workflow_id`: every recorded step replays from DBOS's
`operation_outputs` without re-executing — completed rounds are not re-called, completed tools not
re-applied, drained arrivals not re-consumed — and execution resumes at the first unrecorded step.
The queue claims before loading; setup then reclaims the same attempt while loading context,
attaching the sandbox, and re-deciding spend. Each step is idempotent across replay."""

import asyncio
import json
import time
from base64 import b64decode, b64encode
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from html import escape
from io import BytesIO
from uuid import UUID
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from dbos import DBOS
from dbos._error import DBOSWorkflowCancelledError
from PIL import Image
from pydantic import BaseModel, ValidationError
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.blob import WorkspaceBlobStore
from ufo.browser import CdpProvider
from ufo.db import workspace_tx
from ufo.harness.agent import (
    AgentDefinition as HarnessAgentDefinition,
)
from ufo.harness.agent import (
    AgentEngine,
    RecoverableModelError,
)
from ufo.harness.agent import (
    Image as HarnessImage,
)
from ufo.harness.agent import (
    Message as HarnessMessage,
)
from ufo.harness.agent import (
    ModelRequest as HarnessModelRequest,
)
from ufo.harness.agent import (
    ModelRound as HarnessModelRound,
)
from ufo.harness.agent import (
    PreparedRound as HarnessPreparedRound,
)
from ufo.harness.agent import (
    Reasoning as HarnessReasoning,
)
from ufo.harness.agent import (
    RoundMode as HarnessRoundMode,
)
from ufo.harness.agent import (
    StructuredOutput as HarnessStructuredOutput,
)
from ufo.harness.agent import (
    Text as HarnessText,
)
from ufo.harness.agent import (
    ToolCall as HarnessToolCall,
)
from ufo.harness.agent import (
    ToolDefinition as HarnessToolDefinition,
)
from ufo.harness.agent import (
    ToolResult as HarnessToolResult,
)
from ufo.harness.context import is_context_overflow
from ufo.harness.models.catalog import CORE_PRICING
from ufo.harness.models.interface import (
    AUTO_MODEL,
    ImageBlock,
    ImageSource,
    Message,
    ModelClient,
    ModelRequest,
    ModelResponseTruncated,
    ModelStreamStart,
    ReasoningBlock,
    ReasoningItemBlock,
    RedactedThinkingBlock,
    TextBlock,
    TextDelta,
    ThinkingBlock,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
)
from ufo.harness.models.pricing import Pricing
from ufo.harness.models.spec import ModelSpec, ReasoningSupport
from ufo.harness.o11y import (
    emit_histogram,
    emit_metric,
    emit_up_down_metric,
    formatted_stack,
    log,
    log_error,
    span,
    turn_profile,
)
from ufo.harness.replies import MarkedReply, ReplyRedaction
from ufo.harness.rounds import ModelRoundRunner, RoundEventTypes
from ufo.harness.sandbox.session import TOOL_OUTPUT_DIRNAME, Sandbox
from ufo.harness.sandbox.terminal import TerminalAbsent, TerminalGone
from ufo.harness.untrusted import wall
from ufo.runtime.access.connectors import ConnectorRegistry
from ufo.runtime.access.credentials import CredentialRequests
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.billing.accounting import (
    ALLOW,
    TOKENS_DIMENSION,
    BalanceGate,
    SpendEvaluator,
    TurnCost,
    TurnUsageConflict,
    applicable_caps_absent,
    read_turn_cost,
    record_turn_usage,
)
from ufo.runtime.billing.balance import balance_absent
from ufo.runtime.compaction import Compaction
from ufo.runtime.ext.context import ExtensionContext, SourceReader
from ufo.runtime.ext.hooks import HookChain
from ufo.runtime.ext.manifest import (
    PostToolUse,
    PostToolUseFailure,
    PreToolUse,
    Stop,
    UserPromptSubmit,
)
from ufo.runtime.ext.surface import member_message_text
from ufo.runtime.hub import (
    Absorbed,
    Activity,
    CostTick,
    Hub,
    LiveFrame,
    Parked,
    Reply,
    Resumed,
    SubagentActivity,
    Terminal,
)
from ufo.runtime.media.site_previewer import SitePreviewer
from ufo.runtime.memory import MemorySearch
from ufo.runtime.object_name import ObjectRef
from ufo.runtime.object_scope import ObjectActionTarget
from ufo.runtime.objects import ObjectActionInput, ObjectVerbs
from ufo.runtime.prompts.render import RenderedPrompt
from ufo.runtime.search import SearchProvider
from ufo.runtime.seats import SEAT_REVOKED_MESSAGE, Seats
from ufo.runtime.skills.runtime import CORE_SKILL_REGISTRY, LoadedRef, LoadedSkill, SkillRegistry
from ufo.runtime.tools.bridge import ToolBridgeIntent
from ufo.runtime.tools.context import (
    ImageContent,
    Spawn,
    SpeakerRequired,
    SubagentControl,
    TextContent,
    ToolContext,
    UntrustedContentError,
)
from ufo.runtime.tools.registry import (
    OBJECT_ACTION_TOOL,
    REQUESTED_BY,
    ToolDef,
    ToolRegistry,
)
from ufo.runtime.transcript import Transcript
from ufo.runtime.turns.activity import SKILL_LOAD_TOOL, ActivitySummarizer
from ufo.runtime.turns.audience import Audience, audience_member, audience_subjects
from ufo.runtime.turns.contracts import Contract, freeform_result_contract
from ufo.runtime.turns.delivery_register import DIRECT_PROSE_RESULT_MAX_CHARS
from ufo.runtime.turns.transcript import Conversation
from ufo.runtime.turns.workspace_changes import WorkspaceChangeRecorder, change_targets
from ufo.schema import tables
from ufo.schema.records import (
    CANCELLED,
    FINAL_ACT_FIELDS,
    INTERNAL_ADMISSION,
    LAST_CALL_ACT,
    MEMBER_ADMISSION,
    NON_TERMINAL_STATUSES,
    PARKED,
    PENDING_ACT,
    ROUND_BUDGET_INCOMPLETE,
    RUNNING,
    SCHEDULED_ADMISSION,
    TERMINAL_ERROR_MESSAGE_MAX_CHARS,
    WRITEBACK_PENDING,
    Agent,
    AskUserInput,
    ConnectRequest,
    CredentialRequest,
    IncompleteReason,
    TerminalFrame,
    TerminalStatus,
    ToolIntent,
    Turn,
    TurnAdmissionSource,
    TurnContext,
    Usage,
    mid_turn_reply_id_for,
)

MAX_OUTPUT_TOKENS = 32_768
FIND_MAX_TOKENS = 8_192
MAIN_ROUND_LIMIT = 200
MAX_PARALLEL_TOOL_CALLS = 8
CACHE_5M_SECONDS = 5 * 60
CACHE_1H_SECONDS = 60 * 60
EMPTY_RESPONSE_NUDGE = "Previous model response was empty. Answer now."
MODEL_TRUNCATED_ERROR_CLASS = ModelResponseTruncated.__name__
TRUNCATION_FEEDBACK = (
    "Your previous response exceeded the output budget and was cut off. Produce large content "
    "by writing files with sandbox code or by emitting it in small parts across calls; keep any "
    "single response well under the budget."
)
TRUNCATION_SALVAGE_NOTICE = (
    " The cut-off response (its text and tool-call arguments as raw JSON) was saved to {path} — "
    "read it and salvage what it already contains instead of regenerating it."
)
DENIED_INBOUND_NOTICE = "<denied_member_message>{reason}</denied_member_message>"
REQUESTED_BY_HINT = (
    " Set requested_by to the message_ref of the member who asked; active member messages: {refs}."
)
INTERRUPTED_TURN_NOTICE = (
    "<interrupted_turn>The turn above ended before it answered. Everything it ran is above and "
    "already happened — treat those results as done, and do not repeat them.</interrupted_turn>"
)
INJECTED_CONTEXT = "{content}\n\n<injected_context>\n{injected}\n</injected_context>"
CONTEXT_TIME_FORMAT = "%A %Y-%m-%d %H:%M %Z"
FORCE_FINAL_PROMPT = (
    "You have reached the maximum number of tool-use rounds. Do not call any more tools. "
    "Give your best final answer now using everything gathered so far."
)
TRANSCRIPT_WRITE_ATTEMPTS = 3
TRANSCRIPT_WRITE_RETRY_SECONDS = 0.5
COMMIT_RETRY_INITIAL_SECONDS = 1.0
COMMIT_RETRY_MAX_SECONDS = 30.0
OBJECT_APPLY_TOOL = "object_apply"
FINISH_TOOL = "finish"
FINISH_DESCRIPTION = (
    "End the turn and return your final answer to the parent agent. Call it alone, once the work "
    "is done, without writing a prose answer before it or emitting text alongside it; its input "
    "schema is the output contract. The spawn does not return preceding messages or tool output."
)
FINISH_PROMPT = "End the turn now: call finish with your final answer."
FORCE_FINISH_PROMPT = (
    "You have reached the maximum number of tool-use rounds. Call finish now with your best "
    "final answer from everything gathered so far."
)
FINISH_ALONE = (
    "finish must be the only tool call in its round — finish the other work first, then call it "
    "again alone."
)
FINISH_SCHEMA_ERROR = (
    "finish failed the output schema — fix the payload and call it again:\n{error}"
)

SandboxFor = Callable[[UUID | None], Awaitable[Sandbox]]
SubagentsFor = Callable[[UUID | None], tuple[Spawn, SubagentControl | None]]
SCHEDULED_MEMORY_CONTEXT = "<recalled_memory>\n{recalled}\n</recalled_memory>"
SCHEDULED_MEMORY_SEARCH_TIMEOUT_SECONDS = 4.0
_NOTHING_SPENT = TurnCost(tokens=0, micro_usd=0, model="", cache_percent=0)


FRESH_CLAIM = "fresh"
ADOPTED_CLAIM = "adopted"


async def _claim_turn(turn_id: UUID, attempt: str) -> str | None:
    """Claim the turn for this attempt and name which branch matched: FRESH_CLAIM took a queued or
    parked turn, ADOPTED_CLAIM re-took a turn already running under this same attempt — a DBOS
    crash-recovery replay resuming its own turn — and None lost the claim. The branch is meaningful
    to a workflow's first claim; the engine's in-loop re-claim always reads as adopted because the
    queue claimed ahead of it."""
    async with workspace_tx() as connection:
        prior = (
            await connection.execute(
                sa.select(
                    tables.turn.c.conversation_id,
                    tables.turn.c.status,
                    tables.turn.c.running_attempt,
                ).where(tables.turn.c.id == turn_id)
            )
        ).one()
        await connection.execute(
            sa.select(tables.conversation.c.id)
            .where(tables.conversation.c.id == prior.conversation_id)
            .with_for_update()
        )
        workspace_id = (
            await connection.execute(
                sa.update(tables.turn)
                .values(
                    status=RUNNING,
                    running_attempt=attempt,
                    dispatch_enqueued_at=None,
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.turn.c.id == turn_id,
                    sa.or_(
                        tables.turn.c.status.in_(("queued", PARKED)),
                        sa.and_(
                            tables.turn.c.status == RUNNING,
                            tables.turn.c.running_attempt == attempt,
                        ),
                    ),
                )
                .returning(tables.turn.c.workspace_id)
            )
        ).scalar_one_or_none()
    if workspace_id is None:
        return None
    if prior.status == RUNNING and prior.running_attempt == attempt:
        return ADOPTED_CLAIM
    return FRESH_CLAIM


MAX_TOOL_RESULT_CHARS = 25_600
TOOL_RESULT_PREVIEW_CHARS = 6_144
TOOL_IMAGE_BLOB_DIR = "tool-images"
TOOL_IMAGE_EDGE_LIMIT = 2000
TOOL_IMAGE_SAVE_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
UNREGISTERED_TOOL = "unregistered"
RESULT_CUT_MARKER = "\n…["
OFFLOAD_NOTICE = (
    RESULT_CUT_MARKER + "preview only — the full {total} chars are at {path} — narrow it with bash "
    "(jq, grep, sed) or read it with offset/limit; reading it whole offloads again]"
)
TRUNCATION_NOTICE = RESULT_CUT_MARKER + "truncated {dropped} of {total} chars]"
GUIDANCE_PREEMPTED_NOTICE = (
    "Not executed: the server restarted while this call was running, and member messages arrived "
    "in the meantime — they follow. Work the call did before the restart may have partially "
    "applied. Re-issue it after reading them if it still applies."
)


class StreamResult(BaseModel):
    """One model round's memoized output — the `_stream_once` DBOS step persists this to the step
    log, so it is a boundary type. A mid-stream model error is carried in `error_class` /
    `error_message` rather than raised: a raised step records only the exception, losing the round's
    already-consumed usage, so instead the round returns, its `usages` ride the recorded output (and
    bill even on a failed or replayed turn), and the caller re-raises the error after accumulating
    them — preserving the model's own error class and its message for context-overflow detection.
    `partial_output` rides an errored round for the same reason: the deltas the stream yielded
    before dying are already paid for, so they survive in the recorded output for the truncation
    recovery to salvage into a workspace file. `reasoning` is the round's reasoning blocks in the
    provider's own order: the assistant message that carries this round's tool calls must open with
    that whole sequence, echoed unchanged, for the provider to accept and resume the reasoning when
    the tool results come back — memoized here, so a crash-recovery replay echoes the blocks the
    first run saw rather than a sequence re-derived from a second call."""

    text: str = ""
    tool_calls: tuple[ToolUseBlock, ...] = ()
    reasoning: tuple[ReasoningBlock, ...] = ()
    usages: tuple[Usage, ...] = ()
    error_class: str | None = None
    error_message: str | None = None
    partial_output: str = ""


class Arrival(BaseModel):
    """One drained inbound-queue row — the `_claim_arrivals` DBOS step's memoized output, so a
    crash-recovery replay reads back exactly the batch the first run consumed. `rendered` is the
    message text exactly as the model sees it — user_prompt_submit fired once inside the step.
    A denied arrival carries only the hook's safe denial text, never the member body or authority
    ref, so a workflow replay closes that arrival without re-firing hooks. `admission_source` says
    whether the row is a member's own message or an agent's prompt into the conversation, which is
    what the drain's published frame is about — it defaults to a member's because a batch recorded
    before the field existed replays as the reading the surfaces already acted on."""

    id: UUID
    speaker_member_id: UUID | None = None
    admission_source: TurnAdmissionSource = MEMBER_ADMISSION
    rendered: str | None = None
    denial: str | None = None


@dataclass(frozen=True)
class ActiveMessage:
    member_id: UUID | None
    rendered: str


@dataclass(frozen=True)
class _OpenActs:
    question: AskUserInput | None = None
    credential_request: CredentialRequest | None = None
    connect_request: ConnectRequest | None = None


def _activity_goal(requesters: Mapping[UUID, ActiveMessage]) -> str:
    return "\n".join(member_message_text(message.rendered) for message in requesters.values())


@dataclass(frozen=True, repr=False)
class _RoundInput:
    messages: tuple[Message, ...]
    system: str
    offer_tools: bool
    force_finish: bool
    first_round: bool
    include_requested_by: bool = True
    tool_schemas: tuple[ToolSchema, ...] | None = None
    tool_choice: str | None = None

    def __repr__(self) -> str:
        return (
            f"_RoundInput(messages={len(self.messages)}, system_chars={len(self.system)}, "
            f"offer_tools={self.offer_tools}, force_finish={self.force_finish}, "
            f"first_round={self.first_round})"
        )


@dataclass(frozen=True, repr=False)
class EffectiveCall:
    """One wire call resolved into what every later consumer reads: the declaration behind it (a
    global def, or the bound action an `object_action` call named), the semantic call id (the
    canonical action id for a bound call, the tool name otherwise), and the contributor's
    ExtensionContext its handler runs under. A bound call additionally carries the parsed wire
    fields and its already-validated action input. Resolution happens once, before segmentation,
    so requester policy, scheduling, hooks, idempotency keying, walls, replay, final acts,
    activity, and metering all consume one identity instead of re-looking the name up."""

    call: ToolUseBlock
    tool: ToolDef
    call_id: str
    ext: ExtensionContext | None
    action: ObjectActionInput | None = None
    action_args: BaseModel | None = None

    @property
    def parallel_safe(self) -> bool:
        """Whether this call may share a dispatch segment with the calls beside it — the resolved
        declaration's flag, so a bound action schedules by its own flag, never the wire
        dispatcher's."""
        return self.tool.parallel_safe

    def semantic_call(self) -> ToolUseBlock:
        """The call under its semantic identity — what final-act parsing and the activity
        summarizer read, so neither ever sees the wire dispatcher's name."""
        if self.call_id == self.call.name:
            return self.call
        assert self.action is not None
        return self.call.model_copy(
            update={
                "name": self.call_id,
                "input": self.action.model_dump(mode="json", exclude_defaults=True),
            }
        )

    def meter_dimensions(self) -> dict[str, str]:
        """The semantic telemetry dimensions beside the wire `tool`: the call id, and for a bound
        call its kind, binding, and contributor."""
        if self.tool.bound is None:
            return {"call": self.call_id}
        return {
            "call": self.call_id,
            "kind": self.tool.bound.kind,
            "binding": self.tool.bound.binding,
            "contributor": "core" if self.ext is None else self.ext.store.extension,
        }

    def __repr__(self) -> str:
        return f"EffectiveCall(call={self.call_id}, call_id={self.call.id})"


@dataclass(frozen=True, repr=False)
class _BoundToolCall:
    context: ToolContext
    effective: EffectiveCall
    member_refs: tuple[UUID, ...] = ()

    @property
    def call(self) -> ToolUseBlock:
        return self.effective.call

    def __repr__(self) -> str:
        return f"_BoundToolCall(tool={self.call.name}, call_id={self.call.id})"


@dataclass(frozen=True, repr=False)
class _RejectedToolCall:
    call: ToolUseBlock
    text: str
    outcome: str
    error_class: str
    dimensions: Mapping[str, str] = field(default_factory=dict)

    @property
    def parallel_safe(self) -> bool:
        """An unknown or malformed call is no declaration at all and stays its own in-order
        barrier, so the act the round is read by stays where the model put it."""
        return False

    def __repr__(self) -> str:
        return (
            f"_RejectedToolCall(tool={self.call.name}, call_id={self.call.id}, "
            f"outcome={self.outcome}, error_class={self.error_class})"
        )


type _DispatchInput = _BoundToolCall | _RejectedToolCall
type _Resolution = EffectiveCall | _RejectedToolCall


@dataclass
class _RoundWindow:
    """The turn's durable-so-far record, kept consistent through the round loop so an interrupt at
    any point persists exactly what happened and nothing it did not. `messages` is snapshotted only
    where the window is consistent — the founding once it is rendered, each arrival the moment it is
    absorbed (its queue row is now consumed, so the record is the only place it survives), and each
    round once its tool calls are all answered — never mid-round, where a call would lack its
    result. `ran` is true once a round of THIS turn completed, so the interrupt notice is owed by
    what this turn did, not by an assistant message an earlier turn left in the window. `denied` is
    the safe founding-denial notice when a hook refused the founding message, set before any
    cancellable step so an interrupt can never fall back to rendering the refused body."""

    messages: tuple[Message, ...] = ()
    ran: bool = False
    denied: str | None = None


@dataclass
class _ActivityState:
    sequence: int = 0
    next_publish: int = 1
    tasks: set[asyncio.Task[None]] = field(default_factory=set)
    labels: dict[str, str] = field(default_factory=dict)
    results: set[str] = field(default_factory=set)
    ready: dict[int, tuple[str, str | None]] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ImageRef(BaseModel):
    """A tool-result image `_dispatch_step` offloaded to the blob store instead of returning its
    base64 bytes inline. A DBOS step's output is serialized into the system-DB step log, so a
    browser screenshot returned inline would write tens of KB of base64 into every checkpoint (and
    replay it on recovery) — the exact regression the offload avoids. The blob key is deterministic
    (`{turn}/{call}/{index}`), so a crash-recovery replay reads back the same blob the first run
    wrote; the bytes are rehydrated into an ImageBlock only when the result is assembled for the
    model, and a step's inputs are not persisted, so the rehydrated bytes never re-enter the log."""

    media_type: str
    blob_key: str


class DispatchResult(BaseModel):
    """The `_dispatch_step` memoized output: a tool result decomposed into serialization-safe
    parts — the text (already bounded), the error flag, and any image blocks replaced by blob
    references. Keeping images out of `content` keeps the step log bounded even for a
    screenshot-heavy browser turn; the workflow reassembles the `ToolResultBlock` (rehydrating the
    referenced images) after the step returns. `usages` preserves every external find call the step
    consumed. A preempted body returns `interrupted=True` so DBOS records that partial usage before
    the live caller re-raises cancellation; recovery replays it, then advances to a fresh dispatch
    step with the same tool call and idempotency key."""

    tool_use_id: str
    text: str
    is_error: bool
    activity: bool = False
    image_refs: tuple[ImageRef, ...] = ()
    usages: tuple[Usage, ...] = ()
    interrupted: bool = False
    resume_target: ObjectActionTarget | None = None


class ModelStreamError(Exception):
    """A model stream that raised mid-round, re-raised by the caller once the round's usage is
    accumulated so a failed turn bills the partial burn and the terminal records the model's own
    error class and message. `args` carries all parts, so the pickle DBOS persists for a failed
    workflow reconstructs the exception on retrieval; str() re-embeds the class so context-overflow
    detection still matches, and leaves out `partial_output` — the deltas the stream yielded
    before dying, carried for the truncation recovery to salvage, never for the terminal."""

    def __init__(self, error_class: str, message: str, partial_output: str = "") -> None:
        super().__init__(error_class, message, partial_output)

    def __str__(self) -> str:
        error_class, message, _ = self.args
        return f"{error_class}: {message}"

    @property
    def model_error_class(self) -> str:
        error_class, _, _ = self.args
        return error_class

    @property
    def partial_output(self) -> str:
        _, _, partial_output = self.args
        return partial_output

    @property
    def model_error_message(self) -> str:
        _, message, _ = self.args
        return message


class TurnParked(Exception):
    """A running turn crossed a spend cap: it stops mid-run and is held non-terminally, resumable by
    the resume job once the cap is raised. Carries the in-surface reason for the Parked frame."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


CRUD_INTENT_TOOLS = frozenset({"object_apply", "object_delete"})


def _intent_admits(tool: ToolDef) -> bool:
    """Whether a speaking member's prepared intent may reach `tool`. The object verbs are the record
    panels' own typed lane; every other callable — a bound action or a retained global — reaches
    the lane only by declaring a `presentation`, the one fence that replaces a curated list of
    names."""
    return tool.name in CRUD_INTENT_TOOLS or tool.presentation is not None


class IntentRefused(Exception):
    """A prepared intent's tool dispatch answered with an error result — the kind's own refusal
    (admin gate, validation, unknown name) — so the intent applied nothing. Carries the refusal
    text into the terminal frame's error_message for the submitting panel."""


def _context_tag(message_id: UUID, context: TurnContext | None, admitted_at: datetime) -> str:
    """The <context> tag rendered before a member inbound: the admission moment (in the sender's
    zone when the surface supplied one, else UTC), the sender the surface named, the question the
    message answers when the surface knew one, and the source it named for the request. The
    persisted moment — the turn row's for the founding message, the queue row's for a drained
    arrival — never the wall clock, so a queued, parked, or replayed message keeps the time the
    member actually spoke."""
    zone = ZoneInfo(context.timezone) if context is not None and context.timezone else UTC
    lines = [
        f"message_ref: {message_id}",
        f"time: {admitted_at.astimezone(zone).strftime(CONTEXT_TIME_FORMAT)}",
    ]
    if context is not None and context.sender:
        lines.append(f"sender: {context.sender}")
    if context is not None and context.question:
        lines.append(f"question: {context.question}")
    if context is not None and context.source:
        lines.append(f"source: {context.source}")
    return "<context>\n" + "\n".join(lines) + "\n</context>\n"


def _bounded(content: str) -> str:
    if len(content) <= MAX_TOOL_RESULT_CHARS:
        return content
    return content[:MAX_TOOL_RESULT_CHARS] + TRUNCATION_NOTICE.format(
        dropped=len(content) - MAX_TOOL_RESULT_CHARS, total=len(content)
    )


def _meter_dispatch(
    tools: ToolRegistry,
    call: ToolUseBlock,
    started: float,
    outcome: str,
    error_class: str | None,
    profile: str,
    semantic: Mapping[str, str] | None = None,
) -> None:
    """One count and one wall-clock observation for a dispatched call, so a dashboard reads which
    tool the fleet spends its time in and where that time fails. `outcome` separates the ends a
    dispatch has by whose fault each one is — the model's, the tool's, a policy hook's, or the
    engine's — because a metric that reports only the successes reads as nothing having failed, and
    one that folds an infrastructure fault into a refusal reads as policy working as designed.
    `error_class` rides every end that carries an exception, and `profile` separates the dispatches
    a subagent makes from the main agent's.

    The wall clock is what the round waited on. `tool` stays the wire transport; `semantic` adds
    the resolved identity — `call`, and a bound action's `kind`, `binding`, and `contributor` —
    so dashboards group on what ran, not what carried it. A name the registry does not hold
    reports as UNREGISTERED_TOOL, and an `object_action` call naming an action no kind registers
    folds its `call` the same way — either name arrives on an assistant message the model wrote,
    so passing it through would mint one series per invented name."""
    registered = any(tool.name == call.name for tool in tools.tools)
    wire = call.name if registered else UNREGISTERED_TOOL
    dimensions = {
        "tool": wire,
        "call": wire if semantic is None else semantic.get("call", wire),
        **({} if semantic is None else {k: v for k, v in semantic.items() if k != "call"}),
        "outcome": outcome,
        "profile": profile,
        **({} if error_class is None else {"error_class": error_class}),
    }
    emit_metric("tool_call_total", **dimensions)
    emit_histogram("tool_call_ms", int((time.monotonic() - started) * 1000), **dimensions)


def _loaded_skill_closures(
    messages: tuple[Message, ...], skills: SkillRegistry
) -> Iterator[tuple[LoadedRef, ...]]:
    """What each completed `load_skill` in the window put in front of the model: the registry's
    card closure of the name the call asked for — the same names `loaded_context` injected for it,
    resolved without touching a stored body, since this runs on the hot path of every round. Read
    from the call's own input and the registry, never from the result's prose — a
    `SKILL.md` body is member-authored text that may quote the `# Skill:` header format, and reading
    headers back would let one skill's body mark another skill as in context and silently suppress
    its real load.

    A call the window carries no result for never completed its round, so nothing reached the model
    and it counts for nothing. Neither does a result the dispatch step offloaded or truncated:
    everything past RESULT_CUT_MARKER — text we append, never the skill's — was severed, and a
    non-text block stands in as a cut, so a result we cannot read back whole makes the skill load
    again instead of being suppressed unread. A name the model invented and a name a pack no longer
    provides both resolve to nothing rather than raising: a stale transcript may legitimately name a
    departed skill, and this runs on the hot path of every round."""
    asked: dict[str, str] = {}
    for message in messages:
        if isinstance(message.content, str):
            continue
        for block in message.content:
            match block:
                case ToolUseBlock(id=call_id, name=name, input=args) if name == SKILL_LOAD_TOOL:
                    requested = args.get("name")
                    if isinstance(requested, str):
                        asked[call_id] = requested
                case ToolResultBlock(tool_use_id=call_id, content=content) if call_id in asked:
                    text = (
                        content
                        if isinstance(content, str)
                        else "".join(
                            part.text if isinstance(part, TextBlock) else RESULT_CUT_MARKER
                            for part in content
                        )
                    )
                    if not text or RESULT_CUT_MARKER in text:
                        continue
                    try:
                        closure = skills.closure(asked[call_id])
                    except ValueError:
                        continue
                    yield closure


def _final_act[PayloadT: BaseModel](
    tool_calls: tuple[ToolUseBlock, ...],
    results: tuple[ToolResultBlock, ...],
    tool_name: str,
    model: type[PayloadT],
) -> PayloadT | None:
    """The structured payload a round leaves pending when `tool_name` was its successful final
    act: parsed from the handler's own result (the directive line, then the payload as one JSON
    line), so a pre_tool_use hook that folded the args is honored — what a surface renders is what
    the handler structured, never the raw call. A result a post hook rewrote past recognition
    carries no payload; the reply's prose still asks.

    A question is the act this reads for, and its rule is the round's last call: a turn that asked
    and then went on working has usually answered itself, so a question the round did not end on is
    stale and never reaches the member. `_pending_act` reads the acts that rule does not fit."""
    last, result = tool_calls[-1], results[-1]
    if last.name != tool_name or result.is_error or not isinstance(result.content, str):
        return None
    _directive, _, rest = result.content.partition("\n")
    try:
        return model.model_validate(json.loads(rest.split("\n", 1)[0]))
    except (json.JSONDecodeError, ValidationError):
        return None


def _pending_act[PayloadT: BaseModel](
    tool_calls: tuple[ToolUseBlock, ...],
    results: tuple[ToolResultBlock, ...],
    tool_name: str,
    model: type[PayloadT],
) -> PayloadT | None:
    """The structured payload a round leaves owed when it called `tool_name` and the call succeeded,
    read from anywhere in the round. Parsed from the handler's own result exactly as `_final_act`
    parses it, and for the same reason — a pre_tool_use hook may have folded the args, so what a
    surface renders is what the handler structured.

    Only the member discharges these acts: nothing the turn does afterwards fills a credential slot
    or presses a connection control, so the round it happened to end on says nothing about whether
    the member still owes it. A model that emits the handoff beside other work leaves a request that
    is still owed, and reading only the round's last call reports it as never asked. The last such
    call in the round wins, so a round that asks twice stands on its most recent ask."""
    outcomes = {result.tool_use_id: result for result in results}
    for call in reversed(tool_calls):
        if call.name != tool_name:
            continue
        result = outcomes.get(call.id)
        if result is None or result.is_error or not isinstance(result.content, str):
            continue
        _directive, _, rest = result.content.partition("\n")
        try:
            return model.model_validate(json.loads(rest.split("\n", 1)[0]))
        except (json.JSONDecodeError, ValidationError):
            return None
    return None


def _round_acts(
    resolved: tuple[_Resolution, ...], results: tuple[ToolResultBlock, ...]
) -> dict[str, BaseModel]:
    """The typed act payloads one round leaves open, keyed by the terminal-frame field each
    declaration maps to. Selection is by the resolved declaration's `final_act_model`, matched
    under each call's semantic identity, so a final act survives the `object_action` dispatcher
    exactly as it rides a named call. The mapping's rule picks the parser: a last-call act reads
    only the round's final call, a pending act the last successful such call anywhere in the
    round."""
    calls = tuple(
        item.semantic_call() if isinstance(item, EffectiveCall) else item.call for item in resolved
    )
    acts: dict[str, BaseModel] = {}
    settled: set[str] = set()
    for item in reversed(resolved):
        if not isinstance(item, EffectiveCall) or item.tool.final_act_model is None:
            continue
        frame_field, rule = FINAL_ACT_FIELDS[item.tool.final_act_model]
        if rule != PENDING_ACT or frame_field in settled:
            continue
        settled.add(frame_field)
        payload = _pending_act(calls, results, item.call_id, item.tool.final_act_model)
        if payload is not None:
            acts[frame_field] = payload
    last = resolved[-1] if resolved else None
    if isinstance(last, EffectiveCall) and last.tool.final_act_model is not None:
        frame_field, rule = FINAL_ACT_FIELDS[last.tool.final_act_model]
        if rule == LAST_CALL_ACT:
            payload = _final_act(calls, results, last.call_id, last.tool.final_act_model)
            if payload is not None:
                acts[frame_field] = payload
    return acts


def _act[PayloadT: BaseModel](
    acts: dict[str, BaseModel], frame_field: str, model: type[PayloadT]
) -> PayloadT | None:
    payload = acts.get(frame_field)
    return payload if isinstance(payload, model) else None


def _created_refs(
    tool_calls: tuple[ToolUseBlock, ...], results: tuple[ToolResultBlock, ...]
) -> tuple[ObjectRef, ...]:
    """The objects a round created, read from each `object_apply` result's own JSON — the handler
    reports `created` or `updated` for the same call, and only a create is one. Read from the
    result rather than the call for the same reason `_final_act` is: a pre_tool_use hook may have
    folded the manifest, and what landed is what the handler answered. A result a post hook
    rewrote past recognition names nothing, exactly as it asks nothing — including one whose kind
    or name no ref can express, which is a rewritten result rather than a turn to fail."""
    outcomes = {result.tool_use_id: result for result in results}
    created: list[ObjectRef] = []
    for call in tool_calls:
        result = outcomes.get(call.id)
        if call.name != OBJECT_APPLY_TOOL or result is None or result.is_error:
            continue
        if not isinstance(result.content, str):
            continue
        try:
            payload = json.loads(result.content.split("\n", 1)[0])
        except json.JSONDecodeError:
            continue
        match payload:
            case {"kind": str(kind), "name": str(name), "result": "created", **rest}:
                agent = rest.get("agent")
                try:
                    created.append(
                        ObjectRef(
                            kind=kind, name=name, agent=agent if isinstance(agent, str) else None
                        )
                    )
                except ValidationError:
                    continue
    return tuple(created)


def _total_usage(usage_events: Sequence[Usage]) -> Usage:
    return Usage(
        input_tokens=sum(u.input_tokens for u in usage_events),
        output_tokens=sum(u.output_tokens for u in usage_events),
        cache_read_tokens=sum(u.cache_read_tokens for u in usage_events),
        cache_write_5m_tokens=sum(u.cache_write_5m_tokens for u in usage_events),
        cache_write_30m_tokens=sum(u.cache_write_30m_tokens for u in usage_events),
        cache_write_1h_tokens=sum(u.cache_write_1h_tokens for u in usage_events),
    )


def _to_harness_reasoning(block: ReasoningBlock) -> HarnessReasoning:
    return HarnessReasoning(kind=block.type, payload=block.model_dump())


def _from_harness_reasoning(block: HarnessReasoning) -> ReasoningBlock:
    match block.kind:
        case "thinking":
            return ThinkingBlock.model_validate(block.payload)
        case "redacted_thinking":
            return RedactedThinkingBlock.model_validate(block.payload)
        case "reasoning":
            return ReasoningItemBlock.model_validate(block.payload)
        case _:
            raise ValueError(f"unknown reasoning block: {block.kind}")


def _to_harness_call(call: ToolUseBlock) -> HarnessToolCall:
    return HarnessToolCall(id=call.id, name=call.name, input=dict(call.input))


def _from_harness_call(call: HarnessToolCall) -> ToolUseBlock:
    return ToolUseBlock(id=call.id, name=call.name, input=dict(call.input))


def _to_harness_result(result: ToolResultBlock) -> HarnessToolResult:
    content: str | tuple[HarnessText | HarnessImage, ...]
    if isinstance(result.content, str):
        content = result.content
    else:
        content = tuple(
            HarnessText(part.text)
            if isinstance(part, TextBlock)
            else HarnessImage(part.source.media_type, part.source.data)
            for part in result.content
        )
    return HarnessToolResult(
        call_id=result.tool_use_id,
        content=content,
        is_error=result.is_error,
        activity=result.activity,
        activity_text=result.activity_text,
    )


def _from_harness_result(result: HarnessToolResult) -> ToolResultBlock:
    content: str | tuple[TextBlock | ImageBlock, ...]
    if isinstance(result.content, str):
        content = result.content
    else:
        content = tuple(
            TextBlock(text=part.text)
            if isinstance(part, HarnessText)
            else ImageBlock(source=ImageSource(media_type=part.media_type, data=part.data))
            for part in result.content
        )
    return ToolResultBlock(
        tool_use_id=result.call_id,
        content=content,
        is_error=result.is_error,
        activity=result.activity,
        activity_text=result.activity_text,
    )


def _to_harness_message(message: Message) -> HarnessMessage:
    if isinstance(message.content, str):
        return HarnessMessage(role=message.role, content=message.content)
    content: list[
        HarnessText | HarnessImage | HarnessToolCall | HarnessToolResult | HarnessReasoning
    ] = []
    for block in message.content:
        match block:
            case TextBlock(text=text):
                content.append(HarnessText(text))
            case ImageBlock(source=source):
                content.append(HarnessImage(source.media_type, source.data))
            case ToolUseBlock():
                content.append(_to_harness_call(block))
            case ToolResultBlock():
                content.append(_to_harness_result(block))
            case ThinkingBlock() | RedactedThinkingBlock() | ReasoningItemBlock():
                content.append(_to_harness_reasoning(block))
    return HarnessMessage(role=message.role, content=tuple(content))


def _from_harness_message(message: HarnessMessage) -> Message:
    if isinstance(message.content, str):
        return Message(role=message.role, content=message.content)
    content: list[TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock | ReasoningBlock] = []
    for block in message.content:
        match block:
            case HarnessText(text=text):
                content.append(TextBlock(text=text))
            case HarnessImage(media_type=media_type, data=data):
                content.append(ImageBlock(source=ImageSource(media_type=media_type, data=data)))
            case HarnessToolCall():
                content.append(_from_harness_call(block))
            case HarnessToolResult():
                content.append(_from_harness_result(block))
            case HarnessReasoning():
                content.append(_from_harness_reasoning(block))
    return Message(role=message.role, content=tuple(content))


def _to_harness_messages(messages: tuple[Message, ...]) -> tuple[HarnessMessage, ...]:
    return tuple(_to_harness_message(message) for message in messages)


def _from_harness_messages(messages: tuple[HarnessMessage, ...]) -> tuple[Message, ...]:
    return tuple(_from_harness_message(message) for message in messages)


@dataclass
class _RuntimeToolState:
    acts: _OpenActs = field(default_factory=_OpenActs)
    resolutions: dict[str, _Resolution] = field(default_factory=dict)
    bindings: dict[str, _DispatchInput] = field(default_factory=dict)


@dataclass(frozen=True)
class _RuntimeConversation:
    engine: "TurnEngine"
    usage_events: list[Usage]
    arrival_log: list[Message]
    absorbed_ids: list[UUID]
    requesters: dict[UUID, ActiveMessage]
    meter: "_TurnMeter"

    async def prepare(
        self, messages: tuple[HarnessMessage, ...], round_index: int
    ) -> HarnessPreparedRound:
        current = _from_harness_messages(messages)
        absorbed = await self.engine._absorb_arrivals(
            current, self.arrival_log, self.absorbed_ids, self.requesters
        )
        self.engine._window.messages = absorbed
        await self.engine._enforce_spend(self.usage_events, self.requesters)
        self.engine._reseed_loaded_skills(absorbed)
        active_requests = tuple(message.rendered for message in self.requesters.values())
        with span("compaction.maybe"):
            compacted, compaction_usage = await self.engine.compaction.maybe_compact(
                absorbed, active_requests=active_requests
            )
        self.engine._reseed_loaded_skills(compacted)
        self.usage_events.extend(compaction_usage)
        self.meter.rounds += 1
        return HarnessPreparedRound(
            messages=_to_harness_messages(compacted),
            interrupted_final_act=len(absorbed) > len(current),
        )

    async def checkpoint(self, messages: tuple[HarnessMessage, ...]) -> None:
        self.engine._window.messages = _from_harness_messages(messages)
        self.engine._window.ran = True

    async def prepare_exhaust(
        self, messages: tuple[HarnessMessage, ...]
    ) -> tuple[HarnessMessage, ...]:
        await self.engine._enforce_spend(self.usage_events, self.requesters)
        active_requests = tuple(message.rendered for message in self.requesters.values())
        compacted, compaction_usage = await self.engine.compaction.maybe_compact(
            _from_harness_messages(messages), active_requests=active_requests
        )
        self.usage_events.extend(compaction_usage)
        return _to_harness_messages(compacted)


@dataclass(frozen=True)
class _RuntimeModel:
    engine: "TurnEngine"
    usage_events: list[Usage]
    requesters: dict[UUID, ActiveMessage]

    async def stream(self, request: HarnessModelRequest, round_index: int) -> HarnessModelRound:
        active_requests = (
            ()
            if request.mode is HarnessRoundMode.FORCE_FINISH
            else tuple(message.rendered for message in self.requesters.values())
        )
        schemas = tuple(
            ToolSchema(
                name=tool.name,
                description=tool.description,
                input_schema=dict(tool.input_schema),
            )
            for tool in request.tools
        )
        try:
            messages, result = await self.engine._stream_recovering_overflow(
                _from_harness_messages(request.messages),
                self.usage_events,
                request.system_prompt,
                schemas,
                request.tool_choice,
                offer_tools=request.mode is HarnessRoundMode.NORMAL,
                force_finish=request.mode is HarnessRoundMode.FORCE_FINISH,
                active_requests=active_requests,
                first_round=request.first_round,
                include_requested_by=bool(self.engine._member_refs(self.requesters)),
            )
        except ModelStreamError as error:
            if error.model_error_class != MODEL_TRUNCATED_ERROR_CLASS:
                raise
            if request.mode is not HarnessRoundMode.NORMAL:
                raise
            emit_metric("turn_truncation_recovered_total", profile=self.engine.profile)
            log(
                "turn.truncation_recovered",
                turn_id=str(self.engine.turn.id),
                round=round_index,
                salvaged_chars=len(error.partial_output),
            )
            feedback = TRUNCATION_FEEDBACK
            if error.partial_output:
                path = await self.engine._offload(
                    f"truncated-{self.engine.turn.id}-{round_index}.txt", error.partial_output
                )
                if path is not None:
                    feedback += TRUNCATION_SALVAGE_NOTICE.format(path=path)
            raise RecoverableModelError(feedback) from error
        await self.engine._publish_cost(self.usage_events)
        return HarnessModelRound(
            messages=_to_harness_messages(messages),
            text=result.text,
            calls=tuple(_to_harness_call(call) for call in result.tool_calls),
            reasoning=tuple(_to_harness_reasoning(block) for block in result.reasoning),
        )


@dataclass(frozen=True)
class _RuntimeTools:
    engine: "TurnEngine"
    context: ToolContext
    usage_events: list[Usage]
    requesters: dict[UUID, ActiveMessage]
    change_paths: dict[str, None]
    created: dict[ObjectRef, None]
    state: _RuntimeToolState

    def definitions(self) -> tuple[HarnessToolDefinition, ...]:
        include_requested_by = bool(self.engine._member_refs(self.requesters))
        return tuple(
            HarnessToolDefinition(
                name=schema.name,
                description=schema.description,
                input_schema=dict(schema.input_schema),
            )
            for tool in self.engine.tools.tools
            for schema in (tool.schema(include_requested_by=include_requested_by),)
        )

    def parallel_safe(self, call: HarnessToolCall) -> bool:
        return self._resolve(call).parallel_safe

    async def prepare(self, calls: tuple[HarnessToolCall, ...]) -> None:
        bound_items = await asyncio.gather(
            *(
                self.engine._bind_or_error(self.context, self._resolve(call), self.requesters)
                for call in calls
            ),
            return_exceptions=True,
        )
        for call, bound in zip(calls, bound_items, strict=True):
            if isinstance(bound, BaseException):
                raise bound
            self.state.bindings[call.id] = bound
        for bound in bound_items:
            if isinstance(bound, _BoundToolCall):
                self.engine._start_activity(
                    bound.effective.semantic_call(), _activity_goal(self.requesters)
                )

    async def execute(self, call: HarnessToolCall) -> HarnessToolResult:
        bound = self.state.bindings[call.id]
        return _to_harness_result(await self.engine._dispatch(bound, self.usage_events))

    async def after_round(
        self,
        calls: tuple[HarnessToolCall, ...],
        results: tuple[HarnessToolResult, ...],
    ) -> None:
        try:
            runtime_calls = tuple(_from_harness_call(call) for call in calls)
            runtime_results = tuple(_from_harness_result(result) for result in results)
            self.change_paths.update(dict.fromkeys(change_targets(runtime_calls)))
            await self.engine._fold_created(self.created, runtime_calls, runtime_results)
            if len(results) != len(calls):
                return
            resolved = tuple(self._resolve(call) for call in calls)
            acts = _round_acts(resolved, runtime_results)
            self.state.acts = _OpenActs(
                question=_act(acts, "question", AskUserInput),
                credential_request=(
                    _act(acts, "credential_request", CredentialRequest)
                    or self.state.acts.credential_request
                ),
                connect_request=(
                    _act(acts, "connect_request", ConnectRequest) or self.state.acts.connect_request
                ),
            )
        finally:
            self.state.resolutions.clear()
            self.state.bindings.clear()

    def interrupted(self) -> None:
        self.state.acts = replace(self.state.acts, question=None)

    def _resolve(self, call: HarnessToolCall) -> _Resolution:
        resolved = self.state.resolutions.get(call.id)
        if resolved is not None:
            return resolved
        resolved = self.engine._resolve_call(_from_harness_call(call))
        self.state.resolutions[call.id] = resolved
        return resolved


@dataclass(frozen=True)
class _RuntimeEvents:
    engine: "TurnEngine"
    meter: "_TurnMeter"

    async def speak(self, replies: tuple[MarkedReply, ...], round_number: int) -> None:
        await self.engine._speak(replies, round_number)

    async def closing(self, replies: tuple[MarkedReply, ...]) -> None:
        await self.engine._stream_closing_spans(replies)

    def exhausted(self) -> None:
        self.meter.incomplete_reason = ROUND_BUDGET_INCOMPLETE
        emit_metric("turn_round_budget_exhausted_total", profile=self.engine.profile)
        log(
            "turn.force_final",
            turn_id=str(self.engine.turn.id),
            rounds=self.engine.max_rounds,
        )


@dataclass(frozen=True)
class TranscriptRepair:
    """The turn's durable-transcript writer, split from the engine so the worker can republish a
    committed terminal without building one. `resolve` ends a redelivered client's wait — a
    duplicate whose original run committed the terminal but may have crashed before publishing it —
    by republishing that terminal; the monotonic transcript guard lets the original run's fuller
    write stand."""

    turn: Turn
    transcript: Transcript
    hub: Hub

    async def resolve(self) -> TerminalFrame | None:
        """Republish the committed terminal for an execution that holds no running claim — a
        redelivery of a finished turn — so the client's wait ends even if the original run crashed
        before publishing. Persist the founding inbound in case that run never wrote its transcript;
        the monotonic guard yields to the fuller write when it landed. No-op (None) while the turn
        is still running so the live execution stays the sole authority."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.turn.c.terminal).where(tables.turn.c.id == self.turn.id)
                )
            ).one()
        if row.terminal is None:
            return None
        frame = TerminalFrame.model_validate(row.terminal)
        await self.persist_inbound()
        try:
            await self.hub.publish(self.turn.id, Terminal(frame=frame))
        except Exception as error:
            log(
                "hub.publish_failed",
                turn_id=str(self.turn.id),
                error_class=type(error).__name__,
            )
        return frame

    async def persist_transcript(
        self, messages: tuple[Message, ...], answer: str, system: str, injected: str
    ) -> None:
        await self.write_conversation(
            (*messages, Message(role="assistant", content=answer)),
            system=system,
            injected=injected or None,
            from_run=True,
        )

    async def persist_interrupted(self, messages: tuple[Message, ...], ran: bool) -> None:
        """What the turn did, for a turn that ended without answering. The rounds it completed are
        facts about the world — a deploy it dispatched, a file it wrote — and the next turn reads
        this window as its history, so dropping them is how an interrupted turn comes to look like
        one that never acted and runs them again. `messages` is the turn's own consistent window —
        its founding, every arrival it absorbed, and every round it completed — snapshotted by the
        loop only where every tool call is answered, so a partial answer or a raised mid-round error
        cannot land. The empty case never reaches here: the caller owes the founding through
        `persist_inbound` instead, which alone knows the safe notice for a refused founding.

        `ran` is whether a round of THIS turn completed — the notice disowns what this turn did, so
        it is owed by this turn's own work, never by an assistant message an earlier turn left in
        the window."""
        await self.write_conversation(
            (*messages, Message(role="user", content=INTERRUPTED_TURN_NOTICE)) if ran else messages,
            from_run=True,
        )

    async def persist_inbound(
        self,
        arrivals: tuple[Message, ...] = (),
        founding_denial: str | None = None,
    ) -> None:
        """Preserve the member's messages on a non-done terminal — the founding inbound plus every
        arrival this run absorbed — so the next turn still sees them; the assistant's error or
        partial text is never persisted, and the monotonic guard lets a done turn's fuller
        transcript win over this at the same seq. Only a run whose turn is over writes here: an
        executor pre-emption commits no terminal and persists nothing, because DBOS re-runs the
        turn and the run that finishes it is the seq's sole transcript writer."""
        founding = (
            await self.load_messages()
            if founding_denial is None
            else (
                *await self._prior_messages(),
                Message(role="user", content=founding_denial),
            )
        )
        await self.write_conversation((*founding, *arrivals))

    async def load_messages(self) -> tuple[Message, ...]:
        """Prior transcript plus this turn's inbound, prefixed with the <context> tag on a member
        turn — the model has no clock, so the tag carries the admission moment, the sender, and the
        source the surface named, and it persists into the transcript so each past exchange keeps
        its moment. A spawned turn's inbound stays the bare payload its target's
        contract promises."""
        inbound = self.turn.inbound
        if not self.turn.spawned:
            inbound = _context_tag(self.turn.id, self.turn.context, self.turn.created_at) + inbound
        return (*await self._prior_messages(), Message(role="user", content=inbound))

    async def _prior_messages(self) -> tuple[Message, ...]:
        """The conversation before this turn; self-exclusion keeps a replay from reading its own
        write (seq >= this turn's) back as prior context."""
        stored = await self.transcript.read()
        if stored is None or stored.seq >= self.turn.seq:
            return ()
        return stored.messages

    async def write_conversation(
        self,
        messages: tuple[Message, ...],
        system: str | None = None,
        injected: str | None = None,
        from_run: bool = False,
    ) -> None:
        conversation = Conversation(
            seq=self.turn.seq,
            messages=messages,
            system=system,
            injected=injected,
            from_run=from_run,
        )
        for attempt in range(TRANSCRIPT_WRITE_ATTEMPTS):
            try:
                await self.transcript.write(conversation)
                return
            except Exception as error:
                log(
                    "transcript.write_failed",
                    turn_id=str(self.turn.id),
                    attempt=attempt + 1,
                    error_class=type(error).__name__,
                )
                await asyncio.sleep(TRANSCRIPT_WRITE_RETRY_SECONDS)


PREEMPTED = "preempted"


@dataclass
class _TurnMeter:
    """What one execution of a turn cost — its wall clock and the model rounds it ran — recorded
    where that execution ends, under the exit it took: the terminal it committed or read back, the
    cap it parked at, the cancel that ended it, or the executor pre-emption that took it away. The
    exit is the execution's, never the row's, so a durable write that matched nothing still ends an
    execution that ran; the counters of those writes stay behind their own transition guards.

    The unit is the execution, not the turn, because this is workflow-body code: a crash-recovery
    re-dispatch enters `run()` again with a fresh meter, and the steps it replays return from the
    step log in milliseconds, so a second execution's wall clock covers the work still left rather
    than the work the crashed one already did. The wall clock is therefore one observation per
    execution that reached an exit — the population `turn_started_total` counts, less the ones that
    reached none: an execution that died, and a duplicate dispatch that lost the running claim and
    did no work — while the durable terminal a re-dispatch may find already written stays counted
    once, by `turn_terminal_total`. A resumed park is a fresh execution the same way, so a cap held
    for days never enters the wall clock. Rounds are counted where the loop enters one, which a
    replay re-enters, so a recovered execution reports the turn's rounds to date against its own
    wall. The first exit an execution reaches is the one it ended at; the unwinding past it (a
    transcript write that fails after the terminal is durable and re-enters the commit) records
    nothing further."""

    started: float
    profile: str
    rounds: int = 0
    ended: bool = False
    incomplete_reason: IncompleteReason | None = None

    def exited(self, status: str) -> None:
        if self.ended:
            return
        self.ended = True
        emit_histogram(
            "turn_ms",
            int((time.monotonic() - self.started) * 1000),
            status=status,
            profile=self.profile,
        )
        if self.rounds:
            emit_metric("turn_rounds_total", self.rounds, status=status, profile=self.profile)
            emit_metric(
                "turn_round_path_total",
                path="single" if self.rounds == 1 else "multiple",
                status=status,
                profile=self.profile,
            )


@dataclass
class AdoptionReplay:
    """The replay window of an execution that adopted an already-running turn after a crash. Open
    from the claim that re-took the turn until the first live arrival drain — the point where the
    window's contents catch up with the queue. While it is open, a dispatch step whose body actually
    executes is the crashed attempt's in-flight work re-running, chosen by a model that cannot have
    seen anything queued since; an unkeyed redo yields to pending member guidance instead of
    running ahead of it, while a side-effecting re-execution dedups through its idempotency key
    and keeps its reattach.

    It is also what the member is told: an execution that opens this window publishes one Resumed
    frame, because a wait that survived a restart looks from the thread exactly like a wait that
    died."""

    replaying: bool = False


@dataclass(frozen=True)
class RunLineage:
    """Where a subagent turn's live activity publishes and how its row is labeled: the root turn
    every surface tails, the turn its row nests under, and the profile and display name its spawn
    gave it."""

    root_turn_id: UUID
    parent_turn_id: UUID
    profile: str
    name: str


@dataclass(frozen=True, repr=False)
class TurnEngine:
    turn: Turn
    agent: Agent
    byok: bool
    system_prompt: RenderedPrompt
    model: ModelClient
    activity_summarizer: ActivitySummarizer
    provider: str
    transcript: Transcript
    compaction: Compaction
    hub: Hub
    sandbox: Sandbox
    cdp_provider: CdpProvider | None
    search_provider: SearchProvider | None
    connectors: ConnectorRegistry
    tools: ToolRegistry
    tool_ext: dict[str, ExtensionContext]
    hooks: HookChain
    blob: WorkspaceBlobStore
    spawn: Spawn
    audience: Audience
    artifact_token_secret: str
    grants: GrantStore | None
    connector_read_only: bool = False
    site_previewer: SitePreviewer | None = None
    previous_turn_ended_at: datetime | None = None
    lineage: RunLineage | None = None
    sandbox_for: SandboxFor | None = None
    subagents_for: SubagentsFor | None = None
    requestable_credentials: CredentialRequests | None = None
    memory: MemorySearch | None = None
    public_base_url: str | None = None
    billing_url: str | None = None
    models: tuple[str, ...] = ()
    """The model ids this deploy serves, handed to every tool call so a write that stores a
    model can refuse an id the registry cannot answer."""
    model_specs: Mapping[str, ModelSpec] = field(default_factory=dict)
    auto_model: str = AUTO_MODEL
    pricing: Pricing = CORE_PRICING
    subagents: SubagentControl | None = None
    reasoning: ReasoningSupport = field(
        default_factory=lambda: ReasoningSupport(supported=True, tools_with_reasoning=True)
    )
    attempt: str = ""
    max_rounds: int = MAIN_ROUND_LIMIT
    skills: SkillRegistry = CORE_SKILL_REGISTRY
    member_skill_block: str = ""
    preload: tuple[LoadedSkill, ...] = ()
    output_model: Contract | None = None
    adoption: AdoptionReplay = field(default_factory=AdoptionReplay)
    verbs: ObjectVerbs = field(default_factory=lambda: ObjectVerbs({}))
    granted_actions: frozenset[str] = frozenset()
    _activity: _ActivityState = field(default_factory=_ActivityState, init=False, repr=False)
    _window: _RoundWindow = field(default_factory=_RoundWindow, init=False, repr=False)
    _find_usages: ContextVar[list[Usage] | None] = field(
        default_factory=lambda: ContextVar("find_usages", default=None),
        init=False,
        repr=False,
    )
    _live_dispatches: set[str] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        if any(context.audience != self.audience for context in self.tool_ext.values()):
            raise ValueError("tool and turn audiences differ")
        if self.hooks.audience != self.audience:
            raise ValueError("hook and turn audiences differ")
        if self.output_model is None:
            return
        try:
            self.tools.get(FINISH_TOOL)
        except KeyError:
            return
        raise ValueError(f"a subagent turn's tool set may not name a tool {FINISH_TOOL!r}")

    def __repr__(self) -> str:
        return (
            f"TurnEngine(turn_id={self.turn.id}, agent_id={self.turn.agent_id}, "
            f"profile={self.profile})"
        )

    @property
    def profile(self) -> str:
        """This turn's `profile` telemetry dimension — its subagent profile, `agent` for a spawned
        agent child, or `main`."""
        return turn_profile(self.turn.subagent_profile, self.turn.spawned)

    async def run(self) -> TerminalFrame | None:
        meter = _TurnMeter(started=time.monotonic(), profile=self.profile)
        emit_metric("turn_started_total", profile=self.profile)
        log(
            "turn.started",
            turn_id=str(self.turn.id),
            seq=self.turn.seq,
            prompt_digest=self.system_prompt.digest,
            profile=self.profile,
            parent_turn_id=str(self.turn.parent_turn_id or ""),
        )
        if self.adoption.replaying:
            await self._publish(Resumed(attempt=self.attempt))
        usage_events: list[Usage] = []
        arrival_log: list[Message] = []
        absorbed_ids: list[UUID] = []
        requesters: dict[UUID, ActiveMessage] = {}
        founding_denial: str | None = None

        async def rank_find(system: str, user: str) -> str:
            """The browser `find` tool's element ranking: a host-side model call (the engine
            runs on the host, never in the sandbox) whose usage meters onto this turn."""
            request = ModelRequest(
                model=self.agent.model,
                system=system,
                messages=(Message(role="user", content=user),),
                max_tokens=FIND_MAX_TOKENS,
                conversation_cache_ttl="5m",
                session_id=str(self.turn.conversation_id),
                reasoning=self.reasoning.internal_effort(),
            )
            parts: list[str] = []
            async for event in self.model.complete(request):
                match event:
                    case TextDelta(text=text):
                        parts.append(text)
                    case Usage():
                        usage_events.append(event)
                        find_usages = self._find_usages.get()
                        if find_usages is None:
                            raise RuntimeError("find completion ran outside a tool dispatch")
                        find_usages.append(event)
            return "".join(parts)

        context = ToolContext(
            sandbox=self.sandbox,
            blob=self.blob,
            turn=self.turn,
            agent=self.agent,
            spawn=self.spawn,
            subagents=self.subagents,
            speaker_member_id=None,
            audience=self.audience,
            artifact_token_secret=self.artifact_token_secret,
            grants=self.grants,
            granted_actions=self.granted_actions,
            skills=self.skills,
            loaded_skills=self.compaction.loaded_skills,
            cdp_provider=self.cdp_provider,
            search_provider=self.search_provider,
            connectors=self.connectors,
            connector_read_only=self.connector_read_only,
            find=rank_find,
            requestable_credentials=self.requestable_credentials,
            public_base_url=self.public_base_url,
            site_previewer=self.site_previewer,
            models=self.models,
            model_specs=self.model_specs,
            auto_model=self.auto_model,
        )
        created: dict[ObjectRef, None] = dict.fromkeys(self.turn.created_refs)
        messages: tuple[Message, ...] = ()
        try:
            if not await self._mark_running():
                return await self._resolve_unclaimed()
            await self._publish_run()
            system = self.system_prompt.content
            if self.turn.admission_source == SCHEDULED_ADMISSION:
                system = await self._scheduled_system(system)
            with span("hooks.user_prompt_submit"):
                inbound = await self.hooks.fire(
                    "user_prompt_submit",
                    UserPromptSubmit(text=self.turn.inbound),
                    self.turn,
                    self.agent,
                    self.turn.speaker_member_id,
                )
            pending_guard = not self.turn.spawned
            injected = inbound.injected
            if inbound.denied is not None:
                self._window.denied = DENIED_INBOUND_NOTICE.format(reason=escape(inbound.denied))
                denial = await self._commit(
                    "done",
                    usage_events,
                    meter,
                    answer=inbound.denied,
                    unless_arrivals=pending_guard,
                    absorbed=tuple(absorbed_ids),
                )
                if denial is not None:
                    await self._persist_transcript(
                        await self._load_messages(), inbound.denied, system, inbound.injected
                    )
                    await self._publish_terminal(denial)
                    await self._record_workspace_changes(())
                    return denial
                founding_denial = DENIED_INBOUND_NOTICE.format(reason=escape(inbound.denied))
                messages = (
                    *await self._repair()._prior_messages(),
                    Message(role="user", content=founding_denial),
                )
            else:
                messages = await self._load_messages()
                founding = messages[-1].content
                if not isinstance(founding, str):
                    raise RuntimeError("founding inbound did not render as text")
                if not self.turn.spawned:
                    requesters[self.turn.id] = ActiveMessage(
                        member_id=self.turn.speaker_member_id,
                        rendered=founding,
                    )
                injected = "\n\n".join(part for part in (injected, self.member_skill_block) if part)
                if injected:
                    rendered = INJECTED_CONTEXT.format(content=founding, injected=injected)
                    messages = (*messages[:-1], Message(role="user", content=rendered))
            self._window.messages = messages
            change_paths: dict[str, None] = {}
            while True:
                (
                    final_messages,
                    answer,
                    question,
                    credential_request,
                    connect_request,
                ) = await self._model_round(
                    context,
                    messages,
                    usage_events,
                    system,
                    arrival_log,
                    absorbed_ids,
                    requesters,
                    meter,
                    change_paths,
                    created,
                )
                await self.hooks.fire(
                    "stop",
                    Stop(answer=answer),
                    self.turn,
                    self.agent,
                    None,
                )
                frame = await self._commit(
                    "done",
                    usage_events,
                    meter,
                    answer=answer,
                    question=question,
                    credential_request=credential_request,
                    connect_request=connect_request,
                    created=tuple(created),
                    unless_arrivals=pending_guard,
                    absorbed=tuple(absorbed_ids),
                )
                if frame is None:
                    log(
                        "turn.answer_recycled",
                        turn_id=str(self.turn.id),
                        answer_chars=len(answer),
                        absorbed=len(absorbed_ids),
                    )
                    messages = (*final_messages, Message(role="assistant", content=answer))
                    continue
                if frame.status == "done":
                    await self._persist_transcript(final_messages, answer, system, injected)
                else:
                    await self._persist_interrupted(final_messages)
                await self._publish_terminal(frame)
                await self._record_workspace_changes(tuple(change_paths))
                return frame
        except TurnParked as parked:
            meter.exited(PARKED)
            await self._park(parked.message, usage_events)
            raise
        except DBOSWorkflowCancelledError:
            meter.exited(CANCELLED)
            await self._bill_cancelled(usage_events)
            await self._release_unabsorbed(tuple(absorbed_ids))
            await self._persist_interrupted(self._window.messages)
            await self._stop_sandbox_commands()
            raise
        except asyncio.CancelledError:
            meter.exited(PREEMPTED)
            await self._bill_cancelled(usage_events)
            await self._release_unabsorbed(tuple(absorbed_ids))
            raise
        except Exception as error:
            frame = await self._commit(
                "failed", usage_events, meter, error=error, created=tuple(created)
            )
            try:
                await self._release_unabsorbed(tuple(absorbed_ids))
                await self._persist_interrupted(self._window.messages)
            finally:
                if frame is not None:
                    await self._publish_terminal(frame)
            raise
        finally:
            await context.cleanup.drain()

    async def run_intent(self) -> TerminalFrame | None:
        """Run an intent turn: dispatch its one typed tool call verbatim and commit the result.

        A speaking intent is a prepared panel mutation and binds authority through its founding
        member message. A speaking `object_action` intent reaches only an action that declares a
        `presentation` — the one dispatch-point fence on the prepared-intent lane — while a
        speakerless intent is a sandbox bridge call, carries the authority of the live parent run
        on `on_behalf_of_member_id`, and is gated by the turn's granted actions instead. Both take
        the same guarded, memoized dispatch as a model call, with no model round or turn-shaped
        prompt hooks."""
        meter = _TurnMeter(started=time.monotonic(), profile=self.profile)
        emit_metric("turn_started_total", profile=self.profile)
        log(
            "turn.started",
            turn_id=str(self.turn.id),
            seq=self.turn.seq,
            prompt_digest="",
            profile=self.profile,
            parent_turn_id=str(self.turn.parent_turn_id or ""),
        )
        usage_events: list[Usage] = []
        context = ToolContext(
            sandbox=self.sandbox,
            blob=self.blob,
            turn=self.turn,
            agent=self.agent,
            spawn=self.spawn,
            subagents=self.subagents,
            speaker_member_id=None,
            audience=self.audience,
            artifact_token_secret=self.artifact_token_secret,
            grants=self.grants,
            granted_actions=self.granted_actions,
            skills=self.skills,
            loaded_skills=self.compaction.loaded_skills,
            cdp_provider=self.cdp_provider,
            search_provider=self.search_provider,
            connectors=self.connectors,
            connector_read_only=self.connector_read_only,
            requestable_credentials=self.requestable_credentials,
            public_base_url=self.public_base_url,
            site_previewer=self.site_previewer,
            models=self.models,
            model_specs=self.model_specs,
            auto_model=self.auto_model,
        )
        try:
            if not await self._mark_running():
                return await self._resolve_unclaimed()
            if self.turn.speaker_member_id is None:
                bridge = ToolBridgeIntent.model_validate_json(self.turn.inbound)
                call = ToolUseBlock(
                    id=f"bridge-{bridge.request_id.hex[:12]}",
                    name=bridge.tool,
                    input=bridge.input,
                )
                requesters: dict[UUID, ActiveMessage] = {}
            else:
                intent = ToolIntent.model_validate_json(self.turn.inbound)
                call = ToolUseBlock(
                    id=f"intent-{self.turn.id.hex[:12]}",
                    name=intent.tool,
                    input={**intent.input, REQUESTED_BY: str(self.turn.id)},
                )
                requesters = {
                    self.turn.id: ActiveMessage(
                        member_id=self.turn.speaker_member_id, rendered=self.turn.inbound
                    )
                }
            semantic = self._resolve_call(call)
            if (
                self.turn.speaker_member_id is not None
                and isinstance(semantic, EffectiveCall)
                and not _intent_admits(semantic.tool)
            ):
                semantic = self._rejected(
                    call,
                    ValueError(
                        f"{semantic.call_id} declares no presentation — prepared member intents "
                        "reach only presented actions"
                    ),
                    dimensions=semantic.meter_dimensions(),
                )
            bound = await self._bind_or_error(context, semantic, requesters)
            if isinstance(bound, _BoundToolCall):
                self._start_activity(bound.effective.semantic_call(), _activity_goal(requesters))
            result = await self._dispatch_step_recovering(bound, usage_events)
            if result.is_error:
                frame = await self._commit(
                    "failed", usage_events, meter, error=IntentRefused(result.text)
                )
            else:
                dispatched_result = (
                    ToolResultBlock(
                        tool_use_id=call.id,
                        content=result.text,
                        is_error=False,
                    ),
                )
                acts = _round_acts((semantic,), dispatched_result)
                connect_request = _act(acts, "connect_request", ConnectRequest)
                credential_request = _act(acts, "credential_request", CredentialRequest)
                frame = await self._commit(
                    "done",
                    usage_events,
                    meter,
                    answer=result.text,
                    connect_request=connect_request,
                    credential_request=credential_request,
                    created=_created_refs((call,), dispatched_result),
                )
            await self._persist_transcript(await self._load_messages(), result.text, "", "")
            if frame is not None:
                await self._publish_terminal(frame)
            return frame
        except DBOSWorkflowCancelledError:
            meter.exited(CANCELLED)
            await self._stop_sandbox_commands()
            raise
        except asyncio.CancelledError:
            meter.exited(PREEMPTED)
            raise
        except Exception as error:
            frame = await self._commit("failed", usage_events, meter, error=error)
            if frame is not None:
                await self._publish_terminal(frame)
            raise
        finally:
            await context.cleanup.drain()

    async def _scheduled_system(self, system: str) -> str:
        if self.memory is None:
            raise RuntimeError("scheduled turn requires memory search; none is wired")
        try:
            async with asyncio.timeout(SCHEDULED_MEMORY_SEARCH_TIMEOUT_SECONDS):
                matches = await self.memory.search(
                    SourceReader(
                        agent_id=self.turn.agent_id,
                        requesting_member_id=None,
                        subjects=audience_subjects(self.audience),
                    ),
                    (self.turn.inbound,),
                )
        except Exception as error:
            log(
                "memory.scheduled_search_degraded",
                turn_id=str(self.turn.id),
                error_class=type(error).__name__,
            )
            return system
        if not matches:
            return system
        recalled = "\n".join(
            f"- [{escape(match.kind)}] {escape(match.text)}"
            + (
                ""
                if match.ref is None
                else " ("
                + escape(str(match.ref))
                + ("" if match.created_at is None else f", {match.created_at.date().isoformat()}")
                + ")"
            )
            for match in matches
        )
        return f"{system}\n\n{SCHEDULED_MEMORY_CONTEXT.format(recalled=recalled)}"

    async def _mark_running(self) -> bool:
        """Claim the turn as this execution's single owner, keyed by this run's workflow id. A
        queued or parked turn transitions to running under this id; a turn already running is
        re-claimed only by the same id — a DBOS crash-recovery replay of this very workflow, which
        must resume its own turn. A different id (a redundant resume enqueue) matches nothing, loses
        the claim, and is resolved as superseded — single ownership is the DB claim itself.
        Clearing the advisory dispatch stamp here tells the outbox
        the turn is live; a crash before this leaves the turn re-enqueueable."""
        return await _claim_turn(self.turn.id, self.attempt) is not None

    def _repair(self) -> TranscriptRepair:
        return TranscriptRepair(turn=self.turn, transcript=self.transcript, hub=self.hub)

    async def _load_messages(self) -> tuple[Message, ...]:
        with span("transcript.load"):
            return await self._repair().load_messages()

    async def _model_round(
        self,
        context: ToolContext,
        messages: tuple[Message, ...],
        usage_events: list[Usage],
        system: str,
        arrival_log: list[Message],
        absorbed_ids: list[UUID],
        requesters: dict[UUID, ActiveMessage],
        meter: _TurnMeter,
        change_paths: dict[str, None],
        created: dict[ObjectRef, None],
    ) -> tuple[
        tuple[Message, ...],
        str,
        AskUserInput | None,
        CredentialRequest | None,
        ConnectRequest | None,
    ]:
        state = _RuntimeToolState()
        tools = _RuntimeTools(
            self,
            context,
            usage_events,
            requesters,
            change_paths,
            created,
            state,
        )
        structured: HarnessStructuredOutput | None = None
        if self.output_model is not None:
            output_model = self.output_model

            def validate(call: HarnessToolCall) -> str:
                return output_model.model_validate(call.input).model_dump_json()

            def accept_prose(text: str) -> str | None:
                if (
                    not freeform_result_contract(output_model)
                    or len(text) > DIRECT_PROSE_RESULT_MAX_CHARS
                ):
                    return None
                try:
                    return output_model.model_validate({"result": text}).model_dump_json()
                except ValidationError:
                    return None

            structured = HarnessStructuredOutput(
                finish_tool=HarnessToolDefinition(
                    name=FINISH_TOOL,
                    description=FINISH_DESCRIPTION,
                    input_schema=output_model.model_json_schema(),
                ),
                finish_prompt=FINISH_PROMPT,
                force_finish_prompt=FORCE_FINISH_PROMPT,
                coexisting_error=FINISH_ALONE,
                blocked=lambda: state.acts.question is not None,
                validate=validate,
                invalid_error=lambda error: FINISH_SCHEMA_ERROR.format(error=error),
                accept_prose=accept_prose,
            )
        finished = await AgentEngine(
            definition=HarnessAgentDefinition(
                system_prompt=system,
                max_rounds=self.max_rounds,
                max_parallel_calls=MAX_PARALLEL_TOOL_CALLS,
                force_final_prompt=FORCE_FINAL_PROMPT,
                empty_response_feedback=EMPTY_RESPONSE_NUDGE,
            ),
            model=_RuntimeModel(self, usage_events, requesters),
            tools=tools,
            conversation=_RuntimeConversation(
                self,
                usage_events,
                arrival_log,
                absorbed_ids,
                requesters,
                meter,
            ),
            events=_RuntimeEvents(self, meter),
            structured=structured,
        ).run(_to_harness_messages(messages))
        acts = state.acts
        return (
            _from_harness_messages(finished.messages),
            finished.answer,
            None if finished.exhausted or finished.structured else acts.question,
            acts.credential_request,
            acts.connect_request,
        )

    async def _fold_created(
        self,
        created: dict[ObjectRef, None],
        tool_calls: tuple[ToolUseBlock, ...],
        results: tuple[ToolResultBlock, ...],
    ) -> None:
        """Fold a round's creations into the accumulator and write the whole set on the turn row,
        the round they happen — durable ahead of any terminal. Every terminal then names what is
        already true: this execution's own commit reads the accumulator, a canceller reads the row,
        and a resume — a fresh workflow whose re-run rounds find the names already taken — seeds
        from it instead of losing what the parked attempt made. Runs on the dispatch unwind too, so
        a round that created and then faulted still records the creation before the fault routes
        the turn."""
        fresh = [ref for ref in _created_refs(tool_calls, results) if ref not in created]
        if not fresh:
            return
        created.update(dict.fromkeys(fresh))
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .values(
                    created_refs=[ref.model_dump(mode="json") for ref in created],
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.turn.c.id == self.turn.id,
                    tables.turn.c.status.in_(NON_TERMINAL_STATUSES),
                )
            )

    async def _absorb_arrivals(
        self,
        messages: tuple[Message, ...],
        arrival_log: list[Message],
        absorbed_ids: list[UUID],
        requesters: dict[UUID, ActiveMessage] | None = None,
    ) -> tuple[Message, ...]:
        """Fold the conversation's queued arrivals into the window, each as its own
        <context>-tagged user message firing user_prompt_submit exactly as the founding inbound
        did. A denied arrival contributes only a generic marker and the hook's safe denial text,
        never its body or authority ref; an injection rides the message walled in its own delimiter
        so it never reads as member text. Whoever spoke each arrival and whichever agent it named,
        it joins this one turn: multiple members talking to a running bot is one turn, and the
        model handles the mixed voices. A subagent turn folds only what its own children deliver:
        its conversation is the parent's private channel that no member speaks into, so the claim
        leaves an external row there pending rather than rendering it as one of its own.

        The drain publishes the member rows it folded once they are in the window, so a surface
        holding a message it admitted into this turn learns the agent has it. A denied arrival is
        absorbed like any other and rides that frame: the member's message reached the turn,
        whatever the hook did with its content. An internally admitted row does not — it reaches the
        window the same way, but the frame is what a surface answers a member's own message with,
        and no member sent an extension's prompt or a child's result.

        Each drain that folds anything logs the ids it took, so one message is traceable from the
        queue into the window it was answered from — the one arrival event a replayed drain still
        emits, since the claim itself is memoized."""
        drained: list[UUID] = []
        claimed = await self._claim_arrivals(tuple(absorbed_ids))
        for arrival in claimed:
            absorbed_ids.append(arrival.id)
            if arrival.admission_source == MEMBER_ADMISSION:
                drained.append(arrival.id)
            if arrival.denial is not None:
                denied = Message(
                    role="user",
                    content=DENIED_INBOUND_NOTICE.format(reason=escape(arrival.denial)),
                )
                arrival_log.append(denied)
                messages = (*messages, denied)
                continue
            if arrival.rendered is None:
                raise RuntimeError("arrival has neither rendered content nor a denial")
            if requesters is not None:
                requesters[arrival.id] = ActiveMessage(
                    member_id=arrival.speaker_member_id,
                    rendered=arrival.rendered,
                )
            message = Message(role="user", content=arrival.rendered)
            arrival_log.append(message)
            messages = (*messages, message)
        if claimed:
            log(
                "turn.arrivals_absorbed",
                turn_id=str(self.turn.id),
                arrivals=" ".join(str(arrival.id) for arrival in claimed),
                members=len(drained),
            )
        if drained:
            await self._publish(Absorbed(arrivals=tuple(drained)))
        return messages

    async def _speak(self, spoken: tuple[MarkedReply, ...], round_index: int) -> None:
        """Deliver this round's marked spans to the members they answer, mid-turn and in the order
        the model wrote them: one delivery row per span for the poller to send through a surface
        that implements `speak` (Slack), and one Reply frame per span for a surface tailing the
        hub. On a live surface the frame is the whole delivery — the poller settles the row
        untouched, no projection reads it back, and the closing reply carries those words again.

        The row's id is the span's own identity (`mid_turn_reply_id_for`), and the insert ignores a
        conflict on it. That is what makes a replayed turn safe: a recovered workflow re-runs this
        body with its rounds memoized under this same attempt, derives the same ids, and inserts
        nothing — the row it would write is the row a poller already delivered. It is a plain write
        rather than a step for the same reason: the identity carries the idempotency, so a crash
        between the write and a step record cannot double-post either. A resumed run carries a fresh
        attempt, so the spans it marks in its own rounds are written and delivered rather than
        silently dropped onto the ids the parked attempt spent.

        A subagent turn speaks to no member: its conversation is the parent's private channel, and a
        tag in a child's output is text the parent reads, never a member's message. The markup is
        stripped from a child's window text all the same, so nothing can carry it outward."""
        if not spoken or self.turn.subagent_profile is not None:
            return
        for span_index, reply in enumerate(spoken):
            reply_id = mid_turn_reply_id_for(self.turn.id, round_index, span_index, self.attempt)
            async with workspace_tx() as connection:
                insert = (
                    postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert
                )
                written = await connection.execute(
                    insert(tables.mid_turn_reply)
                    .values(
                        id=reply_id,
                        workspace_id=self.turn.workspace_id,
                        turn_id=self.turn.id,
                        round_index=round_index,
                        span_index=span_index,
                        message_ref=reply.message_ref,
                        text=reply.text,
                        status=WRITEBACK_PENDING,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                    .on_conflict_do_nothing(index_elements=[tables.mid_turn_reply.c.id])
                )
            if written.rowcount == 1:
                log(
                    "turn.reply_spoken",
                    turn_id=str(self.turn.id),
                    reply_id=str(reply_id),
                    message_ref=str(reply.message_ref or ""),
                    round=round_index,
                    chars=len(reply.text),
                )
            await self._publish(Reply(id=reply_id, message_ref=reply.message_ref, text=reply.text))

    async def _stream_closing_spans(self, spoken: tuple[MarkedReply, ...]) -> None:
        """Put a closing round's marked spans back on the delta stream. The redaction withholds a
        span from the live text while the round runs, because a span belongs to the member as its
        own delivery: a tool-calling round's spans ride `Reply` frames instead. A closing round's
        spans ride nothing — their words are the answer the terminal frame carries — so a surface
        that prints the delta stream and takes the terminal as its cap, the ufo terminal, would end
        the turn having printed no answer at all. Publishing them here delivers the answer on the
        one stream that was missing it, and still exactly once: no Reply frame names them, and a
        surface that redraws from the terminal frame replaces the stream with the same words."""
        for reply in spoken:
            await self._publish(TextDelta(text=reply.text))

    async def _render_arrival(
        self,
        message_id: UUID,
        body: str,
        context: TurnContext | None,
        speaker_member_id: UUID | None,
        created_at: datetime,
    ) -> tuple[str | None, str | None]:
        """One arrival as the model sees it — user_prompt_submit fired exactly as for the founding
        inbound. An admitted message returns the <context> tag from the persisted moment plus any
        injection walled in its own delimiter. A denied message returns only the hook's safe denial
        text."""
        submitted = await self.hooks.fire(
            "user_prompt_submit",
            UserPromptSubmit(text=body),
            self.turn,
            self.agent,
            speaker_member_id,
        )
        if submitted.denied is not None:
            return None, submitted.denied
        content = _context_tag(message_id, context, created_at) + body
        if submitted.injected:
            content = INJECTED_CONTEXT.format(content=content, injected=submitted.injected)
        return content, None

    @DBOS.step(preemptible=True)
    async def _claim_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[Arrival, ...]:
        """Drain the conversation's pending inbound queue, memoized as a DBOS step: rows are
        stamped consumed by this turn, and the claim re-takes this turn's stamped rows that no
        recorded drain absorbed — so a crash between the stamp committing and the step recording
        re-executes the drain and recovers exactly the batch it had claimed, while absorbed rows
        are never re-taken. Each claimed row is rendered here — user_prompt_submit fires inside
        the step, so a replay of a recorded drain reuses the memoized rendering instead of
        re-firing hooks. An arrival is consumed exactly once and never lost. A subagent turn claims
        only internally admitted rows — the results its own children deliver — so nothing else can
        reach a channel that belongs to its parent. A drain whose body runs is by construction
        live, not a replay, and is where an adopted execution's window catches up with the queue —
        so it closes the adoption replay window."""
        self.adoption.replaying = False
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.update(tables.inbound_message)
                    .values(consumed_turn_id=self.turn.id)
                    .where(
                        tables.inbound_message.c.conversation_id == self.turn.conversation_id,
                        sa.or_(
                            tables.inbound_message.c.consumed_turn_id.is_(None),
                            sa.and_(
                                tables.inbound_message.c.consumed_turn_id == self.turn.id,
                                ~tables.inbound_message.c.id.in_(absorbed),
                            ),
                        ),
                        *(
                            (tables.inbound_message.c.admission_source == INTERNAL_ADMISSION,)
                            if self.turn.spawned
                            else ()
                        ),
                    )
                    .returning(
                        tables.inbound_message.c.id,
                        tables.inbound_message.c.seq,
                        tables.inbound_message.c.body,
                        tables.inbound_message.c.context,
                        tables.inbound_message.c.speaker_member_id,
                        tables.inbound_message.c.admission_source,
                        tables.inbound_message.c.created_at,
                    )
                )
            ).all()
        arrivals: list[Arrival] = []
        for row in sorted(rows, key=lambda row: row.seq):
            rendered, denial = await self._render_arrival(
                row.id,
                row.body,
                None if row.context is None else TurnContext.model_validate(row.context),
                row.speaker_member_id,
                row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC),
            )
            arrivals.append(
                Arrival(
                    id=row.id,
                    speaker_member_id=row.speaker_member_id,
                    admission_source=row.admission_source,
                    rendered=rendered,
                    denial=denial,
                )
            )
        if arrivals:
            log(
                "turn.arrivals_claimed",
                turn_id=str(self.turn.id),
                arrivals=" ".join(str(arrival.id) for arrival in arrivals),
                denied=sum(1 for arrival in arrivals if arrival.denial is not None),
            )
        return tuple(arrivals)

    async def _release_unabsorbed(self, absorbed: tuple[UUID, ...]) -> None:
        """Return stamped-but-unabsorbed arrivals (a drain whose step never recorded) to pending
        on a failed or cancelled exit, so the next live turn drains them. Best-effort: these exits
        must not stall, and the next admission's turn re-drains whatever a miss here left."""
        try:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.inbound_message)
                    .values(consumed_turn_id=None)
                    .where(
                        tables.inbound_message.c.consumed_turn_id == self.turn.id,
                        ~tables.inbound_message.c.id.in_(absorbed),
                    )
                )
        except Exception as error:
            log(
                "turn.arrival_release_failed",
                turn_id=str(self.turn.id),
                error_class=type(error).__name__,
            )

    async def _stream_recovering_overflow(
        self,
        messages: tuple[Message, ...],
        usage_events: list[Usage],
        system: str,
        tool_schemas: tuple[ToolSchema, ...],
        tool_choice: str | None,
        offer_tools: bool = True,
        force_finish: bool = False,
        active_requests: tuple[str, ...] = (),
        first_round: bool = False,
        include_requested_by: bool = True,
    ) -> tuple[tuple[Message, ...], StreamResult]:
        """Run one model round, recovering from a provider context-overflow: the proactive
        compaction already ran, so an overflow here means the window is still too large — force a
        compaction past the trigger and retry once. The recovered window is returned so it carries
        into the rest of the turn. When the forced compaction cannot shrink the window (nothing left
        to summarize), the overflow is unrecoverable and re-raises rather than retrying a doomed
        call; a non-overflow error re-raises unchanged."""
        try:
            result = await self._stream_once(
                _RoundInput(
                    messages=messages,
                    system=system,
                    offer_tools=offer_tools,
                    force_finish=force_finish,
                    first_round=first_round,
                    include_requested_by=include_requested_by,
                    tool_schemas=tool_schemas,
                    tool_choice=tool_choice,
                )
            )
            usage_events.extend(result.usages)
            if result.error_class is not None:
                raise ModelStreamError(
                    result.error_class, result.error_message or "", result.partial_output
                )
            return messages, result
        except Exception as error:
            if not is_context_overflow(error):
                raise
            compacted, compaction_usage = await self.compaction.maybe_compact(
                messages,
                force=True,
                active_requests=active_requests,
            )
            if not compaction_usage:
                raise
            usage_events.extend(compaction_usage)
            self._reseed_loaded_skills(compacted)
            emit_metric("turn_context_overflow_recovered_total", profile=self.profile)
            log("turn.context_overflow_recovered", turn_id=str(self.turn.id))
            result = await self._stream_once(
                _RoundInput(
                    messages=compacted,
                    system=system,
                    offer_tools=offer_tools,
                    force_finish=force_finish,
                    first_round=first_round,
                    include_requested_by=include_requested_by,
                    tool_schemas=tool_schemas,
                    tool_choice=tool_choice,
                )
            )
            usage_events.extend(result.usages)
            if result.error_class is not None:
                raise ModelStreamError(
                    result.error_class, result.error_message or "", result.partial_output
                ) from None
            return compacted, result

    async def _enforce_spend(
        self,
        usage_events: list[Usage],
        requesters: dict[UUID, ActiveMessage],
    ) -> None:
        """Before each model round, re-decide against the caps with this turn's in-flight spend
        priced in (this attempt's tokens land on the ledger at park/terminal, not yet), so a turn
        that crosses a cap mid-run is held rather than left to run the workspace past its limit.

        Any mid-run breach PARKS — the committed work is held and resumable, never discarded — even
        under a reject cap: reject is the inbound gate, applied before any tokens are spent, and a
        turn already running has real spend to preserve. A foreground subagent that parks under a
        reject cap holds its awaiting parent until the cap is raised. The fast-path skips the DB
        round-trip entirely once a recent decision confirmed no cap applies to this turn and the
        workspace holds no balance row to gate on.

        The seat gate re-checks every member whose message the turn has absorbed, so revoking any
        speaker's seat stops the aggregate before its next model call. A turn acting on behalf of
        a member gates on them too, whatever admitted it — a scheduled fire, a subagent, a monitor
        arrival. It costs one indexed read per round whatever the turn
        absorbed, deliberately and with no fast-path: a seat is what an admin revokes to cut someone
        off, so a cached answer would keep answering them for as long as it was held, and a
        running turn is the case the revoke most needs to reach.

        The balance stops at zero rather than at the reserve — the reserve is the headroom a turn
        needs to begin, so testing it again mid-run would park a turn the moment it dipped under a
        line it was only ever required to clear once, and the credit that resumed it would buy one
        round and park again. What is held against the balance is what the burn costs — and a
        burn the workspace's own key pays for costs it nothing, so the balance does not gate it.
        Holding a BYOK turn against a balance it never debits would park it, leave the balance
        untouched, let the dispatcher resume it, and park it again at the same point forever."""
        members = {
            message.member_id for message in requesters.values() if message.member_id is not None
        }
        if self.turn.on_behalf_of_member_id is not None:
            members.add(self.turn.on_behalf_of_member_id)
        if members:
            async with workspace_tx() as connection:
                if not await Seats(self.turn.workspace_id).all_seated(connection, members):
                    raise TurnParked(SEAT_REVOKED_MESSAGE)
        member_id = audience_member(self.audience)
        pending = (
            0 if self.byok else self.pricing.micro_usd(self.agent.model, _total_usage(usage_events))
        )
        if not balance_absent(self.turn.workspace_id):
            async with workspace_tx() as connection:
                sustained = await BalanceGate(self.turn.workspace_id, self.billing_url).sustains(
                    connection, pending, self.turn.id
                )
            if sustained.outcome != ALLOW:
                raise TurnParked(sustained.message)
        if applicable_caps_absent(self.turn.workspace_id, member_id, self.turn.agent_id):
            return
        async with workspace_tx() as connection:
            decision = await SpendEvaluator(
                self.turn.workspace_id, member_id, self.turn.agent_id
            ).decide(
                connection, self.pricing.micro_usd(self.agent.model, _total_usage(usage_events))
            )
        if decision.outcome != ALLOW:
            raise TurnParked(decision.message)

    @DBOS.step(preemptible=True)
    async def _stream_once(self, round_input: _RoundInput) -> StreamResult:
        """One model round, memoized as a DBOS step: it streams the deltas live to the hub and
        returns the round's text, tool calls, reasoning blocks, and usage as a StreamResult.
        Memoizing the round freezes the model-assigned `call_id`s and the round structure, so a
        crash-recovery replay returns this recorded output without re-calling the model (no tokens
        re-spent, the same tool ids, the same reasoning blocks to echo), and the per-tool
        `_dispatch` steps that follow key off those frozen ids. A mid-stream
        model error is caught and carried on the result, never raised out of the step, so the
        already-consumed usage — and the partial deltas, for the truncation salvage — survive in
        the recorded output; the caller re-raises it.

        The harness supplies the complete tool offer and any forced choice. This step supplies only
        the durable provider effect and runtime model settings."""
        tool_schemas = round_input.tool_schemas
        tool_choice = round_input.tool_choice
        if tool_schemas is None:
            finish = (
                None
                if self.output_model is None
                else ToolSchema(
                    name=FINISH_TOOL,
                    description=FINISH_DESCRIPTION,
                    input_schema=self.output_model.model_json_schema(),
                )
            )
            if round_input.force_finish:
                if finish is None:
                    raise RuntimeError("finish forced on a turn with no output model")
                tool_schemas = (finish,)
                tool_choice = FINISH_TOOL
            elif round_input.offer_tools:
                offered = self.tools.schemas(include_requested_by=round_input.include_requested_by)
                tool_schemas = offered if finish is None else (*offered, finish)
            else:
                tool_schemas = ()
        request = ModelRequest(
            model=self.agent.model,
            system=round_input.system,
            messages=round_input.messages,
            max_tokens=MAX_OUTPUT_TOKENS,
            conversation_cache_ttl="5m" if self.turn.spawned else "1h",
            session_id=str(self.turn.conversation_id),
            tools=tool_schemas,
            tool_choice=tool_choice,
            reasoning=(
                self.reasoning.internal_effort()
                if round_input.force_finish
                else self.agent.reasoning
            ),
        )
        if not round_input.first_round:
            gap = "within_turn"
        elif self.previous_turn_ended_at is None:
            gap = "new"
        else:
            gap_seconds = (datetime.now(UTC) - self.previous_turn_ended_at).total_seconds()
            if gap_seconds <= CACHE_5M_SECONDS:
                gap = "lte_5m"
            elif gap_seconds <= CACHE_1H_SECONDS:
                gap = "5m_1h"
            else:
                gap = "gt_1h"
        cache_dimensions = {
            "model": request.model,
            "provider": self.provider,
            "profile": self.profile,
            "conversation_ttl": request.conversation_cache_ttl,
            "round": "first" if round_input.first_round else "later",
            "gap": gap,
        }
        active_dimensions = {
            "model": request.model,
            "provider": self.provider,
            "profile": self.profile,
        }
        emit_up_down_metric("model_round_active", 1, **active_dimensions)
        try:
            with span(
                "model.round",
                model=request.model,
                provider=self.provider,
                round="first" if round_input.first_round else "later",
            ) as round_span:
                runner: ModelRoundRunner[ModelRequest, ToolUseBlock, ReasoningBlock, Usage] = (
                    ModelRoundRunner(
                        complete=self.model.complete,
                        events=RoundEventTypes(
                            stream_start=ModelStreamStart,
                            text=TextDelta,
                            tool_start=ToolCallStart,
                            tool_delta=ToolCallDelta,
                            reasoning=(ThinkingBlock, RedactedThinkingBlock, ReasoningItemBlock),
                            usage=Usage,
                        ),
                        new_tool_call=lambda call_id, name, arguments: ToolUseBlock(
                            id=call_id, name=name, input=arguments
                        ),
                        publish_text=lambda text: self.hub.publish(
                            self.turn.id, TextDelta(text=text)
                        ),
                        milestone=lambda event: round_span.add_event(f"model.{event}"),
                        monotonic=time.monotonic,
                    )
                )
                result = await runner.run(request, ReplyRedaction())
        finally:
            emit_up_down_metric("model_round_active", -1, **active_dimensions)
        emit_histogram(
            "model_round_ms",
            result.wall_ms,
            model=request.model,
            provider=self.provider,
            profile=self.profile,
            **({} if result.error_class is None else {"error_class": result.error_class}),
        )
        round_usage = _total_usage(result.usages)
        cache_result = "hit" if round_usage.cache_read_tokens else "miss"
        emit_metric("model_cache_round_total", **cache_dimensions, result=cache_result)
        if result.provider_start_ms is not None:
            emit_histogram(
                "model_provider_start_ms",
                result.provider_start_ms,
                **cache_dimensions,
                result=cache_result,
            )
        if result.first_visible_event_ms is not None:
            emit_histogram(
                "model_first_visible_event_ms",
                result.first_visible_event_ms,
                **cache_dimensions,
                result=cache_result,
            )
        for kind, amount in (
            ("input", round_usage.input_tokens),
            ("cache_read", round_usage.cache_read_tokens),
            ("cache_write_5m", round_usage.cache_write_5m_tokens),
            ("cache_write_30m", round_usage.cache_write_30m_tokens),
            ("cache_write_1h", round_usage.cache_write_1h_tokens),
        ):
            if amount:
                emit_metric("model_cache_tokens_total", amount, **cache_dimensions, kind=kind)
        for kind, amount in (
            ("input", round_usage.input_tokens),
            ("output", round_usage.output_tokens),
            ("cache_read", round_usage.cache_read_tokens),
            (
                "cache_write",
                round_usage.cache_write_5m_tokens
                + round_usage.cache_write_30m_tokens
                + round_usage.cache_write_1h_tokens,
            ),
        ):
            if amount:
                emit_metric(
                    "model_round_tokens_total",
                    amount,
                    model=request.model,
                    provider=self.provider,
                    kind=kind,
                    profile=self.profile,
                )
        return StreamResult(
            text=result.text,
            tool_calls=result.tool_calls,
            reasoning=result.reasoning,
            usages=result.usages,
            error_class=result.error_class,
            error_message=result.error_message,
            partial_output=result.partial_output,
        )

    async def _publish_cost(self, usage_events: list[Usage]) -> None:
        """After each model round, push the turn's spend so far as a live CostTick — the same priced
        total record_turn_usage will bill at terminal, streamed early so a surface shows a live cost
        meter. The live leg never fails the turn, so a publish failure is swallowed by _publish."""
        usage = _total_usage(usage_events)
        tokens = (
            usage.input_tokens
            + usage.output_tokens
            + usage.cache_read_tokens
            + usage.cache_write_5m_tokens
            + usage.cache_write_30m_tokens
            + usage.cache_write_1h_tokens
        )
        await self._publish(
            CostTick(cost_micro_usd=self.pricing.micro_usd(self.agent.model, usage), tokens=tokens)
        )

    def _reseed_loaded_skills(self, messages: tuple[Message, ...]) -> None:
        """Re-derive which skills' workflows the window holds, for the tracker the turn's
        `ToolContext` shares with `Compaction`. A compaction that may be followed by another
        `load_skill` needs this after it: draining the tracker into the summary empties it, but a
        compaction keeps a verbatim tail, so a load that survived there is still in front of the
        model and must stay suppressed for the dispatches that follow. The compaction inside
        The harness's forced final round is the exception: it ends the turn without offering tools.
        `preload` rides every reseed: a subagent's preloaded workflows sit in its system prompt,
        which no compaction touches."""
        self.compaction.loaded_skills.reseed(
            _loaded_skill_closures(messages, self.skills), preloaded=self.preload
        )

    def _resolve_call(self, call: ToolUseBlock) -> _Resolution:
        """Resolve one wire call into its declaration before anything reads it — the pass
        segmentation, requester policy, and the dispatch step all consume. An `object_action`
        call resolves into the bound action it names; every other name resolves in the wire
        registry. A failure here is a pre-dispatch error: it still claims its dispatch step in
        model order and fires no hooks, exactly as a malformed global call does."""
        if call.name == OBJECT_ACTION_TOOL:
            return self._resolve_action(call)
        try:
            tool = self.tools.get(call.name)
        except KeyError as error:
            return self._rejected(call, error)
        return EffectiveCall(
            call=call, tool=tool, call_id=tool.name, ext=self.tool_ext.get(call.name)
        )

    def _resolve_action(self, call: ToolUseBlock) -> _Resolution:
        """Resolve an `object_action` wire call: parse the structured target, look the action up
        on its kind, hold arity to the declared binding, enforce this turn's grant of the
        canonical id, and validate the action input against the action's own model — in that
        order, each failing loud before dispatch. A rejection BEFORE the lookup lands — a
        malformed wire shape, an unknown kind or action — meters `call=unregistered`, since the
        string it would name arrives on an assistant message the model wrote; a rejection on a
        looked-up action meters its registered canonical id, so per-action refusal rates stay
        readable at bounded cardinality."""
        unresolved = {"call": UNREGISTERED_TOOL}
        try:
            wire = ObjectActionInput.model_validate(call.input)
        except ValidationError as error:
            return self._rejected(call, error, dimensions=unresolved)
        held = self.verbs.actions.get(wire.kind, {})
        if not held and wire.kind not in self.verbs.registry:
            registered = ", ".join(sorted(self.verbs.registry)) or "none"
            return self._rejected(
                call,
                ValueError(f"no object kind {wire.kind!r}; registered kinds: {registered}"),
                dimensions=unresolved,
            )
        binding = held.get(wire.action)
        if binding is None:
            registered = ", ".join(sorted(held)) or "none"
            return self._rejected(
                call,
                ValueError(
                    f"no action {wire.action!r} on kind {wire.kind!r}; "
                    f"registered actions: {registered}"
                ),
                dimensions=unresolved,
            )
        tool = binding.action
        effective = EffectiveCall(
            call=call, tool=tool, call_id=tool.canonical_id, ext=binding.context, action=wire
        )
        dimensions = effective.meter_dimensions()
        declared = tool.bound
        assert declared is not None
        if declared.binding == "instance" and not wire.name:
            return self._rejected(
                call,
                ValueError(f"{tool.canonical_id} is an instance action; pass the object name"),
                dimensions=dimensions,
            )
        if declared.binding == "collection" and wire.name:
            return self._rejected(
                call,
                ValueError(f"{tool.canonical_id} is a collection action; it takes no name"),
                dimensions=dimensions,
            )
        if declared.binding == "collection" and wire.generation is not None:
            return self._rejected(
                call,
                ValueError(f"{tool.canonical_id} is a collection action; it takes no generation"),
                dimensions=dimensions,
            )
        if wire.agent and not tool.agent_targetable:
            return self._rejected(
                call,
                ValueError(f"{tool.canonical_id} takes no agent target"),
                dimensions=dimensions,
            )
        if tool.canonical_id not in self.granted_actions:
            return self._rejected(
                call,
                ValueError(f"action {tool.canonical_id} is not granted to this agent"),
                dimensions=dimensions,
            )
        try:
            args = tool.input_model.model_validate(wire.input)
        except ValidationError as error:
            return self._rejected(call, error, dimensions=dimensions)
        return replace(effective, action_args=args)

    def _rejected(
        self,
        call: ToolUseBlock,
        error: Exception,
        dimensions: Mapping[str, str] | None = None,
    ) -> _RejectedToolCall:
        return _RejectedToolCall(
            call=call,
            text=f"{type(error).__name__}: {error}",
            outcome="invalid_call" if isinstance(error, (ValueError, KeyError)) else "step_failed",
            error_class=type(error).__name__,
            dimensions={} if dimensions is None else dimensions,
        )

    async def _bind_or_error(
        self,
        context: ToolContext,
        item: _Resolution,
        requesters: dict[UUID, ActiveMessage],
    ) -> _DispatchInput:
        if isinstance(item, _RejectedToolCall):
            return item
        started = time.monotonic()
        try:
            bound_context, call = await self._bind_requester(context, item, requesters)
            return _BoundToolCall(
                context=bound_context,
                effective=replace(item, call=call),
                member_refs=self._member_refs(requesters),
            )
        except asyncio.CancelledError as error:
            _meter_dispatch(
                self.tools,
                item.call,
                started,
                "step_failed",
                type(error).__name__,
                self.profile,
                item.meter_dimensions(),
            )
            raise
        except TerminalAbsent as error:
            _meter_dispatch(
                self.tools,
                item.call,
                started,
                "step_failed",
                TerminalGone.__name__,
                self.profile,
                item.meter_dimensions(),
            )
            raise TerminalGone(str(error)) from error
        except Exception as error:
            return self._rejected(item.call, error, dimensions=item.meter_dimensions())

    async def _dispatch(
        self,
        bound: _DispatchInput,
        usage_events: list[Usage] | None = None,
    ) -> ToolResultBlock:
        result = await self._dispatch_step_recovering(bound, usage_events)
        return await self._dispatch_result(result)

    async def _dispatch_result(
        self,
        result: DispatchResult,
    ) -> ToolResultBlock:
        if not result.image_refs:
            block = ToolResultBlock(
                tool_use_id=result.tool_use_id,
                content=result.text,
                is_error=result.is_error,
                activity=result.activity,
            )
        else:
            images = [
                ImageBlock(
                    source=ImageSource(
                        media_type=ref.media_type,
                        data=(await self.blob.get(ref.blob_key)).decode(),
                    )
                )
                for ref in result.image_refs
            ]
            blocks: tuple[TextBlock | ImageBlock, ...] = (
                *((TextBlock(text=result.text),) if result.text else ()),
                *images,
            )
            block = ToolResultBlock(
                tool_use_id=result.tool_use_id,
                content=blocks,
                is_error=result.is_error,
                activity=result.activity,
            )
        self._activity.results.add(block.tool_use_id)
        return block

    async def _dispatch_step_recovering(
        self,
        bound: _DispatchInput,
        usage_events: list[Usage] | None,
    ) -> DispatchResult:
        target: ObjectActionTarget | None = None
        while True:
            result = await self._dispatch_step(bound, target)
            target = result.resume_target
            if self._accept_dispatch_result(result, usage_events):
                return result

    def _accept_dispatch_result(
        self,
        result: DispatchResult,
        usage_events: list[Usage] | None,
    ) -> bool:
        live = result.tool_use_id in self._live_dispatches
        self._live_dispatches.discard(result.tool_use_id)
        if usage_events is not None and not live:
            usage_events.extend(result.usages)
        if result.interrupted and live:
            raise asyncio.CancelledError
        return not result.interrupted

    async def _bind_requester(
        self,
        context: ToolContext,
        item: EffectiveCall,
        requesters: dict[UUID, ActiveMessage],
    ) -> tuple[ToolContext, ToolUseBlock]:
        """Bind the member this call acts for. A `requested_by` ref names one of the turn's active
        messages and binds its author. Without the ref, a call in a member's own conversation — the
        audience is that member's — binds that member while one of their messages is active, because
        nobody else can be asking there; the ref carries information only where more than one member
        could be, and there its omission means conversation-common work. Both routes read the same
        active messages, so a message a hook denied — absorbed without ever entering them —
        withholds its author's authority whichever route the model takes."""
        call = item.call
        tool_input = dict(call.input)
        requester: UUID | None = None
        profile_only = item.tool.profile_only
        if self.turn.subagent_profile is not None and profile_only:
            tool_input.pop(REQUESTED_BY, None)
        elif REQUESTED_BY in tool_input:
            raw = tool_input.pop(REQUESTED_BY)
            if not isinstance(raw, str):
                raise ValueError(f"{REQUESTED_BY} must be a message ref")
            try:
                message_id = UUID(raw)
            except ValueError as error:
                raise ValueError(f"{REQUESTED_BY} must be a message ref") from error
            if message_id not in requesters:
                raise ValueError(f"{REQUESTED_BY} does not name an active inbound message")
            requester = requesters[message_id].member_id
            if requester is None:
                raise ValueError(f"{REQUESTED_BY} message has no member requester")
        elif (member := self._own_member(requesters)) is not None:
            requester = member
        acting_member = requester if requester is not None else self.turn.on_behalf_of_member_id
        sandbox = (
            self.sandbox if self.sandbox_for is None else await self.sandbox_for(acting_member)
        )
        spawn, subagents = (
            (context.spawn, context.subagents)
            if self.subagents_for is None
            else self.subagents_for(acting_member)
        )
        return (
            replace(
                context,
                sandbox=sandbox,
                spawn=spawn,
                subagents=subagents,
                speaker_member_id=requester,
            ),
            call.model_copy(update={"input": tool_input}),
        )

    def _own_member(self, requesters: Mapping[UUID, ActiveMessage]) -> UUID | None:
        """The member whose conversation this is, while one of their messages is active — the one
        member who can be asking here, so an omitted `requested_by` binds them. Read off the active
        messages, never the turn row: a message a hook denied never enters them, so the denial
        withholds authority on this route exactly as it does for a named ref."""
        member = audience_member(self.audience)
        if member is None or all(message.member_id != member for message in requesters.values()):
            return None
        return member

    def _member_refs(self, requesters: Mapping[UUID, ActiveMessage]) -> tuple[UUID, ...]:
        """The message refs `requested_by` may name this round: the active member messages, where
        more than one member could be asking. In the member's own conversation the ref says nothing
        the binding does not already know, so none are offered and the schema omits the field."""
        if self._own_member(requesters) is not None:
            return ()
        return tuple(ref for ref, message in requesters.items() if message.member_id is not None)

    async def _offload(self, name: str, content: str) -> str | None:
        """Write `content` into the turn's private runtime output dir and return its path, ensuring
        the directory exists first. A member write (or bash) can leave a file squatting the name,
        which the bare `mkdir -p` in the write step cannot reclaim — left unhandled it fails
        `File exists` and poisons every later offload and salvage in the workspace.

        None means the write failed, and every caller degrades on it rather than lose the turn to
        plumbing: a turn that reached this point has already done its work. Loud, because what
        degrades is invisible to the model — it silently loses either a result's tail or its own
        salvaged partial."""
        relative = f"{TOOL_OUTPUT_DIRNAME}/{name}"
        try:
            if await self.sandbox.ensure_tool_output_dir():
                emit_metric("sandbox_tool_output_dir_reclaimed_total", profile=self.profile)
                log("sandbox.tool_output_dir_reclaimed", turn_id=str(self.turn.id))
            await self.sandbox.write_runtime_file(relative, content.encode())
        except Exception as error:
            emit_metric("tool_offload_failed_total", profile=self.profile)
            log(
                "tool.offload_failed",
                turn_id=str(self.turn.id),
                error_class=type(error).__name__,
                chars=len(content),
            )
            return None
        return await self.sandbox.runtime_display_path(relative)

    def _start_activity(self, call: ToolUseBlock, goal: str) -> None:
        self._activity.sequence += 1
        sequence = self._activity.sequence
        task = asyncio.create_task(self._generate_activity(call, goal, sequence))
        self._activity.tasks.add(task)
        task.add_done_callback(self._activity.tasks.discard)

    async def _generate_activity(self, call: ToolUseBlock, goal: str, sequence: int) -> None:
        activity = await self.activity_summarizer.summarize(call, goal)
        if activity is not None:
            self._activity.labels[call.id] = activity
        async with self._activity.lock:
            self._activity.ready[sequence] = (call.id, activity)
            while current := self._activity.ready.pop(self._activity.next_publish, None):
                self._activity.next_publish += 1
                _call_id, current_activity = current
                if current_activity is None:
                    continue
                await self._publish(Activity(text=current_activity))
                await self._publish_run(activity=current_activity)

    def _stop_activity(self) -> None:
        for task in tuple(self._activity.tasks):
            task.cancel()

    @DBOS.step(preemptible=True)
    async def _dispatch_step(
        self,
        bound: _DispatchInput,
        resume_target: ObjectActionTarget | None = None,
    ) -> DispatchResult:
        """Run one resolved binding in the DBOS step claimed for it in model order. A rejected bind
        claims the same step and records its error, so bind latency or outcome cannot change step
        order or count on recovery. A completed `DispatchResult` replays without rebinding or
        re-invoking the handler. An interrupted result checkpoints partial find usage; recovery
        consumes it and advances to a fresh step whose identical idempotency key deduplicates any
        side effect the interrupted handler applied. A bad requester, name, or arguments becomes an
        is_error result before any hook fires (there is no validated input to police). Then
        pre_tool_use may Deny
        (the tool never dispatches) or ModifyInput (fold the args); the handler runs in the sandbox
        with the folded args (a raising handler is an is_error result unless no terminal returned
        within its reconnect grace, which ends the turn). A bound action's target is read under
        the kind owner's context before the pre hook: a missing or refused object is an is_error
        result (`invalid_call`, no hook fires), while a kind store that faults on the read is the
        engine's failure, not the model's, and raises out of the step as `step_failed`. A
        non-error result over
        MAX_TOOL_RESULT_CHARS is offloaded — its full text written to the run's `tool-output`
        file and only a TOOL_RESULT_PREVIEW_CHARS preview plus that path kept in context, so no
        single result is re-ingested whole on every later round of the turn. The cap is a context
        budget, not a per-producer allowance: a tool's own limit caps a field, and the JSON its
        handler serializes around that field grows by an escaping cost the limit says nothing about,
        so what a producer declares says nothing about what it costs here. Past the cap the model
        reaches the result as a file it narrows with the filters the notice names, rather than as
        context every later round re-reads; the preview carries one whole record of a structured
        payload, which is what it needs to write that filter.
        An error result is instead bounded to the same cap. An untrusted result — the tool declares
        it, or the result carries a subagent profile's `untrusted_output` — is then walled in a
        data-only span so the model reads it as data, not instructions — the offload/bound and the
        wall both before post_tool_use, so any InjectContext guidance stays trusted outside the wall
        and the wall's close tag survives.
        A tool that dispatched and succeeded fires post_tool_use, which may ModifyOutput (replace
        the result) or InjectContext (append to it); a tool that dispatched and errored fires
        post_tool_use_failure instead, observe-only, so the error content the model recovers from is
        never rewritten. The pre-dispatch is_error result (a bad name or bad arguments) fires
        neither — it never ran. An extension tool gets its owning ExtensionContext; a builtin runs
        ext=None. A side-effecting tool additionally receives `ctx.idempotency_key`
        (`{turn}/{call}/{call_id}`, keyed by the semantic call id — a bound action's canonical
        id, never the wire dispatcher's name) to dedup its external write on a cross-attempt
        resume; a read
        tool receives None. A tool's image content (a read of an image/PDF, a browser screenshot)
        bypasses the text bound, wall, and hooks and rides a successful result as image blocks the
        model sees — bounded to TOOL_IMAGE_EDGE_LIMIT (Anthropic rejects any image over 2000px on
        a many-image request and downscales anything over ~1568px before the model sees it, so
        pixels past the limit buy no fidelity) and offloaded to the blob store and returned as
        references so the step log carries no image bytes; an error result drops its images and
        stays plain text so error-content consumers stay str-typed.
        One try encloses the whole step and its finally meters the call, so an end reaches the
        counter by leaving the body rather than by a call site remembering to name it — a raise past
        the handler (an image's blob put, a cancellation) is `step_failed` with its class, never an
        unrecorded call whose handler already ran. A gating hook that fails closed denies the call
        like a policy Deny and counts as `hook_failed`, so an extension hook that crashes or hangs
        is not read as policy. Inside the step is where it counts: the recorded result replays on a
        crash-recovery re-run without re-entering the body, so a replayed turn re-counts nothing."""
        call = bound.call
        self._live_dispatches.add(call.id)
        find_usages: list[Usage] = []
        started = time.monotonic()
        outcome, error_class = "ok", None
        semantic = (
            bound.dimensions
            if isinstance(bound, _RejectedToolCall)
            else bound.effective.meter_dimensions()
        )
        target = resume_target
        with span("tool.dispatch", tool=call.name, call=semantic.get("call", call.name)):
            try:
                if isinstance(bound, _RejectedToolCall):
                    outcome, error_class = bound.outcome, bound.error_class
                    return DispatchResult(
                        tool_use_id=call.id,
                        text=bound.text,
                        is_error=True,
                    )
                effective = bound.effective
                tool = effective.tool
                if (
                    self.adoption.replaying
                    and self._redoes_on_replay(tool)
                    and await self._pending_member_guidance()
                ):
                    outcome = "guidance_preempted"
                    log(
                        "turn.dispatch_preempted_by_guidance",
                        turn_id=str(self.turn.id),
                        tool=call.name,
                        call=effective.call_id,
                    )
                    return DispatchResult(
                        tool_use_id=call.id,
                        text=GUIDANCE_PREEMPTED_NOTICE,
                        is_error=True,
                        activity=True,
                    )
                context = bound.context
                if effective.action_args is not None:
                    args: BaseModel = effective.action_args
                else:
                    try:
                        args = tool.input_model.model_validate(call.input)
                    except Exception as error:
                        outcome, error_class = "invalid_call", type(error).__name__
                        return DispatchResult(
                            tool_use_id=call.id,
                            text=f"{type(error).__name__}: {error}",
                            is_error=True,
                            activity=True,
                        )
                if effective.action is not None and target is None:
                    try:
                        target = await self.verbs.action_target(context, tool, effective.action)
                    except ValueError as error:
                        outcome, error_class = "invalid_call", type(error).__name__
                        return DispatchResult(
                            tool_use_id=call.id,
                            text=f"{type(error).__name__}: {error}",
                            is_error=True,
                            activity=True,
                        )
                pre = await self.hooks.fire(
                    "pre_tool_use",
                    PreToolUse(
                        tool_name=call.name,
                        tool_input=args,
                        call=effective.call_id,
                        target=target,
                    ),
                    self.turn,
                    self.agent,
                    context.speaker_member_id,
                )
                if pre.denied is not None:
                    outcome, error_class = (
                        ("hook_denied", None)
                        if pre.failed_closed is None
                        else ("hook_failed", pre.failed_closed)
                    )
                    return DispatchResult(
                        tool_use_id=call.id,
                        text=pre.denied,
                        is_error=True,
                        activity=True,
                    )
                args = pre.tool_input if pre.tool_input is not None else args
                images: list[ImageBlock] = []
                key = (
                    f"{self.turn.id}/{effective.call_id}/{call.id}" if tool.side_effecting else None
                )
                try:
                    handler_context = replace(
                        context, ext=effective.ext, idempotency_key=key, target=target
                    )
                    find_usage_token = self._find_usages.set(find_usages)
                    try:
                        result = await tool.handler(handler_context, args)
                    finally:
                        self._find_usages.reset(find_usage_token)
                    text_parts: list[str] = []
                    for block in result.content:
                        match block:
                            case TextContent(text=text):
                                text_parts.append(text)
                            case ImageContent(media_type=media_type, data=data):
                                images.append(
                                    ImageBlock(source=ImageSource(media_type=media_type, data=data))
                                )
                    content = "".join(text_parts)
                    is_error = result.is_error
                    untrusted = tool.untrusted or result.untrusted
                    outcome, error_class = ("handler_error" if is_error else "ok"), None
                except TerminalAbsent as error:
                    raise TerminalGone(str(error)) from error
                except Exception as error:
                    content, is_error = f"{type(error).__name__}: {error}", True
                    if isinstance(error, SpeakerRequired) and bound.member_refs:
                        content += REQUESTED_BY_HINT.format(
                            refs=", ".join(str(ref) for ref in bound.member_refs)
                        )
                    untrusted = tool.untrusted or isinstance(error, UntrustedContentError)
                    outcome, error_class = "handler_raised", type(error).__name__
                if is_error:
                    content = _bounded(content)
                elif len(content) > MAX_TOOL_RESULT_CHARS:
                    path = await self._offload(f"{call.id}.txt", content)
                    content = (
                        content[:TOOL_RESULT_PREVIEW_CHARS]
                        + OFFLOAD_NOTICE.format(total=len(content), path=path)
                        if path is not None
                        else _bounded(content)
                    )
                if untrusted:
                    content = wall(effective.call_id, content)
                if is_error:
                    await self.hooks.fire(
                        "post_tool_use_failure",
                        PostToolUseFailure(
                            tool_name=call.name,
                            tool_input=args,
                            output=content,
                            call=effective.call_id,
                            target=target,
                        ),
                        self.turn,
                        self.agent,
                        context.speaker_member_id,
                    )
                else:
                    post = await self.hooks.fire(
                        "post_tool_use",
                        PostToolUse(
                            tool_name=call.name,
                            tool_input=args,
                            output=content,
                            call=effective.call_id,
                            target=target,
                        ),
                        self.turn,
                        self.agent,
                        context.speaker_member_id,
                    )
                    if post.output is not None:
                        content = post.output
                    if post.injected:
                        content = f"{content}\n{post.injected}"
                image_refs: list[ImageRef] = []
                if images and not is_error:
                    for index, image in enumerate(images):
                        bounded = await self._bounded_image(image)
                        blob_key = f"{TOOL_IMAGE_BLOB_DIR}/{self.turn.id}/{call.id}/{index}"
                        await self.blob.put(blob_key, bounded.source.data.encode())
                        image_refs.append(
                            ImageRef(media_type=bounded.source.media_type, blob_key=blob_key)
                        )
                return DispatchResult(
                    tool_use_id=call.id,
                    text=content,
                    is_error=is_error,
                    activity=True,
                    image_refs=tuple(image_refs),
                    usages=tuple(find_usages),
                )
            except asyncio.CancelledError:
                outcome, error_class = "step_failed", "CancelledError"
                return DispatchResult(
                    tool_use_id=call.id,
                    text="",
                    is_error=False,
                    activity=True,
                    usages=tuple(find_usages),
                    interrupted=True,
                    resume_target=target,
                )
            except Exception as error:
                outcome, error_class = "step_failed", type(error).__name__
                raise
            finally:
                _meter_dispatch(
                    self.tools, call, started, outcome, error_class, self.profile, semantic
                )

    def _redoes_on_replay(self, tool: ToolDef) -> bool:
        """Whether re-executing this call redoes its work, making it preemptible. A side-effecting
        declaration's re-execution dedups through the call's idempotency key — a spawn reattaches
        to its running child, a connector send dedups at the provider — and must keep that:
        preempting it strands the keyed work, and a re-issued call would duplicate it under a
        fresh call id. The flag is the resolved declaration's, so a bound action answers for
        itself, never for the wire dispatcher."""
        return not tool.side_effecting

    async def _pending_member_guidance(self) -> bool:
        """Whether a member message is queued for this conversation that no drain has taken. Read
        plainly, never as a step: it runs only inside a dispatch body that is already executing
        live, and gates that one execution."""
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(tables.inbound_message.c.id)
                    .where(
                        tables.inbound_message.c.conversation_id == self.turn.conversation_id,
                        tables.inbound_message.c.admission_source == MEMBER_ADMISSION,
                        tables.inbound_message.c.consumed_turn_id.is_(None),
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
        return row is not None

    async def _bounded_image(self, image: ImageBlock) -> ImageBlock:
        source = image.source
        try:
            opened: Image.Image = await asyncio.to_thread(
                Image.open, BytesIO(b64decode(source.data))
            )
            if max(opened.size) <= TOOL_IMAGE_EDGE_LIMIT:
                return image
            await asyncio.to_thread(
                opened.thumbnail, (TOOL_IMAGE_EDGE_LIMIT, TOOL_IMAGE_EDGE_LIMIT)
            )
            save_format = TOOL_IMAGE_SAVE_FORMATS.get(source.media_type, "PNG")
            if save_format == "JPEG" and opened.mode not in ("RGB", "L"):
                opened = await asyncio.to_thread(opened.convert, "RGB")
            buffer = BytesIO()
            await asyncio.to_thread(opened.save, buffer, format=save_format)
        except (Image.DecompressionBombError, OSError, ValueError) as error:
            log(
                "tool_image.bound_failed",
                turn_id=str(self.turn.id),
                error_class=type(error).__name__,
            )
            return image
        media_type = (
            source.media_type if source.media_type in TOOL_IMAGE_SAVE_FORMATS else "image/png"
        )
        return ImageBlock(
            source=ImageSource(media_type=media_type, data=b64encode(buffer.getvalue()).decode())
        )

    async def _commit(
        self,
        status: TerminalStatus,
        usage_events: list[Usage],
        meter: _TurnMeter,
        answer: str = "",
        error: BaseException | None = None,
        question: AskUserInput | None = None,
        credential_request: CredentialRequest | None = None,
        connect_request: ConnectRequest | None = None,
        created: tuple[ObjectRef, ...] = (),
        unless_arrivals: bool = False,
        absorbed: tuple[UUID, ...] = (),
    ) -> TerminalFrame | None:
        """Retries until the terminal state is durable: a client's wait always ends,
        so a database outage delays the commit rather than losing it. With unless_arrivals the
        commit holds the conversation lock admission inserts under and yields None instead of
        committing while any arrival is unabsorbed — pending in the queue, or stamped by a drain
        this execution never recorded — so a reply never closes over an unseen message.

        The terminal counter counts the transition, so it is emitted only by the call that wrote it:
        a commit that finds the row already terminal — a cancel that landed mid-turn — returns the
        frame it read, which the cancel path already counted. The execution's own wall clock and
        rounds are recorded either way, under the status it ended at."""
        delay = COMMIT_RETRY_INITIAL_SECONDS
        while True:
            try:
                frame, committed = await self._commit_once(
                    status,
                    usage_events,
                    answer,
                    error,
                    question,
                    credential_request,
                    connect_request,
                    created,
                    unless_arrivals,
                    absorbed,
                    meter.incomplete_reason,
                )
                break
            except Exception as commit_error:
                log(
                    "turn.commit_retry",
                    turn_id=str(self.turn.id),
                    error_class=type(commit_error).__name__,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2, COMMIT_RETRY_MAX_SECONDS)
        if frame is None:
            return None
        if committed:
            emit_metric(
                "turn_terminal_total",
                status=frame.status,
                error_class=frame.error_class or "",
                profile=self.profile,
            )
        meter.exited(frame.status)
        log(
            "turn.terminal",
            turn_id=str(self.turn.id),
            status=frame.status,
            error_class=frame.error_class or "",
            profile=self.profile,
            parent_turn_id=str(self.turn.parent_turn_id or ""),
            **(
                {"stack": formatted_stack(error)}
                if committed and error is not None and error.__traceback__ is not None
                else {}
            ),
        )
        return frame

    async def _publish_terminal(self, frame: TerminalFrame) -> None:
        await self._publish(Terminal(frame=frame))
        await self._publish_run(status=frame.status)
        self._stop_activity()

    async def _record_workspace_changes(self, targets: tuple[str, ...]) -> None:
        """Refresh what the portal's Changes reads, once the turn has nothing left the member is
        waiting on — the terminal frame is published and the transcript is durable, so a scan of the
        sandbox delays neither. It answers for the conversation that owns the sandbox rather than
        this turn's, since a subagent shares its parent's workspace and a member asks the parent
        what changed. The targets accumulated round by round from the memoized model outputs, so a
        recovered turn replays them and a mid-turn compaction of the message window cannot lose
        them."""
        await WorkspaceChangeRecorder(
            sandbox=self.sandbox,
            workspace_id=self.turn.workspace_id,
            conversation_id=self.turn.sandbox_conversation_id or self.turn.conversation_id,
            targets=targets,
        ).record()

    async def _commit_once(
        self,
        status: TerminalStatus,
        usage_events: list[Usage],
        answer: str,
        error: BaseException | None,
        question: AskUserInput | None,
        credential_request: CredentialRequest | None,
        connect_request: ConnectRequest | None,
        created: tuple[ObjectRef, ...],
        unless_arrivals: bool,
        absorbed: tuple[UUID, ...],
        incomplete_reason: IncompleteReason | None,
    ) -> tuple[TerminalFrame | None, bool]:
        usage = _total_usage(usage_events)
        async with workspace_tx() as connection:
            if unless_arrivals:
                await connection.execute(
                    sa.select(tables.conversation.c.id)
                    .where(tables.conversation.c.id == self.turn.conversation_id)
                    .with_for_update()
                )
                pending = (
                    await connection.execute(
                        sa.select(sa.func.count())
                        .select_from(tables.inbound_message)
                        .where(
                            tables.inbound_message.c.conversation_id == self.turn.conversation_id,
                            sa.or_(
                                tables.inbound_message.c.consumed_turn_id.is_(None),
                                sa.and_(
                                    tables.inbound_message.c.consumed_turn_id == self.turn.id,
                                    ~tables.inbound_message.c.id.in_(absorbed),
                                ),
                            ),
                        )
                    )
                ).scalar_one()
                if pending:
                    log(
                        "turn.commit_refused_by_arrivals",
                        turn_id=str(self.turn.id),
                        status=status,
                        pending=pending,
                        absorbed=len(absorbed),
                    )
                    return None, False
            try:
                await record_turn_usage(
                    connection,
                    self.turn.workspace_id,
                    self.turn.id,
                    self.agent.model,
                    usage,
                    self.attempt,
                    pricing=self.pricing,
                    byok=self.byok,
                )
            except TurnUsageConflict as conflict:
                log_error(
                    "turn.billing_conflict",
                    turn_id=str(self.turn.id),
                    error_class=type(conflict).__name__,
                )
            cost = await read_turn_cost(connection, self.turn.id, TOKENS_DIMENSION)
            spend = cost if cost is not None else _NOTHING_SPENT
            match error:
                case None:
                    error_class = error_message = None
                case ModelStreamError():
                    error_class = error.model_error_class
                    error_message = error.model_error_message
                case _:
                    error_class = type(error).__name__
                    error_message = str(error)
            frame = TerminalFrame(
                status=status,
                text=answer,
                incomplete_reason=incomplete_reason,
                error_class=error_class,
                error_message=(
                    error_message[:TERMINAL_ERROR_MESSAGE_MAX_CHARS]
                    if error_message is not None
                    else None
                ),
                tokens=spend.tokens,
                cost_micro_usd=spend.micro_usd,
                cache_percent=spend.cache_percent,
                model=spend.model,
                reasoning=self.agent.reasoning if spend.model else None,
                environment=(
                    None
                    if self.turn.runtime_config is None
                    else self.turn.runtime_config.environment
                ),
                question=question,
                credential_request=credential_request,
                connect_request=connect_request,
                created=created,
            )
            updated = await connection.execute(
                sa.update(tables.turn)
                .values(
                    status=status,
                    terminal=frame.model_dump(mode="json"),
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.turn.c.id == self.turn.id,
                    tables.turn.c.status.in_(("queued", "running")),
                )
            )
            if updated.rowcount == 0:
                row = (
                    await connection.execute(
                        sa.select(tables.turn.c.terminal).where(tables.turn.c.id == self.turn.id)
                    )
                ).one()
                return TerminalFrame.model_validate(row.terminal), False
        return frame, True

    async def _park(self, message: str, usage_events: list[Usage]) -> None:
        """Hold the turn at a spend cap: bill this attempt's consumed tokens, commit the
        non-terminal parked state (durable, resumable), release the arrivals this attempt claimed
        (a resume is a fresh workflow with an empty step log, so it must re-drain them), and end
        the surface's stream with the reason — one transaction.
        Billing at park is what makes a tight cap CONVERGE: the ledger
        reflects the real burn, so the resume sweep re-decides against actual spend and finds no
        headroom until the cap is raised — never an unbilled runaway re-burning tokens the cap
        can't see. Keyed by this attempt's workflow id, so the aborted partial and the eventual
        full run both count."""
        async with workspace_tx() as connection:
            updated = await connection.execute(
                sa.update(tables.turn)
                .values(status=PARKED, updated_at=sa.func.now())
                .where(
                    tables.turn.c.id == self.turn.id,
                    tables.turn.c.status.in_(NON_TERMINAL_STATUSES),
                )
            )
            if updated.rowcount == 1:
                await record_turn_usage(
                    connection,
                    self.turn.workspace_id,
                    self.turn.id,
                    self.agent.model,
                    _total_usage(usage_events),
                    self.attempt,
                    pricing=self.pricing,
                    byok=self.byok,
                )
                await connection.execute(
                    sa.update(tables.inbound_message)
                    .values(consumed_turn_id=None)
                    .where(tables.inbound_message.c.consumed_turn_id == self.turn.id)
                )
        if updated.rowcount == 1:
            await self._publish(Parked(message=message))
            emit_metric("turn_parked_total", profile=self.profile)
            log("turn.parked", turn_id=str(self.turn.id))

    async def _publish(self, frame: LiveFrame) -> None:
        """The live leg never fails the turn; the durable terminal/parked state is authoritative."""
        try:
            await self.hub.publish(self.turn.id, frame)
        except Exception as error:
            log(
                "hub.publish_failed",
                turn_id=str(self.turn.id),
                error_class=type(error).__name__,
            )

    async def _publish_run(self, activity: str = "", status: str = "") -> None:
        """Mirror a subagent turn's member-facing moment — starting, a dispatch, its terminal —
        onto the root turn's stream, the one every surface tails. A main turn has no lineage and
        publishes nothing here. The live leg never fails the turn, as in `_publish`."""
        if self.lineage is None:
            return
        frame = SubagentActivity(
            turn_id=self.turn.id,
            parent_turn_id=self.lineage.parent_turn_id,
            conversation_id=self.turn.conversation_id,
            profile=self.lineage.profile,
            name=self.lineage.name,
            activity=activity,
            status=status,
        )
        try:
            await self.hub.publish(self.lineage.root_turn_id, frame)
        except Exception as error:
            log(
                "hub.publish_failed",
                turn_id=str(self.turn.id),
                error_class=type(error).__name__,
            )

    async def _stop_sandbox_commands(self) -> None:
        """Stop what the turn left running in its sandbox, on a deliberate cancel only. This is the
        place the two cancels are told apart: a carrier's `exec` meets a member's stop and an
        executor preemption as the same bare `asyncio.CancelledError`, while this path is the one
        DBOS raises after `cancel_workflow` landed — the turn is over and its commands answer to
        nobody, where a preempted step is replayed and needs the work its command is still doing. It
        runs after the turn's own records are written, since a provider round trip must not hold
        them up, and a stop that fails leaves the cancel standing: the cancelled terminal is already
        durable and a sandbox fault must not re-label it."""
        try:
            await self.sandbox.stop_commands()
        except Exception as error:
            log_error(
                "turn.sandbox_stop_failed",
                turn_id=str(self.turn.id),
                error_class=type(error).__name__,
            )

    async def _bill_cancelled(self, usage_events: list[Usage]) -> None:
        """Best-effort: cancellation must not stall on billing, but consumed tokens count."""
        usage = _total_usage(usage_events)
        try:
            async with workspace_tx() as connection:
                await record_turn_usage(
                    connection,
                    self.turn.workspace_id,
                    self.turn.id,
                    self.agent.model,
                    usage,
                    self.attempt,
                    pricing=self.pricing,
                    byok=self.byok,
                )
        except Exception as error:
            log(
                "turn.cancel_billing_failed",
                turn_id=str(self.turn.id),
                error_class=type(error).__name__,
            )

    async def _resolve_unclaimed(self) -> TerminalFrame | None:
        """This execution lost the running claim — the turn is owned by another live execution (a
        duplicate resume enqueue) or already finished (a re-delivery). The repair flow republishes
        its committed terminal, or no-ops (None) while it is still running so the live execution
        stays the sole authority and this duplicate never clobbers it with a spurious terminal."""
        return await self._repair().resolve()

    async def _persist_transcript(
        self, messages: tuple[Message, ...], answer: str, system: str, injected: str
    ) -> None:
        await self._repair().persist_transcript(self._labeled(messages), answer, system, injected)

    async def _persist_interrupted(self, messages: tuple[Message, ...]) -> None:
        if messages:
            await self._repair().persist_interrupted(self._labeled(messages), ran=self._window.ran)
        else:
            await self._repair().persist_inbound(founding_denial=self._window.denied)

    def _labeled(self, messages: tuple[Message, ...]) -> tuple[Message, ...]:
        """The window with each tool result carrying the activity line this turn showed for it, so
        a stored record reads the way the surface did."""
        return tuple(
            message.model_copy(
                update={
                    "content": tuple(
                        block.model_copy(
                            update={
                                "activity_text": self._activity.labels.get(block.tool_use_id, "")
                            }
                        )
                        if (
                            isinstance(block, ToolResultBlock)
                            and block.activity
                            and block.tool_use_id in self._activity.results
                        )
                        else block
                        for block in message.content
                    )
                }
            )
            if not isinstance(message.content, str)
            else message
            for message in messages
        )
