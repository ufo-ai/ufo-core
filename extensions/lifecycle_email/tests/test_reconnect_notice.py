"""End-to-end proof of the reconnect notice: a parked stream reaches the member who owns the
account it hangs off, a shared account nobody owns reaches the seated admins, and a break already
told about is not told again until the account breaks afresh.

The gateway stands in as an httpx transport — the route it serves is proved by
`servers/control/tests/email_send_it.rs`. What is asserted here is the extension's own rows and the
messages it composes."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_lifecycle_email.reconnect_notice import (
    ACCOUNT_PARKED,
    ACTION_LABEL,
    SUBJECT,
    ReconnectNotice,
    reconnect_workspaces,
)
from ufo_ext_lifecycle_email.sends import SENT, lifecycle_send
from ufo_testsupport.lifecycle_email import BASE_URL, HOME_SURFACE, Gateway, context

from ufo.db import workspace_tx
from ufo.runtime.access.grants import CREDENTIALS_SCREEN_FRAGMENT, PARK_WINDOW
from ufo.runtime.workspace import ws
from ufo.schema import tables

pytestmark = pytest.mark.usefixtures("db")

ACCOUNT = "sam@acme.com"


async def _seed() -> tuple[UUID, UUID, UUID]:
    """A workspace, its admin, and an ordinary seated member."""
    workspace_id, admin_id, member_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        for member, email, admin in (
            (admin_id, "dana@acme.com", True),
            (member_id, ACCOUNT, False),
        ):
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member,
                    workspace_id=workspace_id,
                    email=email,
                    is_admin=admin,
                    seated_at=sa.func.now(),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return workspace_id, admin_id, member_id


async def _connect(
    workspace_id: UUID, owner_member_id: UUID | None, account_id: str = ACCOUNT
) -> UUID:
    connection_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider="gmail",
                account_id=account_id,
                host="mail.google.com",
                owner_member_id=owner_member_id,
                shared=owner_member_id is None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return connection_id


async def _stream(
    workspace_id: UUID,
    connection_id: UUID,
    *,
    parked_ago: timedelta | None,
    awaits_grant: bool = True,
) -> UUID:
    """One stream, parked `parked_ago` if at all. `awaits_grant` false is the throttle: a park the
    driver clears by itself on the next read."""
    uid = uuid4()
    since = None if parked_ago is None else datetime.now(UTC) - parked_ago
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                uid=uid,
                workspace_id=workspace_id,
                backend="gmail",
                config={},
                feed_handle=str(uid),
                connection_id=connection_id,
                next_sync_at=datetime.now(UTC),
                parked_at=since,
                parked_since=since,
                parked_awaits_grant=parked_ago is not None and awaits_grant,
                parked_reason=None if parked_ago is None else "gmail.readonly was withdrawn",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return uid


async def _reparked(workspace_id: UUID) -> None:
    """The hourly re-park: the driver rewrites `parked_at` and leaves `parked_since` alone."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.source)
            .where(tables.source.c.workspace_id == workspace_id)
            .values(parked_at=datetime.now(UTC))
        )


async def _outgoing_unpark(workspace_id: UUID) -> None:
    """The unpark the image this one replaces writes: it names `parked_at`, `parked_reason` and the
    counters, and knows neither column beside them."""
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.source)
            .where(tables.source.c.workspace_id == workspace_id)
            .values(parked_at=None, parked_reason=None)
        )


async def _unpark(workspace_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.source)
            .where(tables.source.c.workspace_id == workspace_id)
            .values(
                parked_at=None,
                parked_since=None,
                parked_awaits_grant=False,
                parked_reason=None,
            )
        )


async def _rows(workspace_id: UUID) -> list[sa.Row]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(lifecycle_send).where(
                        lifecycle_send.c.workspace_id == workspace_id,
                        lifecycle_send.c.kind == ACCOUNT_PARKED,
                    )
                )
            ).all()
        )


async def test_a_parked_stream_reaches_the_member_who_owns_the_account() -> None:
    workspace_id, _, member_id = await _seed()
    connection_id = await _connect(workspace_id, member_id)
    await _stream(workspace_id, connection_id, parked_ago=timedelta(hours=2))
    gateway = Gateway()
    ctx = context(gateway)

    assert workspace_id in await reconnect_workspaces()()
    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()
        await ReconnectNotice(ctx=ctx).run()

    assert [message["email"] for message in gateway.sent] == [ACCOUNT], (
        "the owner is the only person who can grant what the provider withdrew, and one pass tells "
        "them once"
    )
    (message,) = gateway.sent
    assert message["kind"] == ACCOUNT_PARKED
    assert message["subject"] == SUBJECT.format(workspace="acme.com", account=ACCOUNT)
    assert f"{ACCOUNT} has stopped syncing to acme.com" in message["body"]
    assert message["action_label"] == ACTION_LABEL
    assert (
        message["action_url"] == f"{BASE_URL}/surface/{HOME_SURFACE}{CREDENTIALS_SCREEN_FRAGMENT}"
    )

    (row,) = await _rows(workspace_id)
    assert row.state == SENT
    assert row.member_id == member_id


async def test_a_shared_account_nobody_owns_reaches_the_seated_admins() -> None:
    workspace_id, admin_id, _ = await _seed()
    connection_id = await _connect(workspace_id, None)
    await _stream(workspace_id, connection_id, parked_ago=timedelta(hours=2))
    gateway = Gateway()
    ctx = context(gateway)

    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()

    assert [message["email"] for message in gateway.sent] == ["dana@acme.com"]
    (row,) = await _rows(workspace_id)
    assert row.member_id == admin_id


async def test_a_healthy_account_is_never_mentioned() -> None:
    workspace_id, _, member_id = await _seed()
    connection_id = await _connect(workspace_id, member_id)
    await _stream(workspace_id, connection_id, parked_ago=None)
    gateway = Gateway()
    ctx = context(gateway)

    assert workspace_id not in await reconnect_workspaces()()
    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()

    assert gateway.sent == []
    assert await _rows(workspace_id) == []


async def test_a_second_stream_of_one_account_breaking_is_the_same_break() -> None:
    """The key is the oldest park still standing, so a second stream of the same account going down
    is the break the member already knows about, not a new one."""
    workspace_id, _, member_id = await _seed()
    connection_id = await _connect(workspace_id, member_id)
    await _stream(workspace_id, connection_id, parked_ago=timedelta(hours=2))
    gateway = Gateway()
    ctx = context(gateway)

    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()
    assert len(gateway.sent) == 1

    await _stream(workspace_id, connection_id, parked_ago=timedelta(minutes=5))
    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()

    assert len(gateway.sent) == 1, "one account down is one message, however many streams it holds"


async def test_an_account_fixed_and_broken_again_is_a_new_break() -> None:
    workspace_id, _, member_id = await _seed()
    connection_id = await _connect(workspace_id, member_id)
    await _stream(workspace_id, connection_id, parked_ago=timedelta(hours=2))
    gateway = Gateway()
    ctx = context(gateway)

    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()
    assert len(gateway.sent) == 1

    await _unpark(workspace_id)
    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()
    assert len(gateway.sent) == 1, "a working account says nothing"

    await _stream(workspace_id, connection_id, parked_ago=timedelta(minutes=1))
    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()

    assert len(gateway.sent) == 2, "the account broke again, so the member is told again"
    assert len({row.reason for row in await _rows(workspace_id)}) == 2


async def test_a_park_that_clears_itself_is_never_mentioned() -> None:
    """A throttle, a rate-limited org and a plan gate all park, and the driver clears them on the
    next read. Telling a member their access was withdrawn would state a false cause and ask for an
    act that repairs nothing."""
    workspace_id, _, member_id = await _seed()
    connection_id = await _connect(workspace_id, member_id)
    await _stream(workspace_id, connection_id, parked_ago=timedelta(hours=2), awaits_grant=False)
    gateway = Gateway()
    ctx = context(gateway)

    assert workspace_id not in await reconnect_workspaces()()
    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()

    assert gateway.sent == []
    assert await _rows(workspace_id) == []


async def test_an_hourly_re_park_is_the_same_break() -> None:
    """A refused stream re-parks every hour and `parked_at` moves with it. The key is
    `parked_since`, so the owner is told once rather than once an hour until they fix it."""
    workspace_id, _, member_id = await _seed()
    connection_id = await _connect(workspace_id, member_id)
    await _stream(workspace_id, connection_id, parked_ago=timedelta(hours=2))
    gateway = Gateway()
    ctx = context(gateway)

    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()
    assert len(gateway.sent) == 1

    for _ in range(3):
        await _reparked(workspace_id)
        with ws(workspace_id):
            await ReconnectNotice(ctx=ctx).run()

    assert len(gateway.sent) == 1, "one break is one message, however often it re-parks"


async def test_a_park_the_outgoing_image_wrote_says_nothing_until_this_one_re_parks_it() -> None:
    """The migrate Job runs to completion before the fleet rolls, and the image being replaced
    keeps serving. Every park it writes in that window carries the old shape: `parked_at` set,
    `parked_since` null, and no idea whether a member could repair it.

    Such a row must say nothing. It joins the set on the first refusal this image parks, which is
    when both facts are written and the break is one this deploy can describe."""
    workspace_id, _, member_id = await _seed()
    connection_id = await _connect(workspace_id, member_id)
    uid = await _stream(workspace_id, connection_id, parked_ago=timedelta(hours=2))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.source)
            .where(tables.source.c.uid == uid)
            .values(parked_since=None, parked_awaits_grant=False)
        )
    gateway = Gateway()
    ctx = context(gateway)

    assert workspace_id not in await reconnect_workspaces()()
    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()
    assert gateway.sent == [], "the old shape carries no break this deploy can name"

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.source)
            .where(tables.source.c.uid == uid)
            .values(parked_since=datetime.now(UTC), parked_awaits_grant=True)
        )
    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()

    assert [message["email"] for message in gateway.sent] == [ACCOUNT], (
        "and it is told once this image has parked it in the shape that says what happened"
    )


async def test_a_told_workspace_leaves_the_candidate_set() -> None:
    """Only a granted account clears the park, and an account nobody repairs never grants one. The
    ledger is the only thing that takes the workspace back out, so without it a broken stream would
    hold its workspace in the per-minute set for as long as it stayed broken."""
    workspace_id, _, member_id = await _seed()
    connection_id = await _connect(workspace_id, member_id)
    await _stream(workspace_id, connection_id, parked_ago=timedelta(hours=2))
    gateway = Gateway()
    ctx = context(gateway)
    candidates = reconnect_workspaces()

    assert workspace_id in await candidates()
    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()
    (row,) = await _rows(workspace_id)
    gateway.reported[row.ses_message_id] = "delivered"
    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()

    assert workspace_id not in await candidates(), "this break is decided"


async def test_a_second_stream_of_one_connection_parking_later_still_leaves_the_set() -> None:
    """The streams of one connection park on their own sync phases hours apart, and the notice is
    keyed on the oldest — so a read per stream would ask for a send against an instant no message
    was ever keyed to, and the workspace would never leave the per-minute set."""
    workspace_id, _, member_id = await _seed()
    connection_id = await _connect(workspace_id, member_id)
    await _stream(workspace_id, connection_id, parked_ago=timedelta(hours=4))
    gateway = Gateway()
    ctx = context(gateway)
    candidates = reconnect_workspaces()

    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()
    (row,) = await _rows(workspace_id)
    gateway.reported[row.ses_message_id] = "delivered"
    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()
    assert workspace_id not in await candidates()

    # The idle stream of the same connection parks strictly after the notice went out, which is
    # the ordinary order: each stream parks on its own sync phase.
    later = await _stream(workspace_id, connection_id, parked_ago=timedelta(0))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.source)
            .where(tables.source.c.uid == later)
            .values(parked_since=row.created_at + timedelta(seconds=1))
        )
    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()

    assert len(gateway.sent) == 1, "one break, one message, whichever stream carries it"
    assert workspace_id not in await candidates(), (
        "the break is the connection's and it is already told"
    )


async def test_a_second_account_breaking_is_a_break_of_its_own() -> None:
    """A workspace holds more than one connection and they break separately. The second one parks
    while the pass that mails the first is between its read and its claim, so its break is dated at
    or before that message — and a test that read the workspace alone would take the message sent
    for the first account as proof that the second was told."""
    workspace_id, _, member_id = await _seed()
    first = await _connect(workspace_id, member_id)
    await _stream(workspace_id, first, parked_ago=timedelta(hours=4))
    gateway = Gateway()
    ctx = context(gateway)
    candidates = reconnect_workspaces()

    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()
    (row,) = await _rows(workspace_id)
    gateway.reported[row.ses_message_id] = "delivered"
    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()
    assert workspace_id not in await candidates()

    second = await _connect(workspace_id, member_id, account_id="dana@acme.com")
    stream = await _stream(workspace_id, second, parked_ago=timedelta(0))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.source)
            .where(tables.source.c.uid == stream)
            .values(parked_since=row.created_at - timedelta(seconds=1))
        )

    assert workspace_id in await candidates(), "the second account is nobody's message yet"
    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()

    assert len(gateway.sent) == 2, "one message per broken account"
    for sent in await _rows(workspace_id):
        gateway.reported[sent.ses_message_id] = "delivered"
    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()
    assert workspace_id not in await candidates(), "and both breaks are decided"


async def test_an_account_the_outgoing_image_unparked_is_not_reported_as_broken() -> None:
    """The migrate Job fills the two new columns before the fleet rolls. A member who reconnects
    while the old image still serves gets an unpark that names `parked_at` alone, so the fact that
    the park stands has to be read from the column both images write."""
    workspace_id, _, member_id = await _seed()
    connection_id = await _connect(workspace_id, member_id)
    await _stream(workspace_id, connection_id, parked_ago=timedelta(hours=2))
    gateway = Gateway()
    ctx = context(gateway)
    candidates = reconnect_workspaces()

    assert workspace_id in await candidates()
    await _outgoing_unpark(workspace_id)

    assert workspace_id not in await candidates(), "the account is no longer broken"
    with ws(workspace_id):
        await ReconnectNotice(ctx=ctx).run()
    assert gateway.sent == [], (
        "telling the owner their access was withdrawn is the one thing a reconnect must stop"
    )
    assert await _rows(workspace_id) == []


async def test_a_break_older_than_the_window_is_no_longer_work() -> None:
    """The ledger drops a workspace once the pass has acted. A pass held back by the flag acts on
    nothing and writes nothing, so the break's own instant is the bound that holds either way."""
    workspace_id, _, member_id = await _seed()
    connection_id = await _connect(workspace_id, member_id)
    await _stream(workspace_id, connection_id, parked_ago=timedelta(hours=2))
    candidates = reconnect_workspaces()

    assert workspace_id in await candidates()

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.source)
            .where(tables.source.c.workspace_id == workspace_id)
            .values(parked_since=datetime.now(UTC) - PARK_WINDOW - timedelta(hours=1))
        )
    assert workspace_id not in await candidates(), "nobody is waiting on a reaction this late"
