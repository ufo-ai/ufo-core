"""Cancel one turn, durably — the single cancel primitive every cancel path shares.

A turn's subagents run as independent DBOS workflows keyed by their own turn ids, so cancelling a
turn's workflow leaves its descendants running unless something cancels them too. This primitive
cancels exactly one turn; the cascade to descendants is the cancel reconciler's job. It lives
outside any role package (surfaces, loop, jobs) because every cancel initiator reaches it — the eval
driver, the `cancel_spawn` tool, and the reconciler — and it talks to the durable turn row and
the DBOS store directly, the shared substrate both roles hold, never across a role's queue/blob/hub
seam."""

from uuid import UUID

import sqlalchemy as sa
from dbos import DBOSClient

from ufo.db import workspace_tx
from ufo.o11y import emit_metric, turn_profile
from ufo.schema import tables
from ufo.schema.records import CANCELLED, NON_TERMINAL_STATUSES, TerminalFrame

CANCELLED_FRAME = TerminalFrame(status=CANCELLED)


async def cancel_one_turn(client: DBOSClient, turn_id: UUID) -> bool:
    """Cancel a single turn: cancel its durable workflow, then commit its cancelled terminal.
    Returns True iff this call transitioned the turn to cancelled.

    Cancel-before-commit is the invariant every cancel path upholds through this one function: the
    row is written terminal only after the workflow cancel is durable, so a `cancelled` row always
    implies its workflow was already cancelled. A crash or fault between the two leaves the row
    non-terminal — where the reconciler and DBOS recovery both still reach it — never a cancelled
    row whose workflow was never told to stop. A turn that already reached its own terminal is left
    untouched (returns False), so a cancel racing a turn's own `done` commit never disturbs it.
    `cancel_workflow_async` is a conditional update that silently no-ops on a workflow that is
    absent or already complete, so a queued turn never enqueued needs no special case — the cancel
    is a no-op and the row is committed all the same. This is where a cancelled turn is counted: the
    turn's own execution never writes the row, and a turn cancelled before one started has no
    execution at all."""
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.turn.c.status,
                    tables.turn.c.subagent_profile,
                    tables.turn.c.parent_turn_id,
                ).where(tables.turn.c.id == turn_id)
            )
        ).one_or_none()
    if row is None or row.status not in NON_TERMINAL_STATUSES:
        return False
    await client.cancel_workflow_async(str(turn_id))
    async with workspace_tx() as connection:
        result = await connection.execute(
            sa.update(tables.turn)
            .values(
                status=CANCELLED,
                terminal=CANCELLED_FRAME.model_dump(mode="json"),
                updated_at=sa.func.now(),
            )
            .where(
                tables.turn.c.id == turn_id,
                tables.turn.c.status.in_(NON_TERMINAL_STATUSES),
            )
        )
    if result.rowcount == 0:
        return False
    emit_metric(
        "turn_terminal_total",
        status=CANCELLED,
        error_class="",
        profile=turn_profile(row.subagent_profile, spawned=row.parent_turn_id is not None),
    )
    return True
