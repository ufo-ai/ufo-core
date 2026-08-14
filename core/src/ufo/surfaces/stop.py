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
from ufo.ext.surface import Stopped
from ufo.hub import Absorbed, Hub, Terminal
from ufo.schema import tables
from ufo.surfaces.admission import Admission


@dataclass(frozen=True)
class MemberStop:
    """One stop, end to end: verify the turn is the authorized conversation's, cancel it durably,
    push its cancelled terminal onto the hub, then give a follow-up the member already sent its
    own run — named in the answer, so the surface can move the member's screen onto it. A turn
    already terminal comes back untouched (`ended` False), so a stop racing the turn's own commit
    never disturbs it and a double press is a no-op.

    The redispatched run's first hub frame names the arrival it was founded on: founding consumes
    the row outside any drain, so no `Absorbed` would ever name it, and the surface holding that
    message as pending would wait forever for the settle every absorbed message gets. The cancelled
    terminal publishes last — it is what wakes the stopped turn's tails, and a tail that then asks
    whether the conversation moved on must find the new turn already standing."""

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
        if not await cancel_one_turn(self.client, turn_id):
            return Stopped(ended=False, founded_turn_id=None)
        founded = await self.admission.redispatch(workspace_id, conversation_id)
        if founded is not None:
            new_turn_id, arrival_id = founded
            await self.hub.publish(new_turn_id, Absorbed(arrivals=(arrival_id,)))
        await self.hub.publish(turn_id, Terminal(frame=CANCELLED_FRAME))
        return Stopped(
            ended=True,
            founded_turn_id=None if founded is None else founded[0],
        )
