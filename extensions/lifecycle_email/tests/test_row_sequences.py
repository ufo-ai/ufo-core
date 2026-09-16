"""A drip sequence an operator approved runs on the same engine a sequence in the tree does: it
enrolls from the same event log, its steps are measured from the same instant, and it stops the
moment it leaves the approved set.

The approval gate itself is control's, proved by `servers/control/tests/lifecycle_it.rs`; what the
gateway hands back stands in here, and what is asserted is what the runner makes of it."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_lifecycle_email.enrollments import (
    ENDED,
    LIVE,
    Enrollments,
    lifecycle_enrollment,
    lifecycle_event,
)
from ufo_ext_lifecycle_email.runner import (
    UNKNOWN_SEQUENCE_GRACE,
    Reconciling,
    SequenceRunner,
    sequences,
)
from ufo_ext_lifecycle_email.sequences import INVITED_TEAMMATE, MEMBER_INVITED
from ufo_testsupport.lifecycle_email import BASE_URL, Gateway, context

from ufo.db import workspace_tx
from ufo.runtime.workspace import ws
from ufo.schema import tables

pytestmark = pytest.mark.usefixtures("db")

WELCOME_ID = "9f2c0b3e-0f5a-4a1e-9a4c-1d7c1f2a9b01"
SUCCESSOR_ID = "9f2c0b3e-0f5a-4a1e-9a4c-1d7c1f2a9b02"

WELCOME = {
    "id": WELCOME_ID,
    "name": "welcome_drip",
    "event": MEMBER_INVITED,
    "steps": [
        {
            "after_seconds": 0,
            "kind": "welcome_day_one",
            "subject": "Getting started",
            "body": "Ask it to connect an account.",
            "action_label": "Open the workspace",
            "action_url": "{url}",
        },
        {
            "after_seconds": 60,
            "kind": "welcome_day_four",
            "subject": "One more thing",
            "body": "Give it a repository to read.",
            "action_label": None,
            "action_url": None,
        },
    ],
}


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


async def _end_every_enrollment(workspace_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(lifecycle_enrollment)
            .where(lifecycle_enrollment.c.workspace_id == workspace_id)
            .values(state=ENDED)
        )


async def _seed() -> tuple[UUID, UUID]:
    workspace_id, member_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="sam@acme.com",
                seated_at=sa.func.now(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, member_id


async def _enrollment(workspace_id: UUID, sequence: str) -> sa.Row | None:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(lifecycle_enrollment).where(
                    lifecycle_enrollment.c.workspace_id == workspace_id,
                    lifecycle_enrollment.c.sequence == sequence,
                )
            )
        ).one_or_none()


async def _older_than_the_grace(workspace_id: UUID) -> None:
    """Leave the step due for longer than a rolling deploy can account for, so a sequence the pass
    cannot resolve reads as retired rather than as an image that has not caught up."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(lifecycle_enrollment)
            .where(lifecycle_enrollment.c.workspace_id == workspace_id)
            .values(next_due_at=datetime.now(UTC) - UNKNOWN_SEQUENCE_GRACE - timedelta(minutes=1))
        )


async def _due_now(workspace_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(lifecycle_enrollment)
            .where(lifecycle_enrollment.c.workspace_id == workspace_id)
            .values(next_due_at=datetime.now(UTC) - timedelta(seconds=1))
        )


async def test_an_approved_sequence_runs_its_steps_in_order_from_the_event() -> None:
    workspace_id, member_id = await _seed()
    gateway = Gateway()
    gateway.approved = [WELCOME]
    ctx = context(gateway)
    occurred_at = datetime.now(UTC) - timedelta(minutes=5)

    with ws(workspace_id):
        await Enrollments(ctx=ctx).record(MEMBER_INVITED, member_id, occurred_at)
        await Reconciling(ctx=ctx).run()
        await SequenceRunner(ctx=ctx).run()

    assert [message["kind"] for message in gateway.sent] == ["welcome_day_one"]
    assert gateway.sent[0]["subject"] == "Getting started"
    assert f"{BASE_URL}/surface/web" in gateway.sent[0]["action_url"], "{url} becomes the portal"

    held = await _enrollment(workspace_id, WELCOME_ID)
    assert held is not None
    assert held.state == LIVE
    assert held.step == 1
    assert held.next_due_at - held.occurred_at == timedelta(seconds=60), (
        "the second step is measured from the event, not from the first send"
    )

    await _due_now(workspace_id)
    with ws(workspace_id):
        await SequenceRunner(ctx=ctx).run()

    assert [message["kind"] for message in gateway.sent] == [
        "welcome_day_one",
        "welcome_day_four",
    ]
    held = await _enrollment(workspace_id, WELCOME_ID)
    assert held is not None and held.state == ENDED


async def test_a_sequence_that_leaves_the_approved_set_stops_mid_flight() -> None:
    """Retiring one, or editing it so its approval drops, takes it out of what the gateway hands
    back. A live enrollment then has no step left, so it ends rather than waiting on words that are
    not coming."""
    workspace_id, member_id = await _seed()
    gateway = Gateway()
    gateway.approved = [WELCOME]
    ctx = context(gateway)

    with ws(workspace_id):
        await Enrollments(ctx=ctx).record(
            MEMBER_INVITED, member_id, datetime.now(UTC) - timedelta(minutes=5)
        )
        await Reconciling(ctx=ctx).run()
        await SequenceRunner(ctx=ctx).run()
    assert len(gateway.sent) == 1

    gateway.approved = []
    await _due_now(workspace_id)
    with ws(workspace_id):
        await SequenceRunner(ctx=ctx).run()

    assert len(gateway.sent) == 1, "words nobody signed off go nowhere"
    held = await _enrollment(workspace_id, WELCOME_ID)
    assert held is not None and held.state == LIVE, (
        "a pass that cannot resolve a sequence defers first: a rolling deploy looks the same"
    )

    await _older_than_the_grace(workspace_id)
    with ws(workspace_id):
        await SequenceRunner(ctx=ctx).run()

    assert len(gateway.sent) == 1
    held = await _enrollment(workspace_id, WELCOME_ID)
    assert held is not None and held.state == ENDED, "past the window it is retired, not skewed"


async def test_an_event_read_once_is_not_offered_to_a_sequence_written_later() -> None:
    """The reconciler marks an event read whether or not anything measured from it. A sequence
    approved afterwards reaches the events logged since, and never a year of them."""
    workspace_id, member_id = await _seed()
    gateway = Gateway()
    ctx = context(gateway)

    with ws(workspace_id):
        await Enrollments(ctx=ctx).record(
            MEMBER_INVITED, member_id, datetime.now(UTC) - timedelta(minutes=5)
        )
        await Reconciling(ctx=ctx).run()

    gateway.approved = [WELCOME]
    with ws(workspace_id):
        await Reconciling(ctx=ctx).run()
        await SequenceRunner(ctx=ctx).run()

    assert gateway.sent == []
    assert await _enrollment(workspace_id, WELCOME_ID) is None


async def test_an_event_the_outgoing_image_enrolled_from_is_not_enrolled_again() -> None:
    """A roll writes the first enrolment somewhere else. The image this one replaces enrols where
    it logs the event, and every event it logs after the migrate Job carries a null stamp, so the
    reconciling pass reads it unread. The refusal is on the event in every state, because a
    one-step sequence due at once fires and ends inside one pass — the live index alone would not
    see the enrolment already made, and the member would be sent the same message twice."""
    workspace_id, member_id = await _seed()
    enrollments = Enrollments(ctx=context(Gateway()))

    with ws(workspace_id):
        event_id = await enrollments.record(MEMBER_INVITED, member_id, datetime.now(UTC))
        assert event_id is not None
        async with enrollments.ctx.transaction() as connection:
            await enrollments._enroll(
                connection, INVITED_TEAMMATE, member_id, event_id, datetime.now(UTC), timedelta(0)
            )
        await _end_every_enrollment(workspace_id)

    gateway = Gateway()
    ctx = context(gateway)
    with ws(workspace_id):
        await Reconciling(ctx=ctx).run()

    assert len(await _enrollments(workspace_id)) == 1, (
        "the event had already been measured from, whatever state that enrolment reached"
    )


async def test_a_second_sequence_taking_a_freed_name_does_not_serve_the_first_one_s_members() -> (
    None
):
    """An operator renames a sequence, which drops its approval, and approves a second one under
    the name the first gave up. A member half-way through the first must not be sent the second
    one's words: the enrolment keys on the row, and the row it keys on is no longer approved."""
    workspace_id, member_id = await _seed()
    gateway = Gateway()
    gateway.approved = [WELCOME]
    ctx = context(gateway)

    with ws(workspace_id):
        await Enrollments(ctx=ctx).record(
            MEMBER_INVITED, member_id, datetime.now(UTC) - timedelta(minutes=5)
        )
        await Reconciling(ctx=ctx).run()
        await SequenceRunner(ctx=ctx).run()
    assert [message["kind"] for message in gateway.sent] == ["welcome_day_one"]

    successor = {
        **WELCOME,
        "id": SUCCESSOR_ID,
        "steps": [{**WELCOME["steps"][0], "kind": "successor_day_one"}],
    }
    gateway.approved = [successor]
    await _due_now(workspace_id)
    with ws(workspace_id):
        await SequenceRunner(ctx=ctx).run()

    assert [message["kind"] for message in gateway.sent] == ["welcome_day_one"], (
        "the words this member enrolled on are gone, and the name says nothing about that"
    )
    await _older_than_the_grace(workspace_id)
    with ws(workspace_id):
        await SequenceRunner(ctx=ctx).run()

    assert [message["kind"] for message in gateway.sent] == ["welcome_day_one"]
    held = await _enrollment(workspace_id, WELCOME_ID)
    assert held is not None and held.state == ENDED


async def test_a_drip_row_cannot_take_a_name_the_tree_holds() -> None:
    """Both halves share one name space and every read answers with one of them, so a row taking a
    tree name would enrol a member on its own event and then send them the tree's copy."""
    workspace_id, _ = await _seed()
    gateway = Gateway()
    gateway.approved = [
        {
            "id": SUCCESSOR_ID,
            "name": INVITED_TEAMMATE,
            "event": "nothing_connected",
            "steps": [
                {
                    "after_seconds": 0,
                    "kind": "impostor",
                    "subject": "Not the tree's words",
                    "body": "Nor its trigger.",
                    "action_label": None,
                    "action_url": None,
                }
            ],
        }
    ]
    ctx = context(gateway)

    with ws(workspace_id):
        held = await sequences(ctx)

    names = [sequence.name for sequence in held]
    assert names.count(INVITED_TEAMMATE) == 1, "one name, one sequence"
    (kept,) = [sequence for sequence in held if sequence.name == INVITED_TEAMMATE]
    assert kept.event == MEMBER_INVITED, "the tree keeps the name a diff reviewed"


async def test_a_deploy_with_no_send_seam_consumes_no_event() -> None:
    """Reading an event marks it read, and nothing ever unmarks one. A pass that read the log while
    it could reach no sequence would consume every event in it for good, so the absent seam is
    fatal here exactly as it is for the runner."""
    workspace_id, member_id = await _seed()
    seamless = context(Gateway())
    seamless = replace(seamless, email=None)

    with ws(workspace_id):
        await Enrollments(ctx=seamless).record(MEMBER_INVITED, member_id, datetime.now(UTC))
        with pytest.raises(RuntimeError):
            await Reconciling(ctx=seamless).run()

    async with workspace_tx() as connection:
        unread = (
            await connection.execute(
                sa.select(lifecycle_event.c.matched_at).where(
                    lifecycle_event.c.workspace_id == workspace_id
                )
            )
        ).all()
    assert [row.matched_at for row in unread] == [None], "the event is still there to be measured"
