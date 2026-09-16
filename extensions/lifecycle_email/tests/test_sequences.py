"""End-to-end proof of the sequence engine: an invitation logs the instant it happened, the member
is enrolled from that instant, nothing fires before the delay is up, the step fires once when it
is, and the enrollment ends unsent once the member has shown up.

The gateway stands in as an httpx transport — the route it serves is proved by
`servers/control/tests/email_send_it.rs`. What is asserted here is the event log, the enrollments,
and the messages the steps compose."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_lifecycle_email.runner as runner_module
from ufo_ext_lifecycle_email.enrollments import (
    ENDED,
    LIVE,
    Enrollments,
    due_enrollment_workspaces,
    lifecycle_enrollment,
    lifecycle_event,
)
from ufo_ext_lifecycle_email.events import WriteEvents
from ufo_ext_lifecycle_email.runner import (
    UNKNOWN_SEQUENCE_GRACE,
    Reconciling,
    SequenceRunner,
)
from ufo_ext_lifecycle_email.sends import SENT, lifecycle_send
from ufo_ext_lifecycle_email.sequences import (
    INVITED_TEAMMATE,
    INVITED_TEAMMATE_AFTER,
    INVITED_TEAMMATE_KIND,
    MEMBER_INVITED,
)
from ufo_testsupport.lifecycle_email import Gateway, context

from ufo.db import workspace_tx
from ufo.runtime.workspace import ws
from ufo.schema import tables

pytestmark = pytest.mark.usefixtures("db")


async def _seed(*, invited_ago: timedelta = timedelta(hours=1)) -> tuple[UUID, UUID, UUID]:
    """A workspace, the admin who invited, and the teammate they added."""
    workspace_id, admin_id, invited_id = uuid4(), uuid4(), uuid4()
    invited_at = datetime.now(UTC) - invited_ago
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
                id=invited_id,
                workspace_id=workspace_id,
                email="sam@acme.com",
                seated_at=sa.func.now(),
                invited_at=invited_at,
                invited_by=admin_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, admin_id, invited_id


async def _spoke(workspace_id: UUID, member_id: UUID) -> None:
    conversation_id, agent_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="main",
                prompt="",
                model="auto",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=str(conversation_id),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=uuid4(),
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="hello",
                terminal={"reply": "hi"},
                speaker_member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


async def _events(workspace_id: UUID) -> list[sa.Row]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(lifecycle_event).where(lifecycle_event.c.workspace_id == workspace_id)
                )
            ).all()
        )


async def _enrollments(workspace_id: UUID) -> list[sa.Row]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(lifecycle_enrollment).where(
                        lifecycle_enrollment.c.workspace_id == workspace_id
                    )
                )
            ).all()
        )


async def _due_now(workspace_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(lifecycle_enrollment)
            .where(lifecycle_enrollment.c.workspace_id == workspace_id)
            .values(next_due_at=datetime.now(UTC) - timedelta(seconds=1))
        )


async def test_an_invitation_logs_its_instant_and_enrolls_the_member_once() -> None:
    workspace_id, _, invited_id = await _seed()
    ctx = context(Gateway())

    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
        await Reconciling(ctx=ctx).run()
        await WriteEvents(ctx=ctx).run()
        await Reconciling(ctx=ctx).run()

    async with workspace_tx() as connection:
        events = list(
            (
                await connection.execute(
                    sa.select(lifecycle_event).where(lifecycle_event.c.workspace_id == workspace_id)
                )
            ).all()
        )
    assert [(event.name, event.member_id) for event in events] == [(MEMBER_INVITED, invited_id)], (
        "the admin who invited is not themselves an invitation, and a second pass logs nothing"
    )

    (enrollment,) = await _enrollments(workspace_id)
    assert enrollment.sequence == INVITED_TEAMMATE
    assert enrollment.state == LIVE
    assert enrollment.step == 0
    assert enrollment.event_id == events[0].id
    assert enrollment.next_due_at - enrollment.occurred_at == INVITED_TEAMMATE_AFTER, (
        "the step is measured from the instant, not from the pass that enrolled it"
    )


async def test_nothing_fires_before_the_delay_is_up() -> None:
    workspace_id, _, _ = await _seed()
    gateway = Gateway()
    ctx = context(gateway)

    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
        await Reconciling(ctx=ctx).run()
        await SequenceRunner(ctx=ctx).run()

    assert gateway.sent == []
    assert workspace_id not in await due_enrollment_workspaces()()
    (enrollment,) = await _enrollments(workspace_id)
    assert enrollment.state == LIVE


async def test_the_due_step_fires_once_and_ends_the_enrollment() -> None:
    workspace_id, _, invited_id = await _seed()
    gateway = Gateway()
    ctx = context(gateway)

    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
        await Reconciling(ctx=ctx).run()
    await _due_now(workspace_id)
    assert workspace_id in await due_enrollment_workspaces()()

    with ws(workspace_id):
        await SequenceRunner(ctx=ctx).run()
        await SequenceRunner(ctx=ctx).run()

    assert [message["email"] for message in gateway.sent] == ["sam@acme.com"]
    (message,) = gateway.sent
    assert message["kind"] == INVITED_TEAMMATE_KIND
    assert message["subject"] == "dana@acme.com added you to acme.com"
    assert "You have a seat on acme.com" in message["body"]
    assert message["action_label"] == "Sign in"
    assert "/surface/web" in message["action_url"]

    (enrollment,) = await _enrollments(workspace_id)
    assert enrollment.state == ENDED, "a sequence with no step left is over"
    assert enrollment.claimed_by is None

    async with workspace_tx() as connection:
        (send,) = (
            await connection.execute(
                sa.select(lifecycle_send).where(lifecycle_send.c.workspace_id == workspace_id)
            )
        ).all()
    assert (send.state, send.member_id, send.kind) == (SENT, invited_id, INVITED_TEAMMATE_KIND)


async def test_a_member_who_showed_up_ends_the_enrollment_unsent() -> None:
    workspace_id, _, invited_id = await _seed()
    gateway = Gateway()
    ctx = context(gateway)

    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
        await Reconciling(ctx=ctx).run()
    await _due_now(workspace_id)
    await _spoke(workspace_id, invited_id)

    with ws(workspace_id):
        await SequenceRunner(ctx=ctx).run()

    assert gateway.sent == [], "the reason for the message is gone"
    (enrollment,) = await _enrollments(workspace_id)
    assert enrollment.state == ENDED


async def test_a_lapsed_lease_re_fires_the_enrollment_and_sends_nothing_twice() -> None:
    """A pass that died after claiming leaves the lease to expire. The next pass takes the row
    again — and the attempt key, which names the enrollment and the step, refuses the second
    send."""
    workspace_id, _, invited_id = await _seed()
    gateway = Gateway()
    ctx = context(gateway)

    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
        await Reconciling(ctx=ctx).run()
    await _due_now(workspace_id)

    with ws(workspace_id):
        claimed = await Enrollments(ctx=ctx).claim_due(datetime.now(UTC))
    assert len(claimed) == 1
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(lifecycle_send).values(state="attempted", ses_message_id=None)
        )
        await connection.execute(
            sa.insert(lifecycle_send).values(
                id=uuid4(),
                workspace_id=workspace_id,
                member_id=invited_id,
                kind=INVITED_TEAMMATE_KIND,
                reason=f"{claimed[0].id}:0",
                state="attempted",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.update(lifecycle_enrollment).values(
                claim_expires_at=datetime.now(UTC) - timedelta(seconds=1)
            )
        )

    with ws(workspace_id):
        await SequenceRunner(ctx=ctx).run()

    assert gateway.sent == [], "the attempt was already claimed, so nothing is sent again"
    (enrollment,) = await _enrollments(workspace_id)
    assert enrollment.state == ENDED


async def test_a_reconcile_that_stops_mid_event_offers_it_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One event is one transaction. Its enrollments and the mark that says it was read commit
    together, so a pass that stops between them leaves the event unread rather than read with
    nothing measured from it."""
    workspace_id, _, invited_id = await _seed()
    ctx = context(Gateway())

    async def refuse(*args: object, **kwargs: object) -> None:
        raise RuntimeError("the pass stopped")

    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
    monkeypatch.setattr(Enrollments, "_mark_read", refuse)
    with ws(workspace_id), pytest.raises(RuntimeError):
        await Reconciling(ctx=ctx).run()

    (event,) = await _events(workspace_id)
    assert event.matched_at is None, "an event whose enrollments did not commit stays unread"
    assert await _enrollments(workspace_id) == []

    monkeypatch.undo()
    with ws(workspace_id):
        await Reconciling(ctx=ctx).run()

    (event,) = await _events(workspace_id)
    (enrollment,) = await _enrollments(workspace_id)
    assert event.matched_at is not None
    assert enrollment.event_id == event.id
    assert enrollment.member_id == invited_id


async def test_an_invitation_already_past_the_first_step_enrolls_nobody() -> None:
    """Turning the flag on starts from the fleet as it stands. An invitation read back further than
    the first step's delay would be enrolled already past due, and the runner claims every such row
    on the next tick — so everyone invited between the two figures would be mailed at once."""
    workspace_id, _, _ = await _seed(invited_ago=INVITED_TEAMMATE_AFTER + timedelta(hours=1))
    gateway = Gateway()
    ctx = context(gateway)

    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
        await SequenceRunner(ctx=ctx).run()

    assert await _events(workspace_id) == []
    assert await _enrollments(workspace_id) == []
    assert gateway.sent == [], "a backlog is not a delay that came due"


async def test_an_image_that_does_not_hold_the_sequence_leaves_the_enrollment_for_one_that_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A roll runs both images over one set of rows, and the outgoing one holds neither the
    sequences the new one ships nor the steps they name. Ending the enrollment there would settle
    it unsent, and the unique key over the event refuses to write it again — so the member would
    never be told."""
    workspace_id, _, _ = await _seed()
    gateway = Gateway()
    ctx = context(gateway)

    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
        await Reconciling(ctx=ctx).run()
    await _due_now(workspace_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(lifecycle_enrollment)
            .where(lifecycle_enrollment.c.workspace_id == workspace_id)
            .values(created_at=datetime.now(UTC) - timedelta(days=2))
        )

    monkeypatch.setattr(runner_module, "SEQUENCES", ())
    with ws(workspace_id):
        await SequenceRunner(ctx=ctx).run()

    (enrollment,) = await _enrollments(workspace_id)
    assert enrollment.state == LIVE, "the older image defers rather than settling it unsent"
    assert enrollment.claimed_by is None, "and drops its lease so the newer one claims it"
    assert gateway.sent == []

    monkeypatch.undo()
    await _due_now(workspace_id)
    with ws(workspace_id):
        await SequenceRunner(ctx=ctx).run()

    assert [message["kind"] for message in gateway.sent] == [INVITED_TEAMMATE_KIND], (
        "the image that holds the sequence sends it"
    )


async def test_an_enrollment_past_the_grace_ends_when_no_sequence_claims_it() -> None:
    """A sequence torn out of the tree leaves its enrollments behind. Past the window a roll can
    account for, the row is orphaned rather than skewed, and holding it live forever would keep its
    workspace in the per-minute set."""
    workspace_id, _, _ = await _seed()
    gateway = Gateway()
    ctx = context(gateway)

    with ws(workspace_id):
        await WriteEvents(ctx=ctx).run()
        await Reconciling(ctx=ctx).run()
    stale = datetime.now(UTC) - UNKNOWN_SEQUENCE_GRACE - timedelta(minutes=1)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(lifecycle_enrollment)
            .where(lifecycle_enrollment.c.workspace_id == workspace_id)
            .values(next_due_at=stale, sequence="a_sequence_nothing_holds")
        )

    with ws(workspace_id):
        await SequenceRunner(ctx=ctx).run()

    (enrollment,) = await _enrollments(workspace_id)
    assert enrollment.state == ENDED
    assert gateway.sent == []
