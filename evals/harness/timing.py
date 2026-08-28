"""Where a case's wall-clock went.

The engine already records durable per-step timing: every model round runs as one DBOS step and
every tool call as another, each stamped with the epoch millisecond it started and completed. This
reads that record for the evaluated turn and each delegated child and turns it into the shape a
reader can act on — how long the turn took, how much of it was the model thinking versus tools
working, and which individual steps were the expensive ones.

A delegated case's cost is mostly its child's, so every turn is reported separately rather than
summed: a case that took twelve minutes because one coding child ran eleven of them reads
differently from one that spent them in the parent.

Output-token counts cover completed model rounds. `None` means the durable record has no completed
round or lacks usage for at least one round. A done turn excludes its final round from intermediate
output; a failed or cancelled turn has no delivered final round, so all its output is intermediate.
Compaction is a separate step and is not included.

Each completed model or tool step also carries the message that its durable output can rebuild. A
timeout can therefore keep the work completed before cancellation and use the same call ids to
join tool names to timing. A step whose id resolves to no call is named generically rather than
mislabeled."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ufo.schema.records import TurnStatus
from ufo.sdk.models import Message, ToolUseBlock

MODEL_ROUND_STEP = "_stream_once"
TOOL_CALL_STEP = "_dispatch_step"
SLOWEST_STEPS = 12
UNNAMED_TOOL = "tool"

type StepKind = Literal["model_round", "tool_call", "other"]
type TurnRole = Literal["evaluated", "child"]


class TurnStep(BaseModel):
    """One durable engine step: a model round, a tool call, or the engine's own bookkeeping."""

    model_config = ConfigDict(frozen=True)

    function_name: str
    started_at_epoch_ms: int | None = None
    completed_at_epoch_ms: int | None = None
    call_id: str = ""
    call_ids: tuple[str, ...] = ()
    tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_micro_usd: int | None = Field(default=None, ge=0)
    messages: tuple[Message, ...] = ()


class StepTiming(BaseModel):
    model_config = ConfigDict(frozen=True)

    turn_id: UUID | None = None
    number: int = Field(default=0, ge=0)
    kind: StepKind
    name: str
    duration_ms: int = Field(ge=0)
    tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost_micro_usd: int | None = Field(default=None, ge=0)
    message_index: int | None = Field(default=None, ge=1)
    call_id: str = ""


class TurnTiming(BaseModel):
    """One turn's own timing: its span, how that span divided between model rounds and tool calls,
    and what it spent. A round dispatches its tool calls concurrently, so each bucket is the wall
    those steps occupied — overlapping intervals merged, never summed — and `unaccounted_ms` is the
    span the buckets do not cover: engine overhead, queue wait, and the gaps between steps.

    `output_tokens` sums completed model rounds. For a done turn, `intermediate_output_tokens`
    excludes the final completed round. For a failed or cancelled turn, it includes every round.
    Both are `None` when no model round completed or any round lacks usage."""

    model_config = ConfigDict(frozen=True)

    turn_id: UUID
    role: TurnRole
    status: TurnStatus = "done"
    span_ms: int = Field(ge=0)
    model_round_ms: int = Field(ge=0)
    tool_call_ms: int = Field(ge=0)
    unaccounted_ms: int = Field(ge=0)
    rounds: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    tokens: int = Field(ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    intermediate_output_tokens: int | None = Field(default=None, ge=0)
    cost_micro_usd: int = Field(ge=0)
    steps: tuple[StepTiming, ...] = ()


class CaseTiming(BaseModel):
    """A case's latency record: the wall-clock the harness measured around the turn, each turn's own
    timing, and the slowest individual steps across all of them — the first place to look for a
    trajectory that took longer than the work it did."""

    model_config = ConfigDict(frozen=True)

    wall_ms: int = Field(ge=0)
    turns: tuple[TurnTiming, ...] = ()
    slowest: tuple[StepTiming, ...] = ()
    error: str = ""


class TurnSteps(Protocol):
    async def steps(self, turn_id: UUID) -> tuple[TurnStep, ...]: ...


def turn_timing(
    turn_id: UUID,
    role: TurnRole,
    steps: tuple[TurnStep, ...],
    tool_names: Mapping[str, str],
    tokens: int = 0,
    cost_micro_usd: int = 0,
    messages: tuple[Message, ...] = (),
    status: TurnStatus = "done",
) -> TurnTiming:
    """One turn's timing from its durable steps, with each tool call named by the transcript entry
    its recorded call id belongs to."""
    timed = tuple(step for step in steps if step.started_at_epoch_ms is not None)
    dispatches = tuple(step for step in steps if step.function_name.endswith(TOOL_CALL_STEP))
    entries: list[StepTiming] = []
    assistant_messages = tuple(
        index for index, message in enumerate(messages, 1) if message.role == "assistant"
    )
    tool_messages = {
        block.id: index
        for index, message in enumerate(messages, 1)
        if not isinstance(message.content, str)
        for block in message.content
        if isinstance(block, ToolUseBlock)
    }
    model_steps = tuple(
        step
        for step in steps
        if step.function_name.endswith(MODEL_ROUND_STEP)
        and step.started_at_epoch_ms is not None
        and step.completed_at_epoch_ms is not None
    )
    aligned_model_messages = (
        assistant_messages[-len(model_steps) :]
        if model_steps and len(assistant_messages) >= len(model_steps)
        else ()
    )
    model_index = 0
    for number, step in enumerate(steps, 1):
        if step.started_at_epoch_ms is None or step.completed_at_epoch_ms is None:
            continue
        duration = max(step.completed_at_epoch_ms - step.started_at_epoch_ms, 0)
        if step.function_name.endswith(TOOL_CALL_STEP):
            entries.append(
                StepTiming(
                    turn_id=turn_id,
                    number=number,
                    kind="tool_call",
                    name=tool_names.get(step.call_id, UNNAMED_TOOL),
                    duration_ms=duration,
                    tokens=step.tokens,
                    output_tokens=step.output_tokens,
                    cost_micro_usd=step.cost_micro_usd,
                    message_index=tool_messages.get(step.call_id),
                    call_id=step.call_id,
                )
            )
        elif step.function_name.endswith(MODEL_ROUND_STEP):
            exact_messages = {
                tool_messages[call_id] for call_id in step.call_ids if call_id in tool_messages
            }
            message_index = (
                exact_messages.pop()
                if len(exact_messages) == 1
                else aligned_model_messages[model_index]
                if aligned_model_messages
                else None
            )
            entries.append(
                StepTiming(
                    turn_id=turn_id,
                    number=number,
                    kind="model_round",
                    name="model round",
                    duration_ms=duration,
                    tokens=step.tokens,
                    output_tokens=step.output_tokens,
                    cost_micro_usd=step.cost_micro_usd,
                    message_index=message_index,
                )
            )
            model_index += 1
        else:
            entries.append(
                StepTiming(
                    turn_id=turn_id,
                    number=number,
                    kind="other",
                    name=step.function_name.rsplit(".", 1)[-1],
                    duration_ms=duration,
                    tokens=step.tokens,
                    output_tokens=step.output_tokens,
                    cost_micro_usd=step.cost_micro_usd,
                )
            )
    starts = tuple(
        step.started_at_epoch_ms for step in timed if step.started_at_epoch_ms is not None
    )
    ends = tuple(
        step.completed_at_epoch_ms for step in timed if step.completed_at_epoch_ms is not None
    )
    span = max(ends) - min(starts) if starts and ends else 0
    model_ms = _occupied(timed, MODEL_ROUND_STEP)
    tool_ms = _occupied(timed, TOOL_CALL_STEP)
    output_measured = bool(model_steps) and all(
        step.output_tokens is not None for step in model_steps
    )
    measured_output_tokens = (
        sum(step.output_tokens for step in model_steps if step.output_tokens is not None)
        if output_measured
        else None
    )
    measured_intermediate_output_tokens = (
        sum(
            step.output_tokens
            for step in (model_steps[:-1] if status == "done" else model_steps)
            if step.output_tokens is not None
        )
        if output_measured
        else None
    )
    return TurnTiming(
        turn_id=turn_id,
        role=role,
        status=status,
        span_ms=max(span, 0),
        model_round_ms=model_ms,
        tool_call_ms=tool_ms,
        unaccounted_ms=max(span - model_ms - tool_ms, 0),
        rounds=sum(1 for entry in entries if entry.kind == "model_round"),
        tool_calls=len(dispatches),
        tokens=tokens,
        output_tokens=measured_output_tokens,
        intermediate_output_tokens=measured_intermediate_output_tokens,
        cost_micro_usd=cost_micro_usd,
        steps=tuple(entries),
    )


def _occupied(steps: tuple[TurnStep, ...], step_name: str) -> int:
    """The wall these steps occupied, merging overlaps. A round dispatches its tool calls
    concurrently, so three sixty-second reads inside one minute cost that minute, not three."""
    spans = sorted(
        (step.started_at_epoch_ms, step.completed_at_epoch_ms)
        for step in steps
        if step.function_name.endswith(step_name)
        and step.started_at_epoch_ms is not None
        and step.completed_at_epoch_ms is not None
    )
    occupied = 0
    open_start: int | None = None
    open_end = 0
    for start, end in spans:
        if open_start is None:
            open_start, open_end = start, end
            continue
        if start <= open_end:
            open_end = max(open_end, end)
            continue
        occupied += open_end - open_start
        open_start, open_end = start, end
    if open_start is not None:
        occupied += open_end - open_start
    return max(occupied, 0)


def case_timing(wall_ms: int, turns: tuple[TurnTiming, ...], error: str = "") -> CaseTiming:
    """The case's record, with the slowest steps across every turn lifted to the top."""
    slowest = sorted(
        (step for turn in turns for step in turn.steps),
        key=lambda step: step.duration_ms,
        reverse=True,
    )
    return CaseTiming(
        wall_ms=max(wall_ms, 0),
        turns=turns,
        slowest=tuple(slowest[:SLOWEST_STEPS]),
        error=error,
    )
