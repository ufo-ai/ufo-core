from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from selfhost.accounting import (
    read_turn_cost,
    record_turn_usage,
    usage_priced_micro_usd,
)
from selfhost.db import workspace_tx
from selfhost.schema import tables
from selfhost.schema.records import Usage

FULL_USAGE = Usage(
    input_tokens=1000, output_tokens=2000, cache_read_tokens=3000, cache_write_tokens=4000
)


def test_priced_micro_usd_matches_hand_math() -> None:
    usage = Usage(input_tokens=1000, output_tokens=2000)
    assert usage_priced_micro_usd("claude-opus-4-8", usage) == 55_000


def test_cache_tokens_are_priced() -> None:
    usage = Usage(cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
    assert usage_priced_micro_usd("claude-opus-4-8", usage) == 6_750_000


def test_sub_micro_usd_floors() -> None:
    assert usage_priced_micro_usd("claude-haiku-4-5", Usage(cache_read_tokens=9)) == 0


def test_openai_row_converted_from_usd_per_mtok() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert usage_priced_micro_usd("gpt-5.4", usage) == 17_500_000


def test_unknown_model_prices_zero_never_raises() -> None:
    assert usage_priced_micro_usd("gpt-4o", FULL_USAGE) == 0


async def test_unknown_model_records_tokens_at_zero_price(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "gpt-4o", FULL_USAGE)
    async with workspace_tx() as connection:
        cost = await read_turn_cost(connection, turn_id)
    assert cost == (10_000, 0, "gpt-4o")


async def _seed_turn(connection: AsyncConnection) -> tuple[UUID, UUID]:
    workspace_id, member_id, agent_id, conversation_id, turn_id = (uuid4() for _ in range(5))
    await connection.execute(
        sa.insert(tables.workspace).values(
            id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
        )
    )
    await connection.execute(
        sa.insert(tables.member).values(
            id=member_id,
            workspace_id=workspace_id,
            email="a@b.c",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    await connection.execute(
        sa.insert(tables.agent).values(
            id=agent_id,
            workspace_id=workspace_id,
            name="assistant",
            prompt="p",
            model="claude-opus-4-8",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    await connection.execute(
        sa.insert(tables.conversation).values(
            id=conversation_id,
            workspace_id=workspace_id,
            surface="cli",
            queue_key="session",
            member_id=member_id,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    await connection.execute(
        sa.insert(tables.turn).values(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="queued",
            inbound="hi",
            terminal=None,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return workspace_id, turn_id


async def test_record_then_read_back(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
    async with workspace_tx() as connection:
        cost = await read_turn_cost(connection, turn_id)
    assert cost == (10_000, 81_500, "claude-opus-4-8")


async def test_replay_leaves_one_row(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
    async with workspace_tx() as connection:
        count = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.ledger)
                .where(tables.ledger.c.turn_id == turn_id)
            )
        ).scalar_one()
    assert count == 1


async def test_zero_usage_writes_nothing(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", Usage())
        assert await read_turn_cost(connection, turn_id) is None
