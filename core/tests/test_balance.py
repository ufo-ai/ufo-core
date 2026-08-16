from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.accounting import ALLOW, PARK, SpendEvaluator
from ufo.balance import BALANCE_HELD_MESSAGE, balance_absent, credit, read_balance, set_reserve
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


async def test_a_balance_below_its_reserve_parks_new_work(db: None) -> None:
    """The gate `reserve_micro_usd` exists for: a workspace whose balance fell under its reserve
    holds work — parked, never rejected, so a credit resumes everything the hold parked."""
    agent_id = uuid4()
    async with workspace_tx() as connection:
        workspace_id = await _workspace(connection)
        await credit(connection, workspace_id, 1_000_000, 1_000_000, "first")
        await set_reserve(connection, workspace_id, 2_000_000)
        held = await SpendEvaluator(workspace_id, None, agent_id).decide(connection, 0)
        await credit(connection, workspace_id, 5_000_000, 5_000_000, "top-up")
        resumed = await SpendEvaluator(workspace_id, None, agent_id).decide(connection, 0)
    assert held.outcome == PARK
    assert held.message == BALANCE_HELD_MESSAGE
    assert resumed.outcome == ALLOW


async def test_decide_notes_an_absent_balance_for_the_fast_path(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id = await _workspace(connection)
        decision = await SpendEvaluator(workspace_id, None, uuid4()).decide(connection, 0)
    assert decision.outcome == ALLOW
    assert balance_absent(workspace_id)


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
