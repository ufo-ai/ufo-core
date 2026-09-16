"""The job that writes events.

An event says one thing: this member reached this state, at this time. Two states are written here.
A member was invited. A member has connected nothing.

Both are read from rows that already exist — core's members, and core's connections. Nothing is
caught as it happens, because nothing needs to be: the rows hold the same truth, and reading them
on a clock is the standing rule that batch-at-interval is the default.

A member reaches a named state once. The unique key on the event says so, so this job can read the
same rows every minute and write nothing after the first pass."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa

from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import WorkspaceCandidates, owner_candidates
from ufo.sdk.seats import invited_members, recently_invited_workspaces
from ufo_ext_lifecycle_email.enrollments import Enrollments, lifecycle_event
from ufo_ext_lifecycle_email.sequences import (
    CONNECT_NUDGE_AFTER,
    CONNECT_NUDGE_UNTIL,
    INVITATION_WINDOW,
    MEMBER_INVITED,
    NOTHING_CONNECTED,
    who_has_connected,
)


def _unconnected(workspace_id: UUID | None = None) -> sa.Select:
    """Members who were invited long enough ago to count, recently enough to still be worth asking
    about, and who have not been told yet.

    The band is what keeps this job off a workspace for good. Below it a member has not got there
    yet; above it the question is stale, and a member who did connect would otherwise keep their
    workspace in the candidate set forever, because nothing is ever written for them.

    Whether they connected anything is not asked here. That is a live read of core's connections,
    made once the workspace is bound, so a member who connected and then removed the account reads
    as having connected nothing — which is what the message would say."""
    invited = lifecycle_event.alias("invited_event")
    told = lifecycle_event.alias("told_event")
    now = datetime.now(UTC)
    chosen = sa.select(invited.c.workspace_id, invited.c.member_id).where(
        invited.c.name == MEMBER_INVITED,
        invited.c.occurred_at <= now - CONNECT_NUDGE_AFTER,
        invited.c.occurred_at > now - CONNECT_NUDGE_UNTIL,
        ~sa.exists(
            sa.select(sa.literal(1)).where(
                told.c.workspace_id == invited.c.workspace_id,
                told.c.member_id == invited.c.member_id,
                told.c.name == NOTHING_CONNECTED,
            )
        ),
    )
    if workspace_id is None:
        return chosen
    return chosen.where(invited.c.workspace_id == workspace_id)


def event_workspaces() -> WorkspaceCandidates:
    """Where this job has work: a workspace where someone was invited recently, and a workspace
    holding an invitation old enough to ask about."""
    invited = recently_invited_workspaces(INVITATION_WINDOW)

    def unconnected() -> sa.Select[tuple[UUID]]:
        found = _unconnected().subquery()
        return sa.select(found.c.workspace_id).distinct()

    aged = owner_candidates(unconnected)

    async def candidates() -> tuple[UUID, ...]:
        return tuple({*await invited(), *await aged()})

    return candidates


@dataclass(frozen=True)
class WriteEvents:
    """One pass over the bound workspace: write the invitation events, then write an event for
    each member the deadline has passed for who has connected nothing."""

    ctx: ExtensionContext

    async def run(self) -> None:
        events = Enrollments(ctx=self.ctx)
        async with self.ctx.transaction() as connection:
            invited = await invited_members(connection, self.ctx.workspace_id, INVITATION_WINDOW)
        for member in invited:
            await events.record(MEMBER_INVITED, member.member_id, member.invited_at)
        for member_id in await self._connected_nothing():
            await events.record(NOTHING_CONNECTED, member_id, datetime.now(UTC))

    async def _connected_nothing(self) -> tuple[UUID, ...]:
        """The instant is now rather than the deadline they crossed: a sequence measures from the
        moment the absence became a fact this deploy holds, so a workspace this job only started
        reading today does not fire a backlog of back-dated steps at once.

        Whether each of them has connected anything is `has_connected`, the same read the step
        makes again when it comes due — one question with one answer, asked at both ends because
        they are a deadline apart."""
        async with self.ctx.transaction() as connection:
            rows = (await connection.execute(_unconnected(self.ctx.workspace_id))).all()
        if not rows:
            return ()
        connected = await who_has_connected(row.member_id for row in rows)
        return tuple(row.member_id for row in rows if row.member_id not in connected)
