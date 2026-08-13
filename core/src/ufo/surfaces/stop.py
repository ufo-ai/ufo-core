"""A member ends a running turn: the surface authorizes the conversation, this workflow refuses a
turn outside it, cancels through the shared primitive, and publishes the cancelled terminal so
every live tail ends now rather than at its next poll. Descendants stay the cancel reconciler's,
as they are for every other cancel path."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from dbos import DBOSClient

from ufo.cancellation import CANCELLED_FRAME, cancel_one_turn
from ufo.db import workspace_tx
from ufo.hub import Hub, Terminal
from ufo.schema import tables


@dataclass(frozen=True)
class MemberStop:
    """One stop, end to end: verify the turn is the authorized conversation's, cancel it durably,
    then push its cancelled terminal onto the hub. Returns True iff this call ended the turn — a
    turn already terminal returns False untouched, so a stop racing the turn's own commit never
    disturbs it and a double press is a no-op."""

    client: DBOSClient
    hub: Hub

    async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> bool:
        async with workspace_tx() as connection:
            owner = (
                await connection.execute(
                    sa.select(tables.turn.c.conversation_id).where(
                        tables.turn.c.id == turn_id,
                        tables.turn.c.workspace_id == workspace_id,
                    )
                )
            ).scalar_one_or_none()
        if owner != conversation_id:
            raise ValueError(f"turn {turn_id} is not a turn of conversation {conversation_id}")
        if not await cancel_one_turn(self.client, turn_id):
            return False
        await self.hub.publish(turn_id, Terminal(frame=CANCELLED_FRAME))
        return True
