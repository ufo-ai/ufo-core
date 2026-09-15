"""The turn record: one turn as its stream told it — the steps in arrival order, the runs it
spawned, the spend so far, and how it ended. Every surface folds live frames into this shape and a
transcript read states it back, so what a member sees is a projection of one record rather than of
each surface's own bookkeeping. `ufo_testsupport.contract` renders these models as the portal's
TypeScript and a fixture holding every variant, so the two languages cannot drift."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from ufo.runtime.hub import SourceRef
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
    """One tool call, as its member-facing label and the places it read. A step whose label never
    landed carries None."""

    kind: Literal["tool"] = "tool"
    label: str | None
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
