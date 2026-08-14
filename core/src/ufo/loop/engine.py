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
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from html import escape
from io import BytesIO
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from dbos import DBOS
from dbos._error import DBOSWorkflowCancelledError
from PIL import Image
from pydantic import BaseModel, ValidationError

from ufo.accounting import (
    ALLOW,
    TOKENS_DIMENSION,
    SpendEvaluator,
    TurnCost,
    applicable_caps_absent,
    read_turn_cost,
    record_turn_usage,
)
from ufo.activity import SKILL_LOAD_TOOL, tool_activity
from ufo.audience import Audience, audience_member, audience_subjects
from ufo.blob import BlobStore
from ufo.browser import CdpProvider
from ufo.connectors import ConnectorRegistry
from ufo.credentials import CredentialRequests
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, SourceReader
from ufo.ext.loader import HookChain
from ufo.ext.manifest import (
    PostToolUse,
    PostToolUseFailure,
    PreToolUse,
    Stop,
    UserPromptSubmit,
)
from ufo.grants import GrantStore
from ufo.hub import Absorbed, CostTick, Hub, LiveFrame, Parked, Terminal
from ufo.loop.compaction import (
    Compaction,
    is_context_overflow,
)
from ufo.loop.prompts.render import RenderedPrompt
from ufo.loop.transcript import Transcript
from ufo.memory import MemorySearch
from ufo.models.catalog import CORE_PRICING
from ufo.models.interface import (
    ImageBlock,
    ImageSource,
    Message,
    ModelClient,
    ModelRequest,
    ModelResponseTruncated,
    PromptCacheTtl,
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
from ufo.models.pricing import Pricing
from ufo.o11y import emit_histogram, emit_metric, formatted_stack, log, span, turn_profile
from ufo.sandbox.session import TOOL_OUTPUT_DIR, SandboxSession
from ufo.schema import tables
from ufo.schema.records import (
    CANCELLED,
    INTERNAL_ADMISSION,
    MEMBER_ADMISSION,
    NON_TERMINAL_STATUSES,
    PARKED,
    ROUND_BUDGET_INCOMPLETE,
    RUNNING,
    SCHEDULED_ADMISSION,
    TERMINAL_ERROR_MESSAGE_MAX_CHARS,
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
)
from ufo.search import SearchProvider
from ufo.seats import SEAT_REVOKED_MESSAGE, Seats
from ufo.skills.runtime import CORE_SKILL_REGISTRY, LoadedSkill, SkillRegistry
from ufo.tools.context import (
    ImageContent,
    Spawn,
    SubagentControl,
    TextContent,
    ToolContext,
    UntrustedContentError,
)
from ufo.tools.registry import REQUESTED_BY, ToolRegistry
from ufo.transcript import Conversation
from ufo.untrusted import wall
from ufo.workspace_changes import WorkspaceChangeRecorder

MAX_OUTPUT_TOKENS = 32_768
FIND_MAX_TOKENS = 8_192
MAIN_ROUND_LIMIT = 200
MAX_PARALLEL_TOOL_CALLS = 8
DELTA_FLUSH_BYTES = 2048
DELTA_FLUSH_SECONDS = 0.04
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
CONTEXT_TIME_FORMAT = "%A %Y-%m-%d %H:%M %Z"
FORCE_FINAL_PROMPT = (
    "You have reached the maximum number of tool-use rounds. Do not call any more tools. "
    "Give your best final answer now using everything gathered so far."
)
TRANSCRIPT_WRITE_ATTEMPTS = 3
TRANSCRIPT_WRITE_RETRY_SECONDS = 0.5
COMMIT_RETRY_INITIAL_SECONDS = 1.0
COMMIT_RETRY_MAX_SECONDS = 30.0
ASK_USER_TOOL = "ask_user"
REQUEST_CREDENTIALS_TOOL = "request_credentials"
CONNECT_ACCOUNT_TOOL = "connect_account"
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

SandboxFor = Callable[[UUID | None], Awaitable[SandboxSession]]
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


@dataclass(frozen=True)
class _TurnHandoff:
    id: UUID
    workspace_id: UUID
    conversation_id: UUID
    workflow_id: str


async def _claim_turn_with_handoff(
    turn_id: UUID, attempt: str
) -> tuple[str | None, _TurnHandoff | None]:
    claim = await _claim_turn(turn_id, attempt)
    if claim is None:
        return None, None
    async with workspace_tx() as connection:
        turn_scope = (
            await connection.execute(
                sa.select(
                    tables.turn.c.workspace_id,
                    tables.turn.c.conversation_id,
                ).where(tables.turn.c.id == turn_id)
            )
        ).one()
        await connection.execute(
            sa.select(tables.conversation.c.id)
            .where(tables.conversation.c.id == turn_scope.conversation_id)
            .with_for_update()
        )
        next_turn = (
            await connection.execute(
                sa.select(
                    tables.turn.c.id,
                    tables.turn.c.dispatch_enqueued_at,
                    tables.turn.c.running_attempt,
                )
                .where(
                    tables.turn.c.conversation_id == turn_scope.conversation_id,
                    tables.turn.c.status == "queued",
                )
                .order_by(tables.turn.c.seq)
                .limit(1)
                .with_for_update()
            )
        ).one_or_none()
        if next_turn is None or next_turn.dispatch_enqueued_at is not None:
            return claim, None
        stamped = (
            await connection.execute(
                sa.update(tables.turn)
                .values(dispatch_enqueued_at=sa.func.now(), updated_at=sa.func.now())
                .where(
                    tables.turn.c.id == next_turn.id,
                    tables.turn.c.status == "queued",
                    tables.turn.c.dispatch_enqueued_at.is_(None),
                )
                .returning(tables.turn.c.id)
            )
        ).scalar_one_or_none()
    return (
        claim,
        None
        if stamped is None
        else _TurnHandoff(
            stamped,
            turn_scope.workspace_id,
            turn_scope.conversation_id,
            str(stamped) if next_turn.running_attempt is None else uuid4().hex,
        ),
    )


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


@dataclass(frozen=True, repr=False)
class _RoundInput:
    messages: tuple[Message, ...]
    system: str
    offer_tools: bool
    force_finish: bool
    first_round: bool

    def __repr__(self) -> str:
        return (
            f"_RoundInput(messages={len(self.messages)}, system_chars={len(self.system)}, "
            f"offer_tools={self.offer_tools}, force_finish={self.force_finish}, "
            f"first_round={self.first_round})"
        )


@dataclass(frozen=True, repr=False)
class _BoundToolCall:
    context: ToolContext
    call: ToolUseBlock

    def __repr__(self) -> str:
        return f"_BoundToolCall(tool={self.call.name}, call_id={self.call.id})"


@dataclass(frozen=True, repr=False)
class _RejectedToolCall:
    call: ToolUseBlock
    text: str
    outcome: str
    error_class: str

    def __repr__(self) -> str:
        return (
            f"_RejectedToolCall(tool={self.call.name}, call_id={self.call.id}, "
            f"outcome={self.outcome}, error_class={self.error_class})"
        )


type _DispatchInput = _BoundToolCall | _RejectedToolCall


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
    referenced images) after the step returns."""

    tool_use_id: str
    text: str
    is_error: bool
    activity: bool = False
    image_refs: tuple[ImageRef, ...] = ()


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


class IntentRefused(Exception):
    """A prepared intent's tool dispatch answered with an error result — the kind's own refusal
    (admin gate, validation, unknown name) — so the intent applied nothing. Carries the refusal
    text into the terminal frame's error_message for the submitting panel."""


def _dispatch_segments(
    tools: ToolRegistry, tool_calls: tuple[ToolUseBlock, ...]
) -> Iterator[tuple[ToolUseBlock, ...]]:
    """Split a round's calls into dispatch groups that preserve the model's call order: a run of
    consecutive parallel-safe calls executes concurrently (bounded by MAX_PARALLEL_TOOL_CALLS),
    and every other call — a mutation, an unknown name, anything with cross-call dependencies —
    is its own in-order barrier, so an edit never races the read it depends on."""
    segment: list[ToolUseBlock] = []
    for call in tool_calls:
        try:
            safe = tools.get(call.name).parallel_safe
        except KeyError:
            safe = False
        if safe:
            if len(segment) == MAX_PARALLEL_TOOL_CALLS:
                yield tuple(segment)
                segment = []
            segment.append(call)
            continue
        if segment:
            yield tuple(segment)
            segment = []
        yield (call,)
    if segment:
        yield tuple(segment)


def _parse_args(partials: list[str]) -> dict[str, object]:
    joined = "".join(partials)
    return json.loads(joined) if joined.strip() else {}


def _context_tag(message_id: UUID, context: TurnContext | None, admitted_at: datetime) -> str:
    """The <context> tag rendered before a member inbound: the admission moment (in the sender's
    zone when the surface supplied one, else UTC), the sender the surface named, and the source it
    named for the request. The persisted moment — the turn row's for the founding message, the queue
    row's for a drained arrival — never the wall clock, so a queued, parked, or replayed message
    keeps the time the member actually spoke."""
    zone = ZoneInfo(context.timezone) if context is not None and context.timezone else UTC
    lines = [
        f"message_ref: {message_id}",
        f"time: {admitted_at.astimezone(zone).strftime(CONTEXT_TIME_FORMAT)}",
    ]
    if context is not None and context.sender:
        lines.append(f"sender: {context.sender}")
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
) -> None:
    """One count and one wall-clock observation for a dispatched call, so a dashboard reads which
    tool the fleet spends its time in and where that time fails. `outcome` separates the ends a
    dispatch has by whose fault each one is — the model's, the tool's, a policy hook's, or the
    engine's — because a metric that reports only the successes reads as nothing having failed, and
    one that folds an infrastructure fault into a refusal reads as policy working as designed.
    `error_class` rides every end that carries an exception, and `profile` separates the dispatches
    a subagent makes from the main agent's.

    The wall clock is what the round waited on. A name the registry does not hold reports as
    UNREGISTERED_TOOL — the name arrives on an assistant message the model wrote, so passing it
    through would mint one series per invented name."""
    registered = any(tool.name == call.name for tool in tools.tools)
    dimensions = {
        "tool": call.name if registered else UNREGISTERED_TOOL,
        "outcome": outcome,
        "profile": profile,
        **({} if error_class is None else {"error_class": error_class}),
    }
    emit_metric("tool_call_total", **dimensions)
    emit_histogram("tool_call_ms", int((time.monotonic() - started) * 1000), **dimensions)


def _loaded_skill_closures(
    messages: tuple[Message, ...], skills: SkillRegistry
) -> Iterator[tuple[LoadedSkill, ...]]:
    """What each completed `load_skill` in the window put in front of the model: the registry
    closure of the name the call asked for, which is exactly the entries `loaded_context` injected
    for it. Read from the call's own input and the registry, never from the result's prose — a
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
    carries no payload; the reply's prose still asks."""
    last, result = tool_calls[-1], results[-1]
    if last.name != tool_name or result.is_error or not isinstance(result.content, str):
        return None
    _directive, _, rest = result.content.partition("\n")
    try:
        return model.model_validate(json.loads(rest.split("\n", 1)[0]))
    except (json.JSONDecodeError, ValidationError):
        return None


def _total_usage(usage_events: list[Usage]) -> Usage:
    return Usage(
        input_tokens=sum(u.input_tokens for u in usage_events),
        output_tokens=sum(u.output_tokens for u in usage_events),
        cache_read_tokens=sum(u.cache_read_tokens for u in usage_events),
        cache_write_5m_tokens=sum(u.cache_write_5m_tokens for u in usage_events),
        cache_write_1h_tokens=sum(u.cache_write_1h_tokens for u in usage_events),
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
        its moment. A subagent's inbound stays the bare schema payload its profile
        contract promises."""
        inbound = self.turn.inbound
        if self.turn.subagent_profile is None:
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
    ) -> None:
        conversation = Conversation(
            seq=self.turn.seq, messages=messages, system=system, injected=injected
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
    and keeps its reattach."""

    replaying: bool = False


@dataclass(frozen=True, repr=False)
class TurnEngine:
    turn: Turn
    agent: Agent
    system_prompt: RenderedPrompt
    model: ModelClient
    provider: str
    transcript: Transcript
    compaction: Compaction
    hub: Hub
    sandbox: SandboxSession
    cdp_provider: CdpProvider | None
    search_provider: SearchProvider | None
    connectors: ConnectorRegistry
    tools: ToolRegistry
    tool_ext: dict[str, ExtensionContext]
    hooks: HookChain
    blob: BlobStore
    spawn: Spawn
    audience: Audience
    artifact_token_secret: str
    grants: GrantStore | None
    previous_turn_ended_at: datetime | None = None
    sandbox_for: SandboxFor | None = None
    subagents_for: SubagentsFor | None = None
    requestable_credentials: CredentialRequests | None = None
    memory: MemorySearch | None = None
    public_base_url: str | None = None
    pricing: Pricing = CORE_PRICING
    subagents: SubagentControl | None = None
    attempt: str = ""
    max_rounds: int = MAIN_ROUND_LIMIT
    skills: SkillRegistry = CORE_SKILL_REGISTRY
    preload: tuple[LoadedSkill, ...] = ()
    output_model: type[BaseModel] | None = None
    adoption: AdoptionReplay = field(default_factory=AdoptionReplay)

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
        """This turn's `profile` telemetry dimension — its subagent profile, or `main`."""
        return turn_profile(self.turn.subagent_profile)

    @property
    def cache_ttl(self) -> PromptCacheTtl:
        """The TTL every model call this turn writes its cache breakpoints at. A subagent turn ends
        with its conversation, so nothing reads the prefix an hour later and it pays the cheaper 5m
        write; the main agent's conversation spans bursty gaps between member messages, where a 5m
        entry expires before the next turn reads it."""
        return "1h" if self.turn.subagent_profile is None else "5m"

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
                reasoning="off",
                prompt_cache_ttl=self.cache_ttl,
            )
            parts: list[str] = []
            async for event in self.model.complete(request):
                match event:
                    case TextDelta(text=text):
                        parts.append(text)
                    case Usage():
                        usage_events.append(event)
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
            on_behalf_of_member_id=self.turn.on_behalf_of_member_id,
            artifact_token_secret=self.artifact_token_secret,
            grants=self.grants,
            skills=self.skills,
            loaded_skills=self.compaction.loaded_skills,
            cdp_provider=self.cdp_provider,
            search_provider=self.search_provider,
            connectors=self.connectors,
            find=rank_find,
            requestable_credentials=self.requestable_credentials,
            public_base_url=self.public_base_url,
        )
        try:
            if not await self._mark_running():
                return await self._resolve_unclaimed()
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
            pending_guard = self.turn.subagent_profile is None
            if inbound.denied is not None:
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
                    await self._record_workspace_changes()
                    return denial
                founding_denial = DENIED_INBOUND_NOTICE.format(reason=escape(inbound.denied))
                messages = (
                    *await self._repair()._prior_messages(),
                    Message(role="user", content=founding_denial),
                )
            else:
                if inbound.injected:
                    system = f"{system}\n\n{inbound.injected}"
                messages = await self._load_messages()
                if self.turn.subagent_profile is None:
                    founding = messages[-1].content
                    if not isinstance(founding, str):
                        raise RuntimeError("founding inbound did not render as text")
                    requesters[self.turn.id] = ActiveMessage(
                        member_id=self.turn.speaker_member_id,
                        rendered=founding,
                    )
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
                    unless_arrivals=pending_guard,
                    absorbed=tuple(absorbed_ids),
                )
                if frame is None:
                    messages = (*final_messages, Message(role="assistant", content=answer))
                    continue
                if frame.status == "done":
                    await self._persist_transcript(final_messages, answer, system, inbound.injected)
                else:
                    await self._persist_inbound(tuple(arrival_log), founding_denial)
                await self._record_workspace_changes()
                return frame
        except TurnParked as parked:
            meter.exited(PARKED)
            await self._park(parked.message, usage_events)
            raise
        except DBOSWorkflowCancelledError:
            meter.exited(CANCELLED)
            await self._bill_cancelled(usage_events)
            await self._release_unabsorbed(tuple(absorbed_ids))
            await self._persist_inbound(tuple(arrival_log), founding_denial)
            raise
        except asyncio.CancelledError:
            meter.exited(PREEMPTED)
            await self._bill_cancelled(usage_events)
            await self._release_unabsorbed(tuple(absorbed_ids))
            raise
        except Exception as error:
            await self._commit("failed", usage_events, meter, error=error)
            await self._release_unabsorbed(tuple(absorbed_ids))
            await self._persist_inbound(tuple(arrival_log), founding_denial)
            raise
        finally:
            await context.cleanup.drain()

    async def run_intent(self) -> TerminalFrame | None:
        """Run a prepared-intent turn: dispatch the one tool call the inbound envelope names,
        verbatim, and commit its result — no model round, so the submitted values apply exactly or
        the kind's refusal returns, never a paraphrase. A successful `connect_account` dispatch
        leaves its private OAuth handoff on the terminal exactly as a chat round does, so the
        panel's stream mints the member's URL the same way. The dispatch is the same guarded step a
        model call takes: `pre_tool_use` may deny or fold arguments, `post_tool_use`/
        `post_tool_use_failure` fire on the result, member authority binds through the founding
        message, and the step memoizes across crash recovery. The turn-shaped hooks do not fire —
        `user_prompt_submit` polices member prose and the inbound is a machine envelope; `stop`
        observes a model's answer and none exists; the compaction pair has no window. Spend is
        enforced at admission, where a capped member's intent parks — a running intent makes no
        model call, so it never crosses the per-round check. Arrivals cannot exist: an intent
        admission never folds into a live turn, so this turn's queue is empty by construction and
        the per-conversation partition runs a member's intents one at a time in order."""
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
            on_behalf_of_member_id=self.turn.on_behalf_of_member_id,
            artifact_token_secret=self.artifact_token_secret,
            grants=self.grants,
            skills=self.skills,
            loaded_skills=self.compaction.loaded_skills,
            cdp_provider=self.cdp_provider,
            search_provider=self.search_provider,
            connectors=self.connectors,
            requestable_credentials=self.requestable_credentials,
            public_base_url=self.public_base_url,
        )
        try:
            if not await self._mark_running():
                return await self._resolve_unclaimed()
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
            bound = await self._bind_or_error(context, call, requesters)
            result = await self._dispatch_step(bound)
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
                connect_request = _final_act(
                    (call,),
                    dispatched_result,
                    CONNECT_ACCOUNT_TOOL,
                    ConnectRequest,
                )
                credential_request = _final_act(
                    (call,),
                    dispatched_result,
                    REQUEST_CREDENTIALS_TOOL,
                    CredentialRequest,
                )
                frame = await self._commit(
                    "done",
                    usage_events,
                    meter,
                    answer=result.text,
                    connect_request=connect_request,
                    credential_request=credential_request,
                )
            await self._persist_transcript(await self._load_messages(), result.text, "", "")
            return frame
        except DBOSWorkflowCancelledError:
            meter.exited(CANCELLED)
            raise
        except asyncio.CancelledError:
            meter.exited(PREEMPTED)
            raise
        except Exception as error:
            await self._commit("failed", usage_events, meter, error=error)
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
        the claim, and is resolved as superseded, so single ownership is the DB claim itself, not
        the per-conversation partition. Clearing the advisory dispatch stamp here tells the outbox
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
    ) -> tuple[
        tuple[Message, ...],
        str,
        AskUserInput | None,
        CredentialRequest | None,
        ConnectRequest | None,
    ]:
        """Call the model until it answers with text and no tool calls; each tool-calling round
        dispatches consecutive parallel-safe calls concurrently and everything else as an
        in-order barrier — result order stays call order, the memoized dispatch steps stay
        replay-deterministic because tasks start in call order on the one loop, and a failing
        dispatch raises only after its segment's siblings finish, so no call is left running
        while the turn commits its terminal — and feeds all results back as one user turn.
        Every round opens by absorbing queued arrivals — messages admitted while the previous
        round streamed or its tools ran — so the drain always lands between a completed
        (tool_use, tool_result) pair and the next model call, never inside one. It then re-derives
        which skill workflows the window holds — from the window itself, before the compaction that
        may drop them, so a repeat `load_skill` re-mounts its files without re-injecting its
        instructions, and the summary replacing the head carries the names to re-load. A round that
        calls a tool still narrates: its text streams live to any tailing surface, but only the
        closing round's text is the returned answer — mid-turn narration is transient working prose,
        and the shell prompt binds the model to a self-contained closing message, so a durable
        surface delivers one reply, never the stacked steps that produced it. Also returns the
        structured question, credential request, or connect request left pending when its tool was
        the turn's final act — each round overwrites all three, so a turn that asked and then
        worked on carries none.

        A round whose stream dies at the max_tokens budget is dropped from the window — its
        partial tool calls cannot be replayed as a valid assistant message — but its already-paid
        deltas are salvaged to a workspace file, and the corrective user message fed back carries
        the path, so the retried round continues from the partial instead of regenerating it. The
        retries draw on the same round budget as every other fed-back failure; a turn that keeps
        truncating exhausts its rounds and fails when the forced final round truncates too. Any
        other mid-stream model error fails immediately.

        A subagent turn (`output_model` set) ends only through the finish tool: a lone finish call
        whose args validate is the terminal, and its canonical JSON — never its narration — is
        the answer the parent validates. A finish call with a bad payload or sharing its round
        with other work comes back as an error result the model corrects; a turn that stops on
        plain prose instead is closed by one forced finish round, so the terminal is schema-shaped
        by construction."""
        nudged = False
        question: AskUserInput | None = None
        credential_request: CredentialRequest | None = None
        connect_request: ConnectRequest | None = None
        for round_index in range(self.max_rounds):
            absorbed = await self._absorb_arrivals(messages, arrival_log, absorbed_ids, requesters)
            if len(absorbed) > len(messages):
                question = credential_request = connect_request = None
            messages = absorbed
            await self._enforce_spend(usage_events, requesters)
            self._reseed_loaded_skills(messages)
            active_requests = tuple(message.rendered for message in requesters.values())
            with span("compaction.maybe"):
                messages, compaction_usage = await self.compaction.maybe_compact(
                    messages, active_requests=active_requests
                )
            self._reseed_loaded_skills(messages)
            usage_events.extend(compaction_usage)
            meter.rounds += 1
            try:
                messages, round_result = await self._stream_recovering_overflow(
                    messages,
                    usage_events,
                    system,
                    active_requests=active_requests,
                    first_round=meter.rounds == 1,
                )
                text, tool_calls = round_result.text, round_result.tool_calls
            except ModelStreamError as error:
                if error.model_error_class != MODEL_TRUNCATED_ERROR_CLASS:
                    raise
                emit_metric("turn_truncation_recovered_total", profile=self.profile)
                log(
                    "turn.truncation_recovered",
                    turn_id=str(self.turn.id),
                    round=round_index,
                    salvaged_chars=len(error.partial_output),
                )
                feedback = TRUNCATION_FEEDBACK
                if error.partial_output:
                    path = await self._offload(
                        f"truncated-{self.turn.id}-{round_index}.txt", error.partial_output
                    )
                    if path is not None:
                        feedback += TRUNCATION_SALVAGE_NOTICE.format(path=path)
                messages = (*messages, Message(role="user", content=feedback))
                continue
            await self._publish_cost(usage_events)
            if not tool_calls:
                if text.strip():
                    if self.output_model is not None:
                        messages = (
                            *messages,
                            Message(role="assistant", content=text),
                            Message(role="user", content=FINISH_PROMPT),
                        )
                        messages, answer = await self._force_finish(messages, usage_events, system)
                        return messages, answer, None, None, None
                    return messages, text, question, credential_request, connect_request
                if nudged:
                    raise RuntimeError("model returned an empty response twice")
                nudged = True
                messages = (*messages, Message(role="user", content=EMPTY_RESPONSE_NUDGE))
                continue
            finish_error: str | None = None
            if self.output_model is not None and any(
                call.name == FINISH_TOOL for call in tool_calls
            ):
                if len(tool_calls) > 1:
                    finish_error = FINISH_ALONE
                else:
                    try:
                        output = self.output_model.model_validate(tool_calls[0].input)
                    except ValidationError as error:
                        finish_error = FINISH_SCHEMA_ERROR.format(error=error)
                    else:
                        return messages, output.model_dump_json(), None, None, None
            assistant_blocks = (
                *round_result.reasoning,
                *((TextBlock(text=text),) if text else ()),
                *tool_calls,
            )
            results: tuple[ToolResultBlock, ...] = ()
            for segment in _dispatch_segments(self.tools, tool_calls):
                if finish_error is not None and segment[0].name == FINISH_TOOL:
                    results = (
                        *results,
                        ToolResultBlock(
                            tool_use_id=segment[0].id,
                            content=_bounded(finish_error),
                            is_error=True,
                        ),
                    )
                    continue
                bound = await asyncio.gather(
                    *(self._bind_or_error(context, call, requesters) for call in segment),
                    return_exceptions=True,
                )
                failures = [outcome for outcome in bound if isinstance(outcome, BaseException)]
                if failures:
                    raise failures[0]
                dispatched = await asyncio.gather(
                    *(
                        self._dispatch(item)
                        for item in bound
                        if not isinstance(item, BaseException)
                    ),
                    return_exceptions=True,
                )
                failures = [outcome for outcome in dispatched if isinstance(outcome, BaseException)]
                if failures:
                    raise failures[0]
                results = (
                    *results,
                    *(o for o in dispatched if not isinstance(o, BaseException)),
                )
            question = _final_act(tool_calls, results, ASK_USER_TOOL, AskUserInput)
            credential_request = _final_act(
                tool_calls, results, REQUEST_CREDENTIALS_TOOL, CredentialRequest
            )
            connect_request = _final_act(tool_calls, results, CONNECT_ACCOUNT_TOOL, ConnectRequest)
            messages = (
                *messages,
                Message(role="assistant", content=assistant_blocks),
                Message(role="user", content=results),
            )
        meter.incomplete_reason = ROUND_BUDGET_INCOMPLETE
        messages, text = await self._force_final(messages, usage_events, system, requesters)
        return messages, text, None, None, None

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
        and no member sent an extension's prompt or a child's result."""
        drained: list[UUID] = []
        for arrival in await self._claim_arrivals(tuple(absorbed_ids)):
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
        if drained:
            await self._publish(Absorbed(arrivals=tuple(drained)))
        return messages

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
            content = f"{content}\n\n<injected_context>\n{submitted.injected}\n</injected_context>"
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
                            if self.turn.subagent_profile is not None
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

    async def _force_final(
        self,
        messages: tuple[Message, ...],
        usage_events: list[Usage],
        system: str,
        requesters: dict[UUID, ActiveMessage],
    ) -> tuple[tuple[Message, ...], str]:
        """The round budget is spent: rather than fail the turn, force one closing answer. Append
        the force-final prompt and run a single model turn with no tools offered — the model can no
        longer call a tool, so it answers with what it gathered instead of the turn erroring out. A
        subagent that exhausts its smaller budget closes through a forced finish call instead — a
        best-effort `done` answer that keeps its output schema; only a forced call that still
        violates the schema ends the turn `failed`, which the parent's spawn receives as an
        ordinary tool error, never a crash. Exhaustion is a distinct terminal shape: its
        `incomplete_reason` travels with the answer, and a metric and log let an operator spot an
        agent chronically hitting its ceiling (a prompt or tool-loop bug) that `done` would hide."""
        emit_metric("turn_round_budget_exhausted_total", profile=self.profile)
        log("turn.force_final", turn_id=str(self.turn.id), rounds=self.max_rounds)
        await self._enforce_spend(usage_events, requesters)
        active_requests = tuple(message.rendered for message in requesters.values())
        messages, compaction_usage = await self.compaction.maybe_compact(
            messages, active_requests=active_requests
        )
        usage_events.extend(compaction_usage)
        if self.output_model is not None:
            messages = (*messages, Message(role="user", content=FORCE_FINISH_PROMPT))
            return await self._force_finish(messages, usage_events, system)
        messages = (*messages, Message(role="user", content=FORCE_FINAL_PROMPT))
        messages, result = await self._stream_recovering_overflow(
            messages,
            usage_events,
            system,
            offer_tools=False,
            active_requests=active_requests,
        )
        await self._publish_cost(usage_events)
        return messages, result.text

    async def _force_finish(
        self,
        messages: tuple[Message, ...],
        usage_events: list[Usage],
        system: str,
    ) -> tuple[tuple[Message, ...], str]:
        """One forced closing round for a subagent turn: only the finish tool is offered and
        tool_choice compels it, so a child that stopped on prose or spent its round budget still
        commits a schema-shaped terminal. A forced call that fails the schema anyway is a hard
        fault — the turn fails loud rather than committing a malformed answer."""
        if self.output_model is None:
            raise RuntimeError("finish forced on a turn with no output model")
        messages, result = await self._stream_recovering_overflow(
            messages, usage_events, system, force_finish=True
        )
        await self._publish_cost(usage_events)
        match result.tool_calls:
            case (ToolUseBlock(name=name, input=args),) if name == FINISH_TOOL:
                try:
                    return messages, self.output_model.model_validate(args).model_dump_json()
                except ValidationError as error:
                    raise RuntimeError(
                        f"forced finish failed the output schema: {error}"
                    ) from error
            case _:
                raise RuntimeError("forced finish round did not return a lone finish call")

    async def _stream_recovering_overflow(
        self,
        messages: tuple[Message, ...],
        usage_events: list[Usage],
        system: str,
        offer_tools: bool = True,
        force_finish: bool = False,
        active_requests: tuple[str, ...] = (),
        first_round: bool = False,
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
        reject cap holds its awaiting parent until the cap is raised. The no-caps fast-path skips
        the DB round-trip entirely once a recent decision confirmed no cap applies to this turn.

        The seat gate re-checks every member whose message the turn has absorbed, so revoking any
        speaker's seat stops the aggregate before its next model call. A scheduled turn gates on
        the member it acts on behalf of. It costs one indexed read per round whatever the turn
        absorbed, deliberately and with no fast-path: a seat is what an admin revokes to cut someone
        off, so a cached answer would keep answering them for as long as it was held, and a
        running turn is the case the revoke most needs to reach."""
        members = {
            message.member_id for message in requesters.values() if message.member_id is not None
        }
        if (
            self.turn.admission_source == SCHEDULED_ADMISSION
            and self.turn.on_behalf_of_member_id is not None
        ):
            members.add(self.turn.on_behalf_of_member_id)
        if members:
            async with workspace_tx() as connection:
                if not await Seats(self.turn.workspace_id).all_seated(connection, members):
                    raise TurnParked(SEAT_REVOKED_MESSAGE)
        member_id = audience_member(self.audience)
        if applicable_caps_absent(self.turn.workspace_id, member_id, self.turn.agent_id):
            return
        pending = self.pricing.micro_usd(self.agent.model, _total_usage(usage_events))
        async with workspace_tx() as connection:
            decision = await SpendEvaluator(
                self.turn.workspace_id, member_id, self.turn.agent_id
            ).decide(connection, pending)
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

        A subagent turn offers the finish tool beside the registry's set — its input schema is the
        profile's output model, so the return shape is a tool contract the model corrects against,
        not a prose convention. `force_finish` offers finish alone and compels it via tool_choice
        (reasoning off — a forced choice cannot run under extended thinking)."""
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
            tools: tuple[ToolSchema, ...] = (finish,)
        elif round_input.offer_tools:
            tools = self.tools.schemas() if finish is None else (*self.tools.schemas(), finish)
        else:
            tools = ()
        request = ModelRequest(
            model=self.agent.model,
            system=round_input.system,
            messages=round_input.messages,
            max_tokens=MAX_OUTPUT_TOKENS,
            tools=tools,
            tool_choice=FINISH_TOOL if round_input.force_finish else None,
            reasoning="off" if round_input.force_finish else self.agent.reasoning,
            prompt_cache_ttl=self.cache_ttl,
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
            "provider": self.provider,
            "profile": self.profile,
            "ttl": request.prompt_cache_ttl,
            "round": "first" if round_input.first_round else "later",
            "gap": gap,
        }
        parts: list[str] = []
        buffer: list[str] = []
        pending = 0
        flush_lock = asyncio.Lock()
        stop = asyncio.Event()
        call_names: dict[str, str] = {}
        call_json: dict[str, list[str]] = {}
        call_order: list[str] = []
        reasoning: list[ThinkingBlock | RedactedThinkingBlock | ReasoningItemBlock] = []
        usages: list[Usage] = []
        error: Exception | None = None

        async def flush() -> None:
            nonlocal pending
            async with flush_lock:
                if not buffer:
                    return
                text = "".join(buffer)
                buffer.clear()
                pending = 0
                await self.hub.publish(self.turn.id, TextDelta(text=text))

        async def pace() -> None:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), DELTA_FLUSH_SECONDS)
                except TimeoutError:
                    await flush()

        started = time.monotonic()
        first_event_ms: int | None = None
        with span(
            "model.round",
            model=request.model,
            provider=self.provider,
            round="first" if round_input.first_round else "later",
        ) as round_span:
            pacer = asyncio.ensure_future(pace())
            try:
                async for event in self.model.complete(request):
                    if first_event_ms is None:
                        first_event_ms = int((time.monotonic() - started) * 1000)
                        round_span.add_event("model.first_event")
                    match event:
                        case TextDelta(text=chunk):
                            parts.append(chunk)
                            buffer.append(chunk)
                            pending += len(chunk)
                            if pending >= DELTA_FLUSH_BYTES:
                                await flush()
                        case ToolCallStart(id=call_id, name=name):
                            call_names[call_id] = name
                            call_json[call_id] = []
                            call_order.append(call_id)
                        case ToolCallDelta(id=call_id, partial_json=partial):
                            call_json[call_id].append(partial)
                        case ThinkingBlock() | RedactedThinkingBlock() | ReasoningItemBlock():
                            reasoning.append(event)
                        case Usage():
                            usages.append(event)
            except Exception as caught:
                error = caught
            finally:
                stop.set()
                try:
                    await pacer
                except Exception as caught:
                    if error is None:
                        error = caught
            wall_ms = int((time.monotonic() - started) * 1000)
            await flush()
        emit_histogram(
            "model_round_ms",
            wall_ms,
            model=request.model,
            provider=self.provider,
            profile=self.profile,
            **({} if error is None else {"error_class": type(error).__name__}),
        )
        round_usage = _total_usage(usages)
        cache_result = "hit" if round_usage.cache_read_tokens else "miss"
        emit_metric("model_cache_round_total", **cache_dimensions, result=cache_result)
        if first_event_ms is not None:
            emit_histogram(
                "model_first_event_ms",
                first_event_ms,
                model=request.model,
                **cache_dimensions,
                result=cache_result,
            )
        for kind, amount in (
            ("input", round_usage.input_tokens),
            ("cache_read", round_usage.cache_read_tokens),
            ("cache_write_5m", round_usage.cache_write_5m_tokens),
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
                round_usage.cache_write_5m_tokens + round_usage.cache_write_1h_tokens,
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
        if error is not None:
            partial_calls = tuple(
                f"[tool call: {call_names[call_id]}]\n{''.join(call_json[call_id])}"
                for call_id in call_order
            )
            return StreamResult(
                usages=tuple(usages),
                error_class=type(error).__name__,
                error_message=str(error),
                partial_output="\n\n".join(
                    segment for segment in ("".join(parts), *partial_calls) if segment
                ),
            )
        if not usages:
            raise RuntimeError("model stream produced no usage")
        tool_calls = tuple(
            ToolUseBlock(
                id=call_id, name=call_names[call_id], input=_parse_args(call_json[call_id])
            )
            for call_id in call_order
        )
        return StreamResult(
            text="".join(parts),
            tool_calls=tool_calls,
            reasoning=tuple(reasoning),
            usages=tuple(usages),
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
        `_force_final` is the one exception — that round ends the turn without offering tools again.
        `preload` rides every reseed: a subagent's preloaded workflows sit in its system prompt,
        which no compaction touches."""
        self.compaction.loaded_skills.reseed(
            _loaded_skill_closures(messages, self.skills), preloaded=self.preload
        )

    async def _bind_or_error(
        self,
        context: ToolContext,
        call: ToolUseBlock,
        requesters: dict[UUID, ActiveMessage],
    ) -> _DispatchInput:
        started = time.monotonic()
        try:
            context, call = await self._bind_requester(context, call, requesters)
            return _BoundToolCall(context=context, call=call)
        except asyncio.CancelledError as error:
            _meter_dispatch(
                self.tools, call, started, "step_failed", type(error).__name__, self.profile
            )
            raise
        except Exception as error:
            return _RejectedToolCall(
                call=call,
                text=f"{type(error).__name__}: {error}",
                outcome="invalid_call" if isinstance(error, ValueError) else "step_failed",
                error_class=type(error).__name__,
            )

    def _dispatch(self, bound: _DispatchInput) -> Awaitable[ToolResultBlock]:
        return self._dispatch_result(self._dispatch_step(bound))

    async def _dispatch_result(self, step: Awaitable[DispatchResult]) -> ToolResultBlock:
        result = await step
        if not result.image_refs:
            return ToolResultBlock(
                tool_use_id=result.tool_use_id,
                content=result.text,
                is_error=result.is_error,
                activity=result.activity,
            )
        images = [
            ImageBlock(
                source=ImageSource(
                    media_type=ref.media_type, data=(await self.blob.get(ref.blob_key)).decode()
                )
            )
            for ref in result.image_refs
        ]
        blocks: tuple[TextBlock | ImageBlock, ...] = (
            *((TextBlock(text=result.text),) if result.text else ()),
            *images,
        )
        return ToolResultBlock(
            tool_use_id=result.tool_use_id,
            content=blocks,
            is_error=result.is_error,
            activity=result.activity,
        )

    async def _bind_requester(
        self,
        context: ToolContext,
        call: ToolUseBlock,
        requesters: dict[UUID, ActiveMessage],
    ) -> tuple[ToolContext, ToolUseBlock]:
        tool_input = dict(call.input)
        requester: UUID | None = None
        if REQUESTED_BY in tool_input:
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
        acting_member = requester if requester is not None else context.on_behalf_of_member_id
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

    async def _offload(self, name: str, content: str) -> str | None:
        """Write `content` into the turn's private `.tool-output` dir and return its path, ensuring
        the directory exists first. A member write (or bash) can leave a file squatting the name,
        which the bare `mkdir -p` in the write step cannot reclaim — left unhandled it fails
        `File exists` and poisons every later offload and salvage in the workspace.

        None means the write failed, and every caller degrades on it rather than lose the turn to
        plumbing: a turn that reached this point has already done its work. Loud, because what
        degrades is invisible to the model — it silently loses either a result's tail or its own
        salvaged partial."""
        path = f"{TOOL_OUTPUT_DIR}/{name}"
        try:
            if await self.sandbox.ensure_tool_output_dir():
                emit_metric("sandbox_tool_output_dir_reclaimed_total", profile=self.profile)
                log("sandbox.tool_output_dir_reclaimed", turn_id=str(self.turn.id))
            await self.sandbox.write_file(path, content.encode())
        except Exception as error:
            emit_metric("tool_offload_failed_total", profile=self.profile)
            log(
                "tool.offload_failed",
                turn_id=str(self.turn.id),
                error_class=type(error).__name__,
                chars=len(content),
            )
            return None
        return path

    @DBOS.step(preemptible=True)
    async def _dispatch_step(self, bound: _DispatchInput) -> DispatchResult:
        """Run one resolved binding in the DBOS step claimed for it in model order. A rejected bind
        claims the same step and records its error, so bind latency or outcome cannot change step
        order or count on recovery. The recorded `DispatchResult` replays without rebinding or
        re-invoking the handler, so a side-effecting tool's external write is never re-applied. A
        bad requester, name, or arguments becomes an is_error result before any hook fires (there
        is no validated input to police). Then
        pre_tool_use may Deny
        (the tool never dispatches) or ModifyInput (fold the args); the handler runs in the sandbox
        with the folded args (a raising handler is an is_error result). A non-error result over
        MAX_TOOL_RESULT_CHARS is offloaded — its full text written to a workspace `.tool-output`
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
        (`{turn}/{name}/{call_id}`) to dedup its external write on a cross-attempt resume; a read
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
        started = time.monotonic()
        outcome, error_class = "ok", None
        with span("tool.dispatch", tool=call.name):
            try:
                if isinstance(bound, _RejectedToolCall):
                    outcome, error_class = bound.outcome, bound.error_class
                    return DispatchResult(
                        tool_use_id=call.id,
                        text=bound.text,
                        is_error=True,
                    )
                if (
                    self.adoption.replaying
                    and self._redoes_on_replay(call.name)
                    and await self._pending_member_guidance()
                ):
                    outcome = "guidance_preempted"
                    log(
                        "turn.dispatch_preempted_by_guidance",
                        turn_id=str(self.turn.id),
                        tool=call.name,
                    )
                    return DispatchResult(
                        tool_use_id=call.id,
                        text=GUIDANCE_PREEMPTED_NOTICE,
                        is_error=True,
                    )
                context = bound.context
                await self._publish(tool_activity(call))
                try:
                    tool = self.tools.get(call.name)
                    args = tool.input_model.model_validate(call.input)
                except Exception as error:
                    outcome, error_class = "invalid_call", type(error).__name__
                    return DispatchResult(
                        tool_use_id=call.id,
                        text=f"{type(error).__name__}: {error}",
                        is_error=True,
                        activity=True,
                    )
                pre = await self.hooks.fire(
                    "pre_tool_use",
                    PreToolUse(tool_name=call.name, tool_input=args),
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
                        tool_use_id=call.id, text=pre.denied, is_error=True, activity=True
                    )
                args = pre.tool_input if pre.tool_input is not None else args
                images: list[ImageBlock] = []
                key = f"{self.turn.id}/{call.name}/{call.id}" if tool.side_effecting else None
                try:
                    handler_context = replace(
                        context, ext=self.tool_ext.get(call.name), idempotency_key=key
                    )
                    result = await tool.handler(handler_context, args)
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
                except Exception as error:
                    content, is_error = f"{type(error).__name__}: {error}", True
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
                    content = wall(tool.name, content)
                if is_error:
                    await self.hooks.fire(
                        "post_tool_use_failure",
                        PostToolUseFailure(tool_name=call.name, tool_input=args, output=content),
                        self.turn,
                        self.agent,
                        context.speaker_member_id,
                    )
                else:
                    post = await self.hooks.fire(
                        "post_tool_use",
                        PostToolUse(tool_name=call.name, tool_input=args, output=content),
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
                )
            except (Exception, asyncio.CancelledError) as error:
                outcome, error_class = "step_failed", type(error).__name__
                raise
            finally:
                _meter_dispatch(self.tools, call, started, outcome, error_class, self.profile)

    def _redoes_on_replay(self, name: str) -> bool:
        """Whether re-executing this call redoes its work, making it preemptible. A side-effecting
        tool's re-execution dedups through the call's idempotency key — a spawn reattaches to its
        running child, a connector send dedups at the provider — and must keep that: preempting it
        strands the keyed work, and a re-issued call would duplicate it under a fresh call id. An
        unknown name never dispatches, so there is nothing to preempt."""
        try:
            return not self.tools.get(name).side_effecting
        except KeyError:
            return False

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
        await self._publish(Terminal(frame=frame))
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

    async def _record_workspace_changes(self) -> None:
        """Refresh what the portal's Changes reads, once the turn has nothing left the member is
        waiting on — the terminal frame is published and the transcript is durable, so a scan of the
        sandbox delays neither. It answers for the conversation that owns the sandbox rather than
        this turn's, since a subagent shares its parent's workspace and a member asks the parent
        what changed."""
        await WorkspaceChangeRecorder(
            sandbox=self.sandbox,
            workspace_id=self.turn.workspace_id,
            conversation_id=self.turn.sandbox_conversation_id or self.turn.conversation_id,
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
                    return None, False
            await record_turn_usage(
                connection,
                self.turn.workspace_id,
                self.turn.id,
                self.agent.model,
                usage,
                self.attempt,
                pricing=self.pricing,
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
                question=question,
                credential_request=credential_request,
                connect_request=connect_request,
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
        await self._repair().persist_transcript(messages, answer, system, injected)

    async def _persist_inbound(
        self,
        arrivals: tuple[Message, ...] = (),
        founding_denial: str | None = None,
    ) -> None:
        await self._repair().persist_inbound(arrivals, founding_denial)
