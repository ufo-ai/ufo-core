"""Cancel one turn, durably — the single cancel primitive every cancel path shares.

A turn's subagents run as independent DBOS workflows, so cancelling a turn's workflow leaves its
descendants running unless something cancels them too. This primitive
cancels exactly one turn; the cascade to descendants is the cancel reconciler's job. It lives
outside any role package (surfaces, loop, jobs) because every cancel initiator reaches it — the eval
driver, the `cancel_spawn` tool, and the reconciler — and it talks to the durable turn row and
the DBOS store directly, the shared substrate both roles hold, never across a role's queue/blob/hub
seam."""

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from dbos import DBOSClient

from ufo.db import workspace_tx
from ufo.harness.o11y import emit_metric, log, turn_profile, warn
from ufo.runtime.access.proxy_sessions import ProxySessions
from ufo.runtime.billing.accounting import TURN_LABEL
from ufo.runtime.object_name import ObjectRef
from ufo.runtime.turns.changes import turn_conversation_changed
from ufo.runtime.turns.dispatch import dispatch_next_turn
from ufo.schema import tables
from ufo.schema.records import CANCELLED, NON_TERMINAL_STATUSES, TerminalFrame


async def cancel_one_turn(
    client: DBOSClient, sessions: ProxySessions | None, turn_id: UUID
) -> TerminalFrame | None:
    """Cancel a single turn: cancel its live workflow, then commit its cancelled terminal.
    Returns the committed frame iff this call transitioned the turn to cancelled — carrying
    the objects the row already says the turn created, so a member who stopped a turn is
    still told what it made.

    Cancel-before-commit is the invariant every cancel path upholds through this one function: the
    row is written terminal only after the workflow cancel is durable, so a `cancelled` row always
    implies its workflow was already cancelled. A crash or fault between the two leaves the row
    non-terminal — where the reconciler and DBOS recovery both still reach it — never a cancelled
    row whose workflow was never told to stop. A turn that already reached its own terminal is left
    untouched (returns None), so a cancel racing a turn's own `done` commit never disturbs it.
    The row's `running_attempt` names a resumed or redispatched workflow; an unclaimed turn falls
    back to its turn id. The terminal update is conditional on that attempt still owning the row.
    If a claim changes it during the cancel, the new owner is cancelled before the row changes.
    `cancel_workflow_async` silently no-ops on an absent or complete workflow, so a queued turn
    never enqueued needs no special case. This is where a cancelled turn is counted: the turn's own
    execution never writes the row, and a turn cancelled before one started has no execution at
    all. It is also where a cancelled turn's proxy sessions end: once `cancelled` is committed,
    every session under the turn's label is revoked, unless a detached command of the turn is still
    followed, whose settling revokes them instead. A revoke fault is logged, since each session's
    deadline bounds it."""
    while True:
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.status,
                        tables.turn.c.running_attempt,
                    ).where(tables.turn.c.id == turn_id)
                )
            ).one_or_none()
        if row is None or row.status not in NON_TERMINAL_STATUSES:
            return None
        await client.cancel_workflow_async(row.running_attempt or str(turn_id))
        async with workspace_tx() as connection:
            current = (
                await connection.execute(
                    sa.select(
                        tables.turn.c.status,
                        tables.turn.c.subagent_profile,
                        tables.turn.c.parent_turn_id,
                        tables.turn.c.conversation_id,
                        tables.turn.c.running_attempt,
                        tables.turn.c.created_refs,
                        tables.turn.c.workspace_id,
                        tables.turn.c.detached_until,
                    )
                    .where(tables.turn.c.id == turn_id)
                    .with_for_update()
                )
            ).one_or_none()
            if current is None or current.status not in NON_TERMINAL_STATUSES:
                return None
            if current.running_attempt != row.running_attempt:
                continue
            frame = TerminalFrame(
                status=CANCELLED,
                created=tuple(ObjectRef.model_validate(ref) for ref in current.created_refs or ()),
            )
            result = await connection.execute(
                sa.update(tables.turn)
                .values(
                    status=CANCELLED,
                    terminal=frame.model_dump(mode="json"),
                    retry_at=None,
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.turn.c.id == turn_id,
                    tables.turn.c.status.in_(NON_TERMINAL_STATUSES),
                )
            )
            if result.rowcount == 1:
                await turn_conversation_changed(connection, turn_id)
        if result.rowcount == 1:
            row = current
            break
    emit_metric(
        "turn_terminal_total",
        status=CANCELLED,
        error_class="",
        profile=turn_profile(row.subagent_profile, spawned=row.parent_turn_id is not None),
    )
    await dispatch_next_turn(client, row.conversation_id)
    until = row.detached_until
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    if sessions is not None and (until is None or until <= datetime.now(UTC)):
        try:
            revoked = await sessions.revoke_labelled(row.workspace_id, TURN_LABEL, str(turn_id))
        except Exception as error:
            warn(
                "turn.sessions.close_failed",
                turn_id=str(turn_id),
                error_class=type(error).__name__,
            )
        else:
            log("turn.sessions.revoked", turn_id=str(turn_id), count=revoked)
    return frame
