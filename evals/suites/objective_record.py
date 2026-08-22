"""What an objective's own rows say, for graders that need to tell noticing from not looking.

An arc that grades long-horizon work off the workspace alone cannot distinguish an agent that
caught a regression and stopped from one that shipped and never checked: both leave the same files
behind and both get woken the same number of times. The difference is in the record — a `did`
followed by a `blocked`, on a step whose condition names the thing that broke.

Graders run inside the eval's workspace scope, so this reads the extension's tables directly rather
than routing through a harness field no other suite wants."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from ufo_ext_objectives.store import objective, objective_event, objective_step

from ufo.db import workspace_tx


@dataclass(frozen=True)
class RecordedStep:
    title: str
    conditions: tuple[str, ...]
    kinds: tuple[str, ...]
    independent: bool = False

    @property
    def attempted(self) -> bool:
        return "did" in self.kinds

    @property
    def blocked(self) -> bool:
        return bool(self.kinds) and self.kinds[-1] == "blocked"

    def checks(self, fragment: str) -> bool:
        """Whether any condition names `fragment` — the way a grader asks whether the step was
        gated on the thing that actually mattered rather than on a proxy for it."""
        return any(fragment in condition for condition in self.conditions)


@dataclass(frozen=True)
class RecordedObjective:
    name: str
    directive: str
    steps: tuple[RecordedStep, ...]

    @property
    def raised_a_block(self) -> bool:
        return any(step.blocked for step in self.steps)


async def recorded_objective(conversation_id: UUID) -> RecordedObjective | None:
    """The most recent objective on this conversation, or None if the turn never recorded one."""
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(objective.c.id, objective.c.name, objective.c.directive)
                .where(objective.c.conversation_id == conversation_id)
                .order_by(objective.c.created_at.desc())
                .limit(1)
            )
        ).one_or_none()
        if row is None:
            return None
        step_rows = (
            await connection.execute(
                sa.select(
                    objective_step.c.id,
                    objective_step.c.title,
                    objective_step.c.accepts,
                    objective_step.c.independent,
                )
                .where(objective_step.c.objective_id == row.id)
                .order_by(objective_step.c.position)
            )
        ).all()
        event_rows = (
            await connection.execute(
                sa.select(objective_event.c.step_id, objective_event.c.kind)
                .where(objective_event.c.step_id.in_([step.id for step in step_rows]))
                .order_by(objective_event.c.created_at)
            )
        ).all()
    kinds: dict[UUID, list[str]] = {}
    for event in event_rows:
        kinds.setdefault(event.step_id, []).append(event.kind)
    return RecordedObjective(
        name=row.name,
        directive=row.directive,
        steps=tuple(
            RecordedStep(
                title=step.title,
                conditions=tuple(
                    str(condition) for condition in (step.accepts if step.accepts else ())
                ),
                kinds=tuple(kinds.get(step.id, ())),
                independent=bool(step.independent),
            )
            for step in step_rows
        ),
    )
