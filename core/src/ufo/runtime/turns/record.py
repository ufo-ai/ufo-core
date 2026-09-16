"""The turn record: one turn as its stream told it — the steps in arrival order, the runs it
spawned, the spend so far, and how it ended. Every surface folds live frames into this shape and a
transcript read states it back, so what a member sees is a projection of one record rather than of
each surface's own bookkeeping. `ufo_testsupport.contract` renders these models as the portal's
TypeScript and a fixture holding every variant, so the two languages cannot drift."""

from datetime import datetime
from typing import Annotated, Literal, cast
from uuid import UUID

from pydantic import BaseModel, Field

from ufo.harness.models.interface import TextDelta
from ufo.harness.o11y import log_error
from ufo.runtime.hub import (
    Absorbed,
    Activity,
    ArtifactsChanged,
    CostTick,
    LiveFrame,
    Parked,
    Reply,
    Resumed,
    SourceRef,
    Sources,
    SubagentActivity,
    Terminal,
)
from ufo.schema.records import TerminalFrame


class ActivityEvent(BaseModel):
    """One thing a settled turn did, as a transcript states it: a tool step's label, or the words
    written between steps."""

    kind: Literal["activity", "note"]
    text: str


class SubagentRun(BaseModel):
    """One spawned run as a conversation shows it: the target it ran (`agent:<name>` for an agent
    child, the bare profile otherwise), the display name its spawn gave it, the conversation that
    holds its record, its work, its answer, the runs it spawned in turn, and whether it is still
    going. `turn_id`, `parent_turn_id` and `current` are known only while the run is watched
    live."""

    profile: str
    name: str = ""
    conversation_id: UUID
    events: tuple[ActivityEvent, ...]
    output: str
    subagents: tuple["SubagentRun", ...]
    running: bool
    turn_id: UUID | None = None
    parent_turn_id: UUID | None = None
    current: str | None = None


class TextStep(BaseModel):
    """Words the turn wrote. `open` while the stream may still add to them."""

    kind: Literal["text"] = "text"
    text: str
    open: bool


class ToolStep(BaseModel):
    """One tool call, as its member-facing label, the call it labels, and the places it read. A
    step whose label never landed carries None; one that sources opened before any label names no
    call."""

    kind: Literal["tool"] = "tool"
    label: str | None
    call_id: str
    sources: tuple[SourceRef, ...]
    open: bool


class ReplyStep(BaseModel):
    """A span the turn delivered to the member while it ran."""

    kind: Literal["reply"] = "reply"
    id: UUID
    text: str


class CommentStep(BaseModel):
    """The notice that a member commented from another surface."""

    kind: Literal["comment"] = "comment"
    id: UUID
    text: str


class DrainStep(BaseModel):
    """The round boundary at which the turn took up messages sent while it ran."""

    kind: Literal["drain"] = "drain"
    arrivals: tuple[UUID, ...]


class ResumedStep(BaseModel):
    """The moment another execution picked the turn back up after its own died."""

    kind: Literal["resumed"] = "resumed"
    attempt: str


Step = Annotated[
    TextStep | ToolStep | ReplyStep | CommentStep | DrainStep | ResumedStep,
    Field(discriminator="kind"),
]


class TerminalEnd(BaseModel):
    kind: Literal["terminal"] = "terminal"
    frame: TerminalFrame
    at: datetime


class ParkedEnd(BaseModel):
    kind: Literal["parked"] = "parked"
    message: str


class LostEnd(BaseModel):
    """The stream dropped for good before a verdict; the server's turn outlives the drop."""

    kind: Literal["lost"] = "lost"


TurnEnd = Annotated[TerminalEnd | ParkedEnd | LostEnd, Field(discriminator="kind")]


class Meter(BaseModel):
    tokens: int
    cost_micro_usd: int


class TurnRecord(BaseModel):
    """`id` is None for a turn a send has posted but not yet heard back about."""

    id: UUID | None
    steps: tuple[Step, ...]
    runs: tuple[SubagentRun, ...]
    meter: Meter | None
    end: TurnEnd | None


def consulted(held: tuple[SourceRef, ...], items: tuple[SourceRef, ...]) -> tuple[SourceRef, ...]:
    """What a step has read, each place once."""
    seen = {source.url or source.ref for source in held}
    fresh: list[SourceRef] = []
    for source in items:
        key = source.url or source.ref
        if key in seen:
            continue
        seen.add(key)
        fresh.append(source)
    return (*held, *fresh) if fresh else held


def _closed(steps: tuple[Step, ...]) -> tuple[Step, ...]:
    return tuple(
        step.model_copy(update={"open": False})
        if isinstance(step, TextStep | ToolStep) and step.open
        else step
        for step in steps
    )


def _opening(record: TurnRecord, step: Step) -> TurnRecord:
    return record.model_copy(update={"steps": (*_closed(record.steps), step)})


def _with_step(record: TurnRecord, index: int, step: Step) -> TurnRecord:
    steps = list(record.steps)
    steps[index] = step
    return record.model_copy(update={"steps": tuple(steps)})


def _open_text(steps: tuple[Step, ...]) -> int:
    """The text step the next words extend: the newest step once the replies delivered beside it
    are looked past, when that step is text still open."""
    for index in range(len(steps) - 1, -1, -1):
        step = steps[index]
        if isinstance(step, ReplyStep | CommentStep):
            continue
        return index if isinstance(step, TextStep) and step.open else -1
    return -1


def _holds_run(runs: tuple[SubagentRun, ...], turn_id: UUID) -> bool:
    return any(run.turn_id == turn_id or _holds_run(run.subagents, turn_id) for run in runs)


def _advance_run(run: SubagentRun, frame: SubagentActivity) -> SubagentRun:
    update: dict[str, object] = {}
    if frame.activity:
        update["events"] = (*run.events, ActivityEvent(kind="activity", text=frame.activity))
        update["current"] = frame.activity
    if frame.status:
        update["running"] = False
        update["current"] = None
    return run.model_copy(update=update)


def apply_run_frame(
    runs: tuple[SubagentRun, ...], frame: SubagentActivity, owner: UUID | None
) -> tuple[SubagentRun, ...]:
    """The runs with one frame applied: a frame naming a run the tree holds advances it where it
    stands, one naming a run nested under a held run descends to it, and one naming a new run
    appends it under the owner."""
    if frame.parent_turn_id != owner and _holds_run(runs, frame.parent_turn_id):
        return tuple(
            run.model_copy(update={"subagents": apply_run_frame(run.subagents, frame, run.turn_id)})
            if run.turn_id == frame.parent_turn_id
            or _holds_run(run.subagents, frame.parent_turn_id)
            else run
            for run in runs
        )
    if not any(run.turn_id == frame.turn_id for run in runs):
        fresh = SubagentRun(
            profile=frame.profile,
            name=frame.name,
            conversation_id=frame.conversation_id,
            events=(),
            output="",
            subagents=(),
            running=True,
            turn_id=frame.turn_id,
            parent_turn_id=frame.parent_turn_id,
        )
        return (*runs, _advance_run(fresh, frame))
    return tuple(_advance_run(run, frame) if run.turn_id == frame.turn_id else run for run in runs)


def current_step(record: TurnRecord) -> Step | None:
    """The step the turn is on: its newest, the replies delivered beside it looked past."""
    for step in reversed(record.steps):
        if not isinstance(step, ReplyStep | CommentStep):
            return step
    return None


def find_run(runs: tuple[SubagentRun, ...], turn_id: UUID) -> SubagentRun | None:
    """The run of that turn wherever it nests, or None."""
    for run in runs:
        if run.turn_id == turn_id:
            return run
        nested = find_run(run.subagents, turn_id)
        if nested is not None:
            return nested
    return None


def frame_event(frame: LiveFrame) -> str | None:
    """The event a frame crosses a wire under — the web's SSE event and the terminal's `frame`
    directive alike, the names the conformance fixture's rows carry — or None for a frame no wire
    carries as itself."""
    match frame:
        case TextDelta():
            return "message"
        case Activity():
            return "activity"
        case Sources():
            return "sources"
        case SubagentActivity():
            return "subagent_activity"
        case Reply(is_comment=True):
            return "comment"
        case Reply():
            return "reply"
        case Absorbed():
            return "absorbed"
        case Resumed():
            return "resumed"
        case CostTick():
            return "cost"
        case Terminal():
            return "terminal"
        case Parked():
            return "parked"
        case ArtifactsChanged():
            return None
    raise TypeError(f"unnamed live frame {type(frame).__name__}")


def frame_payload(frame: LiveFrame) -> str:
    """The JSON a frame crosses a wire as: a terminal frame unwrapped to the `TerminalFrame` the
    record's end holds, every other frame as itself."""
    if isinstance(frame, Terminal):
        return frame.frame.model_dump_json()
    return frame.model_dump_json()


def fold(record: TurnRecord, frame: LiveFrame, at: datetime) -> TurnRecord:
    """The record after one live frame — the reference every surface's fold answers to, replayed
    against the portal's by the conformance fixture `ufo_testsupport.contract` renders. `at` is the
    moment a terminal frame lands. A frame after the turn's end, a run naming a parent the record
    does not hold, and a reply with no words are faults: logged, and the record stands as it was."""
    if record.end is not None:
        log_error(
            "record.frame_after_end",
            turn=_named(record),
            frame=type(frame).__name__,
            end=record.end.kind,
        )
        return record
    match frame:
        case TextDelta(text=text):
            return _words(record, text)
        case Activity(text=""):
            return record
        case Activity(text=label, call_id=call_id):
            return _opening(record, ToolStep(label=label, call_id=call_id, sources=(), open=True))
        case Sources(items=items):
            return _sources(record, items)
        case SubagentActivity():
            return _run(record, frame)
        case Reply():
            return _reply(record, frame)
        case Absorbed(arrivals=arrivals):
            return _drain(record, arrivals)
        case Resumed(attempt=attempt):
            return _opening(record, ResumedStep(attempt=attempt))
        case CostTick(tokens=tokens, cost_micro_usd=cost):
            return record.model_copy(update={"meter": Meter(tokens=tokens, cost_micro_usd=cost)})
        case ArtifactsChanged():
            return record
        case Terminal(frame=terminal):
            return _ended(record, TerminalEnd(frame=terminal, at=at))
        case Parked(message=message):
            return _ended(record, ParkedEnd(message=message))
    raise TypeError(f"unfolded live frame {type(frame).__name__}")


def _named(record: TurnRecord) -> str | None:
    return None if record.id is None else str(record.id)


def _words(record: TurnRecord, text: str) -> TurnRecord:
    if not text:
        return record
    index = _open_text(record.steps)
    if index == -1:
        return _opening(record, TextStep(text=text, open=True))
    held = cast(TextStep, record.steps[index])
    return _with_step(record, index, held.model_copy(update={"text": held.text + text}))


def _sources(record: TurnRecord, items: tuple[SourceRef, ...]) -> TurnRecord:
    last = record.steps[-1] if record.steps else None
    if isinstance(last, ToolStep) and last.open:
        sources = consulted(last.sources, items)
        return _with_step(
            record, len(record.steps) - 1, last.model_copy(update={"sources": sources})
        )
    return _opening(
        record, ToolStep(label=None, call_id="", sources=consulted((), items), open=True)
    )


def _run(record: TurnRecord, frame: SubagentActivity) -> TurnRecord:
    if frame.parent_turn_id != record.id and not _holds_run(record.runs, frame.parent_turn_id):
        log_error(
            "record.run_orphan",
            turn=_named(record),
            run=str(frame.turn_id),
            parent=str(frame.parent_turn_id),
        )
    return record.model_copy(update={"runs": apply_run_frame(record.runs, frame, record.id)})


def _reply(record: TurnRecord, frame: Reply) -> TurnRecord:
    if not frame.text:
        log_error("record.reply_wordless", turn=_named(record), reply=str(frame.id))
        return record
    if any(
        isinstance(step, ReplyStep | CommentStep) and step.id == frame.id for step in record.steps
    ):
        return record
    step: Step = (
        CommentStep(id=frame.id, text=frame.text)
        if frame.is_comment
        else ReplyStep(id=frame.id, text=frame.text)
    )
    return record.model_copy(update={"steps": (*record.steps, step)})


def _drain(record: TurnRecord, arrivals: tuple[UUID, ...]) -> TurnRecord:
    drained = {
        arrival for step in record.steps if isinstance(step, DrainStep) for arrival in step.arrivals
    }
    fresh = tuple(arrival for arrival in arrivals if arrival not in drained)
    if not fresh:
        return record
    return _opening(record, DrainStep(arrivals=fresh))


def _ended(record: TurnRecord, end: TerminalEnd | ParkedEnd) -> TurnRecord:
    return record.model_copy(update={"steps": _closed(record.steps), "end": end})
