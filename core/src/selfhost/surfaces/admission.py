"""Shared turn admission: the one producer every surface, job, and extension-invoked turn calls to
place an inbound message on the durable turn queue — so the spend cap is evaluated once here, at the
boundary, and no caller can bypass it. A conversation-row lock serializes seq allocation; the turn
id is the DBOS workflow id, so a re-enqueue is idempotent. When an idempotency key is given, a
redelivery of the same message joins the turn already admitted for it instead of spawning a second.

The inbound spend decision routes the turn before it is enqueued: allow queues it; a breached cap
either parks it (held, not enqueued — the resume job re-admits it when the cap is raised) or, when
the cap rejects, commits it cancelled with the reason, so a client's wait ends in-surface either
way."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from dbos import DBOSClient, EnqueueOptions

from selfhost.accounting import SpendEvaluator
from selfhost.db import workspace_tx
from selfhost.schema import tables
from selfhost.schema.records import (
    DBOS_APP_VERSION,
    PARKED,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    TerminalFrame,
    TurnStatus,
    turn_id_for,
)

QUEUED: TurnStatus = "queued"
CANCELLED: TurnStatus = "cancelled"


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
            conversation = (
                await connection.execute(
                    sa.select(tables.conversation.c.member_id)
                    .where(tables.conversation.c.id == conversation_id)
                    .with_for_update()
                )
            ).one()
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
            existing = (
                await connection.execute(
                    sa.select(tables.turn.c.status).where(tables.turn.c.id == turn_id)
                )
            ).one_or_none()
            if existing is not None:
                status = existing.status
            else:
                decision = await SpendEvaluator(
                    workspace_id, conversation.member_id, agent_id
                ).decide(connection, 0)
                match decision.outcome:
                    case "allow":
                        status, terminal = QUEUED, None
                    case "park":
                        status, terminal = PARKED, None
                    case _:
                        status = CANCELLED
                        terminal = TerminalFrame(status=CANCELLED, text=decision.message)
                await connection.execute(
                    sa.insert(tables.turn).values(
                        id=turn_id,
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        seq=seq,
                        status=status,
                        inbound=body,
                        terminal=None if terminal is None else terminal.model_dump(mode="json"),
                        idempotency_key=idempotency_key,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        if status == QUEUED:
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


@dataclass(frozen=True)
class AdmissionInvoker:
    """The admit-turn seam bound to one workspace: a background handler that holds it (through its
    ExtensionContext) invokes a turn for a conversation's agent without knowing the workspace or
    reaching admission's internals — it delegates to the one shared producer, so a surface, a job,
    or an extension `invoke` all admit through the one boundary that evaluates the spend cap.
    Structurally a `TurnInvoker`; the composition root binds the workspace and hands it down."""

    admission: Admission
    workspace_id: UUID

    async def invoke(
        self,
        conversation_id: UUID,
        agent_id: UUID,
        message: str,
        idempotency_key: str | None = None,
    ) -> UUID:
        return await self.admission.admit(
            self.workspace_id, conversation_id, agent_id, message, idempotency_key=idempotency_key
        )
