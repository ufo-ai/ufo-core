"""A member ends a running turn: the surface authorizes the conversation, this workflow refuses a
turn outside it, cancels through the shared primitive, and publishes the cancelled terminal so
every live tail ends now rather than at its next poll. Descendants stay the cancel reconciler's,
as they are for every other cancel path."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from dbos import DBOSClient

from ufo.db import workspace_tx
from ufo.runtime.ext.surface import Stopped
from ufo.runtime.hub import Hub, Terminal
from ufo.runtime.surfaces.admission import Admission
from ufo.runtime.turns.cancellation import cancel_one_turn
from ufo.schema import tables


@dataclass(frozen=True)
class MemberStop:
    """One stop, end to end: verify the turn is the authorized conversation's, cancel it durably,
    push its cancelled terminal onto the hub, then give a follow-up the member already sent its
    own run — named in the answer, so the surface can move the member's screen onto it. A turn
    already terminal comes back untouched (`ended` False), so a stop racing the turn's own commit
    never disturbs it and a double press is a no-op.

    The cancelled terminal publishes last — it is what wakes the stopped turn's tails, and a tail
    that then asks whether the conversation moved on must find the new turn already standing."""

    client: DBOSClient
    hub: Hub
    admission: Admission

    async def stop(self, workspace_id: UUID, conversation_id: UUID, turn_id: UUID) -> Stopped:
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
        frame = await cancel_one_turn(self.client, turn_id)
        if frame is None:
            return Stopped(ended=False, founded_turn_id=None)
        founded = await self.admission.redispatch(workspace_id, conversation_id, turn_id)
        await self.hub.publish(turn_id, Terminal(frame=frame))
        return Stopped(ended=True, founded_turn_id=founded)
