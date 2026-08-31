"""One conversation, one dispatched turn: the handoff every workflow exit makes.

A conversation's turns are serialized here and at admission rather than by the queue: admission
folds a message into the live turn or founds a queued successor under the conversation lock, and
whatever ends a turn's workflow — its terminal, a park, the failure backstop, a cancel from
outside — offers the next queued turn through this one function. The turns queue itself is plain,
so an executor's `worker_concurrency` claims spread across the fleet; nothing about ordering rides
the queue. A crash between a terminal and this call is caught by the dispatcher sweep's grace, and
both may fire: the stamp update admits one winner, and the enqueue is dedup'd on the workflow id.
"""

from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOSClient, EnqueueOptions

from ufo.db import workspace_tx
from ufo.harness.o11y import log
from ufo.schema import tables
from ufo.schema.records import (
    DBOS_APP_VERSION,
    RUNNING,
    TURN_WORKFLOW_NAME,
    turn_queue_for,
)


async def dispatch_next_turn(client: DBOSClient, conversation_id: UUID) -> None:
    """Stamp and enqueue the conversation's frontmost queued turn, once nothing runs ahead of it.

    A parked predecessor does not block the offer — a park ends its workflow and its resume is the
    resume sweep's own admission — and a turn that has ever been claimed rides a fresh workflow id,
    because the run that claimed it consumed its own and DBOS would drop a duplicate as complete."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.select(tables.conversation.c.id)
            .where(tables.conversation.c.id == conversation_id)
            .with_for_update()
        )
        running = (
            await connection.execute(
                sa.select(tables.turn.c.id)
                .where(
                    tables.turn.c.conversation_id == conversation_id,
                    tables.turn.c.status == RUNNING,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if running is not None:
            return
        offered = (
            await connection.execute(
                sa.select(
                    tables.turn.c.id,
                    tables.turn.c.workspace_id,
                    tables.turn.c.parent_turn_id,
                    tables.turn.c.admission_source,
                    tables.turn.c.running_attempt,
                    tables.turn.c.dispatch_enqueued_at,
                )
                .where(
                    tables.turn.c.conversation_id == conversation_id,
                    tables.turn.c.status == "queued",
                )
                .order_by(tables.turn.c.seq)
                .limit(1)
                .with_for_update()
            )
        ).one_or_none()
        if offered is None or offered.dispatch_enqueued_at is not None:
            return
        await connection.execute(
            sa.update(tables.turn)
            .values(dispatch_enqueued_at=sa.func.now(), updated_at=sa.func.now())
            .where(tables.turn.c.id == offered.id)
        )
    workflow_id = uuid4().hex if offered.running_attempt is not None else str(offered.id)
    options: EnqueueOptions = {
        "queue_name": turn_queue_for(offered.parent_turn_id, offered.admission_source),
        "workflow_name": TURN_WORKFLOW_NAME,
        "workflow_id": workflow_id,
        "app_version": DBOS_APP_VERSION,
    }
    try:
        await client.enqueue_async(options, str(offered.workspace_id), str(offered.id))
    except Exception as error:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .values(dispatch_enqueued_at=None, updated_at=sa.func.now())
                .where(tables.turn.c.id == offered.id, tables.turn.c.status == "queued")
            )
        log(
            "turn.enqueue_deferred",
            turn_id=str(offered.id),
            error_class=type(error).__name__,
        )
