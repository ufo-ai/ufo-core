"""End-to-end proof of the job that writes events: an invitation is logged at the instant it
happened, a member who has connected nothing is logged once the deadline passes, and a member who
has connected something is not.

What counts as connected is read live from core's connections, so a member who connected an account
and later removed it reads as having connected nothing — which is what the message would say."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_lifecycle_email.enrollments import (
    LIVE,
    lifecycle_enrollment,
    lifecycle_event,
)
from ufo_ext_lifecycle_email.events import WriteEvents, event_workspaces
from ufo_ext_lifecycle_email.runner import Reconciling, SequenceRunner
from ufo_ext_lifecycle_email.sequences import (
    CONNECT_NUDGE_AFTER,
    CONNECT_NUDGE_UNTIL,
    CONNECT_SOMETHING,
    CONNECT_SOMETHING_KIND,
    INVITATION_WINDOW,
    MEMBER_INVITED,
    NOTHING_CONNECTED,
)
from ufo_testsupport.lifecycle_email import Gateway, context

from ufo.db import workspace_tx
from ufo.runtime.workspace import ws
from ufo.schema import tables

pytestmark = pytest.mark.usefixtures("db")


async def _seed(*, invited_ago: timedelta) -> tuple[UUID, UUID, UUID]:
    """A workspace, the admin who invited, and the teammate they added."""
    workspace_id, admin_id, member_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=admin_id,
                workspace_id=workspace_id,
                email="dana@acme.com",
                is_admin=True,
                seated_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="sam@acme.com",
                seated_at=sa.func.now(),
                invited_at=datetime.now(UTC) - invited_ago,
                invited_by=admin_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, admin_id, member_id


async def _connect(workspace_id: UUID, owner_member_id: UUID | None, *, shared: bool) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.connection).values(
                id=uuid4(),
                workspace_id=workspace_id,
                provider="gmail",
                account_id="sam@acme.com",
                host="mail.google.com",
                owner_member_id=owner_member_id,
                shared=shared,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def _names(workspace_id: UUID) -> set[str]:
    async with workspace_tx() as connection:
        return {
            row.name
            for row in (
                await connection.execute(
                    sa.select(lifecycle_event.c.name).where(
                        lifecycle_event.c.workspace_id == workspace_id
                    )
                )
            ).all()
        }


async def _sequences(workspace_id: UUID) -> set[str]:
    async with workspace_tx() as connection:
        return {
            row.sequence
            for row in (
                await connection.execute(
                    sa.select(lifecycle_enrollment.c.sequence).where(
                        lifecycle_enrollment.c.workspace_id == workspace_id,
                        lifecycle_enrollment.c.state == LIVE,
                    )
                )
            ).all()
        }


async def _aged(workspace_id: UUID, ago: timedelta) -> None:
    """Move the invitation and the event it logged back in time together, as the clock would."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(lifecycle_event)
            .where(lifecycle_event.c.workspace_id == workspace_id)
            .values(occurred_at=datetime.now(UTC) - ago)
        )
        await connection.execute(
            sa.update(tables.member)
            .where(
                tables.member.c.workspace_id == workspace_id,
                tables.member.c.invited_at.is_not(None),
            )
            .values(invited_at=datetime.now(UTC) - ago)
        )


async def _logged_then_aged(ago: timedelta) -> tuple[UUID, UUID, UUID]:
    """A workspace whose invitation was logged while it was fresh, then moved back in time.

    The sweep reads core's members only as far back as the first step is due, so an invitation is
    logged while it is new and every later band measures from the event rather than from the member
    row. Seeding a stale invitation and sweeping would log nothing — which is also what production
    does, and why turning the flag on sends no backlog."""
    workspace_id, admin_id, member_id = await _seed(invited_ago=timedelta(minutes=1))
    with ws(workspace_id):
        await WriteEvents(ctx=context(Gateway())).run()
    await _aged(workspace_id, ago)
    return workspace_id, admin_id, member_id


async def test_an_invitation_is_logged_once_at_the_instant_it_happened() -> None:
    workspace_id, admin_id, member_id = await _seed(invited_ago=timedelta(hours=1))
    ctx = context(Gateway())

    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
        await WriteEvents(ctx=ctx).run()
        await Reconciling(ctx=ctx).run()

    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(lifecycle_event).where(lifecycle_event.c.workspace_id == workspace_id)
            )
        ).all()
    assert [(row.name, row.member_id) for row in rows] == [(MEMBER_INVITED, member_id)], (
        "the admin who invited is not themselves an invitation, and a second pass writes nothing"
    )
    logged = rows[0].occurred_at
    logged = logged if logged.tzinfo is not None else logged.replace(tzinfo=UTC)
    assert logged < datetime.now(UTC) - timedelta(minutes=30), (
        "the instant is when they were invited, not when the job read the row"
    )
    assert admin_id not in {row.member_id for row in rows}


async def test_a_member_who_connected_nothing_is_logged_once_the_deadline_passes() -> None:
    workspace_id, _, _ = await _logged_then_aged(CONNECT_NUDGE_AFTER + timedelta(days=1))
    gateway = Gateway()
    ctx = context(gateway)

    assert workspace_id in await event_workspaces()()
    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
        await WriteEvents(ctx=ctx).run()
        await Reconciling(ctx=ctx).run()

    assert await _names(workspace_id) == {MEMBER_INVITED, NOTHING_CONNECTED}
    assert CONNECT_SOMETHING in await _sequences(workspace_id)

    with ws(workspace_id):
        await SequenceRunner(ctx=ctx).run()
    sent = {message["kind"]: message["email"] for message in gateway.sent}
    assert sent.get(CONNECT_SOMETHING_KIND) == "sam@acme.com"


async def test_an_invitation_inside_the_deadline_is_not_an_absence() -> None:
    workspace_id, _, _ = await _seed(invited_ago=timedelta(hours=1))
    ctx = context(Gateway())

    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
        await Reconciling(ctx=ctx).run()

    assert await _names(workspace_id) == {MEMBER_INVITED}, (
        "a member who has not got there yet is not a member who did not get there"
    )


async def test_a_connection_the_member_owns_stops_the_absence() -> None:
    workspace_id, _, member_id = await _logged_then_aged(CONNECT_NUDGE_AFTER + timedelta(days=1))
    await _connect(workspace_id, member_id, shared=False)
    ctx = context(Gateway())

    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
        await Reconciling(ctx=ctx).run()

    assert await _names(workspace_id) == {MEMBER_INVITED}
    assert CONNECT_SOMETHING not in await _sequences(workspace_id)


async def test_a_shared_connection_stops_it_for_everyone() -> None:
    """The agent reaches a shared account on any member's behalf, so telling them they connected
    nothing would be wrong."""
    workspace_id, _, _ = await _logged_then_aged(CONNECT_NUDGE_AFTER + timedelta(days=1))
    await _connect(workspace_id, None, shared=True)
    ctx = context(Gateway())

    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
        await Reconciling(ctx=ctx).run()

    assert await _names(workspace_id) == {MEMBER_INVITED}


async def test_a_connection_removed_again_reads_as_nothing_connected() -> None:
    """The question is asked of core's rows every pass, never of a record that a member once
    connected something. A member who connected an account and removed it has connected nothing."""
    workspace_id, _, member_id = await _logged_then_aged(CONNECT_NUDGE_AFTER + timedelta(days=1))
    await _connect(workspace_id, member_id, shared=False)
    ctx = context(Gateway())

    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
    assert NOTHING_CONNECTED not in await _names(workspace_id)

    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.connection).where(tables.connection.c.workspace_id == workspace_id)
        )
    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
    assert NOTHING_CONNECTED in await _names(workspace_id)


async def test_a_stale_invitation_leaves_the_candidate_set() -> None:
    """Past the band the question is not worth asking. Without that bound a member who did connect
    would keep their workspace in the set forever, because nothing is ever written for them."""
    workspace_id, _, member_id = await _seed(invited_ago=timedelta(days=1))
    await _connect(workspace_id, member_id, shared=False)
    ctx = context(Gateway())

    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
        await Reconciling(ctx=ctx).run()
    assert await _names(workspace_id) == {MEMBER_INVITED}

    await _aged(workspace_id, CONNECT_NUDGE_UNTIL + timedelta(days=1))
    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
        await Reconciling(ctx=ctx).run()

    assert await _names(workspace_id) == {MEMBER_INVITED}
    assert workspace_id not in await event_workspaces()()


async def test_an_invitation_older_than_the_window_is_never_logged() -> None:
    """The window bounds the members the pass reads, not only the workspaces it runs in. A
    workspace that holds one recent invitation would otherwise hand back its whole backlog, and
    every sequence measured from those instants would be due the moment it was written."""
    workspace_id, _, _ = await _seed(invited_ago=INVITATION_WINDOW + timedelta(days=1))
    ctx = context(Gateway())

    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()

    assert await _names(workspace_id) == set()
    assert workspace_id not in await event_workspaces()()


async def test_a_member_who_connects_between_the_event_and_the_send_is_not_told_otherwise() -> None:
    """The event and the send are two jobs on two ticks, and the gap holds a roll, a flag turned
    off and on, and a queue the workspace absorbed. The step asks the absence again when it comes
    due, so a member who connected an account inside that gap is not told they connected none."""
    workspace_id, _, member_id = await _logged_then_aged(CONNECT_NUDGE_AFTER + timedelta(days=1))
    gateway = Gateway()
    ctx = context(gateway)

    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
        await Reconciling(ctx=ctx).run()
    assert NOTHING_CONNECTED in await _names(workspace_id)

    await _connect(workspace_id, member_id, shared=False)
    with ws(workspace_id):
        await SequenceRunner(ctx=ctx).run()

    assert CONNECT_SOMETHING_KIND not in [message["kind"] for message in gateway.sent], (
        "the reason for the message was gone by the time it came due"
    )
    async with workspace_tx() as connection:
        states = {
            row.state
            for row in (
                await connection.execute(
                    sa.select(lifecycle_enrollment.c.state).where(
                        lifecycle_enrollment.c.workspace_id == workspace_id,
                        lifecycle_enrollment.c.sequence == CONNECT_SOMETHING,
                    )
                )
            ).all()
        }
    assert states == {"ended"}, "the enrolment ends unsent rather than waiting"
