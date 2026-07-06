import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from selfhost import accounting
from selfhost.accounting import (
    SpendRollup,
    read_turn_cost,
    record_egress_request,
    record_sandbox_tokens,
    record_turn_usage,
    record_workspace_usage,
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


def test_price_digest_is_stable_sha256() -> None:
    assert accounting.PRICE_DIGEST.startswith("sha256:")
    assert len(accounting.PRICE_DIGEST) == len("sha256:") + 64
    assert accounting.price_digest() == accounting.PRICE_DIGEST


def test_price_digest_changes_when_price_table_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = accounting.price_digest()
    monkeypatch.setitem(
        accounting.MODEL_TOKEN_PRICE,
        "claude-opus-4-8",
        accounting.ModelPrice(1, 1, 1, 1),
    )
    assert accounting.price_digest() != baseline


def test_pricing_with_prices_a_contributed_model() -> None:
    contributed = {"vendor/model-x": accounting.ModelPrice(1_000_000, 2_000_000, 0, 0)}
    pricing = accounting.pricing_with(contributed)
    assert (
        pricing.micro_usd("vendor/model-x", Usage(input_tokens=1_000_000, output_tokens=1_000_000))
        == 3_000_000
    )
    assert pricing.micro_usd("claude-opus-4-8", Usage(input_tokens=1_000_000)) == 5_000_000
    assert pricing.digest != accounting.CORE_PRICING.digest


def test_core_pricing_is_the_core_table() -> None:
    assert accounting.CORE_PRICING.digest == accounting.PRICE_DIGEST
    assert (
        accounting.CORE_PRICING.micro_usd(
            "claude-opus-4-8", Usage(input_tokens=1000, output_tokens=2000)
        )
        == 55_000
    )
    assert accounting.CORE_PRICING.micro_usd("vendor/model-x", FULL_USAGE) == 0


async def test_unknown_model_records_tokens_at_zero_price(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "gpt-4o", FULL_USAGE)
    async with workspace_tx() as connection:
        cost = await read_turn_cost(connection, turn_id)
        stamped = (
            await connection.execute(
                sa.select(tables.ledger.c.price_digest).where(
                    tables.ledger.c.turn_id == turn_id,
                    tables.ledger.c.dimension == "tokens",
                )
            )
        ).scalar_one()
    assert cost == (10_000, 0, "gpt-4o")
    assert stamped == accounting.PRICE_DIGEST


def test_absent_caps_cache_evicts_expired_entries_when_full() -> None:
    accounting._no_applicable_caps.clear()
    try:
        expired = time.monotonic() - 1.0
        for _ in range(accounting.CAP_PRESENCE_CACHE_MAX):
            accounting._no_applicable_caps[(uuid4(), uuid4(), uuid4())] = expired
        accounting._note_absent_caps((uuid4(), None, uuid4()))
        assert len(accounting._no_applicable_caps) == 1
    finally:
        accounting._no_applicable_caps.clear()


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


async def test_ledger_insert_stamps_current_price_digest(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
    async with workspace_tx() as connection:
        stamped = (
            await connection.execute(
                sa.select(tables.ledger.c.price_digest).where(tables.ledger.c.turn_id == turn_id)
            )
        ).scalar_one()
    assert stamped == accounting.PRICE_DIGEST


async def test_egress_row_carries_no_price_digest(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_egress_request(connection, workspace_id, turn_id)
    async with workspace_tx() as connection:
        stamped = (
            await connection.execute(
                sa.select(tables.ledger.c.price_digest).where(tables.ledger.c.turn_id == turn_id)
            )
        ).scalar_one()
    assert stamped is None


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


async def test_egress_request_accumulates_a_priced_zero_count(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_egress_request(connection, workspace_id, turn_id)
        await record_egress_request(connection, workspace_id, turn_id)
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.dimension,
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                ).where(tables.ledger.c.turn_id == turn_id)
            )
        ).one()
    assert (row.dimension, int(row.amount), int(row.priced_micro_usd)) == ("egress", 2, 0)


async def test_egress_never_double_counts_the_token_cost(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
        await record_egress_request(connection, workspace_id, turn_id)
        await record_egress_request(connection, workspace_id, turn_id)
    async with workspace_tx() as connection:
        cost = await read_turn_cost(connection, turn_id)
    assert cost == (10_000, 81_500, "claude-opus-4-8")


async def test_sandbox_tokens_row_is_disjoint_from_the_host_token_row(db: None) -> None:
    """An in-sandbox model call metered under `sandbox_tokens` and the host loop's terminal `tokens`
    bill for one turn are two rows with distinct ids; read_turn_cost bills only `tokens`, so the
    sandbox meter is additive, never a double-count of the host burn."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
        await record_sandbox_tokens(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.id,
                    tables.ledger.c.dimension,
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.price_digest,
                )
                .where(tables.ledger.c.turn_id == turn_id)
                .order_by(tables.ledger.c.dimension)
            )
        ).all()
        cost = await read_turn_cost(connection, turn_id)
    assert {
        row.dimension: (int(row.amount), int(row.priced_micro_usd), row.price_digest)
        for row in rows
    } == {
        "sandbox_tokens": (10_000, 81_500, accounting.PRICE_DIGEST),
        "tokens": (10_000, 81_500, accounting.PRICE_DIGEST),
    }
    assert len({row.id for row in rows}) == 2
    assert cost == (10_000, 81_500, "claude-opus-4-8")


async def test_sandbox_tokens_accumulate_into_one_row(db: None) -> None:
    usage = Usage(input_tokens=1000, output_tokens=2000)
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_sandbox_tokens(connection, workspace_id, turn_id, "claude-opus-4-8", usage)
        await record_sandbox_tokens(connection, workspace_id, turn_id, "claude-opus-4-8", usage)
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.dimension,
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                ).where(tables.ledger.c.turn_id == turn_id)
            )
        ).one()
    assert (row.dimension, int(row.amount), int(row.priced_micro_usd)) == (
        "sandbox_tokens",
        6000,
        110_000,
    )


async def test_spend_rollup_surfaces_sandbox_tokens(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
        await record_sandbox_tokens(
            connection,
            workspace_id,
            turn_id,
            "claude-opus-4-8",
            Usage(input_tokens=1000, output_tokens=2000),
        )
        await record_egress_request(connection, workspace_id, turn_id)
    async with workspace_tx() as connection:
        report = await SpendRollup(workspace_id).read(connection, 3600)
    assert {d.dimension: (d.amount, d.priced_micro_usd) for d in report.by_dimension} == {
        "egress": (1, 0),
        "sandbox_tokens": (3000, 55_000),
        "tokens": (10_000, 81_500),
    }
    assert report.total_micro_usd == 81_500 + 55_000


async def test_spend_rollup_matches_ledger_sums(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
        await record_egress_request(connection, workspace_id, turn_id)
        await record_egress_request(connection, workspace_id, turn_id)
    async with workspace_tx() as connection:
        report = await SpendRollup(workspace_id).read(connection, 3600)
    assert report.total_micro_usd == 81_500
    assert {d.dimension: (d.amount, d.priced_micro_usd) for d in report.by_dimension} == {
        "egress": (2, 0),
        "tokens": (10_000, 81_500),
    }
    assert [(s.label, s.priced_micro_usd) for s in report.by_member] == [("a@b.c", 81_500)]
    assert [(s.label, s.priced_micro_usd) for s in report.by_agent] == [("assistant", 81_500)]
    assert [(p.price_digest, p.priced_micro_usd) for p in report.by_price_digest] == [
        (accounting.PRICE_DIGEST, 81_500)
    ]


async def test_workspace_usage_is_anchorless_priced_and_stamped(db: None) -> None:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await record_workspace_usage(connection, workspace_id, "claude-opus-4-8", FULL_USAGE)
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.turn_id,
                    tables.ledger.c.dimension,
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.model,
                    tables.ledger.c.price_digest,
                ).where(tables.ledger.c.workspace_id == workspace_id)
            )
        ).one()
    assert row.turn_id is None
    assert (row.dimension, int(row.amount), int(row.priced_micro_usd)) == ("tokens", 10_000, 81_500)
    assert row.model == "claude-opus-4-8"
    assert row.price_digest == accounting.PRICE_DIGEST


async def test_workspace_usage_counts_in_total_not_member_or_agent(db: None) -> None:
    """A background job's metered spend lands in the workspace total and the per-dimension token
    total, but is attributed to no member or agent — those breakdowns join through the turn a
    workspace-anchored row lacks."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
        await record_workspace_usage(connection, workspace_id, "claude-opus-4-8", FULL_USAGE)
    async with workspace_tx() as connection:
        report = await SpendRollup(workspace_id).read(connection, 3600)
    assert report.total_micro_usd == 81_500 * 2
    assert {d.dimension: (d.amount, d.priced_micro_usd) for d in report.by_dimension} == {
        "tokens": (20_000, 81_500 * 2)
    }
    assert [(s.label, s.priced_micro_usd) for s in report.by_member] == [("a@b.c", 81_500)]
    assert [(s.label, s.priced_micro_usd) for s in report.by_agent] == [("assistant", 81_500)]
    assert [(p.price_digest, p.priced_micro_usd) for p in report.by_price_digest] == [
        (accounting.PRICE_DIGEST, 81_500 * 2)
    ]


async def test_spend_rollup_excludes_ledger_outside_the_window(db: None) -> None:
    old = datetime.now(UTC) - timedelta(hours=2)
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await connection.execute(
            sa.insert(tables.ledger).values(
                id=uuid4(),
                workspace_id=workspace_id,
                turn_id=turn_id,
                dimension="tokens",
                amount=10,
                priced_micro_usd=100,
                model="claude-opus-4-8",
                created_at=old,
                updated_at=sa.func.now(),
            )
        )
    async with workspace_tx() as connection:
        report = await SpendRollup(workspace_id).read(connection, 3600)
    assert report.total_micro_usd == 0
    assert report.by_dimension == ()
    assert report.by_member == ()
