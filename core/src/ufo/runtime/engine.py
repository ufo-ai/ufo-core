"""One turn, top to bottom: mark running, load context, model rounds absorbing queued arrivals,
terminal commit.

`run()` is the body of the `turn_workflow` DBOS workflow. Its non-deterministic, side-effecting
units are DBOS steps — each model round (`_stream_once`), each tool dispatch (`_dispatch_step`),
each arrival drain (`_claim_arrivals`), and each context boundary (the selected strategy's
own memoized step). On a
crash the workflow re-dispatches under the same `workflow_id`: every recorded step replays from
DBOS's `operation_outputs` without re-executing — completed rounds are not re-called, completed
tools not re-applied, drained arrivals not re-consumed — and execution resumes at the first
unrecorded step. The queue claims before loading; setup then reclaims the same attempt while
loading context, attaching the sandbox, and re-deciding spend. Each step is idempotent across
replay."""

import asyncio
import json
import time
from base64 import b64decode, b64encode
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from html import escape
from io import BytesIO
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from dbos import DBOS
from dbos._error import DBOSWorkflowCancelledError
from PIL import Image
from pydantic import BaseModel, ValidationError, model_validator
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

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
    ModelAccountRateLimited,
    ModelAccountUnavailable,
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
from ufo.harness.models.registry import ServingModel
from ufo.harness.models.spec import ModelSpec
from ufo.harness.o11y import (
    emit_histogram,
    emit_metric,
    emit_up_down_metric,
    formatted_stack,
    log,
    log_error,
    mark_span_outcome,
    span,
    turn_profile,
    warn,
)
from ufo.harness.replies import (
    MarkedArtifact,
    MarkedReply,
    SpanRedaction,
    marked_artifacts,
)
from ufo.harness.rounds import ModelRoundRunner, RoundEventTypes
from ufo.harness.sandbox.session import (
    TOOL_CALL_ID,
    TOOL_OUTPUT_DIRNAME,
    Sandbox,
    SandboxProviderUnavailable,
    workspace_path,
)
from ufo.harness.sandbox.terminal import TerminalAbsent, TerminalGone
from ufo.harness.untrusted import wall
from ufo.runtime.access.connectors import ConnectorRegistry
from ufo.runtime.access.credentials import CredentialRequests
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.access.member_authorization import (
    MEMBER_AUTHORIZATION_ACTIVE_MESSAGES,
    MEMBER_AUTHORIZATION_CONTEXT_CHARS,
    MEMBER_AUTHORIZATION_CONTEXT_MESSAGE_CHARS,
    MEMBER_AUTHORIZATION_MESSAGE_CHARS,
    MEMBER_AUTHORIZATION_RECENT_MESSAGES,
    AuthorizationAnswer,
    AuthorizationAttempt,
    AuthorizationBinding,
    AuthorizationContext,
    AuthorizationContextMessage,
    AuthorizationEffect,
    AuthorizationGate,
    AuthorizationRequest,
    AuthorizationScope,
)
from ufo.runtime.access.workspace_slots import WorkspaceSlots
from ufo.runtime.billing.accounting import (
    TOKENS_DIMENSION,
    UNGATED_LEDGER,
    Ledger,
    SpendEvaluator,
    TurnCost,
    TurnUsageConflict,
    applicable_caps_absent,
    read_turn_cost,
)
from ufo.runtime.billing.spend import ALLOW, NO_SPEND_GATES, SpendGates
from ufo.runtime.context_boundary import ContextBoundary
from ufo.runtime.ext.context import ExtensionContext
from ufo.runtime.ext.hooks import HookChain
from ufo.runtime.ext.manifest import (
    PostToolUse,
    PostToolUseFailure,
    PreToolUse,
    Stop,
    UserPromptSubmit,
)
from ufo.runtime.ext.source_reader import SourceReader
from ufo.runtime.ext.surface import member_message_ref, member_message_text
from ufo.runtime.hub import (
    Absorbed,
    Activity,
    ArtifactsChanged,
    CostTick,
    Created,
    Hub,
    LiveFrame,
    Parked,
    Reply,
    Resumed,
    SourceRef,
    Sources,
    SubagentActivity,
    Terminal,
)
from ufo.runtime.media.artifact_url import ARTIFACT_KEY_PREFIX, artifact_media_type
from ufo.runtime.media.site_previewer import SitePreviewer
from ufo.runtime.memory import MemorySearch
from ufo.runtime.object_name import ObjectRef
from ufo.runtime.object_scope import ObjectActionRequestTarget, ObjectActionTarget
from ufo.runtime.objects import ObjectActionInput, ObjectVerbs
from ufo.runtime.prompts.render import RenderedPrompt
from ufo.runtime.search import SearchProvider
from ufo.runtime.seats import SEAT_REVOKED_MESSAGE, Seats
from ufo.runtime.skills.runtime import CORE_SKILL_REGISTRY, LoadedRef, LoadedSkill, SkillRegistry
from ufo.runtime.tools.bridge import ToolBridgeIntent
from ufo.runtime.tools.context import (
    RESULT_CUT_MARKER,
    SHARED_BYTES_LIMIT,
    ImageContent,
    Spawn,
    SpeakerRequired,
    SubagentControl,
    TextContent,
    ToolContext,
    UntrustedContentError,
    clipped,
    measure_file,
    store_artifact,
)
from ufo.runtime.tools.question import question_result_text
from ufo.runtime.tools.registry import (
    OBJECT_ACTION_TOOL,
    OBJECT_GET_TOOL,
    OBJECT_LIST_TOOL,
    REQUESTED_BY,
    TRUSTED_TOOL_INPUT,
    ToolDef,
    ToolRegistry,
)
from ufo.runtime.transcript import Transcript
from ufo.runtime.turns.activity import (
    ACTIVITY_RECENT_LABELS,
    SKILL_LOAD_TOOL,
    ActivitySummarizer,
)
from ufo.runtime.turns.audience import Audience, audience_subjects
from ufo.runtime.turns.changes import turn_conversation_changed
from ufo.runtime.turns.contracts import Contract, freeform_result_contract
from ufo.runtime.turns.delivery_register import DIRECT_PROSE_RESULT_MAX_CHARS
from ufo.runtime.turns.record import subagent_activity
from ufo.runtime.turns.transcript import (
    MEMBER_CONTEXT_OPENING,
    Conversation,
    ParkedRequester,
    ParkedTurn,
)
from ufo.runtime.turns.workspace_changes import WorkspaceChangeRecorder, change_targets
from ufo.runtime.workspace import PLATFORM_FUNDED
from ufo.schema import tables
from ufo.schema.records import (
    CANCELLED,
    DELIVERY_PENDING,
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
    ActivityEvent,
    Agent,
    AskUserInput,
    AuthorizationChoice,
    ConnectRequest,
    CredentialRequest,
    IncompleteReason,
    ModelRouteChange,
    ModelRouteFailure,
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
MAX_MIDSTREAM_ROUND_RETRIES = 1
CARRIED_FILE_KEY_PART = "artifact"
PROVIDER_RETRY_NOTICE = "The model provider limited this task. It will retry after {retry_at}."
MODEL_ROUTE_FAILURES: Mapping[str, ModelRouteFailure] = {
    ModelAccountRateLimited.__name__: "rate_limited",
    ModelAccountUnavailable.__name__: "unavailable",
}
SANDBOX_PROVIDER_RETRY_NOTICE = (
    "The sandbox provider is unavailable. This task will retry after {retry_at}."
)
SANDBOX_PROVIDER_RETRY_SECONDS = 60
SANDBOX_PROVIDER_RETRY_LIMIT = 15
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
NO_REQUESTER_HINT = (
    " No member message is active in this turn, so there is no message_ref to name and this call "
    "cannot carry member authority now. Do the part of the work that needs no member, and report "
    "what a member must ask for."
)
SCHEMA_HINT = (
    " {model} takes these fields at the top level of the input: {fields}. Pass each one there, "
    "under that exact name, and pass no field this tool's schema does not declare."
)
SCHEMA_HINT_KINDS = frozenset({"missing", "extra_forbidden"})
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
SCHEDULED_MEMORY_CONTEXT = "<recalled_memory>\n{recalled}\n</recalled_memory>"
SCHEDULED_MEMORY_SEARCH_TIMEOUT_SECONDS = 4.0
_NOTHING_SPENT = TurnCost(tokens=0, micro_usd=0, model="", cache_percent=0)


FRESH_CLAIM = "fresh"
ADOPTED_CLAIM = "adopted"


async def _claim_turn(turn_id: UUID, attempt: str) -> str | None:
    """The engine's in-loop re-claim always reads as adopted: the queue claimed ahead of it."""
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
                    retry_at=None,
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
        if workspace_id is not None:
            await turn_conversation_changed(connection, turn_id)
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
OFFLOAD_NOTICE = (
    RESULT_CUT_MARKER + "preview only — the full {total} chars are at {path} — narrow it with bash "
    "(jq, grep, sed) or read it with offset/limit; reading it whole offloads again]"
)
BARE_RAISE_NOTICE = (
    "{cls}: {tool} raised {cls} with no message. Nothing further was recorded about this "
    "failure — the exception class is the whole diagnostic."
)
NO_DIAGNOSTIC_NOTICE = (
    "This call failed and returned no diagnostic. Nothing about the cause reached the result, so "
    "there is nothing here to correct against; the call may have applied part of its effect."
)
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
    recovery to salvage into a workspace file. `error_kind` is set exactly when the stream died to
    a `ModelStreamInterrupted` — the transient-fault signal the round retry keys on, named so the
    retry metric can say which fault shape it recovered. `reasoning` is the round's blocks in the
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
    error_kind: str | None = None
    retry_after_seconds: float | None = None
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
    authorization_answer: AuthorizationAnswer | None = None
    requesting_message_ref: UUID | None = None


@dataclass(frozen=True)
class ActiveMessage:
    member_id: UUID | None
    rendered: str
    admission_source: TurnAdmissionSource = MEMBER_ADMISSION
    authorization_answer: AuthorizationAnswer | None = None
    reply_to_ref: str | None = None
    reply_to_text: str | None = None
    continued: bool = False


def _authorization_message_text(message: Message) -> str:
    if isinstance(message.content, str):
        return member_message_text(message.content) if message.role == "user" else message.content
    return "".join(block.text for block in message.content if isinstance(block, TextBlock))


def _authorization_assistant(
    turn_id: UUID, index: int, message: Message
) -> AuthorizationContextMessage | None:
    if message.role != "assistant":
        return None
    text = _authorization_message_text(message).strip()
    if not text:
        return None
    return AuthorizationContextMessage(
        ref=str(uuid5(NAMESPACE_URL, f"{turn_id}/authorization-context/{index}/{text}")),
        role="assistant",
        member_id=None,
        text=text[:MEMBER_AUTHORIZATION_CONTEXT_MESSAGE_CHARS],
    )


@dataclass(frozen=True)
class _PreparedRun:
    system: str
    messages: tuple[Message, ...]
    injected: str
    terminal: TerminalFrame | None = None


@dataclass(frozen=True)
class _OpenActs:
    question: AskUserInput | None = None
    credential_request: CredentialRequest | None = None
    connect_request: ConnectRequest | None = None


@dataclass(frozen=True, repr=False)
class _RoundInput:
    messages: tuple[Message, ...]
    system: str
    offer_tools: bool
    force_finish: bool
    first_round: bool
    round_index: int
    tool_schemas: tuple[ToolSchema, ...] | None = None
    tool_choice: str | None = None

    def __repr__(self) -> str:
        return (
            f"_RoundInput(messages={len(self.messages)}, system_chars={len(self.system)}, "
            f"offer_tools={self.offer_tools}, force_finish={self.force_finish}, "
            f"first_round={self.first_round}, round_index={self.round_index})"
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
    kind: str = ""

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
        """The semantic telemetry dimensions beside the wire `tool`: the call id, the object kind
        the call names when it names one, and for a bound call its binding and contributor."""
        if self.tool.bound is None:
            return {"call": self.call_id, **({"kind": self.kind} if self.kind else {})}
        return {
            "call": self.call_id,
            "kind": self.tool.bound.kind,
            "binding": self.tool.bound.binding,
            "contributor": "core" if self.ext is None else self.ext.store.extension,
        }

    def dispatch_key(self, turn_id: UUID) -> str:
        return f"{turn_id}/{self.call_id}/{self.call.id}"

    def __repr__(self) -> str:
        return f"EffectiveCall(call={self.call_id}, call_id={self.call.id})"


@dataclass(frozen=True, repr=False)
class _BoundToolCall:
    context: ToolContext
    effective: EffectiveCall
    member_refs: tuple[UUID, ...] = ()
    selected_message_ref: UUID | None = None
    selected_message: str = ""
    selected_from_multiple: bool = False
    authorization_pending: bool = False
    authorization_answer: AuthorizationAnswer | None = None
    authorization_context: AuthorizationContext | None = None
    selected_message_complete: bool = True

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


def _selected_member_authorization(bound: _BoundToolCall) -> bool:
    return bound.selected_message_ref is not None and (
        bound.selected_from_multiple or bound.authorization_pending
    )


@dataclass(frozen=True)
class _DispatchReady:
    context: ToolContext
    effective: EffectiveCall
    args: BaseModel
    target: ObjectActionTarget | None


class _AuthorizationPreflight(BaseModel, frozen=True):
    args_json: str
    request_target: ObjectActionRequestTarget | None
    attempt: AuthorizationAttempt | None = None
    denied: str | None = None
    failed_closed: str | None = None
    authority_error: str | None = None
    authority_error_class: str | None = None

    def __setstate__(self, state: dict[str, object]) -> None:
        if "__dict__" not in state:
            fields = dict(state)
            args = fields.pop("args")
            if not isinstance(args, BaseModel):
                raise TypeError("authorization preflight arguments must be a model")
            fields["args_json"] = args.model_dump_json(round_trip=True, by_alias=True)
            state = type(self).model_validate(fields).__getstate__()
        super().__setstate__(state)


@dataclass(frozen=True)
class _ValidatedDispatch:
    args: BaseModel
    request_target: ObjectActionRequestTarget | None


@dataclass(frozen=True)
class _AuthorizedDispatch:
    context: ToolContext
    args: BaseModel


@dataclass(frozen=True)
class _DispatchGate:
    target: ObjectActionTarget | None
    ready: _DispatchReady | None = None
    result: "DispatchResult | None" = None
    outcome: str = "ok"
    error_class: str | None = None


@dataclass(frozen=True)
class _HandlerOutput:
    content: str
    is_error: bool
    untrusted: bool
    images: tuple[ImageBlock, ...]
    outcome: str
    error_class: str | None = None
    sources: tuple[SourceRef, ...] = ()
    completion: str | None = None
    created: tuple[ObjectRef, ...] = ()


@dataclass
class _RoundWindow:
    messages: tuple[Message, ...] = ()
    ran: bool = False
    denied: str | None = None


@dataclass(frozen=True)
class _Segment:
    """One route's share of an attempt's burn: the model that served it, the ledger series it
    bills under, and the usage it consumed."""

    model: str
    attempt: str
    usage: Usage
    byok: bool


@dataclass
class _Burn:
    """The starting route keeps the attempt's ledger series: a rolling deploy's recovery advances
    the row the outgoing image wrote at shutdown."""

    left: list[tuple[str, int, bool]] = field(default_factory=list)

    def segments(
        self, serving: str, attempt: str, usage_events: Sequence[Usage], serving_byok: bool
    ) -> tuple[_Segment, ...]:
        cut: list[_Segment] = []
        start = 0
        for index, (model, end, byok) in enumerate(
            (*self.left, (serving, len(usage_events), serving_byok))
        ):
            series = attempt if index == 0 else f"{attempt}/{model}"
            cut.append(_Segment(model, series, _total_usage(usage_events[start:end]), byok))
            start = end
        return tuple(cut)


@dataclass
class _ActivityState:
    sequence: int = 0
    next_publish: int = 1
    tasks: set[asyncio.Task[None]] = field(default_factory=set)
    labels: dict[str, str] = field(default_factory=dict)
    results: set[str] = field(default_factory=set)
    ready: dict[int, tuple[str, str, str | None]] = field(default_factory=dict)
    started: set[str] = field(default_factory=set)
    labeled: set[str] = field(default_factory=set)
    sources: dict[str, tuple[SourceRef, ...]] = field(default_factory=dict)
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
    step with the same tool call and idempotency key. `unwalled_error` is an untrusted tool's error
    text before its wall, for the member a prepared intent's refusal reaches; it is None when the
    error carries no wall."""

    tool_use_id: str
    text: str
    is_error: bool
    activity: bool = False
    image_refs: tuple[ImageRef, ...] = ()
    usages: tuple[Usage, ...] = ()
    interrupted: bool = False
    resume_target: ObjectActionTarget | None = None
    question: AskUserInput | None = None
    sources: tuple[SourceRef, ...] = ()
    completion: str | None = None
    created: tuple[ObjectRef, ...] = ()
    unwalled_error: str | None = None

    @model_validator(mode="after")
    def _errors_say_something(self) -> "DispatchResult":
        if self.is_error and not self.text.strip():
            self.text = NO_DIAGNOSTIC_NOTICE
        return self


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
    """A running turn is held non-terminally until its gate clears or its retry time arrives."""

    def __init__(
        self,
        message: str,
        retry_at: datetime | None = None,
        external_retry_count: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retry_at = retry_at
        self.external_retry_count = external_retry_count


def sandbox_provider_park(turn: Turn) -> TurnParked | None:
    """The next hold in the sandbox provider's retry schedule, or None when this turn must take the
    provider fault instead: its retries are spent, or a parent awaits its result inline.

    A child the parent waits on is cancelled the moment it parks — `Subagents._terminal_or_park`
    reads the parked row, cancels the turn, and raises — so a park there spends no retry, discards
    the rounds the child already ran, and names a cause that did not happen. The provider fault
    stands instead, which fails the child on the real error the parent can report. It is the gate
    `defer_long_retry` puts on the model provider's long retry, which the same turns keep inline."""
    if turn.spawned and turn.result_delivery != DELIVERY_PENDING:
        return None
    retry_count = turn.external_retry_count + 1
    if retry_count > SANDBOX_PROVIDER_RETRY_LIMIT:
        return None
    retry_at = datetime.now(UTC) + timedelta(seconds=SANDBOX_PROVIDER_RETRY_SECONDS)
    return TurnParked(
        SANDBOX_PROVIDER_RETRY_NOTICE.format(retry_at=retry_at.isoformat()),
        retry_at,
        retry_count,
    )


CRUD_INTENT_TOOLS = frozenset({"object_apply", "object_delete"})


def _intent_admits(tool: ToolDef) -> bool:
    return tool.name in CRUD_INTENT_TOOLS or tool.presentation is not None


class IntentRefused(Exception):
    """A prepared intent's tool dispatch answered with an error result — the kind's own refusal
    (admin gate, validation, unknown name) — so the intent applied nothing. Carries the refusal
    text into the terminal frame's error_message for the submitting panel."""


def _context_tag(message_id: UUID, context: TurnContext | None, admitted_at: datetime) -> str:
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
    if context is not None and context.reply_reaches:
        lines.append(f"reply_reaches: {context.reply_reaches}")
    return MEMBER_CONTEXT_OPENING + "\n".join(lines) + "\n</context>\n"


def _authorization_answer_values(
    authorization_id: UUID | None,
    choice: AuthorizationChoice | None,
) -> AuthorizationAnswer | None:
    if authorization_id is None:
        if choice is not None:
            raise RuntimeError("authorization choice has no authorization id")
        return None
    if choice is None:
        raise RuntimeError("authorization id has no authorization choice")
    return AuthorizationAnswer(
        authorization_id=authorization_id,
        choice=choice,
    )


def _authorization_answer(context: TurnContext | None) -> AuthorizationAnswer | None:
    if context is None:
        return None
    return _authorization_answer_values(context.authorization_id, context.authorization_choice)


def _bounded(content: str) -> str:
    return clipped(content, MAX_TOOL_RESULT_CHARS)


def _speaker_hint(error: Exception, member_refs: Sequence[UUID]) -> str:
    if not isinstance(error, SpeakerRequired):
        return ""
    return "" if member_refs else NO_REQUESTER_HINT


def _schema_hint(error: Exception, input_model: type[BaseModel] | None) -> str:
    """Pydantic names the missing or forbidden field, never the set of fields the call should have
    passed."""
    if input_model is None or not isinstance(error, ValidationError):
        return ""
    if not any(detail["type"] in SCHEMA_HINT_KINDS for detail in error.errors()):
        return ""
    fields = ", ".join(
        f"{field.alias or name}{'' if field.is_required() else ' (optional)'}"
        for name, field in input_model.model_fields.items()
    )
    return SCHEMA_HINT.format(model=input_model.__name__, fields=fields)


def _error_text(
    tool_name: str,
    error: Exception,
    member_refs: Sequence[UUID] = (),
    input_model: type[BaseModel] | None = None,
) -> str:
    """A bare-raised exception's `str()` is "", which would leave its class name over a trailing
    colon."""
    detail = str(error).strip()
    text = (
        f"{type(error).__name__}: {detail}"
        if detail
        else BARE_RAISE_NOTICE.format(cls=type(error).__name__, tool=tool_name)
    )
    return text + _speaker_hint(error, member_refs) + _schema_hint(error, input_model)


def _meter_dispatch(
    tools: ToolRegistry,
    call: ToolUseBlock,
    started: float,
    outcome: str,
    error_class: str | None,
    profile: str,
    semantic: Mapping[str, str] | None = None,
) -> None:
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
    """Read from the call input, never the result's prose: a member-authored `SKILL.md` may quote
    the `# Skill:` header and suppress another skill's real load."""
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
    authorization_preflights: dict[str, _AuthorizationPreflight] = field(default_factory=dict)
    parked: TurnParked | None = None
    authorization_question: AskUserInput | None = None
    authorization_pending: bool = False


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
        with span("context_boundary.maybe"):
            outcome = await self.engine.context.maybe_cross(
                absorbed, active_requests=active_requests
            )
        self.engine._reseed_loaded_skills(outcome.messages)
        self.usage_events.extend(outcome.usage)
        self.meter.rounds += 1
        return HarnessPreparedRound(
            messages=_to_harness_messages(outcome.messages),
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
        outcome = await self.engine.context.maybe_cross(
            _from_harness_messages(messages), active_requests=active_requests, final=True
        )
        self.usage_events.extend(outcome.usage)
        return _to_harness_messages(outcome.messages)


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
                round_index,
                offer_tools=request.mode is HarnessRoundMode.NORMAL,
                force_finish=request.mode is HarnessRoundMode.FORCE_FINISH,
                active_requests=active_requests,
                first_round=request.first_round,
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
        await self.engine._enforce_seats(self.requesters)
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
        return tuple(
            HarnessToolDefinition(
                name=schema.name,
                description=schema.description,
                input_schema=dict(schema.input_schema),
            )
            for tool in self.engine.tools.tools
            for schema in (tool.schema(),)
        )

    def parallel_safe(self, call: HarnessToolCall) -> bool:
        return (
            self._resolve(call).parallel_safe
            and not self.state.authorization_pending
            and REQUESTED_BY not in call.input
        )

    async def preflight(self, calls: tuple[HarnessToolCall, ...]) -> None:
        if self.state.parked is not None:
            return
        bound_items = await asyncio.gather(
            *(
                self.engine._bind_or_error(
                    self.context,
                    self._resolve(call),
                    self.requesters,
                    authorization_pending=self.state.authorization_pending,
                )
                for call in calls
            ),
            return_exceptions=True,
        )
        for call, bound in zip(calls, bound_items, strict=True):
            if isinstance(bound, BaseException):
                raise bound
            self.state.bindings[call.id] = bound
        selected = tuple(
            (call, bound)
            for call, bound in zip(calls, bound_items, strict=True)
            if isinstance(bound, _BoundToolCall)
            and bound.selected_message_ref is not None
            and (bound.selected_from_multiple or bound.authorization_pending)
        )
        if selected:
            preflights = await self.engine._preflight_member_authorizations(
                tuple(bound for _, bound in selected)
            )
            self.state.authorization_preflights.update(
                (call.id, preflight)
                for (call, _), preflight in zip(selected, preflights, strict=True)
                if preflight is not None
            )

    async def prepare(self, calls: tuple[HarnessToolCall, ...]) -> None:
        if self.state.parked is not None:
            return
        bound_items = tuple(self.state.bindings[call.id] for call in calls)
        for bound in bound_items:
            if isinstance(bound, _BoundToolCall):
                self.engine._start_activity(
                    bound.effective.semantic_call(),
                    bound.effective.tool.activity,
                    "\n".join(
                        member_message_text(message.rendered)
                        for message in self.requesters.values()
                    ),
                )

    async def execute(self, call: HarnessToolCall) -> HarnessToolResult:
        if self.state.parked is not None:
            return HarnessToolResult(call.id, self.state.parked.message, is_error=True)
        bound = self.state.bindings[call.id]
        try:
            dispatched = await self.engine._dispatch_step_recovering(
                bound,
                self.usage_events,
                self.state.authorization_preflights.get(call.id),
            )
            if dispatched.question is not None:
                self.state.authorization_question = dispatched.question
            await self.engine._fold_created(self.created, dispatched.created)
            result = _to_harness_result(await self.engine._dispatch_result(dispatched))
            completion = (
                dispatched.completion
                if not dispatched.is_error and self.engine.output_model is None
                else None
            )
            if completion is not None and await self.engine._pending_member_guidance():
                completion = None
            return replace(
                result,
                ends_turn=dispatched.question is not None or completion is not None,
                completion=completion,
            )
        except TurnParked as parked:
            self.state.parked = parked
            return HarnessToolResult(call.id, parked.message, is_error=True)

    async def after_round(
        self,
        calls: tuple[HarnessToolCall, ...],
        results: tuple[HarnessToolResult, ...],
    ) -> None:
        try:
            runtime_calls = tuple(_from_harness_call(call) for call in calls)
            runtime_results = tuple(_from_harness_result(result) for result in results)
            self.change_paths.update(dict.fromkeys(change_targets(runtime_calls)))
            if len(results) != len(calls):
                return
            resolved = tuple(self._resolve(call) for call in calls)
            acts = _round_acts(resolved, runtime_results)
            self.state.acts = _OpenActs(
                question=(
                    self.state.authorization_question or _act(acts, "question", AskUserInput)
                ),
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
            self.state.authorization_preflights.clear()

    async def after_checkpoint(self) -> None:
        parked = self.state.parked
        self.state.parked = None
        if parked is not None:
            raise parked

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

    async def _persist_parked(
        self,
        messages: tuple[Message, ...],
        absorbed: tuple[UUID, ...],
        requesters: Mapping[UUID, ActiveMessage],
        system: str,
        injected: str,
    ) -> bool:
        return await self.write_conversation(
            messages,
            system=system,
            injected=injected or None,
            from_run=True,
            parked=ParkedTurn(
                absorbed=absorbed,
                requesters=tuple(
                    ParkedRequester(
                        id=id_,
                        member_id=requester.member_id,
                        rendered=requester.rendered,
                        admission_source=requester.admission_source,
                        authorization_id=(
                            None
                            if requester.authorization_answer is None
                            else requester.authorization_answer.authorization_id
                        ),
                        authorization_choice=(
                            None
                            if requester.authorization_answer is None
                            else requester.authorization_answer.choice
                        ),
                        reply_to_ref=requester.reply_to_ref,
                        reply_to_text=requester.reply_to_text,
                        continued=requester.continued,
                    )
                    for id_, requester in requesters.items()
                ),
            ),
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
        """A parked turn's saved window, or prior transcript plus this turn's inbound. A member
        turn's inbound has the <context> tag with its admission moment, sender, and surface source.
        A spawned turn's inbound stays the bare payload its target's contract promises."""
        stored = await self._parked_record()
        if stored is not None:
            return stored.messages
        inbound = self.turn.inbound
        if not self.turn.spawned:
            inbound = _context_tag(self.turn.id, self.turn.context, self.turn.created_at) + inbound
        return (*await self._prior_messages(), Message(role="user", content=inbound))

    async def _parked_record(self) -> Conversation | None:
        stored = await self.transcript.read()
        if (
            stored is None
            or stored.seq != self.turn.seq
            or not stored.from_run
            or stored.parked is None
        ):
            return None
        return stored

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
        parked: ParkedTurn | None = None,
    ) -> bool:
        conversation = Conversation(
            seq=self.turn.seq,
            messages=messages,
            system=system,
            injected=injected,
            from_run=from_run,
            parked=parked,
        )
        for attempt in range(TRANSCRIPT_WRITE_ATTEMPTS):
            try:
                return await self.transcript.write(conversation)
            except Exception as error:
                log(
                    "transcript.write_failed",
                    turn_id=str(self.turn.id),
                    attempt=attempt + 1,
                    error_class=type(error).__name__,
                )
                if attempt + 1 < TRANSCRIPT_WRITE_ATTEMPTS:
                    await asyncio.sleep(TRANSCRIPT_WRITE_RETRY_SECONDS)
        return False


PREEMPTED = "preempted"


@dataclass
class _TurnMeter:
    """Metered per execution: a DBOS crash-recovery re-dispatch enters `run()` with a fresh meter
    and replays its steps from the step log in milliseconds."""

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


@dataclass(frozen=True)
class _CarriedFile:
    """A carried artifact staged in the blob store, waiting on the commit that lands its row; its
    `subject` is the link text the answer named, None for the surface's own."""

    key: str
    filename: str
    size_bytes: int
    digest: str
    subject: str | None


@dataclass(frozen=True, repr=False)
class TurnEngine:
    turn: Turn
    agent: Agent
    byok: bool
    system_prompt: RenderedPrompt
    serving: ServingModel
    activity_summarizer: ActivitySummarizer
    transcript: Transcript
    context: ContextBoundary
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
    member_authorization: AuthorizationGate
    connector_read_only: bool = False
    site_previewer: SitePreviewer | None = None
    previous_turn_ended_at: datetime | None = None
    lineage: RunLineage | None = None
    sandbox_for: SandboxFor | None = None
    requestable_credentials: CredentialRequests | None = None
    workspace_slots: WorkspaceSlots | None = None
    """The slots an extension resolves for this workspace alone, which no manifest names — None
    where no installed extension resolves any."""
    memory: MemorySearch | None = None
    sign_in_path: str | None = None
    page_kit: Path | None = None
    public_base_url: str | None = None
    spend: SpendGates = NO_SPEND_GATES
    ledger: Ledger = UNGATED_LEDGER
    models: tuple[str, ...] = ()
    """The model ids this deploy serves, handed to every tool call so a write that stores a
    model can refuse an id the registry cannot answer."""
    model_specs: Mapping[str, ModelSpec] = field(default_factory=dict)
    auto_model: str = AUTO_MODEL
    pricing: Pricing = CORE_PRICING
    subagents: SubagentControl | None = None
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
    _burn: _Burn = field(default_factory=_Burn, init=False, repr=False)
    _model_route_changes: list[ModelRouteChange] = field(
        default_factory=list, init=False, repr=False
    )
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
            requesting_message_ref=(
                self.turn.id
                if self.turn.admission_source == MEMBER_ADMISSION
                and self.turn.speaker_member_id is not None
                else (
                    None if self.turn.context is None else self.turn.context.requesting_message_ref
                )
            ),
            grants=self.grants,
            granted_actions=self.granted_actions,
            skills=self.skills,
            loaded_skills=self.context.loaded_skills,
            context=self.context,
            cdp_provider=self.cdp_provider,
            search_provider=self.search_provider,
            connectors=self.connectors,
            connector_read_only=self.connector_read_only,
            find=partial(self._rank_find, usage_events),
            requestable_credentials=self.requestable_credentials,
            workspace_slots=self.workspace_slots,
            public_base_url=self.public_base_url,
            sign_in_path=self.sign_in_path,
            page_kit=self.page_kit,
            site_previewer=self.site_previewer,
            models=self.models,
            model_specs=self.model_specs,
            auto_model=self.auto_model,
            ledger=self.ledger,
            publish_artifacts=lambda: self._publish(ArtifactsChanged()),
        )
        created: dict[ObjectRef, None] = dict.fromkeys(self.turn.created_refs)
        messages: tuple[Message, ...] = ()
        system = self.system_prompt.content
        injected = ""
        try:
            if not await self._mark_running():
                return await self._resolve_unclaimed()
            await self._publish_run()
            prepared = await self._prepare_run(usage_events, meter, absorbed_ids, requesters)
            if prepared.terminal is not None:
                return prepared.terminal
            system = prepared.system
            messages = prepared.messages
            injected = prepared.injected
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
                carried, delivered = self._carried_artifacts(answer)
                staged = await self._stage_carried_artifacts(carried)
                await self.hooks.fire(
                    "stop",
                    Stop(answer=answer),
                    self.turn,
                    self.agent,
                    None,
                    self.sandbox,
                )
                frame = await self._commit(
                    "done",
                    usage_events,
                    meter,
                    answer=delivered,
                    question=question,
                    credential_request=credential_request,
                    connect_request=connect_request,
                    created=tuple(created),
                    unless_arrivals=True,
                    absorbed=tuple(absorbed_ids),
                    carried=staged,
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
            persisted = bool(self._window.messages) and await self._persist_parked(
                self._window.messages,
                tuple(absorbed_ids),
                requesters,
                system,
                injected,
            )
            if not persisted:
                error = RuntimeError("The turn could not save its resumable state.")
                frame = await self._commit(
                    "failed", usage_events, meter, error=error, created=tuple(created)
                )
                await self._release_unabsorbed(())
                if frame is not None:
                    await self._publish_terminal(frame)
                raise error from parked
            meter.exited(PARKED)
            await self._park(
                parked.message,
                usage_events,
                parked.retry_at,
                tuple(absorbed_ids),
                external_retry_count=parked.external_retry_count,
            )
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

    async def _rank_find(self, usage_events: list[Usage], system: str, user: str) -> str:
        request = ModelRequest(
            model=self.serving.model,
            system=system,
            messages=(Message(role="user", content=user),),
            max_tokens=FIND_MAX_TOKENS,
            conversation_cache_ttl="5m",
            session_id=str(self.turn.conversation_id),
            reasoning=self.serving.spec.reasoning.internal_effort(),
        )
        parts: list[str] = []
        async for event in self.serving.client.complete(request):
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

    async def _prepare_run(
        self,
        usage_events: list[Usage],
        meter: _TurnMeter,
        absorbed_ids: list[UUID],
        requesters: dict[UUID, ActiveMessage],
    ) -> _PreparedRun:
        repair = self._repair()
        parked_record = await repair._parked_record()
        if parked_record is not None:
            parked = parked_record.parked
            if parked is None:
                raise RuntimeError("parked transcript has no resumable state")
            absorbed_ids.extend(parked.absorbed)
            requesters.update(
                {
                    requester.id: ActiveMessage(
                        member_id=requester.member_id,
                        rendered=requester.rendered,
                        admission_source=requester.admission_source,
                        authorization_answer=(
                            _authorization_answer_values(
                                requester.authorization_id,
                                requester.authorization_choice,
                            )
                        ),
                        reply_to_ref=requester.reply_to_ref,
                        reply_to_text=requester.reply_to_text,
                        continued=requester.continued,
                    )
                    for requester in parked.requesters
                }
            )
            return _PreparedRun(
                parked_record.system
                if parked_record.system is not None
                else self.system_prompt.content,
                parked_record.messages,
                parked_record.injected or "",
            )
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
                self.sandbox,
            )
        injected = inbound.injected
        if inbound.denied is not None:
            self._window.denied = DENIED_INBOUND_NOTICE.format(reason=escape(inbound.denied))
            denial = await self._commit(
                "done",
                usage_events,
                meter,
                answer=inbound.denied,
                unless_arrivals=True,
                absorbed=tuple(absorbed_ids),
            )
            if denial is not None:
                await self._persist_transcript(
                    await self._load_messages(), inbound.denied, system, inbound.injected
                )
                await self._publish_terminal(denial)
                await self._record_workspace_changes(())
                return _PreparedRun(system, (), injected, denial)
            founding_denial = DENIED_INBOUND_NOTICE.format(reason=escape(inbound.denied))
            messages = (
                *await self._repair()._prior_messages(),
                Message(role="user", content=founding_denial),
            )
            return _PreparedRun(system, messages, injected)
        if inbound.sources:
            await self._publish(Sources(items=inbound.sources))
        messages = await self._load_messages()
        founding = messages[-1].content
        if not isinstance(founding, str):
            raise RuntimeError("founding inbound did not render as text")
        requesting_message_ref = (
            None if self.turn.context is None else self.turn.context.requesting_message_ref
        )
        if requesting_message_ref is not None:
            requesters[requesting_message_ref] = await self._continued_requester(
                requesting_message_ref
            )
        elif not self.turn.spawned:
            parent = (
                _authorization_assistant(self.turn.id, len(messages) - 2, messages[-2])
                if len(messages) > 1
                else None
            )
            requesters[self.turn.id] = ActiveMessage(
                member_id=self.turn.speaker_member_id,
                rendered=member_message_text(founding),
                admission_source=self.turn.admission_source,
                authorization_answer=_authorization_answer(self.turn.context),
                reply_to_ref=None if parent is None else parent.ref,
                reply_to_text=None if parent is None else parent.text,
            )
        injected = "\n\n".join(part for part in (injected, self.member_skill_block) if part)
        if injected:
            rendered = INJECTED_CONTEXT.format(content=founding, injected=injected)
            messages = (*messages[:-1], Message(role="user", content=rendered))
        return _PreparedRun(system, messages, injected)

    async def _continued_requester(self, message_ref: UUID) -> ActiveMessage:
        async with workspace_tx() as connection:
            founding = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.speaker_member_id,
                        tables.turn.c.inbound,
                        tables.turn.c.context,
                        tables.turn.c.admission_source,
                    ).where(
                        tables.turn.c.workspace_id == self.turn.workspace_id,
                        tables.turn.c.id == message_ref,
                    )
                )
            ).one_or_none()
            arrival = (
                await connection.execute(
                    sa.select(
                        tables.inbound_message.c.speaker_member_id,
                        tables.inbound_message.c.body.label("inbound"),
                        tables.inbound_message.c.context,
                        tables.inbound_message.c.admission_source,
                    ).where(
                        tables.inbound_message.c.workspace_id == self.turn.workspace_id,
                        tables.inbound_message.c.id == message_ref,
                    )
                )
            ).one_or_none()
        rows = tuple(row for row in (founding, arrival) if row is not None)
        if len(rows) != 1:
            raise RuntimeError("requesting message ref does not name exactly one message")
        row = rows[0]
        if row.admission_source != MEMBER_ADMISSION or row.speaker_member_id is None:
            raise RuntimeError(
                "requesting message ref does not name an authenticated member message"
            )
        context = None if row.context is None else TurnContext.model_validate(row.context)
        return ActiveMessage(
            member_id=row.speaker_member_id,
            rendered=row.inbound,
            admission_source=row.admission_source,
            authorization_answer=_authorization_answer(context),
            continued=True,
        )

    async def run_intent(self) -> TerminalFrame | None:
        """Run an intent turn: dispatch its one typed tool call verbatim and commit the result.

        A speaking intent is a prepared panel mutation and binds its requester through its founding
        member message. A speaking `object_action` intent reaches only an action that declares a
        `presentation` — the one dispatch-point fence on the prepared-intent lane — while a
        speakerless intent is a sandbox bridge call gated by the parent turn's granted actions.
        Both take the same guarded, memoized dispatch as a model call, with no model round or
        turn-shaped prompt hooks."""
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
            requesting_message_ref=(
                self.turn.id
                if self.turn.admission_source == MEMBER_ADMISSION
                and self.turn.speaker_member_id is not None
                else (
                    None if self.turn.context is None else self.turn.context.requesting_message_ref
                )
            ),
            grants=self.grants,
            granted_actions=self.granted_actions,
            skills=self.skills,
            loaded_skills=self.context.loaded_skills,
            context=self.context,
            cdp_provider=self.cdp_provider,
            search_provider=self.search_provider,
            connectors=self.connectors,
            connector_read_only=self.connector_read_only,
            requestable_credentials=self.requestable_credentials,
            workspace_slots=self.workspace_slots,
            public_base_url=self.public_base_url,
            sign_in_path=self.sign_in_path,
            page_kit=self.page_kit,
            site_previewer=self.site_previewer,
            models=self.models,
            model_specs=self.model_specs,
            auto_model=self.auto_model,
            ledger=self.ledger,
            publish_artifacts=lambda: self._publish(ArtifactsChanged()),
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
                        member_id=self.turn.speaker_member_id,
                        rendered=self.turn.inbound,
                        admission_source=self.turn.admission_source,
                    )
                }
            await self._enforce_seats(requesters)
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
            if isinstance(bound, _BoundToolCall) and (
                presentation := bound.effective.tool.presentation
            ):
                self._activity.labels[call.id] = presentation.label
                await self._publish(Activity(text=presentation.label, call_id=call.id))
                await self._publish_run(activity=presentation.label)
            result = await self._dispatch_step_recovering(bound, usage_events)
            if result.is_error:
                refusal = (
                    result.text
                    if self.turn.speaker_member_id is None
                    else result.unwalled_error or result.text
                )
                frame = await self._commit(
                    "failed", usage_events, meter, error=IntentRefused(refusal)
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
                    created=result.created,
                )
            await self._persist_transcript(await self._load_messages(), result.text, "", "")
            if frame is not None:
                await self._publish_terminal(frame)
            return frame
        except TurnParked as parked:
            meter.exited(PARKED)
            await self._park(
                parked.message,
                usage_events,
                parked.retry_at,
                external_retry_count=parked.external_retry_count,
            )
            raise
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
                        requesting_member_id=self.turn.member_id,
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
        """Clearing the advisory dispatch stamp tells the outbox the turn is live; a crash before
        this leaves the turn re-enqueueable."""
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
        member_ids = frozenset(
            message.member_id for message in requesters.values() if message.member_id is not None
        )
        state = _RuntimeToolState(
            authorization_pending=await self._member_authorization_pending(member_ids)
        )
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

    @DBOS.step(preemptible=True)
    async def _member_authorization_pending(self, member_ids: frozenset[UUID]) -> bool:
        return await self.member_authorization.has_pending(
            self.turn.workspace_id, self.turn.conversation_id, member_ids
        )

    async def _fold_created(
        self,
        created: dict[ObjectRef, None],
        refs: tuple[ObjectRef, ...],
    ) -> None:
        fresh = [ref for ref in refs if ref not in created]
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
        await self._publish(Created(refs=tuple(fresh)))

    async def _absorb_arrivals(
        self,
        messages: tuple[Message, ...],
        arrival_log: list[Message],
        absorbed_ids: list[UUID],
        requesters: dict[UUID, ActiveMessage] | None = None,
    ) -> tuple[Message, ...]:
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
                parent = (
                    _authorization_assistant(self.turn.id, len(messages) - 1, messages[-1])
                    if messages
                    else None
                )
                if arrival.requesting_message_ref is None:
                    requesters[arrival.id] = ActiveMessage(
                        member_id=arrival.speaker_member_id,
                        rendered=member_message_text(arrival.rendered),
                        admission_source=arrival.admission_source,
                        authorization_answer=arrival.authorization_answer,
                        reply_to_ref=None if parent is None else parent.ref,
                        reply_to_text=None if parent is None else parent.text,
                    )
                else:
                    requesters[arrival.requesting_message_ref] = await self._continued_requester(
                        arrival.requesting_message_ref
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
        """A plain write, not a step: the span id carries the idempotency, so a crash between the
        write and a step record cannot double-post."""
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
        """The ufo terminal prints the delta stream and caps on the terminal frame, so without this
        a closing round's withheld spans never print."""
        for reply in spoken:
            await self._publish(TextDelta(text=reply.text))

    def _carried_artifacts(self, answer: str) -> tuple[tuple[MarkedArtifact, ...], str]:
        if self.turn.parent_turn_id is not None:
            return (), answer
        return marked_artifacts(answer)

    async def _stage_carried_artifacts(
        self, carried: tuple[MarkedArtifact, ...]
    ) -> tuple[_CarriedFile, ...]:
        """Staged ahead of the commit that lands the `details` row, so a writeback claimed the
        instant the turn is terminal finds the bytes."""
        shared = await self._shared_digests()
        staged = []
        for index, artifact in enumerate(carried):
            artifact_id = uuid5(
                NAMESPACE_URL, f"{self.turn.id}/{self.attempt}/{CARRIED_FILE_KEY_PART}/{index}"
            )
            key = f"{ARTIFACT_KEY_PREFIX}{artifact_id}/{artifact.name}"
            try:
                scoped = workspace_path(artifact.path)
                measured = await measure_file(self.sandbox, scoped)
                if measured.digest in shared:
                    log(
                        "turn.carried_file_already_shared",
                        turn_id=str(self.turn.id),
                        path=artifact.path,
                        digest=measured.digest,
                    )
                    continue
                if measured.size_bytes > SHARED_BYTES_LIMIT:
                    raise ValueError(
                        f"carried artifact {artifact.name!r} exceeds {SHARED_BYTES_LIMIT} bytes"
                    )
                await store_artifact(
                    self.sandbox, self.blob, scoped, key, measured.size_bytes, measured.digest
                )
            except Exception as error:
                warn(
                    "turn.carried_file_unavailable",
                    turn_id=str(self.turn.id),
                    path=artifact.path,
                    error_class=type(error).__name__,
                )
                continue
            shared.add(measured.digest)
            staged.append(
                _CarriedFile(
                    key=key,
                    filename=artifact.name,
                    size_bytes=measured.size_bytes,
                    digest=measured.digest,
                    subject=artifact.text,
                )
            )
        return tuple(staged)

    async def _shared_digests(self) -> set[str]:
        async with workspace_tx() as connection:
            rows = await connection.execute(
                sa.select(tables.shared_artifact.c.digest).where(
                    tables.shared_artifact.c.turn_id == self.turn.id,
                    tables.shared_artifact.c.digest.is_not(None),
                )
            )
            return set(rows.scalars())

    async def _discard_unreferenced(
        self, connection: AsyncConnection, staged: tuple[_CarriedFile, ...]
    ) -> None:
        if not staged:
            return
        referenced = set(
            (
                await connection.execute(
                    sa.select(tables.shared_artifact.c.blob_key).where(
                        tables.shared_artifact.c.turn_id == self.turn.id,
                        tables.shared_artifact.c.blob_key.in_([file.key for file in staged]),
                    )
                )
            ).scalars()
        )
        for file in staged:
            if file.key in referenced:
                continue
            try:
                await self.blob.delete(file.key)
            except Exception as error:
                log(
                    "turn.carried_discard_failed",
                    blob_key=file.key,
                    error_class=type(error).__name__,
                )

    async def _render_arrival(
        self,
        message_id: UUID,
        body: str,
        context: TurnContext | None,
        speaker_member_id: UUID | None,
        created_at: datetime,
    ) -> tuple[str | None, str | None]:
        submitted = await self.hooks.fire(
            "user_prompt_submit",
            UserPromptSubmit(text=body),
            self.turn,
            self.agent,
            speaker_member_id,
            self.sandbox,
        )
        if submitted.denied is not None:
            return None, submitted.denied
        content = _context_tag(message_id, context, created_at) + body
        if submitted.injected:
            content = INJECTED_CONTEXT.format(content=content, injected=submitted.injected)
        return content, None

    def _owed_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[sa.ColumnElement[bool], ...]:
        """The claim and the commit guard both read this predicate: a guard counting a row the claim
        leaves pending would hold a terminal no round satisfies."""
        return (
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

    @DBOS.step(preemptible=True)
    async def _claim_arrivals(self, absorbed: tuple[UUID, ...]) -> tuple[Arrival, ...]:
        """user_prompt_submit fires inside the step, so a replay of a recorded drain reuses the
        memoized rendering instead of re-firing hooks."""
        self.adoption.replaying = False
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.update(tables.inbound_message)
                    .values(consumed_turn_id=self.turn.id)
                    .where(*self._owed_arrivals(absorbed))
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
            context = None if row.context is None else TurnContext.model_validate(row.context)
            rendered, denial = await self._render_arrival(
                row.id,
                row.body,
                context,
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
                    authorization_answer=_authorization_answer(context),
                    requesting_message_ref=(
                        None if context is None else context.requesting_message_ref
                    ),
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
        round_index: int,
        offer_tools: bool = True,
        force_finish: bool = False,
        active_requests: tuple[str, ...] = (),
        first_round: bool = False,
    ) -> tuple[tuple[Message, ...], StreamResult]:
        try:
            result = await self._stream_retrying_interruption(
                _RoundInput(
                    messages=messages,
                    system=system,
                    offer_tools=offer_tools,
                    force_finish=force_finish,
                    first_round=first_round,
                    round_index=round_index,
                    tool_schemas=tool_schemas,
                    tool_choice=tool_choice,
                ),
                usage_events,
            )
            if result.error_class is not None:
                raise ModelStreamError(
                    result.error_class, result.error_message or "", result.partial_output
                )
            return messages, result
        except Exception as error:
            if not is_context_overflow(error):
                raise
            outcome = await self.context.maybe_cross(
                messages,
                force=True,
                active_requests=active_requests,
            )
            if not outcome.crossed:
                raise
            usage_events.extend(outcome.usage)
            compacted = outcome.messages
            self._reseed_loaded_skills(compacted)
            emit_metric("turn_context_overflow_recovered_total", profile=self.profile)
            log("turn.context_overflow_recovered", turn_id=str(self.turn.id))
            result = await self._stream_retrying_interruption(
                _RoundInput(
                    messages=compacted,
                    system=system,
                    offer_tools=offer_tools,
                    force_finish=force_finish,
                    first_round=first_round,
                    round_index=round_index,
                    tool_schemas=tool_schemas,
                    tool_choice=tool_choice,
                ),
                usage_events,
            )
            if result.error_class is not None:
                raise ModelStreamError(
                    result.error_class, result.error_message or "", result.partial_output
                ) from None
            return compacted, result

    async def _stream_retrying_interruption(
        self, round_input: _RoundInput, usage_events: list[Usage]
    ) -> StreamResult:
        """The retry logs only the fault kind: the message is provider text and `log` redacts by
        field name."""
        interruptions = 0
        while True:
            result = await self._stream_once(round_input)
            usage_events.extend(result.usages)
            route_failure = MODEL_ROUTE_FAILURES.get(result.error_class or "")
            if route_failure is not None and await self._move_route(usage_events, route_failure):
                continue
            if result.retry_after_seconds is not None:
                retry_at = datetime.now(UTC) + timedelta(seconds=result.retry_after_seconds)
                raise TurnParked(
                    PROVIDER_RETRY_NOTICE.format(retry_at=retry_at.isoformat()), retry_at
                )
            if result.error_kind is None or interruptions >= MAX_MIDSTREAM_ROUND_RETRIES:
                return result
            interruptions += 1
            emit_metric(
                "model_provider_retry_total",
                provider=self.serving.spec.provider,
                model=self.serving.model,
                kind=result.error_kind,
            )
            log(
                "model.round_interrupted_retry",
                turn_id=str(self.turn.id),
                provider=self.serving.spec.provider,
                model=self.serving.model,
                kind=result.error_kind,
                attempt=interruptions,
            )

    async def _move_route(self, usage_events: list[Usage], failure: ModelRouteFailure) -> bool:
        left = self.serving.model
        funding = self.serving.funding
        if not await self.serving.move():
            return False
        self._burn.left.append((left, len(usage_events), funding != PLATFORM_FUNDED))
        self._model_route_changes.append(
            ModelRouteChange(
                failed_model=left,
                replacement_model=self.serving.model,
                failure=failure,
            )
        )
        log(
            "model.route_failover",
            turn_id=str(self.turn.id),
            model=left,
            moved_to=self.serving.model,
        )
        if self.serving.routes is not None and not self.serving.routes.remaining:
            await self._publish(Activity(text=f"Continuing on {self.serving.model}."))
        return True

    def _priced(self, usage_events: Sequence[Usage]) -> int:
        """This attempt's burn in micro-USD, each route's share at the rate of the model that
        served it."""
        return sum(
            self.pricing.micro_usd(segment.model, segment.usage)
            for segment in self._segments(usage_events)
        )

    def _platform_priced(self, usage_events: Sequence[Usage]) -> int:
        return sum(
            self.pricing.micro_usd(segment.model, segment.usage)
            for segment in self._segments(usage_events)
            if not segment.byok
        )

    def _segments(self, usage_events: Sequence[Usage]) -> tuple[_Segment, ...]:
        return self._burn.segments(
            self.serving.model,
            self.attempt,
            usage_events,
            self.serving.funding != PLATFORM_FUNDED,
        )

    async def _enforce_spend(
        self,
        usage_events: list[Usage],
        requesters: dict[UUID, ActiveMessage],
    ) -> None:
        """Caps weigh every route's price; spend gates weigh only platform-paid segments."""
        await self._enforce_seats(requesters)
        member_id = self.turn.speaker_member_id
        if not applicable_caps_absent(self.turn.workspace_id, member_id, self.turn.agent_id):
            async with workspace_tx() as connection:
                decision = await SpendEvaluator(
                    self.turn.workspace_id, member_id, self.turn.agent_id
                ).decide(connection, self._priced(usage_events))
            if decision.outcome != ALLOW:
                raise TurnParked(decision.message)
        if self.spend.absent(self.turn.workspace_id):
            return
        async with workspace_tx() as connection:
            sustained = await self.spend.sustain(
                connection,
                self.turn.workspace_id,
                self.turn.id,
                self._platform_priced(usage_events),
            )
        if sustained.outcome != ALLOW:
            raise TurnParked(sustained.message)

    async def _enforce_seats(self, requesters: Mapping[UUID, ActiveMessage]) -> None:
        members = {
            message.member_id
            for message in requesters.values()
            if message.member_id is not None and not message.continued
        }
        if members:
            async with workspace_tx() as connection:
                if not await Seats(self.turn.workspace_id).all_seated(connection, members):
                    raise TurnParked(SEAT_REVOKED_MESSAGE)

    async def _enforce_requester_seat(self, member_id: UUID | None) -> None:
        if member_id is None:
            return
        async with workspace_tx() as connection:
            if not await Seats(self.turn.workspace_id).all_seated(connection, (member_id,)):
                raise TurnParked(SEAT_REVOKED_MESSAGE)

    @DBOS.step(preemptible=True)
    async def _stream_once(self, round_input: _RoundInput) -> StreamResult:
        """Memoized so a crash-recovery replay returns the recorded round: the same `call_id`s the
        `_dispatch` steps key off, and no tokens re-spent."""
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
                offered = self.tools.schemas()
                tool_schemas = offered if finish is None else (*offered, finish)
            else:
                tool_schemas = ()
        request = ModelRequest(
            model=self.serving.model,
            system=round_input.system,
            messages=round_input.messages,
            max_tokens=MAX_OUTPUT_TOKENS,
            conversation_cache_ttl="5m" if self.turn.spawned else "1h",
            session_id=str(self.turn.conversation_id),
            tools=tool_schemas,
            tool_choice=tool_choice,
            reasoning=(
                self.serving.spec.reasoning.internal_effort()
                if round_input.force_finish
                else self.agent.reasoning
            ),
            defer_long_retry=self.turn.result_delivery == DELIVERY_PENDING,
        )
        provider = self.serving.spec.provider
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
            "provider": provider,
            "profile": self.profile,
            "conversation_ttl": request.conversation_cache_ttl,
            "round": "first" if round_input.first_round else "later",
            "gap": gap,
        }
        active_dimensions = {
            "model": request.model,
            "provider": provider,
            "profile": self.profile,
        }
        emit_up_down_metric("model_round_active", 1, **active_dimensions)
        try:
            with span(
                "model.round",
                model=request.model,
                provider=provider,
                profile=self.profile,
                round=round_input.round_index,
            ) as round_span:
                runner: ModelRoundRunner[ModelRequest, ToolUseBlock, ReasoningBlock, Usage] = (
                    ModelRoundRunner(
                        complete=self.serving.client.complete,
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
                        publish_text=lambda text: self._publish(TextDelta(text=text)),
                        milestone=lambda event: round_span.add_event(f"model.{event}"),
                        monotonic=time.monotonic,
                    )
                )
                result = await runner.run(request, SpanRedaction())
                mark_span_outcome(round_span, result.error_class, result.error_message)
        finally:
            emit_up_down_metric("model_round_active", -1, **active_dimensions)
        emit_histogram(
            "model_round_ms",
            result.wall_ms,
            model=request.model,
            provider=provider,
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
                    provider=provider,
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
            error_kind=result.error_kind,
            retry_after_seconds=result.retry_after_seconds,
            partial_output=result.partial_output,
        )

    async def _publish_cost(self, usage_events: list[Usage]) -> None:
        usage = _total_usage(usage_events)
        tokens = (
            usage.input_tokens
            + usage.output_tokens
            + usage.cache_read_tokens
            + usage.cache_write_5m_tokens
            + usage.cache_write_30m_tokens
            + usage.cache_write_1h_tokens
        )
        await self._publish(CostTick(cost_micro_usd=self._priced(usage_events), tokens=tokens))

    def _reseed_loaded_skills(self, messages: tuple[Message, ...]) -> None:
        self.context.loaded_skills.reseed(
            _loaded_skill_closures(messages, self.skills), preloaded=self.preload
        )

    def _resolve_call(self, call: ToolUseBlock) -> _Resolution:
        if call.name == OBJECT_ACTION_TOOL:
            return self._resolve_action(call)
        try:
            tool = self.tools.get(call.name)
        except KeyError as error:
            return self._rejected(call, error)
        return EffectiveCall(
            call=call,
            tool=tool,
            call_id=tool.name,
            ext=self.tool_ext.get(call.name),
            kind=self._called_kind(call),
        )

    def _called_kind(self, call: ToolUseBlock) -> str:
        if call.name == OBJECT_GET_TOOL:
            ref = call.input.get("ref")
            named = ref.split("/")[0] if isinstance(ref, str) else ""
        elif call.name == OBJECT_LIST_TOOL:
            listed = call.input.get("kind")
            named = listed if isinstance(listed, str) else ""
        else:
            return ""
        return named if named in self.verbs.registry else ""

    def _resolve_action(self, call: ToolUseBlock) -> _Resolution:
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
            return self._rejected(call, error, dimensions=dimensions, input_model=tool.input_model)
        return replace(effective, action_args=args)

    def _rejected(
        self,
        call: ToolUseBlock,
        error: Exception,
        dimensions: Mapping[str, str] | None = None,
        member_refs: tuple[UUID, ...] = (),
        input_model: type[BaseModel] | None = None,
    ) -> _RejectedToolCall:
        return _RejectedToolCall(
            call=call,
            text=_error_text(call.name, error, member_refs, input_model),
            outcome="invalid_call" if isinstance(error, (ValueError, KeyError)) else "step_failed",
            error_class=type(error).__name__,
            dimensions={} if dimensions is None else dimensions,
        )

    async def _bind_or_error(
        self,
        context: ToolContext,
        item: _Resolution,
        requesters: dict[UUID, ActiveMessage],
        *,
        authorization_pending: bool = False,
    ) -> _DispatchInput:
        if isinstance(item, _RejectedToolCall):
            return item
        started = time.monotonic()
        try:
            return self._bind_requester(
                context,
                item,
                requesters,
                authorization_pending=authorization_pending,
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
            return self._rejected(
                item.call,
                error,
                dimensions=item.meter_dimensions(),
                member_refs=self._member_refs(requesters),
            )

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
        if result.sources:
            await self._publish_sources(result.tool_use_id, result.sources)
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
        authorization_preflight: _AuthorizationPreflight | None = None,
    ) -> DispatchResult:
        target: ObjectActionTarget | None = None
        while True:
            result = await self._dispatch_step(bound, target, authorization_preflight)
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

    def _bind_requester(
        self,
        context: ToolContext,
        item: EffectiveCall,
        requesters: dict[UUID, ActiveMessage],
        *,
        authorization_pending: bool,
    ) -> _BoundToolCall:
        call = item.call
        tool_input = dict(call.input)
        requester: UUID | None = None
        selected_ref: UUID | None = None
        selected_message = ""
        active_members = self._active_member_ids(requesters)
        sole_member = self._sole_active_member(requesters)
        profile_only = item.tool.profile_only
        if self.turn.subagent_profile is not None and profile_only:
            tool_input.pop(REQUESTED_BY, None)
        elif REQUESTED_BY in tool_input and not item.tool.binds_member_authority:
            if item.action is None:
                raise ValueError(f"{REQUESTED_BY} is not accepted by {call.name}")
            tool_input.pop(REQUESTED_BY)
        elif REQUESTED_BY in tool_input:
            raw = tool_input.pop(REQUESTED_BY)
            if not isinstance(raw, str):
                raise SpeakerRequired(f"{REQUESTED_BY} must be a message ref")
            try:
                message_id = UUID(raw)
            except ValueError as error:
                raise SpeakerRequired(f"{REQUESTED_BY} must be a message ref") from error
            if message_id not in requesters:
                raise SpeakerRequired(f"{REQUESTED_BY} does not name an active inbound message")
            selected = requesters[message_id]
            requester = selected.member_id
            if requester is None:
                raise SpeakerRequired(f"{REQUESTED_BY} message has no member requester")
            selected_ref = message_id
            selected_message = selected.rendered
            newest_ref = self._newest_requester_ref(requesters, requester)
            if selected_ref != newest_ref:
                raise SpeakerRequired(
                    f"{REQUESTED_BY} must name this member's newest active inbound message: "
                    f"{newest_ref}"
                )
        elif item.tool.binds_member_authority and sole_member is not None:
            requester = sole_member
            selected_ref = self._newest_requester_ref(requesters, requester)
            selected = requesters[selected_ref]
            selected_message = selected.rendered
        causal_ref = selected_ref
        if causal_ref is None and context.requesting_message_ref in requesters:
            causal = requesters[context.requesting_message_ref]
            if causal.member_id is not None:
                causal_ref = self._newest_requester_ref(requesters, causal.member_id)
        if causal_ref is None and sole_member is not None:
            causal_ref = self._newest_requester_ref(requesters, sole_member)
        bound_context = replace(
            context,
            speaker_member_id=requester,
            requesting_message_ref=causal_ref,
            other_members_active=len(active_members) > 1,
            member_messages_active=any(
                message.admission_source == MEMBER_ADMISSION for message in requesters.values()
            ),
        )
        authorization_context = None
        selected_message_complete = True
        if selected_ref is not None:
            authorization_context, selected_message, selected_message_complete = (
                self._authorization_context(requesters, selected_ref)
            )
        return _BoundToolCall(
            context=bound_context,
            effective=replace(item, call=call.model_copy(update={"input": tool_input})),
            member_refs=self._member_refs(requesters),
            selected_message_ref=selected_ref,
            selected_message=selected_message,
            selected_from_multiple=selected_ref is not None and sole_member is None,
            authorization_pending=authorization_pending and requester is not None,
            authorization_answer=(
                None if selected_ref is None else requesters[selected_ref].authorization_answer
            ),
            authorization_context=authorization_context,
            selected_message_complete=selected_message_complete,
        )

    def _authorization_context(
        self, requesters: Mapping[UUID, ActiveMessage], selected_ref: UUID
    ) -> tuple[AuthorizationContext, str, bool]:
        selected_message = requesters[selected_ref]
        complete = len(selected_message.rendered) <= MEMBER_AUTHORIZATION_MESSAGE_CHARS
        selected_text = selected_message.rendered[:MEMBER_AUTHORIZATION_MESSAGE_CHARS]
        selected = AuthorizationContextMessage(
            ref=str(selected_ref),
            role="user",
            member_id=selected_message.member_id,
            reply_to=selected_message.reply_to_ref,
            text=selected_text,
        )
        parent = (
            None
            if selected_message.reply_to_ref is None or selected_message.reply_to_text is None
            else AuthorizationContextMessage(
                ref=selected_message.reply_to_ref,
                role="assistant",
                member_id=None,
                text=selected_message.reply_to_text,
            )
        )
        budget = (
            MEMBER_AUTHORIZATION_CONTEXT_CHARS
            - len(selected.text)
            - (0 if parent is None else len(parent.text))
        )
        active: list[AuthorizationContextMessage] = []
        for active_ref, active_message in tuple(requesters.items())[
            -MEMBER_AUTHORIZATION_ACTIVE_MESSAGES:
        ]:
            if active_ref == selected_ref or budget <= 0:
                continue
            text = active_message.rendered[
                : min(MEMBER_AUTHORIZATION_CONTEXT_MESSAGE_CHARS, budget)
            ]
            if not text:
                continue
            active.append(
                AuthorizationContextMessage(
                    ref=str(active_ref),
                    role="user",
                    member_id=active_message.member_id,
                    reply_to=active_message.reply_to_ref,
                    text=text,
                )
            )
            budget -= len(text)
        excluded = {selected.ref, *(message.ref for message in active)}
        if parent is not None:
            excluded.add(parent.ref)
        recent: list[AuthorizationContextMessage] = []
        for index, message in reversed(tuple(enumerate(self._window.messages))):
            if len(recent) == MEMBER_AUTHORIZATION_RECENT_MESSAGES or budget <= 0:
                break
            if message.role == "assistant":
                item = _authorization_assistant(self.turn.id, index, message)
            elif message.role == "user" and isinstance(message.content, str):
                user_ref = member_message_ref(message.content)
                if user_ref is None:
                    continue
                requester = None
                try:
                    requester = requesters.get(UUID(user_ref))
                except ValueError:
                    pass
                text = _authorization_message_text(message).strip()
                item = (
                    None
                    if not text
                    else AuthorizationContextMessage(
                        ref=user_ref,
                        role="user",
                        member_id=None if requester is None else requester.member_id,
                        reply_to=None if requester is None else requester.reply_to_ref,
                        text=text[:MEMBER_AUTHORIZATION_CONTEXT_MESSAGE_CHARS],
                    )
                )
            else:
                item = None
            if item is None or item.ref in excluded:
                continue
            text = item.text[:budget]
            if not text:
                continue
            recent.append(item.model_copy(update={"text": text}))
            excluded.add(item.ref)
            budget -= len(text)
        return (
            AuthorizationContext(
                selected=selected,
                direct_reply_parent=parent,
                assistant_proposal=parent,
                active_messages=tuple(active),
                recent_messages=tuple(reversed(recent)),
            ),
            selected_text,
            complete,
        )

    def _active_member_ids(self, requesters: Mapping[UUID, ActiveMessage]) -> frozenset[UUID]:
        return frozenset(
            message.member_id for message in requesters.values() if message.member_id is not None
        )

    def _newest_requester_ref(
        self, requesters: Mapping[UUID, ActiveMessage], member_id: UUID
    ) -> UUID:
        member_messages = tuple(
            (ref, message) for ref, message in requesters.items() if message.member_id == member_id
        )
        return next(
            (ref for ref, message in reversed(member_messages) if not message.continued),
            member_messages[-1][0],
        )

    def _sole_active_member(self, requesters: Mapping[UUID, ActiveMessage]) -> UUID | None:
        active_members = self._active_member_ids(requesters)
        if len(active_members) != 1 or any(
            message.member_id is None for message in requesters.values()
        ):
            return None
        return next(iter(active_members))

    def _member_refs(self, requesters: Mapping[UUID, ActiveMessage]) -> tuple[UUID, ...]:
        return tuple(ref for ref, message in requesters.items() if message.member_id is not None)

    async def _offload(self, name: str, content: str) -> str | None:
        """A member write can leave a file squatting the directory's name, which `mkdir -p` cannot
        reclaim: it fails `File exists` and poisons every later offload."""
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

    def _start_activity(self, call: ToolUseBlock, declared: str | None, goal: str) -> None:
        self._activity.sequence += 1
        self._activity.started.add(call.id)
        sequence = self._activity.sequence
        recent = tuple(self._activity.labels.values())[-ACTIVITY_RECENT_LABELS:]
        task = asyncio.create_task(self._generate_activity(call, declared, goal, sequence, recent))
        self._activity.tasks.add(task)
        task.add_done_callback(self._activity.tasks.discard)

    async def _generate_activity(
        self,
        call: ToolUseBlock,
        declared: str | None,
        goal: str,
        sequence: int,
        recent: tuple[str, ...],
    ) -> None:
        activity = declared or await self.activity_summarizer.summarize(call, goal, recent)
        if activity is not None:
            self._activity.labels[call.id] = activity
        async with self._activity.lock:
            self._activity.ready[sequence] = (call.id, call.name, activity)
            while current := self._activity.ready.pop(self._activity.next_publish, None):
                self._activity.next_publish += 1
                call_id, tool, current_activity = current
                if current_activity is not None:
                    await self._publish(Activity(text=current_activity, call_id=call_id, tool=tool))
                    await self._publish_run(activity=current_activity)
                self._activity.labeled.add(call_id)
                held = self._activity.sources.pop(call_id, None)
                if held:
                    await self._publish(Sources(items=held))

    async def _publish_sources(self, call_id: str, sources: tuple[SourceRef, ...]) -> None:
        # A surface clears the tiles under a step when the next label lands, so a step's sources
        # wait for its own label, which a fast tool can beat.
        async with self._activity.lock:
            if call_id in self._activity.started and call_id not in self._activity.labeled:
                self._activity.sources[call_id] = sources
                return
        await self._publish(Sources(items=sources))

    def _stop_activity(self) -> None:
        for task in tuple(self._activity.tasks):
            task.cancel()

    @DBOS.step(preemptible=True)
    async def _dispatch_step(
        self,
        bound: _DispatchInput,
        resume_target: ObjectActionTarget | None = None,
        authorization_preflight: _AuthorizationPreflight | None = None,
    ) -> DispatchResult:
        """Anthropic rejects any image over 2000px on a many-image request and downscales past
        ~1568px, so pixels beyond TOOL_IMAGE_EDGE_LIMIT buy no fidelity."""
        call = bound.call
        self._live_dispatches.add(call.id)
        issuing = TOOL_CALL_ID.set(call.id)
        find_usages: list[Usage] = []
        started = time.monotonic()
        outcome, error_class = "ok", None
        result: DispatchResult | None = None
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
                    result = DispatchResult(tool_use_id=call.id, text=bound.text, is_error=True)
                    return result
                gate = await self._prepare_dispatch(bound, target, authorization_preflight)
                target = gate.target
                if gate.result is not None:
                    outcome, error_class = gate.outcome, gate.error_class
                    result = gate.result
                    return result
                ready = gate.ready
                if ready is None:
                    raise RuntimeError("dispatch gate returned no result or ready call")
                handled = await self._invoke_dispatch(bound, ready, find_usages)
                outcome, error_class = handled.outcome, handled.error_class
                result = await self._finish_dispatch(ready, handled, find_usages)
                return result
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
                TOOL_CALL_ID.reset(issuing)
                _meter_dispatch(
                    self.tools, call, started, outcome, error_class, self.profile, semantic
                )
                if outcome != "ok":
                    warn(
                        "tool.dispatch_failed",
                        turn_id=str(self.turn.id),
                        tool=call.name,
                        call=semantic.get("call", call.name),
                        outcome=outcome,
                        error_class=error_class,
                        profile=self.profile,
                        error_text=result.text if result is not None and result.is_error else None,
                    )

    @DBOS.step(preemptible=True)
    async def _preflight_member_authorizations(
        self, bounds: tuple[_BoundToolCall, ...]
    ) -> tuple[_AuthorizationPreflight | None, ...]:
        return tuple(
            await asyncio.gather(*(self._preflight_member_authorization(bound) for bound in bounds))
        )

    async def _preflight_member_authorization(
        self, bound: _BoundToolCall
    ) -> _AuthorizationPreflight | None:
        effective = bound.effective
        if effective.action_args is not None:
            args: BaseModel = effective.action_args
        else:
            try:
                args = effective.tool.input_model.model_validate(bound.call.input)
            except ValidationError:
                return None
        request_target: ObjectActionRequestTarget | None = None
        if effective.action is not None:
            try:
                request_target = self.verbs.action_request_target(effective.tool, effective.action)
            except ValueError:
                return None
        prepared = await self._pre_tool_use(bound, args, request_target, None)
        if prepared.denied is not None:
            return prepared
        try:
            _, final_args, scope, binding = await self._standing_authorization(
                bound,
                bound.context,
                type(args).model_validate_json(prepared.args_json, context=TRUSTED_TOOL_INPUT),
            )
        except Exception as error:
            return prepared.model_copy(
                update={
                    "authority_error": _error_text(bound.call.name, error, bound.member_refs),
                    "authority_error_class": type(error).__name__,
                }
            )
        request = self._member_authorization_request(
            bound, final_args, request_target, scope, binding
        )
        if request is None:
            raise RuntimeError("selected member call has no authorization request")
        attempt = await self.member_authorization.preflight(request)
        return prepared.model_copy(update={"attempt": attempt})

    async def _pre_tool_use(
        self,
        bound: _BoundToolCall,
        args: BaseModel,
        request_target: ObjectActionRequestTarget | None,
        speaker_member_id: UUID | None,
    ) -> _AuthorizationPreflight:
        resolved = await self.hooks.fire(
            "pre_tool_use",
            PreToolUse(
                tool_name=bound.call.name,
                tool_input=args,
                call=bound.effective.call_id,
                target=request_target,
            ),
            self.turn,
            self.agent,
            speaker_member_id,
            self.sandbox,
        )
        return _AuthorizationPreflight(
            args_json=(
                resolved.tool_input if resolved.tool_input is not None else args
            ).model_dump_json(round_trip=True, by_alias=True),
            request_target=request_target,
            denied=resolved.denied,
            failed_closed=resolved.failed_closed,
        )

    async def _prepare_dispatch(
        self,
        bound: _BoundToolCall,
        target: ObjectActionTarget | None,
        authorization_preflight: _AuthorizationPreflight | None,
    ) -> _DispatchGate:
        call = bound.call
        effective = bound.effective
        tool = effective.tool
        if (
            self.adoption.replaying
            and self._redoes_on_replay(tool)
            and await self._pending_member_guidance()
        ):
            log(
                "turn.dispatch_preempted_by_guidance",
                turn_id=str(self.turn.id),
                tool=call.name,
                call=effective.call_id,
            )
            return _DispatchGate(
                target,
                result=DispatchResult(
                    tool_use_id=call.id,
                    text=GUIDANCE_PREEMPTED_NOTICE,
                    is_error=True,
                    activity=True,
                ),
                outcome="guidance_preempted",
            )
        if authorization_preflight is None:
            validated = self._validate_dispatch(bound, target)
            if isinstance(validated, _DispatchGate):
                return validated
            args = validated.args
            request_target = validated.request_target
        else:
            input_model = (
                type(effective.action_args)
                if effective.action_args is not None
                else tool.input_model
            )
            args = input_model.model_validate_json(
                authorization_preflight.args_json, context=TRUSTED_TOOL_INPUT
            )
            request_target = authorization_preflight.request_target
        pre = authorization_preflight
        if pre is None:
            pre = await self._pre_tool_use(
                bound,
                args,
                request_target,
                None
                if bound.selected_from_multiple or bound.authorization_pending
                else bound.context.speaker_member_id,
            )
            args = type(args).model_validate_json(pre.args_json, context=TRUSTED_TOOL_INPUT)
        if pre.denied is not None:
            outcome, error_class = (
                ("hook_denied", None)
                if pre.failed_closed is None
                else ("hook_failed", pre.failed_closed)
            )
            return _DispatchGate(
                target,
                result=DispatchResult(
                    tool_use_id=call.id,
                    text=pre.denied,
                    is_error=True,
                    activity=True,
                ),
                outcome=outcome,
                error_class=error_class,
            )
        authorized = await self._authorize_member_dispatch(
            bound,
            pre,
            args,
            request_target,
            target,
        )
        if isinstance(authorized, _DispatchGate):
            return authorized
        context = authorized.context
        final_args = authorized.args
        try:
            context = await self._authorize_context(context)
        except TerminalAbsent as error:
            raise TerminalGone(str(error)) from error
        except Exception as error:
            return _DispatchGate(
                target,
                result=DispatchResult(
                    tool_use_id=call.id,
                    text=_error_text(call.name, error, bound.member_refs),
                    is_error=True,
                    activity=True,
                ),
                outcome="authority_failed",
                error_class=type(error).__name__,
            )
        if effective.action is not None and target is None:
            try:
                target = await self.verbs.action_target(context, tool, effective.action)
            except ValueError as error:
                return _DispatchGate(
                    target,
                    result=DispatchResult(
                        tool_use_id=call.id,
                        text=_error_text(call.name, error, bound.member_refs),
                        is_error=True,
                        activity=True,
                    ),
                    outcome="invalid_call",
                    error_class=type(error).__name__,
                )
        ready = _DispatchReady(context, effective, final_args, target)
        return _DispatchGate(target, ready=ready)

    async def _authorize_member_dispatch(
        self,
        bound: _BoundToolCall,
        pre: _AuthorizationPreflight,
        args: BaseModel,
        request_target: ObjectActionRequestTarget | None,
        target: ObjectActionTarget | None,
    ) -> _AuthorizedDispatch | _DispatchGate:
        call = bound.call
        if pre.authority_error is not None:
            return _DispatchGate(
                target,
                result=DispatchResult(
                    tool_use_id=call.id,
                    text=pre.authority_error,
                    is_error=True,
                    activity=True,
                ),
                outcome="authority_failed",
                error_class=pre.authority_error_class,
            )
        try:
            context, args, scope, binding = await self._standing_authorization(
                bound, bound.context, args
            )
        except Exception as error:
            return _DispatchGate(
                target,
                result=DispatchResult(
                    tool_use_id=call.id,
                    text=_error_text(call.name, error, bound.member_refs),
                    is_error=True,
                    activity=True,
                ),
                outcome="authority_failed",
                error_class=type(error).__name__,
            )
        request = self._member_authorization_request(bound, args, request_target, scope, binding)
        if request is None:
            return _AuthorizedDispatch(context, args)
        authorization = await self.member_authorization.authorize(request, pre.attempt)
        if authorization.decision == "ask":
            assert authorization.question is not None
            return _DispatchGate(
                target,
                result=DispatchResult(
                    tool_use_id=call.id,
                    text=question_result_text(authorization.question),
                    is_error=True,
                    activity=True,
                    question=authorization.question,
                ),
                outcome="member_authorization_required",
            )
        if authorization.decision == "deny":
            return _DispatchGate(
                target,
                result=DispatchResult(
                    tool_use_id=call.id,
                    text=(
                        authorization.refusal
                        or "The selected member did not authorize this request."
                    ),
                    is_error=True,
                    activity=True,
                ),
                outcome="member_authorization_denied",
            )
        return _AuthorizedDispatch(
            (
                context
                if authorization.requesting_message_ref is None
                else replace(
                    context,
                    requesting_message_ref=authorization.requesting_message_ref,
                )
            ),
            args,
        )

    async def _standing_authorization(
        self, bound: _BoundToolCall, context: ToolContext, args: BaseModel
    ) -> tuple[
        ToolContext,
        BaseModel,
        AuthorizationScope | None,
        AuthorizationBinding | None,
    ]:
        resolver = bound.effective.tool.standing_authorization
        if not _selected_member_authorization(bound) or resolver is None:
            return context, args, None, None
        standing = await resolver(context, args)
        return standing.context, standing.input, standing.scope, standing.binding

    def _validate_dispatch(
        self, bound: _BoundToolCall, target: ObjectActionTarget | None
    ) -> _ValidatedDispatch | _DispatchGate:
        call = bound.call
        effective = bound.effective
        tool = effective.tool
        if effective.action_args is not None:
            args: BaseModel = effective.action_args
        else:
            try:
                args = tool.input_model.model_validate(call.input)
            except Exception as error:
                return _DispatchGate(
                    target,
                    result=DispatchResult(
                        tool_use_id=call.id,
                        text=_error_text(
                            call.name,
                            error,
                            bound.member_refs,
                            tool.input_model,
                        ),
                        is_error=True,
                        activity=True,
                    ),
                    outcome="invalid_call",
                    error_class=type(error).__name__,
                )
        request_target: ObjectActionRequestTarget | None = None
        if effective.action is not None:
            try:
                request_target = self.verbs.action_request_target(tool, effective.action)
            except ValueError as error:
                return _DispatchGate(
                    target,
                    result=DispatchResult(
                        tool_use_id=call.id,
                        text=_error_text(call.name, error, bound.member_refs),
                        is_error=True,
                        activity=True,
                    ),
                    outcome="invalid_call",
                    error_class=type(error).__name__,
                )
        return _ValidatedDispatch(args, request_target)

    def _member_authorization_request(
        self,
        bound: _BoundToolCall,
        args: BaseModel,
        target: ObjectActionRequestTarget | None,
        scope: AuthorizationScope | None,
        binding: AuthorizationBinding | None,
    ) -> AuthorizationRequest | None:
        if not _selected_member_authorization(bound):
            return None
        message_ref = bound.selected_message_ref
        assert message_ref is not None
        member_id = bound.context.speaker_member_id
        assert member_id is not None
        context = bound.authorization_context
        assert context is not None
        return AuthorizationRequest(
            workspace_id=self.turn.workspace_id,
            conversation_id=self.turn.conversation_id,
            agent_id=self.turn.agent_id,
            agent_name=self.agent.name,
            member_id=member_id,
            dispatch_key=bound.effective.dispatch_key(self.turn.id),
            message_ref=message_ref,
            message=bound.selected_message,
            message_complete=bound.selected_message_complete,
            context=context,
            effect=AuthorizationEffect(
                call=bound.effective.call_id,
                arguments=args.model_dump(mode="json"),
                target=None if target is None else target.model_dump(mode="json"),
            ),
            scope=scope,
            binding=binding,
            selected_from_multiple=bound.selected_from_multiple,
            answer=bound.authorization_answer,
        )

    async def _authorize_context(self, context: ToolContext) -> ToolContext:
        sandbox = (
            self.sandbox
            if self.sandbox_for is None
            else await self.sandbox_for(context.acting_member_id)
        )
        return replace(context, sandbox=sandbox)

    async def _invoke_dispatch(
        self,
        bound: _BoundToolCall,
        ready: _DispatchReady,
        find_usages: list[Usage],
    ) -> _HandlerOutput:
        await self._enforce_requester_seat(ready.context.speaker_member_id)
        tool = ready.effective.tool
        key = ready.effective.dispatch_key(self.turn.id) if tool.side_effecting else None
        try:
            ext = (
                None
                if ready.effective.ext is None
                else replace(
                    ready.effective.ext,
                    member_context_member_id=ready.context.speaker_member_id,
                )
            )
            handler_context = replace(
                ready.context,
                ext=ext,
                idempotency_key=key,
                target=ready.target,
            )
            find_usage_token = self._find_usages.set(find_usages)
            try:
                result = await tool.handler(handler_context, ready.args)
            finally:
                self._find_usages.reset(find_usage_token)
            text_parts: list[str] = []
            images: list[ImageBlock] = []
            for block in result.content:
                match block:
                    case TextContent(text=text):
                        text_parts.append(text)
                    case ImageContent(media_type=media_type, data=data):
                        images.append(
                            ImageBlock(source=ImageSource(media_type=media_type, data=data))
                        )
            return _HandlerOutput(
                "".join(text_parts),
                result.is_error,
                tool.untrusted or result.untrusted,
                tuple(images),
                "handler_error" if result.is_error else "ok",
                sources=() if result.is_error else result.sources,
                completion=None if result.is_error else result.completion,
                created=() if result.is_error else result.created,
            )
        except TerminalAbsent as error:
            raise TerminalGone(str(error)) from error
        except SandboxProviderUnavailable as error:
            parked = sandbox_provider_park(self.turn)
            if parked is None:
                raise
            raise parked from error
        except TurnParked:
            raise
        except Exception as error:
            return _HandlerOutput(
                _error_text(bound.call.name, error, bound.member_refs),
                True,
                tool.untrusted or isinstance(error, UntrustedContentError),
                (),
                "handler_raised",
                type(error).__name__,
            )

    async def _finish_dispatch(
        self,
        ready: _DispatchReady,
        handled: _HandlerOutput,
        find_usages: list[Usage],
    ) -> DispatchResult:
        call = ready.effective.call
        content = handled.content
        if handled.is_error:
            content = _bounded(content)
        elif len(content) > MAX_TOOL_RESULT_CHARS:
            path = await self._offload(f"{call.id}.txt", content)
            content = (
                content[:TOOL_RESULT_PREVIEW_CHARS]
                + OFFLOAD_NOTICE.format(total=len(content), path=path)
                if path is not None
                else _bounded(content)
            )
        unwalled_error = content if handled.is_error and handled.untrusted else None
        if handled.untrusted:
            content = wall(ready.effective.call_id, content)
        if handled.is_error:
            await self.hooks.fire(
                "post_tool_use_failure",
                PostToolUseFailure(
                    tool_name=call.name,
                    tool_input=ready.args,
                    output=content,
                    call=ready.effective.call_id,
                    target=ready.target,
                ),
                self.turn,
                self.agent,
                ready.context.speaker_member_id,
                ready.context.sandbox,
            )
        else:
            post = await self.hooks.fire(
                "post_tool_use",
                PostToolUse(
                    tool_name=call.name,
                    tool_input=ready.args,
                    output=content,
                    call=ready.effective.call_id,
                    target=ready.target,
                ),
                self.turn,
                self.agent,
                ready.context.speaker_member_id,
                ready.context.sandbox,
            )
            if post.output is not None:
                content = post.output
            if post.injected:
                content = f"{content}\n{post.injected}"
        image_refs: list[ImageRef] = []
        if handled.images and not handled.is_error:
            for index, image in enumerate(handled.images):
                bounded = await self._bounded_image(image)
                blob_key = f"{TOOL_IMAGE_BLOB_DIR}/{self.turn.id}/{call.id}/{index}"
                await self.blob.put(blob_key, bounded.source.data.encode())
                image_refs.append(ImageRef(media_type=bounded.source.media_type, blob_key=blob_key))
        return DispatchResult(
            tool_use_id=call.id,
            text=content,
            is_error=handled.is_error,
            activity=True,
            image_refs=tuple(image_refs),
            usages=tuple(find_usages),
            sources=handled.sources,
            completion=handled.completion,
            created=handled.created,
            unwalled_error=unwalled_error,
        )

    def _redoes_on_replay(self, tool: ToolDef) -> bool:
        return not tool.side_effecting

    async def _pending_member_guidance(self) -> bool:
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

    def _settled_activity(self) -> tuple[ActivityEvent, ...]:
        """What this turn did, for the frame a spawned turn settles with: read off the window the
        rounds built, so no reader has to open the child's transcript to draw its run card."""
        if self.turn.parent_turn_id is None:
            return ()
        return subagent_activity(self._window.messages)

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
        carried: tuple[_CarriedFile, ...] = (),
    ) -> TerminalFrame | None:
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
                    carried,
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
        (log_error if frame.status == "failed" else log)(
            "turn.terminal",
            turn_id=str(self.turn.id),
            turn_status=frame.status,
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
        carried: tuple[_CarriedFile, ...] = (),
    ) -> tuple[TerminalFrame | None, bool]:
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
                        .where(*self._owed_arrivals(absorbed))
                    )
                ).scalar_one()
                if pending:
                    log(
                        "turn.commit_refused_by_arrivals",
                        turn_id=str(self.turn.id),
                        turn_status=status,
                        pending=pending,
                        absorbed=len(absorbed),
                    )
                    await self._discard_unreferenced(connection, carried)
                    return None, False
            try:
                await self._record_usage(connection, usage_events)
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
                activity=self._settled_activity(),
                model_route_changes=tuple(self._model_route_changes),
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
                await self._discard_unreferenced(connection, carried)
                return TerminalFrame.model_validate(row.terminal), False
            insert = postgres_insert if connection.dialect.name == "postgresql" else sqlite_insert
            landed = datetime.now(UTC)
            for index, file in enumerate(carried):
                stamp = landed + timedelta(microseconds=index)
                await connection.execute(
                    insert(tables.shared_artifact)
                    .values(
                        id=uuid5(NAMESPACE_URL, file.key),
                        turn_id=self.turn.id,
                        blob_key=file.key,
                        workspace_id=self.turn.workspace_id,
                        filename=file.filename,
                        subject=file.subject,
                        media_type=artifact_media_type(file.filename),
                        size_bytes=file.size_bytes,
                        digest=file.digest,
                        role="details",
                        created_at=stamp,
                        updated_at=stamp,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            tables.shared_artifact.c.turn_id,
                            tables.shared_artifact.c.blob_key,
                        ]
                    )
                )
            await turn_conversation_changed(connection, self.turn.id)
        return frame, True

    async def _park(
        self,
        message: str,
        usage_events: list[Usage],
        retry_at: datetime | None = None,
        absorbed: tuple[UUID, ...] = (),
        external_retry_count: int | None = None,
    ) -> None:
        async with workspace_tx() as connection:
            turn_update = sa.update(tables.turn).values(
                status=PARKED,
                retry_at=retry_at,
                updated_at=sa.func.now(),
            )
            if external_retry_count is not None:
                turn_update = turn_update.values(external_retry_count=external_retry_count)
            updated = await connection.execute(
                turn_update.where(
                    tables.turn.c.id == self.turn.id,
                    tables.turn.c.status.in_(NON_TERMINAL_STATUSES),
                )
            )
            if updated.rowcount == 1:
                await self._record_usage(connection, usage_events)
                await connection.execute(
                    sa.update(tables.inbound_message)
                    .values(consumed_turn_id=None)
                    .where(
                        tables.inbound_message.c.consumed_turn_id == self.turn.id,
                        ~tables.inbound_message.c.id.in_(absorbed),
                    )
                )
                await turn_conversation_changed(connection, self.turn.id)
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
        """A carrier's `exec` meets a member's stop and an executor preemption as the same bare
        `CancelledError`; only DBOS's raise after `cancel_workflow` reaches here."""
        try:
            await self.sandbox.stop_commands()
        except Exception as error:
            log_error(
                "turn.sandbox_stop_failed",
                turn_id=str(self.turn.id),
                error_class=type(error).__name__,
            )

    async def _record_usage(
        self, connection: AsyncConnection, usage_events: Sequence[Usage]
    ) -> None:
        """Bill this attempt's burn: one cumulative ledger row per route that served it, each
        under the model that burned the tokens and at that model's rate."""
        for segment in self._segments(usage_events):
            await self.ledger.record_turn_usage(
                connection,
                self.turn.workspace_id,
                self.turn.id,
                segment.model,
                segment.usage,
                segment.attempt,
                pricing=self.pricing,
                byok=segment.byok,
            )

    async def _bill_cancelled(self, usage_events: list[Usage]) -> None:
        """Best-effort: cancellation must not stall on billing, but consumed tokens count."""
        try:
            async with workspace_tx() as connection:
                await self._record_usage(connection, usage_events)
        except Exception as error:
            log(
                "turn.cancel_billing_failed",
                turn_id=str(self.turn.id),
                error_class=type(error).__name__,
            )

    async def _resolve_unclaimed(self) -> TerminalFrame | None:
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

    async def _persist_parked(
        self,
        messages: tuple[Message, ...],
        absorbed: tuple[UUID, ...],
        requesters: Mapping[UUID, ActiveMessage],
        system: str,
        injected: str,
    ) -> bool:
        return await self._repair()._persist_parked(
            self._labeled(messages), absorbed, requesters, system, injected
        )

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
