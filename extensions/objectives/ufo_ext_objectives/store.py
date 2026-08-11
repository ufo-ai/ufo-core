"""What an objective is, what has been attempted against it, and which of its conditions hold.

A turn woken by a heartbeat or a subagent hand-back starts with fresh context: it reads the durable
record, never the ending turn's working memory. So the record has to carry the decisions, not a
summary of them — a step's state is derived from its events, and an event is never updated or
deleted, so a plan revision cannot erase what happened.

A step closes when the conditions it declared at plan time hold, and **the extension decides that,
not the worker**. Both closure forms a person or an agent performs were measured against
HANDBOOK.md and both scored below doing nothing (RFC 0025): an author underspecifies the condition
in exactly the way that makes it pass, and a second reader of the same kind misses what the first
missed. What survives is a check that needs no attention — a path compared literally. `accepts` is
therefore data, evaluated here, and frozen once a step has been attempted: time is what separates
the interests, since a condition fixed before the work was known to be hard cannot be softened
after."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncConnection

_metadata = sa.MetaData()

objective = sa.Table(
    "objective",
    _metadata,
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("conversation_id", sa.Uuid, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("directive", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("workspace_id", "conversation_id", "name"),
)

objective_step = sa.Table(
    "objective_step",
    _metadata,
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("objective_id", sa.Uuid, nullable=False),
    sa.Column("position", sa.Integer, nullable=False),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("accepts", sa.JSON, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.UniqueConstraint("objective_id", "title"),
)

objective_event = sa.Table(
    "objective_event",
    _metadata,
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("step_id", sa.Uuid, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("actor_turn_id", sa.Uuid, nullable=False),
    sa.Column("evidence", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

objective_check = sa.Table(
    "objective_check",
    _metadata,
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("step_id", sa.Uuid, nullable=False),
    sa.Column("verdicts", sa.JSON, nullable=False),
    sa.Column("actor_turn_id", sa.Uuid, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)

DID = "did"
BLOCKED = "blocked"
PENDING_STATE = "pending"
ATTEMPTED_STATE = "attempted"
DONE_STATE = "done"
BLOCKED_STATE = "blocked"
UNMET_STATE = "unmet"
EVIDENCE_MAX_CHARS = 2_000
CONDITIONS_MAX = 8


class FileExists(BaseModel):
    """The workspace path that must be there. The path is the check: a step that names the file but
    not the folder it belongs in fails here rather than passing against the wrong place."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: str = Field(default="file_exists", pattern="^file_exists$")
    path: str = Field(min_length=1, max_length=512)


class FileContains(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: str = Field(default="file_contains", pattern="^file_contains$")
    path: str = Field(min_length=1, max_length=512)
    text: str = Field(min_length=1, max_length=512)


class CommandSucceeds(BaseModel):
    """A command whose exit status decides. The check itself carries the different interest."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: str = Field(default="command_succeeds", pattern="^command_succeeds$")
    command: str = Field(min_length=1, max_length=1_000)


type Condition = FileExists | FileContains | CommandSucceeds


class StepPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    title: str = Field(min_length=1, max_length=200)
    accepts: tuple[Condition, ...] = Field(default=(), max_length=CONDITIONS_MAX)


@dataclass(frozen=True)
class ConditionVerdict:
    condition: Condition
    holds: bool
    detail: str


@dataclass(frozen=True)
class StepEvent:
    kind: str
    actor_turn_id: UUID
    evidence: str
    created_at: datetime


@dataclass(frozen=True)
class StepView:
    """`verdicts` is what evaluating this step's conditions found, and `checked_at` is when. A turn
    holding a sandbox evaluates live and passes fresh ones; a turn that is not — the hook that opens
    every turn with the frontier — reads back the last check the extension wrote. Both are the
    extension's own observation, never the worker's claim, which is the whole guarantee."""

    id: UUID
    title: str
    accepts: tuple[Condition, ...]
    events: tuple[StepEvent, ...]
    verdicts: tuple[ConditionVerdict, ...]
    checked_at: datetime | None = None

    @property
    def attempted(self) -> bool:
        return any(event.kind == DID for event in self.events)

    @property
    def open_block(self) -> StepEvent | None:
        """The block still standing, if any: the question already put to a member. A blocked step
        re-raised on every wake is how a heartbeat objective fills a thread overnight with the same
        question, so the record has to say it was already asked."""
        latest = self.events[-1] if self.events else None
        return latest if latest is not None and latest.kind == BLOCKED else None

    @property
    def state(self) -> str:
        """`attempted` is the state that keeps this honest. A step with conditions nobody has run
        is not done — and it is not failing either, so it cannot be called unmet. Only a view that
        actually evaluated the conditions carries verdicts, so anything else reports the claim
        without the check. Reading `all(())` as True is how a step closes on a claim alone."""
        if self.events and self.events[-1].kind == BLOCKED:
            return BLOCKED_STATE
        if not self.attempted:
            return PENDING_STATE
        if not self.accepts:
            return DONE_STATE
        if not self.verdicts:
            return ATTEMPTED_STATE
        if len(self.verdicts) != len(self.accepts):
            return ATTEMPTED_STATE
        return DONE_STATE if all(verdict.holds for verdict in self.verdicts) else UNMET_STATE


@dataclass(frozen=True)
class ObjectiveView:
    """`attempts` against `confirmed` is the reading a worker has no other way to take: attempts
    climbing while confirmed holds flat is an objective buying rounds rather than closing them.
    Reported, never judged — whether to split, escalate, or iterate once more is a judgment."""

    id: UUID
    name: str
    directive: str
    steps: tuple[StepView, ...]

    @property
    def attempts(self) -> int:
        return sum(1 for step in self.steps for event in step.events if event.kind == DID)

    @property
    def confirmed(self) -> int:
        return sum(1 for step in self.steps if step.state == DONE_STATE)

    @property
    def frontier(self) -> tuple[StepView, ...]:
        return tuple(step for step in self.steps if step.state != DONE_STATE)


def condition_summary(condition: Condition) -> str:
    match condition:
        case FileExists():
            return f"{condition.path} exists"
        case FileContains():
            return f"{condition.path} contains {condition.text!r}"
        case CommandSucceeds():
            return f"`{condition.command}` succeeds"


@dataclass(frozen=True)
class Objectives:
    """Read and write one workspace's objectives over the extension's own tables."""

    connection: AsyncConnection
    workspace_id: UUID

    async def named(self, conversation_id: UUID, name: str) -> ObjectiveView | None:
        """An objective belongs to the conversation that planned it. A subagent runs on its own
        conversation and is handed the same tools, so a name scoped to the workspace would let a
        worker reusing its parent's name adopt the parent's objective and delete the steps it had
        not started."""
        row = (
            await self.connection.execute(
                sa.select(objective).where(
                    objective.c.workspace_id == self.workspace_id,
                    objective.c.conversation_id == conversation_id,
                    objective.c.name == name,
                )
            )
        ).one_or_none()
        return None if row is None else await self._view(row)

    async def on_conversation(self, conversation_id: UUID) -> ObjectiveView | None:
        row = (
            await self.connection.execute(
                sa.select(objective)
                .where(
                    objective.c.workspace_id == self.workspace_id,
                    objective.c.conversation_id == conversation_id,
                )
                .order_by(objective.c.created_at.desc())
                .limit(1)
            )
        ).one_or_none()
        return None if row is None else await self._view(row)

    async def plan(
        self, conversation_id: UUID, name: str, directive: str, steps: tuple[StepPlan, ...]
    ) -> ObjectiveView:
        """Create the objective or revise its plan. An `accepts` already attempted is kept as
        written — a condition the worker may rewrite once the work turns out hard is the form
        measured worse than no check at all."""
        existing = await self.named(conversation_id, name)
        if existing is None:
            objective_id = uuid4()
            await self.connection.execute(
                sa.insert(objective).values(
                    workspace_id=self.workspace_id,
                    id=objective_id,
                    conversation_id=conversation_id,
                    name=name,
                    directive=directive,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            frozen: dict[str, tuple[Condition, ...]] = {}
        else:
            objective_id = existing.id
            await self.connection.execute(
                sa.update(objective)
                .values(directive=directive, updated_at=sa.func.now())
                .where(objective.c.id == objective_id)
            )
            frozen = {step.title: step.accepts for step in existing.steps if step.attempted}
            keep = {step.title for step in existing.steps if step.events}
            await self.connection.execute(
                sa.delete(objective_step).where(
                    objective_step.c.objective_id == objective_id,
                    objective_step.c.title.not_in(keep) if keep else sa.true(),
                )
            )
        for position, step in enumerate(steps):
            accepts = frozen.get(step.title, step.accepts)
            payload = [condition.model_dump() for condition in accepts]
            updated = await self.connection.execute(
                sa.update(objective_step)
                .values(position=position, accepts=payload)
                .where(
                    objective_step.c.objective_id == objective_id,
                    objective_step.c.title == step.title,
                )
            )
            if updated.rowcount == 0:
                await self.connection.execute(
                    sa.insert(objective_step).values(
                        workspace_id=self.workspace_id,
                        id=uuid4(),
                        objective_id=objective_id,
                        position=position,
                        title=step.title,
                        accepts=payload,
                        created_at=sa.func.now(),
                    )
                )
        view = await self.named(conversation_id, name)
        if view is None:
            raise RuntimeError(f"objective {name!r} vanished during its own plan")
        return view

    async def record(self, step: StepView, kind: str, actor_turn_id: UUID, evidence: str) -> bool:
        """Append the event, or decline to. A block repeating the one already standing is not
        recorded: it is the same question, and appending it again restates an open ask as though it
        were new, which is how a heartbeat objective fills a thread overnight. Returns whether
        anything was written."""
        bounded = evidence[:EVIDENCE_MAX_CHARS]
        standing = step.open_block
        if kind == BLOCKED and standing is not None and standing.evidence == bounded:
            return False
        await self.connection.execute(
            sa.insert(objective_event).values(
                workspace_id=self.workspace_id,
                id=uuid4(),
                step_id=step.id,
                kind=kind,
                actor_turn_id=actor_turn_id,
                evidence=bounded,
                created_at=sa.func.now(),
            )
        )
        return True

    async def checked(
        self, step: StepView, verdicts: tuple[ConditionVerdict, ...], actor_turn_id: UUID
    ) -> None:
        """Record what evaluating this step's conditions found. Only the extension reaches this —
        no tool writes a verdict — so the record stays an observation rather than a claim. Append
        rather than update: a step that held yesterday and fails today is the reading the whole
        design exists to produce, and an overwritten row cannot show it. The latest wins on read.

        Without this a check is computed and thrown away, so the next turn's frontier reports every
        step carrying conditions as unfinished — and steps carrying conditions are exactly the ones
        the design is for."""
        await self.connection.execute(
            sa.insert(objective_check).values(
                workspace_id=self.workspace_id,
                id=uuid4(),
                step_id=step.id,
                verdicts=[
                    {
                        "condition": verdict.condition.model_dump(),
                        "holds": verdict.holds,
                        "detail": verdict.detail,
                    }
                    for verdict in verdicts
                ],
                actor_turn_id=actor_turn_id,
                created_at=sa.func.now(),
            )
        )

    async def _view(self, row: sa.Row[tuple[object, ...]]) -> ObjectiveView:
        step_rows = (
            await self.connection.execute(
                sa.select(objective_step)
                .where(objective_step.c.objective_id == row.id)
                .order_by(objective_step.c.position)
            )
        ).all()
        event_rows = (
            await self.connection.execute(
                sa.select(objective_event)
                .where(objective_event.c.step_id.in_([step.id for step in step_rows]))
                .order_by(objective_event.c.created_at)
            )
        ).all()
        check_rows = (
            await self.connection.execute(
                sa.select(objective_check)
                .where(objective_check.c.step_id.in_([step.id for step in step_rows]))
                .order_by(objective_check.c.created_at)
            )
        ).all()
        events: dict[UUID, list[StepEvent]] = {}
        for event in event_rows:
            events.setdefault(event.step_id, []).append(
                StepEvent(
                    kind=event.kind,
                    actor_turn_id=event.actor_turn_id,
                    evidence=event.evidence,
                    created_at=event.created_at,
                )
            )
        checks: dict[UUID, sa.Row[tuple[object, ...]]] = {}
        for check in check_rows:
            checks[check.step_id] = check
        return ObjectiveView(
            id=row.id,
            name=row.name,
            directive=row.directive,
            steps=tuple(
                StepView(
                    id=step.id,
                    title=step.title,
                    accepts=_conditions(step.accepts),
                    events=tuple(events.get(step.id, ())),
                    verdicts=_verdicts(checks[step.id].verdicts) if step.id in checks else (),
                    checked_at=checks[step.id].created_at if step.id in checks else None,
                )
                for step in step_rows
            ),
        )


def _conditions(payload: object) -> tuple[Condition, ...]:
    if not isinstance(payload, list):
        return ()
    parsed: list[Condition] = []
    for item in payload:
        match item:
            case {"kind": "file_exists"}:
                parsed.append(FileExists.model_validate(item))
            case {"kind": "file_contains"}:
                parsed.append(FileContains.model_validate(item))
            case {"kind": "command_succeeds"}:
                parsed.append(CommandSucceeds.model_validate(item))
            case _:
                raise ValueError(f"unknown objective condition: {item!r}")
    return tuple(parsed)


def _verdicts(payload: object) -> tuple[ConditionVerdict, ...]:
    if not isinstance(payload, list):
        return ()
    return tuple(
        ConditionVerdict(
            condition=_conditions([item["condition"]])[0],
            holds=bool(item["holds"]),
            detail=str(item["detail"]),
        )
        for item in payload
        if isinstance(item, dict)
    )
