"""The backstop sweep that hands back what a child's own execution could not.

A delegated child delivers its result at the end of its execution, which reaches every way a turn
ends *inside* that execution and no way it ends outside one. `cancel_one_turn` commits the cancelled
terminal from another process: the child's body raises `CancelledError` and re-raises past the
delivery, so a parent that ended its turn expecting a wake is never woken and holds no tool to
discover it. A crash between the terminal commit and the arrival reads the same. This sweep reads
durable state rather than an execution's progress — every delegated child that is terminal and not
yet delivered, which the `result_delivery` column names and its partial index makes cheap to
find — and hands each back through the same `SubagentResult` the event path uses. That delivery is
keyed on the child turn, so both paths may fire and exactly one arrival lands.

This is also what lets `_deliver_to_parent` swallow its own fault rather than raise into the turn's
failure handling: a delivery that errors there costs the parent the time until the next tick, not
the result.

One pass takes a parent conversation's whole outstanding set together, so a fan-out that lands
between two ticks folds into one woken turn rather than one per child. A conversation another
delivery woke inside `RESULT_DELIVERY_COOLDOWN_SECONDS` is skipped, its children left outstanding
for the next tick: that is the bound on a woken turn spawning children that wake it again.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa

from ufo.db import owner_tx, workspace_tx
from ufo.ext.context import AgentArchived, TurnInvoker
from ufo.loop.subagents import SubagentRegistry, SubagentResult
from ufo.schema import tables
from ufo.schema.records import DELIVERY_DELIVERED, DELIVERY_PENDING, Turn
from ufo.workspace import ws_current

RESULT_DELIVERY_COOLDOWN_SECONDS = 60
RESULT_DELIVERY_BATCH_CHILDREN = 100


@dataclass(frozen=True)
class DeliverySweep:
    """The `result_delivery` job's body: find the delegated children whose result never reached the
    conversation that spawned them, and deliver them one parent conversation at a time. The jobs
    role names and schedules it through a Protocol rather than importing the loop.

    A parent whose app was archived takes nothing: its child stays pending and the pass moves on,
    so one retired app cannot hold up the results every other conversation is waiting for, and a
    restore delivers what was owed."""

    invoker_for: Callable[[UUID], TurnInvoker]
    registry: SubagentRegistry

    async def run(self) -> None:
        outstanding = await self._outstanding()
        if not outstanding:
            return
        woken = await self._woken_since(
            datetime.now(UTC) - timedelta(seconds=RESULT_DELIVERY_COOLDOWN_SECONDS),
            tuple(outstanding),
        )
        result = SubagentResult(
            invoker=self.invoker_for(ws_current().workspace_id), registry=self.registry
        )
        for conversation_id, children in outstanding.items():
            if conversation_id in woken:
                continue
            for child in children:
                try:
                    await result.deliver(child)
                except AgentArchived:
                    continue

    async def candidate_workspaces(self) -> tuple[UUID, ...]:
        parent = tables.turn.alias("candidate_parent_turn")
        async with owner_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.turn.c.workspace_id)
                    .select_from(
                        tables.turn.join(parent, parent.c.id == tables.turn.c.parent_turn_id).join(
                            tables.agent, tables.agent.c.id == parent.c.agent_id
                        )
                    )
                    .where(
                        tables.turn.c.result_delivery == DELIVERY_PENDING,
                        tables.turn.c.terminal.is_not(None),
                        tables.agent.c.archived_at.is_(None),
                    )
                    .distinct()
                )
            ).all()
        return tuple(row.workspace_id for row in rows)

    async def _outstanding(self) -> dict[UUID, list[Turn]]:
        """Every terminal, undelivered child in this workspace, grouped under the conversation its
        parent turn runs on. Ordered by that conversation so one pass drains a fan-out together,
        and by the child's own terminal within it so the parent reads them in the order they
        finished."""
        parent = tables.turn.alias("parent_turn")
        async with workspace_tx() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(
                            tables.turn,
                            parent.c.conversation_id.label("parent_conversation_id"),
                        )
                        .select_from(
                            tables.turn.join(
                                parent, parent.c.id == tables.turn.c.parent_turn_id
                            ).join(tables.agent, tables.agent.c.id == parent.c.agent_id)
                        )
                        .where(
                            tables.turn.c.result_delivery == DELIVERY_PENDING,
                            tables.turn.c.terminal.is_not(None),
                            tables.agent.c.archived_at.is_(None),
                        )
                        .order_by(parent.c.conversation_id, tables.turn.c.updated_at)
                        .limit(RESULT_DELIVERY_BATCH_CHILDREN)
                    )
                )
                .mappings()
                .all()
            )
        grouped: dict[UUID, list[Turn]] = {}
        for row in rows:
            fields = dict(row)
            conversation_id = fields.pop("parent_conversation_id")
            grouped.setdefault(conversation_id, []).append(Turn.model_validate(fields))
        return grouped

    async def _woken_since(
        self, cutoff: datetime, conversations: tuple[UUID, ...]
    ) -> frozenset[UUID]:
        """The conversations among these that a delivered child already woke since `cutoff`. The
        stamp on the child is the moment its arrival was posted, whichever path posted it, so a
        conversation the event path just woke defers here exactly as one this sweep woke does."""
        parent = tables.turn.alias("woken_parent_turn")
        delivered = tables.turn.alias("delivered_child_turn")
        async with workspace_tx() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(parent.c.conversation_id)
                        .select_from(
                            parent.join(delivered, delivered.c.parent_turn_id == parent.c.id)
                        )
                        .where(
                            parent.c.conversation_id.in_(conversations),
                            delivered.c.result_delivery == DELIVERY_DELIVERED,
                            delivered.c.updated_at > cutoff,
                        )
                        .distinct()
                    )
                )
                .scalars()
                .all()
            )
        return frozenset(rows)
