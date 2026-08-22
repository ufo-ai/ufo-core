from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.billing.balance import (
    balance_absent,
    balance_refusal_message,
    billing_screen_url,
    credit,
    read_balance,
    recent_purchases,
    set_reserve,
)
from ufo.db import workspace_tx
from ufo.schema import tables


async def _workspace(connection: AsyncConnection) -> UUID:
    workspace_id = uuid4()
    await connection.execute(
        sa.insert(tables.workspace).values(
            id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
        )
    )
    return workspace_id


async def test_a_credit_creates_the_balance_and_a_second_reference_adds_to_it(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id = await _workspace(connection)
        assert await credit(connection, workspace_id, 10_000_000, 10_000_000, "first")
        assert await credit(connection, workspace_id, 5_000_000, 5_000_000, "second")
        current = await read_balance(connection, workspace_id)
    assert current is not None
    assert current.balance_micro_usd == 15_000_000
    assert current.granted_micro_usd == 15_000_000
    assert current.charged_micro_usd == 15_000_000


async def test_a_repeated_reference_credits_once(db: None) -> None:
    """The idempotency a polled payment fulfilment rests on: a second delivery of one session adds
    nothing and says so."""
    async with workspace_tx() as connection:
        workspace_id = await _workspace(connection)
        assert await credit(connection, workspace_id, 10_000_000, 10_000_000, "stripe/cs_1")
        assert not await credit(connection, workspace_id, 10_000_000, 10_000_000, "stripe/cs_1")
        current = await read_balance(connection, workspace_id)
    assert current is not None
    assert current.balance_micro_usd == 10_000_000


async def test_a_grant_larger_than_the_charge_credits_the_grant(db: None) -> None:
    """A signup grant charges nothing and a volume tier grants more than it charges; the difference
    is the whole record of a discount."""
    async with workspace_tx() as connection:
        workspace_id = await _workspace(connection)
        await credit(connection, workspace_id, 100_000_000, 0, "signup")
        await credit(connection, workspace_id, 55_000_000, 50_000_000, "tier")
        current = await read_balance(connection, workspace_id)
    assert current is not None
    assert current.balance_micro_usd == 155_000_000
    assert current.granted_micro_usd == 155_000_000
    assert current.charged_micro_usd == 50_000_000


async def test_a_refund_takes_balance_back_off(db: None) -> None:
    """A refund is a purchase with both amounts negative, so it is idempotent on its own reference
    like every other credit and needs no second table."""
    async with workspace_tx() as connection:
        workspace_id = await _workspace(connection)
        await credit(connection, workspace_id, 50_000_000, 50_000_000, "tier")
        assert await credit(connection, workspace_id, -50_000_000, -50_000_000, "refund/tier")
        assert not await credit(connection, workspace_id, -50_000_000, -50_000_000, "refund/tier")
        current = await read_balance(connection, workspace_id)
    assert current is not None
    assert current.balance_micro_usd == 0
    assert current.charged_micro_usd == 0


async def test_no_balance_row_reads_none_and_reports_absent(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id = await _workspace(connection)
        assert not balance_absent(workspace_id)
        assert await read_balance(connection, workspace_id) is None
        assert balance_absent(workspace_id)
        await credit(connection, workspace_id, 1_000_000, 1_000_000, "first")
    assert not balance_absent(workspace_id)


async def test_a_reserve_needs_a_balance_to_sit_on(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id = await _workspace(connection)
        assert not await set_reserve(connection, workspace_id, 2_000_000)
        await credit(connection, workspace_id, 10_000_000, 10_000_000, "first")
        assert await set_reserve(connection, workspace_id, 2_000_000)
        current = await read_balance(connection, workspace_id)
    assert current is not None
    assert current.reserve_micro_usd == 2_000_000


async def test_a_later_credit_leaves_the_reserve_alone(db: None) -> None:
    """The reserve is the headroom a turn needs to begin, not a share of what was bought, so topping
    up moves the balance and nothing else."""
    async with workspace_tx() as connection:
        workspace_id = await _workspace(connection)
        await credit(connection, workspace_id, 10_000_000, 10_000_000, "first")
        await set_reserve(connection, workspace_id, 2_000_000)
        await credit(connection, workspace_id, 10_000_000, 10_000_000, "second")
        current = await read_balance(connection, workspace_id)
    assert current is not None
    assert current.balance_micro_usd == 20_000_000
    assert current.reserve_micro_usd == 2_000_000


def test_the_billing_screen_url_is_the_home_surface_path_plus_the_billing_route() -> None:
    assert (
        billing_screen_url("https://ufo.example.com/", "web")
        == "https://ufo.example.com/surface/web#/workspace/billing"
    )


def test_a_deploy_missing_either_half_has_no_billing_screen() -> None:
    """A self-host or dev node has no public base, and a deploy installing no browser surface has no
    screen at all — either way there is nowhere to send an admin."""
    assert billing_screen_url(None, "web") is None
    assert billing_screen_url("", "web") is None
    assert billing_screen_url("https://ufo.example.com", None) is None


def test_the_refusal_sends_an_admin_to_the_billing_screen() -> None:
    assert balance_refusal_message("https://ufo.example.com/surface/web#/workspace/billing") == (
        "This workspace is out of credit. An admin can add credit at "
        "https://ufo.example.com/surface/web#/workspace/billing"
    )


def test_a_refusal_with_no_screen_names_the_act_and_trails_off_at_nothing() -> None:
    """The fallback every self-host deploy reads. A message built by appending a link would end at
    "at" here, so the sentence without a screen is written out rather than derived."""
    message = balance_refusal_message(None)
    assert message == "This workspace is out of credit. An admin can set up automatic refills."
    assert " at" not in message


async def _dated_credit(
    workspace_id: UUID, granted: int, charged: int, reference: str, day: int
) -> None:
    """Credit the balance and stamp the row a known day apart from its siblings.

    `credit` stamps `now()`, which is the transaction's clock at whatever resolution the driver
    keeps — one second on SQLite — so credits written in the same second are indistinguishable by
    time. Ordering is what these tests are about, so they set the instant rather than race a clock
    they do not control."""
    async with workspace_tx() as connection:
        await credit(connection, workspace_id, granted, charged, reference)
        await connection.execute(
            sa.update(tables.balance_purchase)
            .where(tables.balance_purchase.c.reference == reference)
            .values(created_at=datetime(2026, 8, day, tzinfo=UTC))
        )


async def test_the_purchase_list_is_newest_first_and_bounded(db: None) -> None:
    """The list behind the lifetime totals, for a screen stating where a balance came from. Newest
    first, because a member reading it is checking what just happened, and bounded at the query so a
    workspace that refills daily cannot hand a screen a year of rows."""
    async with workspace_tx() as connection:
        workspace_id = await _workspace(connection)
    for index in range(4):
        await _dated_credit(
            workspace_id, (index + 1) * 1_000_000, 0, f"credit-{index}", day=index + 1
        )
    async with workspace_tx() as connection:
        listed = await recent_purchases(connection, workspace_id, 3)
    assert [purchase.granted_micro_usd for purchase in listed] == [4_000_000, 3_000_000, 2_000_000]


async def test_a_grant_and_a_purchase_read_apart_in_the_list(db: None) -> None:
    """A grant adds without charging, so the two figures ride every row separately: money the
    workspace paid cannot be told from credit it was given by one amount alone."""
    async with workspace_tx() as connection:
        workspace_id = await _workspace(connection)
    await _dated_credit(workspace_id, 5_000_000, 5_000_000, "paid", day=1)
    await _dated_credit(workspace_id, 100_000_000, 0, "granted", day=2)
    async with workspace_tx() as connection:
        listed = await recent_purchases(connection, workspace_id, 10)
    assert [(row.granted_micro_usd, row.charged_micro_usd) for row in listed] == [
        (100_000_000, 0),
        (5_000_000, 5_000_000),
    ]


async def test_a_workspace_with_no_credits_lists_none(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id = await _workspace(connection)
        assert await recent_purchases(connection, workspace_id, 10) == ()
