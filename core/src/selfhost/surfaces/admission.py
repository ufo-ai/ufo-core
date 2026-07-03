"""Shared turn admission: the one producer both surfaces call to place an inbound message on the
durable turn queue. A conversation-row lock serializes seq allocation; the turn id is the DBOS
workflow id, so a re-enqueue is idempotent. When an idempotency key is given, a redelivery of the
same message joins the turn already admitted for it instead of spawning a second."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from dbos import DBOSClient, EnqueueOptions

from selfhost.db import workspace_tx
from selfhost.schema import tables
from selfhost.schema.records import (
    DBOS_APP_VERSION,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    TerminalFrame,
    turn_id_for,
)


@dataclass(frozen=True)
class Admission:
    dbos: DBOSClient

    async def admit(
        self,
        workspace_id: UUID,
        conversation_id: UUID,
        agent_id: UUID,
        body: str,
        idempotency_key: str | None = None,
    ) -> UUID:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.select(tables.conversation.c.id)
                .where(tables.conversation.c.id == conversation_id)
                .with_for_update()
            )
            if idempotency_key is not None:
                deduped = (
                    await connection.execute(
                        sa.select(tables.turn.c.id).where(
                            tables.turn.c.workspace_id == workspace_id,
                            tables.turn.c.idempotency_key == idempotency_key,
                        )
                    )
                ).one_or_none()
                if deduped is not None:
                    return deduped.id
            seq = (
                await connection.execute(
                    sa.select(sa.func.coalesce(sa.func.max(tables.turn.c.seq), 0) + 1).where(
                        tables.turn.c.conversation_id == conversation_id
                    )
                )
            ).scalar_one()
            turn_id = turn_id_for(workspace_id, conversation_id, seq)
            already_admitted = (
                await connection.execute(
                    sa.select(tables.turn.c.id).where(tables.turn.c.id == turn_id)
                )
            ).one_or_none()
            if already_admitted is None:
                await connection.execute(
                    sa.insert(tables.turn).values(
                        id=turn_id,
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        seq=seq,
                        status="queued",
                        inbound=body,
                        terminal=None,
                        idempotency_key=idempotency_key,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        await self._enqueue(conversation_id, turn_id)
        return turn_id

    async def _enqueue(self, conversation_id: UUID, turn_id: UUID) -> None:
        options: EnqueueOptions = {
            "queue_name": TURN_QUEUE_NAME,
            "workflow_name": TURN_WORKFLOW_NAME,
            "workflow_id": str(turn_id),
            "queue_partition_key": str(conversation_id),
            "app_version": DBOS_APP_VERSION,
        }
        try:
            await self.dbos.enqueue_async(options, str(turn_id))
        except Exception:
            frame = TerminalFrame(status="failed", error_class="EnqueueFailed")
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.turn)
                    .values(
                        status="failed",
                        terminal=frame.model_dump(mode="json"),
                        updated_at=sa.func.now(),
                    )
                    .where(
                        tables.turn.c.id == turn_id,
                        tables.turn.c.status.in_(("queued", "running")),
                    )
                )
            raise
