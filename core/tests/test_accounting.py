from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from selfhost.accounting import model_price, read_turn_cost, record_turn_usage, usage_priced_usd
from selfhost.db import workspace_tx
from selfhost.schema.records import Usage

FULL_USAGE = Usage(
    input_tokens=1000, output_tokens=2000, cache_read_tokens=3000, cache_write_tokens=4000
)


def test_priced_usd_matches_hand_math() -> None:
    usage = Usage(input_tokens=1000, output_tokens=2000)
    assert usage_priced_usd("claude-opus-4-8", usage) == Decimal("0.055")


def test_cache_tokens_are_priced() -> None:
    usage = Usage(cache_read_tokens=1_000_000, cache_write_tokens=1_000_000)
    assert usage_priced_usd("claude-opus-4-8", usage) == Decimal("6.75")


def test_openai_row_converted_from_usd_per_mtok() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert usage_priced_usd("gpt-5.4", usage) == Decimal("17.5")


def test_unknown_model_raises() -> None:
    with pytest.raises(KeyError, match="grok-9"):
        model_price("grok-9")


async def _seed_turn(connection: AsyncConnection) -> tuple[UUID, UUID]:
    workspace_id, member_id, agent_id, conversation_id, turn_id = (uuid4() for _ in range(5))
    await connection.execute(
        text("insert into workspace (id, created_at, updated_at) values (:id, now(), now())"),
        {"id": workspace_id},
    )
    await connection.execute(
        text(
            "insert into member (id, workspace_id, email, created_at, updated_at)"
            " values (:id, :ws, 'a@b.c', now(), now())"
        ),
        {"id": member_id, "ws": workspace_id},
    )
    await connection.execute(
        text(
            "insert into agent (id, workspace_id, name, prompt, model, created_at, updated_at)"
            " values (:id, :ws, 'assistant', 'p', 'claude-opus-4-8', now(), now())"
        ),
        {"id": agent_id, "ws": workspace_id},
    )
    await connection.execute(
        text(
            "insert into conversation"
            " (id, workspace_id, surface, queue_key, member_id, created_at, updated_at)"
            " values (:id, :ws, 'cli', 'session', :member, now(), now())"
        ),
        {"id": conversation_id, "ws": workspace_id, "member": member_id},
    )
    await connection.execute(
        text(
            "insert into turn (id, workspace_id, conversation_id, agent_id, seq, status,"
            " inbound, terminal, created_at, updated_at)"
            " values (:id, :ws, :conv, :agent, 1, 'queued', 'hi', null, now(), now())"
        ),
        {"id": turn_id, "ws": workspace_id, "conv": conversation_id, "agent": agent_id},
    )
    return workspace_id, turn_id


async def test_record_then_read_back(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
    async with workspace_tx() as connection:
        cost = await read_turn_cost(connection, turn_id)
    assert cost == (10_000, Decimal("0.0815"), "claude-opus-4-8")


async def test_replay_leaves_one_row(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
    async with workspace_tx() as connection:
        count = (
            await connection.execute(
                text("select count(*) from ledger where turn_id = :turn"), {"turn": turn_id}
            )
        ).scalar_one()
    assert count == 1


async def test_zero_usage_writes_nothing(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", Usage())
        assert await read_turn_cost(connection, turn_id) is None
