import time
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.db import workspace_tx
from ufo.harness.models.catalog import CORE_MODEL_SPECS, CORE_PRICES, CORE_PRICING, PRICE_DIGEST
from ufo.harness.models.interface import PROVIDER_ANTHROPIC, ModelClient
from ufo.harness.models.pricing import (
    TOKENS_PER_MTOK,
    ModelPrice,
    price_digest,
    pricing_from,
    usage_priced_micro_usd,
)
from ufo.harness.models.registry import ModelRegistry
from ufo.runtime import queue as loop_queue
from ufo.runtime.billing import accounting
from ufo.runtime.billing.accounting import (
    IMAGES_DIMENSION,
    MEMBER_SCOPE,
    SANDBOX_TOKENS_ATTEMPT,
    SANDBOX_TOKENS_DIMENSION,
    TOKENS_DIMENSION,
    SpendRollup,
    TurnCost,
    read_turn_cost,
    record_egress_request,
    record_image_usage,
    record_sandbox_tokens,
    record_turn_usage,
    record_video_usage,
    record_workspace_usage,
)
from ufo.runtime.billing.balance import credit
from ufo.runtime.workspace import (
    KEY_FUNDED,
    PLAN_FUNDED,
    Funding,
    ModelFundingChanged,
    ResolvedModelClient,
)
from ufo.schema import tables
from ufo.schema.records import Usage, ledger_id_for

FULL_USAGE = Usage(
    input_tokens=1000,
    output_tokens=2000,
    cache_read_tokens=3000,
    cache_write_1h_tokens=4000,
)
OPENAI_FULL_USAGE = Usage(
    input_tokens=1000,
    output_tokens=2000,
    cache_read_tokens=3000,
    cache_write_30m_tokens=4000,
)


def test_priced_micro_usd_matches_hand_math() -> None:
    usage = Usage(input_tokens=1000, output_tokens=2000)
    assert CORE_PRICING.micro_usd("claude-opus-4-8", usage) == 55_000


def test_cache_tokens_are_priced() -> None:
    usage = Usage(
        cache_read_tokens=1_000_000,
        cache_write_5m_tokens=1_000_000,
        cache_write_1h_tokens=1_000_000,
    )
    assert CORE_PRICING.micro_usd("claude-opus-4-8", usage) == 16_750_000


def test_anthropic_cache_writes_are_priced_at_their_ttl_rates() -> None:
    anthropic_specs = [s for s in CORE_MODEL_SPECS if s.provider == PROVIDER_ANTHROPIC]
    assert anthropic_specs
    for spec in anthropic_specs:
        write_5m = Usage(cache_write_5m_tokens=TOKENS_PER_MTOK)
        write_1h = Usage(cache_write_1h_tokens=TOKENS_PER_MTOK)
        read = Usage(cache_read_tokens=TOKENS_PER_MTOK)
        assert usage_priced_micro_usd(spec.id, write_5m, CORE_PRICES) == spec.price.input * 5 // 4
        assert usage_priced_micro_usd(spec.id, write_1h, CORE_PRICES) == spec.price.input * 2
        assert usage_priced_micro_usd(spec.id, read, CORE_PRICES) == spec.price.input // 10


def test_sub_micro_usd_floors() -> None:
    assert CORE_PRICING.micro_usd("claude-haiku-4-5", Usage(cache_read_tokens=9)) == 0


def test_price_digest_changes_when_price_table_changes() -> None:
    changed = {**CORE_PRICES, "claude-opus-4-8": ModelPrice(1, 1, 1, 1, 1)}
    assert price_digest(changed) != PRICE_DIGEST


def test_pricing_from_prices_a_contributed_model() -> None:
    contributed = {"vendor/model-x": ModelPrice(1_000_000, 2_000_000, 0, 0, 0)}
    pricing = pricing_from({**CORE_PRICES, **contributed})
    assert (
        pricing.micro_usd("vendor/model-x", Usage(input_tokens=1_000_000, output_tokens=1_000_000))
        == 3_000_000
    )
    assert pricing.micro_usd("claude-opus-4-8", Usage(input_tokens=1_000_000)) == 5_000_000
    assert pricing.digest != CORE_PRICING.digest


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_unknown_model_records_tokens_at_zero_price(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "gpt-4o", FULL_USAGE)
    async with workspace_tx() as connection:
        cost = await read_turn_cost(connection, turn_id, TOKENS_DIMENSION)
        stamped = (
            await connection.execute(
                sa.select(tables.ledger.c.price_digest).where(
                    tables.ledger.c.turn_id == turn_id,
                    tables.ledger.c.dimension == "tokens",
                )
            )
        ).scalar_one()
    assert cost == TurnCost(tokens=10_000, micro_usd=0, model="gpt-4o", cache_percent=38)
    assert stamped == PRICE_DIGEST


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
            agent_id=agent_id,
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
            speaker_member_id=member_id,
            terminal=None,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return workspace_id, turn_id


async def _seed_workspace_turn(
    connection: AsyncConnection, *, spoken: bool
) -> tuple[UUID, UUID, UUID]:
    """A turn in a workspace-shared conversation bound to no member: spoken by the one member when
    `spoken`, speakerless otherwise."""
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
            agent_id=agent_id,
            surface="web",
            queue_key="chat",
            member_id=None,
            audience="shared",
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
            speaker_member_id=member_id if spoken else None,
            terminal=None,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return workspace_id, member_id, turn_id


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
@pytest.mark.parametrize("spoken", [True, False])
async def test_member_usage_follows_the_turns_member_not_the_conversations(
    db: None, spoken: bool
) -> None:
    """A member's turn in a workspace conversation is their usage — in the workspace rollup's
    per-member line and in their own window — while a speakerless turn there is nobody's."""
    async with workspace_tx() as connection:
        workspace_id, member_id, turn_id = await _seed_workspace_turn(connection, spoken=spoken)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
    async with workspace_tx() as connection:
        report = await SpendRollup(workspace_id).read(connection, 3600)
        own = await SpendRollup(workspace_id).read_member(connection, member_id, 3600)
    assert report.total_micro_usd == 96_500
    assert [(s.label, s.priced_micro_usd) for s in report.by_member] == (
        [("a@b.c", 96_500)] if spoken else []
    )
    assert own.total_micro_usd == (96_500 if spoken else 0)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_record_then_read_back(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
    async with workspace_tx() as connection:
        cost = await read_turn_cost(connection, turn_id, TOKENS_DIMENSION)
        ledger = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.input_tokens,
                    tables.ledger.c.output_tokens,
                    tables.ledger.c.cache_read_tokens,
                    tables.ledger.c.cache_write_5m_tokens,
                    tables.ledger.c.cache_write_30m_tokens,
                    tables.ledger.c.cache_write_1h_tokens,
                    tables.ledger.c.byok,
                ).where(tables.ledger.c.turn_id == turn_id)
            )
        ).one()
    assert cost == TurnCost(
        tokens=10_000, micro_usd=96_500, model="claude-opus-4-8", cache_percent=38
    )
    assert tuple(ledger) == (1000, 2000, 3000, 0, 0, 4000, False)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_ledger_freezes_the_serving_byok_decision(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(
            connection,
            workspace_id,
            turn_id,
            "claude-opus-4-8",
            Usage(input_tokens=100),
            byok=True,
        )
        assert (
            await connection.execute(
                sa.select(tables.ledger.c.byok).where(tables.ledger.c.turn_id == turn_id)
            )
        ).scalar_one()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_parked_then_resumed_turn_reads_back_as_one_spend(db: None) -> None:
    """A turn parked mid-run and resumed bills each partial burn under its own attempt, so the read
    totals every field across those rows: tokens, cost and both halves of the prompt split sum, and
    the cache share is the cached part of the whole prompt rather than of whichever attempt ran
    last."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        for attempt, usage in (
            (
                "parked-run",
                Usage(
                    input_tokens=300,
                    output_tokens=100,
                    cache_read_tokens=600,
                    cache_write_1h_tokens=100,
                ),
            ),
            ("resumed-run", Usage(input_tokens=200, output_tokens=100, cache_read_tokens=800)),
        ):
            await record_turn_usage(
                connection,
                workspace_id,
                turn_id,
                "claude-opus-4-8",
                usage,
                attempt,
            )
    async with workspace_tx() as connection:
        billed = sa.select(
            sa.func.sum(tables.ledger.c.amount),
            sa.func.sum(tables.ledger.c.priced_micro_usd),
            sa.func.sum(tables.ledger.c.prompt_tokens),
            sa.func.sum(tables.ledger.c.cache_read_tokens),
        ).where(
            (tables.ledger.c.turn_id == turn_id) & (tables.ledger.c.dimension == TOKENS_DIMENSION)
        )
        totals = (await connection.execute(billed)).one()
        ids = set(
            (
                await connection.execute(
                    sa.select(tables.ledger.c.id).where(tables.ledger.c.turn_id == turn_id)
                )
            ).scalars()
        )
        cost = await read_turn_cost(connection, turn_id, TOKENS_DIMENSION)
    assert ids == {
        ledger_id_for(workspace_id, turn_id, TOKENS_DIMENSION, attempt)
        for attempt in ("parked-run", "resumed-run")
    }
    assert tuple(int(total) for total in totals) == (2_200, 9_200, 2_000, 1_400)
    assert cost == TurnCost(
        tokens=2_200, micro_usd=9_200, model="claude-opus-4-8", cache_percent=70
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    assert stamped == PRICE_DIGEST


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_egress_request_accumulates_a_priced_zero_count(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_egress_request(connection, workspace_id, turn_id)
        await record_egress_request(connection, workspace_id, turn_id)
        await record_egress_request(connection, workspace_id, turn_id, amount=8)
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
    assert (row.dimension, int(row.amount), int(row.priced_micro_usd)) == ("egress", 10, 0)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_sandbox_tokens_row_is_disjoint_from_the_host_token_row(db: None) -> None:
    """An in-sandbox model call metered under `sandbox_tokens` and the host loop's terminal `tokens`
    bill for one turn are two rows with distinct ids, and a read that names `tokens` sees only the
    host row, so the sandbox meter is additive, never a double-count of the host burn."""
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
        cost = await read_turn_cost(connection, turn_id, TOKENS_DIMENSION)
    assert {
        row.dimension: (int(row.amount), int(row.priced_micro_usd), row.price_digest)
        for row in rows
    } == {
        "sandbox_tokens": (10_000, 96_500, PRICE_DIGEST),
        "tokens": (10_000, 96_500, PRICE_DIGEST),
    }
    assert len({row.id for row in rows}) == 2
    assert cost == TurnCost(
        tokens=10_000, micro_usd=96_500, model="claude-opus-4-8", cache_percent=38
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_sandbox_rows_from_both_proxy_shapes_roll_up_and_export_once(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_sandbox_tokens(
            connection,
            workspace_id,
            turn_id,
            "claude-opus-4-8",
            Usage(input_tokens=100),
        )
        old_id = ledger_id_for(workspace_id, turn_id, SANDBOX_TOKENS_DIMENSION)
        await connection.execute(
            sa.insert(tables.ledger).values(
                id=old_id,
                workspace_id=workspace_id,
                turn_id=turn_id,
                dimension=SANDBOX_TOKENS_DIMENSION,
                amount=50,
                prompt_tokens=50,
                priced_micro_usd=0,
                debited_micro_usd=0,
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.update(tables.ledger)
            .where(tables.ledger.c.id == old_id)
            .values(
                amount=tables.ledger.c.amount + 25,
                prompt_tokens=tables.ledger.c.prompt_tokens + 25,
            )
        )
        rows = (
            await connection.execute(
                sa.select(tables.ledger.c.id, tables.ledger.c.amount).where(
                    tables.ledger.c.turn_id == turn_id,
                    tables.ledger.c.dimension == SANDBOX_TOKENS_DIMENSION,
                )
            )
        ).all()
        report = await SpendRollup(workspace_id).read(connection, None)
        await _settle_turn(connection, turn_id, age_seconds=PAST_EXPORT_MARGIN_SECONDS)
        await accounting.mint_usage_exports(
            connection,
            workspace_id,
            CONSUMER,
            datetime.now(UTC) - timedelta(days=7),
            lambda model: None,
        )
        exports = await accounting.read_pending_usage_exports(
            connection, workspace_id, CONSUMER, 100
        )
    assert {row.id for row in rows} == {
        ledger_id_for(workspace_id, turn_id, SANDBOX_TOKENS_DIMENSION, SANDBOX_TOKENS_ATTEMPT),
        old_id,
    }
    assert sum(int(row.amount) for row in rows) == 175
    sandbox_total = next(
        total for total in report.by_dimension if total.dimension == SANDBOX_TOKENS_DIMENSION
    )
    assert sandbox_total.amount == 175
    assert sorted(export.amount for export in exports) == [75, 100]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_sandbox_tokens_priced_and_stamped_by_the_merged_pricing(db: None) -> None:
    """An in-sandbox call on a contributed slug is priced against the deploy's merged table and
    stamped with its digest — never the core rate (which lacks the slug → $0) or the core digest —
    so it bills at the real rate and reconciles with the turn path's rows by digest."""
    pricing = pricing_from(
        {**CORE_PRICES, "vendor/model-x": ModelPrice(1_000_000, 2_000_000, 0, 0, 0)}
    )
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_sandbox_tokens(
            connection, workspace_id, turn_id, "vendor/model-x", usage, pricing=pricing
        )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.price_digest,
                    tables.ledger.c.model,
                ).where(tables.ledger.c.turn_id == turn_id)
            )
        ).one()
    assert int(row.priced_micro_usd) == 3_000_000
    assert row.price_digest == pricing.digest
    assert row.price_digest != PRICE_DIGEST
    assert row.model == "vendor/model-x"


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_generated_images_accumulate_into_one_unstamped_row(db: None) -> None:
    """Image spend is a count and a charge, not a token burn: the row carries the image count as its
    amount, the provider's charge as its price, no price digest (no pinned rate table describes an
    image model), and a second generation on the same turn accumulates into the same row."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await connection.execute(
            sa.update(tables.turn).where(tables.turn.c.id == turn_id).values(byok=True)
        )
        await record_image_usage(
            connection, workspace_id, turn_id, "bytedance-seed/seedream-4.5", 2, 80_000
        )
        await record_image_usage(
            connection, workspace_id, turn_id, "bytedance-seed/seedream-4.5", 1, 40_000
        )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.dimension,
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.model,
                    tables.ledger.c.price_digest,
                ).where(tables.ledger.c.turn_id == turn_id)
            )
        ).one()
    assert (row.dimension, int(row.amount), int(row.priced_micro_usd)) == ("images", 3, 120_000)
    assert row.model == "bytedance-seed/seedream-4.5"
    assert row.price_digest is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_images_are_disjoint_from_the_turns_token_bill(db: None) -> None:
    """A turn that generated an image and burned tokens has two rows with distinct ids, and a cost
    read that names `tokens` sees only the token row — the image charge is additive, never a
    re-billing of the model round that called the tool."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
        await record_image_usage(
            connection, workspace_id, turn_id, "openai/gpt-image-2", 1, 130_000
        )
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.ledger.c.id, tables.ledger.c.dimension).where(
                    tables.ledger.c.turn_id == turn_id
                )
            )
        ).all()
        tokens = await read_turn_cost(connection, turn_id, TOKENS_DIMENSION)
        images = await read_turn_cost(connection, turn_id, IMAGES_DIMENSION)
    assert len({row.id for row in rows}) == 2
    assert {row.dimension for row in rows} == {"tokens", "images"}
    assert tokens == TurnCost(
        tokens=10_000, micro_usd=96_500, model="claude-opus-4-8", cache_percent=38
    )
    assert images == TurnCost(
        tokens=1, micro_usd=130_000, model="openai/gpt-image-2", cache_percent=0
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_spend_rollup_matches_ledger_sums(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
        await record_egress_request(connection, workspace_id, turn_id)
        await record_egress_request(connection, workspace_id, turn_id)
    async with workspace_tx() as connection:
        report = await SpendRollup(workspace_id).read(connection, 3600)
    assert report.total_micro_usd == 96_500
    assert {d.dimension: (d.amount, d.priced_micro_usd) for d in report.by_dimension} == {
        "egress": (2, 0),
        "tokens": (10_000, 96_500),
    }
    assert [(s.label, s.priced_micro_usd) for s in report.by_member] == [("a@b.c", 96_500)]
    assert [(s.label, s.priced_micro_usd) for s in report.by_agent] == [("assistant", 96_500)]
    assert [(p.price_digest, p.priced_micro_usd) for p in report.by_price_digest] == [
        (PRICE_DIGEST, 96_500)
    ]


async def _spawn_child_turn(
    connection: AsyncConnection, workspace_id: UUID, parent_turn_id: UUID
) -> UUID:
    parent = (
        await connection.execute(
            sa.select(
                tables.turn.c.agent_id,
                tables.turn.c.conversation_id,
                tables.conversation.c.member_id,
            )
            .select_from(tables.turn.join(tables.conversation))
            .where(tables.turn.c.id == parent_turn_id)
        )
    ).one()
    child_conversation, child_turn = uuid4(), uuid4()
    await connection.execute(
        sa.insert(tables.conversation).values(
            id=child_conversation,
            workspace_id=workspace_id,
            agent_id=parent.agent_id,
            surface="subagent",
            queue_key=str(child_turn),
            member_id=parent.member_id,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    await connection.execute(
        sa.insert(tables.turn).values(
            id=child_turn,
            workspace_id=workspace_id,
            conversation_id=child_conversation,
            agent_id=parent.agent_id,
            seq=1,
            status="queued",
            inbound="research",
            terminal=None,
            parent_turn_id=parent_turn_id,
            subagent_profile="research",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return child_turn


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_spend_by_origin_gathers_a_subagents_burn_under_the_channel(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await connection.execute(
            sa.update(tables.conversation)
            .where(tables.conversation.c.workspace_id == workspace_id)
            .values(surface="slack", surface_label="#eng")
        )
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
        child_turn = await _spawn_child_turn(connection, workspace_id, turn_id)
        grandchild = await _spawn_child_turn(connection, workspace_id, child_turn)
        await record_turn_usage(connection, workspace_id, child_turn, "claude-opus-4-8", FULL_USAGE)
        await record_turn_usage(connection, workspace_id, grandchild, "claude-opus-4-8", FULL_USAGE)
    async with workspace_tx() as connection:
        report = await SpendRollup(workspace_id).read(connection, 3600)
    assert [(o.surface, o.label, o.tokens, o.priced_micro_usd) for o in report.by_origin] == [
        ("slack", "#eng", 30_000, 289_500)
    ]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_spend_by_origin_names_an_unlabelled_surface_and_a_turnless_job(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
        await record_workspace_usage(
            connection, workspace_id, "claude-opus-4-8", Usage(input_tokens=1000)
        )
    async with workspace_tx() as connection:
        report = await SpendRollup(workspace_id).read(connection, 3600)
    assert [(o.surface, o.label) for o in report.by_origin] == [
        ("cli", "cli"),
        (None, "Workspace jobs"),
    ]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_spend_by_origin_totals_every_row_of_a_turn_and_of_the_jobs(db: None) -> None:
    """The ledger folds to one row per turn before the origin climb, so a turn billed under two
    attempts counts once and every turn-less job row lands in the one `Workspace jobs` total."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await connection.execute(
            sa.update(tables.conversation)
            .where(tables.conversation.c.workspace_id == workspace_id)
            .values(surface="slack", surface_label="#eng")
        )
        for attempt in ("parked-run", "resumed-run"):
            await record_turn_usage(
                connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE, attempt
            )
        child_turn = await _spawn_child_turn(connection, workspace_id, turn_id)
        await record_turn_usage(connection, workspace_id, child_turn, "claude-opus-4-8", FULL_USAGE)
        for _ in range(2):
            await record_workspace_usage(connection, workspace_id, "claude-opus-4-8", FULL_USAGE)
    async with workspace_tx() as connection:
        report = await SpendRollup(workspace_id).read(connection, 3600)
    assert [(o.surface, o.label, o.tokens, o.priced_micro_usd) for o in report.by_origin] == [
        ("slack", "#eng", 30_000, 289_500),
        (None, "Workspace jobs", 20_000, 193_000),
    ]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_usage_details_report_history_models_execution_and_all_time(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
        child_turn = await _spawn_child_turn(connection, workspace_id, turn_id)
        await record_turn_usage(
            connection, workspace_id, child_turn, "gpt-5.6-terra", OPENAI_FULL_USAGE
        )
        await connection.execute(
            sa.insert(tables.ledger).values(
                id=uuid4(),
                workspace_id=workspace_id,
                turn_id=turn_id,
                dimension="tokens",
                amount=7,
                prompt_tokens=7,
                input_tokens=7,
                priced_micro_usd=100,
                model="claude-opus-4-8",
                created_at=datetime.now(UTC) - timedelta(minutes=90),
                updated_at=sa.func.now(),
            )
        )
    async with workspace_tx() as connection:
        report = await SpendRollup(workspace_id).read(connection, 3600)
    assert report.usage.selected.tokens == 20_000
    assert report.usage.selected.token_micro_usd == 96_500 + 36_600
    assert report.usage.all_time.tokens == 20_007
    assert report.usage.all_time.total_micro_usd == 96_500 + 36_600 + 100
    assert report.usage.previous_tokens == 7
    assert report.usage.first_used_at is not None
    assert sum(day.tokens for day in report.usage.daily) == 20_000
    assert [(row.label, row.tokens) for row in report.usage.by_execution] == [
        ("", 10_000),
        ("research", 10_000),
    ]
    assert [(row.label, row.tokens) for row in report.usage.by_model] == [
        ("claude-opus-4-8", 10_000),
        ("gpt-5.6-terra", 10_000),
    ]


@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
async def test_spend_rollup_reads_each_report_in_a_bounded_statement_count(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        member_id = (
            await connection.execute(
                sa.select(tables.conversation.c.member_id).where(
                    tables.conversation.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
    async with workspace_tx() as connection:
        statements: list[str] = []

        def record(
            sync_connection: sa.Connection,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            if statement.lstrip().startswith(("SELECT", "WITH")):
                statements.append(statement)

        sa.event.listen(connection.sync_connection, "before_cursor_execute", record)
        try:
            workspace = await SpendRollup(workspace_id).read(connection, 3600)
            workspace_queries = tuple(statements)
            workspace_statements = len(statements)
            statements.clear()
            member = await SpendRollup(workspace_id).read_member(connection, member_id, 3600)
            member_queries = tuple(statements)
            member_statements = len(statements)
            statements.clear()
            all_time = await SpendRollup(workspace_id).read(connection, None)
            all_time_statements = len(statements)
        finally:
            sa.event.remove(connection.sync_connection, "before_cursor_execute", record)
    assert workspace_statements == 5
    assert member_statements == 3
    assert all_time_statements == 4
    workspace_rollup = next(query for query in workspace_queries if " AS period" in query)
    workspace_rollup = " ".join(workspace_rollup.split())
    workspace_scope = workspace_rollup.rsplit(" WHERE ", 1)[1].split(" GROUP BY ", 1)[0]
    member_rollup = next(query for query in member_queries if " AS period" in query)
    member_rollup = " ".join(member_rollup.split())
    member_scope = member_rollup.rsplit(" WHERE ", 1)[1].split(" GROUP BY ", 1)[0]
    assert "ledger.created_at >=" in workspace_scope
    assert "ledger.created_at >=" in member_scope
    assert workspace.usage == member.usage
    assert workspace.usage.selected.tokens == 10_000
    assert workspace.usage.by_execution == (accounting.UsageBreakdown("", 10_000, 96_500),)
    assert all_time.usage.previous_tokens is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    assert report.total_micro_usd == 96_500 * 2
    assert {d.dimension: (d.amount, d.priced_micro_usd) for d in report.by_dimension} == {
        "tokens": (20_000, 96_500 * 2)
    }
    assert [(s.label, s.priced_micro_usd) for s in report.by_member] == [("a@b.c", 96_500)]
    assert [(s.label, s.priced_micro_usd) for s in report.by_agent] == [
        ("assistant", 96_500),
        ("Workspace jobs", 96_500),
    ]
    assert [(p.price_digest, p.priced_micro_usd) for p in report.by_price_digest] == [
        (PRICE_DIGEST, 96_500 * 2)
    ]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
                prompt_tokens=10,
                input_tokens=10,
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_member_spend_reads_only_that_members_turns_and_caps(db: None) -> None:
    """A member's own slice sums the ledger rows their conversations' turns wrote — the same
    ledger→turn→conversation join a `member` cap binds on — so the number a member reads and the
    number their cap is measured against are one truth. Another member's turn, a memberless
    subagent conversation's turn, and another member's cap all stay out."""
    async with workspace_tx() as connection:
        workspace_id, mine = await _seed_turn(connection)
        member_id = (
            await connection.execute(
                sa.select(tables.conversation.c.member_id).where(
                    tables.conversation.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
        agent_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == workspace_id)
            )
        ).scalar_one()
        stranger_id, stranger_conversation, theirs = uuid4(), uuid4(), uuid4()
        await connection.execute(
            sa.insert(tables.member).values(
                id=stranger_id,
                workspace_id=workspace_id,
                email="n@b.c",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        subagent_conversation, subagent_turn = uuid4(), uuid4()
        for conversation_id, turn_id, owner in (
            (stranger_conversation, theirs, stranger_id),
            (subagent_conversation, subagent_turn, None),
        ):
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    surface="cli",
                    queue_key=uuid4().hex,
                    member_id=owner,
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
        await record_turn_usage(connection, workspace_id, mine, "claude-opus-4-8", FULL_USAGE)
        await record_egress_request(connection, workspace_id, mine)
        await record_turn_usage(connection, workspace_id, theirs, "claude-opus-4-8", FULL_USAGE)
        await record_turn_usage(
            connection,
            workspace_id,
            subagent_turn,
            "claude-opus-4-8",
            FULL_USAGE,
        )
        await record_workspace_usage(connection, workspace_id, "claude-opus-4-8", FULL_USAGE)
        for subject_id, limit in ((member_id, 5_000_000), (stranger_id, 9_000_000)):
            await connection.execute(
                sa.insert(tables.spend_cap).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    scope=MEMBER_SCOPE,
                    subject_id=subject_id,
                    window_seconds=3600,
                    limit_micro_usd=limit,
                    on_breach="park",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    async with workspace_tx() as connection:
        report = await SpendRollup(workspace_id).read_member(connection, member_id, 3600)
        rollup = await SpendRollup(workspace_id).read(connection, 3600)
    assert {d.dimension: (d.amount, d.priced_micro_usd) for d in report.by_dimension} == {
        "egress": (1, 0),
        "tokens": (10_000, 96_500),
    }
    assert report.total_micro_usd == 96_500
    assert report.window_seconds == 3600
    assert [(c.window_seconds, c.limit_micro_usd, c.on_breach) for c in report.caps] == [
        (3600, 5_000_000, "park")
    ]
    assert rollup.total_micro_usd == 96_500 * 4


CONSUMER = "metronome"
PAST_EXPORT_MARGIN_SECONDS = accounting.EXPORT_SETTLE_MARGIN_SECONDS + 100
QUERY_PLAN_HISTORY_ROWS = 5_000
QUERY_PLAN_MAX_MS = 10.0


async def _settle_turn(connection: AsyncConnection, turn_id: UUID, age_seconds: int) -> None:
    await connection.execute(
        sa.update(tables.turn)
        .where(tables.turn.c.id == turn_id)
        .values(
            status="done",
            terminal={"status": "done", "text": "ok"},
            updated_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
        )
    )


async def _pending(
    workspace_id: UUID, consumer: str = CONSUMER
) -> tuple[accounting.UsageExport, ...]:
    floor = datetime.now(UTC) - timedelta(days=7)
    async with workspace_tx() as connection:
        await accounting.mint_usage_exports(
            connection, workspace_id, consumer, floor, lambda model: None
        )
        return await accounting.read_pending_usage_exports(connection, workspace_id, consumer, 100)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_usage_export_settlement_rules(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await connection.execute(
            sa.update(tables.turn).where(tables.turn.c.id == turn_id).values(byok=True)
        )
        await record_turn_usage(
            connection,
            workspace_id,
            turn_id,
            "claude-opus-4-8",
            Usage(input_tokens=1000),
        )
        await record_workspace_usage(
            connection, workspace_id, "claude-opus-4-8", Usage(input_tokens=250)
        )
        await record_sandbox_tokens(
            connection, workspace_id, turn_id, "claude-opus-4-8", Usage(input_tokens=175)
        )
        await record_egress_request(connection, workspace_id, turn_id)

    settled = await _pending(workspace_id)
    assert {export.dimension for export in settled} == {"tokens"}
    assert all(export.from_amount == 0 and export.price_digest for export in settled)
    assert {export.amount for export in settled} == {1000, 250}

    async with workspace_tx() as connection:
        await _settle_turn(connection, turn_id, age_seconds=0)
    assert {export.dimension for export in await _pending(workspace_id)} == {"tokens"}

    async with workspace_tx() as connection:
        await _settle_turn(connection, turn_id, age_seconds=PAST_EXPORT_MARGIN_SECONDS)
    settled = await _pending(workspace_id)
    assert {export.dimension for export in settled} == {"tokens", "sandbox_tokens"}
    sandbox = next(e for e in settled if e.dimension == "sandbox_tokens")
    assert (sandbox.amount, sandbox.from_amount, sandbox.turn_id) == (175, 0, turn_id)
    assert sandbox.byok is False
    assert not any(export.dimension == "egress" for export in settled)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_images_export_with_their_settled_turn_as_platform_served(db: None) -> None:
    """An `images` row accumulates while its turn runs, so it settles like `sandbox_tokens`: nothing
    mints until the turn is terminal and past the margin. It exports as platform-served, because the
    `byok` label resolves a key slot through the model registry and an image model is not in it."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await connection.execute(
            sa.update(tables.turn).where(tables.turn.c.id == turn_id).values(byok=True)
        )
        await record_image_usage(
            connection, workspace_id, turn_id, "bytedance-seed/seedream-4.5", 2, 80_000
        )
    assert await _pending(workspace_id) == ()

    async with workspace_tx() as connection:
        await _settle_turn(connection, turn_id, age_seconds=PAST_EXPORT_MARGIN_SECONDS)
    (export,) = await _pending(workspace_id)
    assert (export.dimension, export.amount, export.from_amount) == ("images", 2, 0)
    assert (export.priced_micro_usd, export.model, export.turn_id) == (
        80_000,
        "bytedance-seed/seedream-4.5",
        turn_id,
    )
    assert export.byok is False
    assert export.price_digest is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_usage_export_uses_turn_byok_when_ledger_byok_is_null(
    db: None,
) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await connection.execute(
            sa.update(tables.turn).where(tables.turn.c.id == turn_id).values(byok=True)
        )
        await connection.execute(
            sa.insert(tables.ledger).values(
                id=uuid4(),
                workspace_id=workspace_id,
                turn_id=turn_id,
                dimension=TOKENS_DIMENSION,
                amount=100,
                prompt_tokens=100,
                priced_micro_usd=500,
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    exports = await _pending(workspace_id)
    assert len(exports) == 1
    assert exports[0].byok is True


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_usage_export_classifies_background_byok_from_the_stored_key(
    db: None,
) -> None:
    async with workspace_tx() as connection:
        workspace_id, _ = await _seed_turn(connection)
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot="anthropic_api_key",
                ciphertext=b"secret",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.ledger).values(
                id=uuid4(),
                workspace_id=workspace_id,
                turn_id=None,
                dimension=TOKENS_DIMENSION,
                amount=100,
                prompt_tokens=100,
                priced_micro_usd=500,
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await accounting.mint_usage_exports(
            connection,
            workspace_id,
            CONSUMER,
            datetime.now(UTC) - timedelta(days=7),
            lambda model: "anthropic_api_key" if model == "claude-opus-4-8" else None,
        )
        exported = (
            await connection.execute(
                sa.select(tables.ledger_export.c.byok).where(
                    tables.ledger_export.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
    assert exported is True


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_usage_export_growth_mints_frozen_top_ups(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_sandbox_tokens(
            connection, workspace_id, turn_id, "claude-opus-4-8", Usage(input_tokens=175)
        )
        await _settle_turn(connection, turn_id, age_seconds=PAST_EXPORT_MARGIN_SECONDS)
    (first,) = await _pending(workspace_id)
    assert (first.from_amount, first.amount) == (0, 175)

    async with workspace_tx() as connection:
        await record_sandbox_tokens(
            connection, workspace_id, turn_id, "claude-opus-4-8", Usage(input_tokens=40)
        )
        await _settle_turn(connection, turn_id, age_seconds=PAST_EXPORT_MARGIN_SECONDS)
    frozen, top_up = sorted(await _pending(workspace_id), key=lambda e: e.from_amount)
    assert (frozen.from_amount, frozen.amount) == (0, 175)
    assert (top_up.from_amount, top_up.amount) == (175, 40)
    assert frozen.priced_micro_usd + top_up.priced_micro_usd > 0

    async with workspace_tx() as connection:
        await accounting.ack_usage_exports(connection, workspace_id, CONSUMER, (frozen, top_up))
    assert await _pending(workspace_id) == ()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_recovered_turn_usage_growth_mints_a_frozen_top_up(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(
            connection,
            workspace_id,
            turn_id,
            "claude-opus-4-8",
            Usage(input_tokens=1000),
            "same-attempt",
        )
    (first,) = await _pending(workspace_id)
    assert (first.from_amount, first.amount) == (0, 1000)

    async with workspace_tx() as connection:
        await record_turn_usage(
            connection,
            workspace_id,
            turn_id,
            "claude-opus-4-8",
            Usage(input_tokens=1500),
            "same-attempt",
        )
    frozen, top_up = sorted(await _pending(workspace_id), key=lambda export: export.from_amount)
    assert (frozen.from_amount, frozen.amount) == (0, 1000)
    assert (top_up.from_amount, top_up.amount) == (1000, 500)
    assert frozen.priced_micro_usd + top_up.priced_micro_usd == CORE_PRICING.micro_usd(
        "claude-opus-4-8", Usage(input_tokens=1500)
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_usage_export_floor_consumer_and_workspace_scoping(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, _ = await _seed_turn(connection)
        other_workspace, _ = await _seed_turn(connection)
        await record_workspace_usage(
            connection, workspace_id, "claude-opus-4-8", Usage(input_tokens=100)
        )
        await record_workspace_usage(
            connection, workspace_id, "claude-opus-4-8", Usage(input_tokens=999)
        )
        await record_workspace_usage(
            connection, other_workspace, "claude-opus-4-8", Usage(input_tokens=7)
        )
        pre_floor = (
            await connection.execute(
                sa.select(tables.ledger.c.id).where(tables.ledger.c.amount == 999)
            )
        ).scalar_one()
        await connection.execute(
            sa.update(tables.ledger)
            .where(tables.ledger.c.id == pre_floor)
            .values(created_at=datetime.now(UTC) - timedelta(days=40))
        )

    (mine,) = await _pending(workspace_id)
    assert mine.amount == 100

    (other_consumer,) = await _pending(workspace_id, consumer="other")
    assert other_consumer.amount == 100
    async with workspace_tx() as connection:
        await accounting.ack_usage_exports(connection, workspace_id, CONSUMER, (mine,))
    assert await _pending(workspace_id) == ()
    (still_pending,) = await _pending(workspace_id, consumer="other")
    assert still_pending.amount == 100

    (theirs,) = await _pending(other_workspace)
    assert theirs.amount == 7


async def test_usage_export_reads_latest_by_consumer_and_ledger(
    db: None, database_url: str
) -> None:
    if not database_url.startswith("postgresql"):
        pytest.skip("query plans are a PostgreSQL contract")
    now = datetime.now(UTC)
    target_ledger_id = UUID(int=(1 << 128) - 1)
    async with workspace_tx() as connection:
        target_workspace, _ = await _seed_turn(connection)
        history_workspace, _ = await _seed_turn(connection)
        history_ledger_ids = tuple(UUID(int=index + 1) for index in range(QUERY_PLAN_HISTORY_ROWS))
        await connection.execute(
            sa.insert(tables.ledger),
            [
                {
                    "id": ledger_id,
                    "workspace_id": history_workspace,
                    "turn_id": None,
                    "dimension": TOKENS_DIMENSION,
                    "amount": 1,
                    "prompt_tokens": 1,
                    "input_tokens": 1,
                    "byok": False,
                    "token_classes_complete": True,
                    "priced_micro_usd": 1,
                    "model": "claude-opus-4-8",
                    "price_digest": PRICE_DIGEST,
                    "created_at": now,
                    "updated_at": now,
                }
                for ledger_id in history_ledger_ids
            ],
        )
        await connection.execute(
            sa.insert(tables.ledger_export),
            [
                {
                    "consumer": CONSUMER,
                    "ledger_id": ledger_id,
                    "from_amount": 0,
                    "workspace_id": history_workspace,
                    "to_amount": 1,
                    "from_micro_usd": 0,
                    "to_micro_usd": 1,
                    "byok": False,
                    "occurred_at": now,
                    "acked_at": now,
                    "created_at": now,
                    "updated_at": now,
                }
                for ledger_id in history_ledger_ids
            ],
        )
        await connection.execute(
            sa.insert(tables.ledger).values(
                id=target_ledger_id,
                workspace_id=target_workspace,
                turn_id=None,
                dimension=TOKENS_DIMENSION,
                amount=1,
                prompt_tokens=1,
                input_tokens=1,
                byok=False,
                token_classes_complete=True,
                priced_micro_usd=1,
                model="claude-opus-4-8",
                price_digest=PRICE_DIGEST,
                created_at=now,
                updated_at=now,
            )
        )
        captured: list[tuple[str, tuple[object, ...]]] = []

        def record(
            sync_connection: sa.Connection,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            if statement.lstrip().startswith("SELECT") and "ledger_export" in statement:
                assert isinstance(parameters, tuple)
                captured.append((statement, parameters))

        sa.event.listen(connection.sync_connection, "before_cursor_execute", record)
        try:
            await accounting.mint_usage_exports(
                connection,
                target_workspace,
                CONSUMER,
                now - timedelta(days=7),
                lambda model: None,
            )
        finally:
            sa.event.remove(connection.sync_connection, "before_cursor_execute", record)
        assert len(captured) == 1
        statement, parameters = captured[0]
        explained = (
            await connection.exec_driver_sql(
                f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {statement}", parameters
            )
        ).scalar_one()[0]
    nodes = [explained["Plan"]]
    for node in nodes:
        nodes.extend(node.get("Plans", ()))
    export_nodes = [node for node in nodes if node.get("Relation Name") == "ledger_export"]
    assert export_nodes
    assert all(node["Node Type"] != "Seq Scan" for node in export_nodes)
    assert sum(node["Actual Rows"] * node["Actual Loops"] for node in export_nodes) <= 2
    assert explained["Execution Time"] < QUERY_PLAN_MAX_MS


async def _balance_of(connection: AsyncConnection, workspace_id: UUID) -> int:
    return (
        await connection.execute(
            sa.select(tables.workspace_balance.c.balance_micro_usd).where(
                tables.workspace_balance.c.workspace_id == workspace_id
            )
        )
    ).scalar_one()


async def _priced_of(connection: AsyncConnection, turn_id: UUID, dimension: str) -> int:
    return (
        await connection.execute(
            sa.select(sa.func.sum(tables.ledger.c.priced_micro_usd)).where(
                tables.ledger.c.turn_id == turn_id, tables.ledger.c.dimension == dimension
            )
        )
    ).scalar_one()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_replayed_attempt_debits_once(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await credit(connection, workspace_id, 100_000_000, 100_000_000, "first")
        for _ in range(3):
            await record_turn_usage(
                connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
            )
        left = await _balance_of(connection, workspace_id)
    assert left == 100_000_000 - CORE_PRICING.micro_usd("claude-opus-4-8", FULL_USAGE)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_job_bill_debits_the_balance(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, _ = await _seed_turn(connection)
        await credit(connection, workspace_id, 100_000_000, 100_000_000, "first")
        await record_workspace_usage(connection, workspace_id, "claude-opus-4-8", FULL_USAGE)
        billed = (
            await connection.execute(
                sa.select(sa.func.sum(tables.ledger.c.priced_micro_usd)).where(
                    tables.ledger.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
        left = await _balance_of(connection, workspace_id)
    assert billed > 0
    assert left == 100_000_000 - billed


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_generated_media_debits_and_egress_does_not(db: None) -> None:
    """An image or video is real money on the platform key, so an empty balance must not keep
    generating. Egress counts requests and prices at zero, so it never moves the balance."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await credit(connection, workspace_id, 100_000_000, 100_000_000, "first")
        await record_egress_request(connection, workspace_id, turn_id)
        after_egress = await _balance_of(connection, workspace_id)
        await record_image_usage(connection, workspace_id, turn_id, "gpt-image-2", 2, 40_000)
        await record_video_usage(connection, workspace_id, turn_id, "hailuo-3", 1, 7_000_000)
        left = await _balance_of(connection, workspace_id)
    assert after_egress == 100_000_000
    assert left == 100_000_000 - 40_000 - 7_000_000


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_workspace_with_no_balance_row_records_usage_unchanged(db: None) -> None:
    """The self-host path: the ledger still carries what it would have charged, and there is nothing
    to take it off."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
        billed = await _priced_of(connection, turn_id, TOKENS_DIMENSION)
        rows = (
            await connection.execute(
                sa.select(sa.func.count()).select_from(tables.workspace_balance)
            )
        ).scalar_one()
    assert billed > 0
    assert rows == 0


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_balance_is_granted_less_every_debit(db: None) -> None:
    """The balance is not a sum over the ledger: a BYOK burn is priced and never debited, so the
    ledger total is only an upper bound. What holds exactly is granted less what was taken."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await credit(connection, workspace_id, 60_000_000, 60_000_000, "first")
        await credit(connection, workspace_id, 40_000_000, 40_000_000, "second")
        await credit(connection, workspace_id, -10_000_000, -10_000_000, "refund/second")
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot="anthropic_api_key",
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await record_turn_usage(
            connection,
            workspace_id,
            turn_id,
            "claude-opus-4-8",
            FULL_USAGE,
            byok=True,
        )
        await record_image_usage(connection, workspace_id, turn_id, "gpt-image-2", 1, 25_000)
        granted = (
            await connection.execute(
                sa.select(sa.func.sum(tables.balance_purchase.c.granted_micro_usd)).where(
                    tables.balance_purchase.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
        priced = (
            await connection.execute(
                sa.select(sa.func.sum(tables.ledger.c.priced_micro_usd)).where(
                    tables.ledger.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
        left = await _balance_of(connection, workspace_id)
    assert left == int(granted) - 25_000
    assert int(granted) - int(priced) < left


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_burn_the_workspaces_own_key_paid_for_never_debits(db: None) -> None:
    """BYOK meters without billing: the burn is still recorded at what it cost, and the balance is
    untouched, because the workspace already paid the provider directly."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await credit(connection, workspace_id, 100_000_000, 100_000_000, "first")
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot="anthropic_api_key",
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await record_turn_usage(
            connection,
            workspace_id,
            turn_id,
            "claude-opus-4-8",
            FULL_USAGE,
            byok=True,
        )
        priced = await _priced_of(connection, turn_id, TOKENS_DIMENSION)
        cost = await read_turn_cost(connection, turn_id, TOKENS_DIMENSION)
        left = await _balance_of(connection, workspace_id)
    assert cost is not None
    assert cost.tokens > 0
    assert priced > 0
    assert left == 100_000_000


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_burn_on_the_platform_key_still_debits(db: None) -> None:
    """The exemption is the key that served the turn, decided when the turn was set up."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await credit(connection, workspace_id, 100_000_000, 100_000_000, "first")
        await record_turn_usage(
            connection,
            workspace_id,
            turn_id,
            "claude-opus-4-8",
            FULL_USAGE,
        )
        left = await _balance_of(connection, workspace_id)
    assert left < 100_000_000


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_job_burn_the_workspaces_own_key_paid_for_never_debits(db: None) -> None:
    """A background job's spend follows the same rule a turn's does: the workspace that paid the
    provider directly is not charged for it a second time."""
    async with workspace_tx() as connection:
        workspace_id, _ = await _seed_turn(connection)
        await credit(connection, workspace_id, 100_000_000, 100_000_000, "first")
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot="anthropic_api_key",
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await record_workspace_usage(
            connection,
            workspace_id,
            "claude-opus-4-8",
            FULL_USAGE,
            CORE_PRICING,
            True,
        )
        left = await _balance_of(connection, workspace_id)
    assert left == 100_000_000


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_job_burn_on_the_platform_key_debits(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, _ = await _seed_turn(connection)
        await credit(connection, workspace_id, 100_000_000, 100_000_000, "first")
        await record_workspace_usage(
            connection,
            workspace_id,
            "claude-opus-4-8",
            FULL_USAGE,
            CORE_PRICING,
            False,
        )
        left = await _balance_of(connection, workspace_id)
    assert left < 100_000_000


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_key_arriving_mid_turn_does_not_make_that_turn_free(db: None) -> None:
    """The exemption is decided against the key that served the turn, not against the credential
    rows as they stand when the bill is written. Re-read at terminal, a key stored mid-run would
    make that turn free and one removed mid-run would charge what the workspace already paid."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await credit(connection, workspace_id, 100_000_000, 100_000_000, "first")
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot="anthropic_api_key",
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE, byok=False
        )
        left = await _balance_of(connection, workspace_id)
    assert left < 100_000_000


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_balance_equals_granted_less_what_the_ledger_debited(db: None) -> None:
    """The identity the audit rests on. Priced is what a burn cost; debited is what it took, and a
    BYOK row is priced while taking nothing, so only the debited column rebuilds the balance."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await credit(connection, workspace_id, 60_000_000, 60_000_000, "first")
        await credit(connection, workspace_id, -10_000_000, -10_000_000, "refund/first")
        await record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE, byok=True
        )
        await record_image_usage(connection, workspace_id, turn_id, "gpt-image-2", 1, 25_000)
        await record_sandbox_tokens(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
        granted = (
            await connection.execute(
                sa.select(sa.func.sum(tables.balance_purchase.c.granted_micro_usd)).where(
                    tables.balance_purchase.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
        totals = (
            await connection.execute(
                sa.select(
                    sa.func.sum(tables.ledger.c.priced_micro_usd),
                    sa.func.sum(tables.ledger.c.debited_micro_usd),
                ).where(tables.ledger.c.workspace_id == workspace_id)
            )
        ).one()
        left = await _balance_of(connection, workspace_id)
    assert int(granted) - int(totals[1]) == left
    assert int(totals[0]) > int(totals[1])


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_workspace_with_no_balance_records_no_deduction(db: None) -> None:
    """Every workspace before its first top-up and every self-hosted install has no balance row, so
    nothing is taken. Recording the deduction that was attempted would claim money left a balance
    that does not exist, and the reconciliation would be wrong in both directions."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
        await record_image_usage(connection, workspace_id, turn_id, "gpt-image-2", 1, 25_000)
        await record_sandbox_tokens(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
        totals = (
            await connection.execute(
                sa.select(
                    sa.func.sum(tables.ledger.c.priced_micro_usd),
                    sa.func.sum(tables.ledger.c.debited_micro_usd),
                ).where(tables.ledger.c.workspace_id == workspace_id)
            )
        ).one()
    assert int(totals[0]) > 0
    assert int(totals[1]) == 0


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_recovery_of_one_attempt_bills_the_key_that_served_it(db: None) -> None:
    """A crash recovery re-executes the same attempt and must bill the tokens already burned under
    the verdict they were burned under. Re-deciding there would make the whole attempt free because
    a key arrived after the crash, or charge for what the workspace's own key paid because one
    left. The verdict is the caller's — decided against the key that resolved for this turn — so
    the freeze is what the recovery reads, whatever the second call is handed."""
    async with workspace_tx() as connection:
        _workspace_id, turn_id = await _seed_turn(connection)
    first = await loop_queue._frozen_byok(turn_id, False, "attempt-1")
    recovered = await loop_queue._frozen_byok(turn_id, True, "attempt-1")
    assert first is False
    assert recovered is False


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_recovery_uses_the_model_and_prices_that_started_the_attempt(db: None) -> None:
    async with workspace_tx() as connection:
        _, turn_id = await _seed_turn(connection)
    first = loop_queue._BillingIdentity(
        attempt="attempt-1",
        model="claude-opus-4-8",
        price_digest="prices-1",
        input=1,
        output=2,
        cache_read=3,
        cache_write_5m=4,
        cache_write_30m=5,
        cache_write_1h=6,
    )
    changed = first.model_copy(
        update={
            "model": "claude-sonnet-5",
            "price_digest": "prices-2",
            "input": 10,
        }
    )

    frozen = await loop_queue._frozen_billing_identity(turn_id, first)
    recovered = await loop_queue._frozen_billing_identity(turn_id, changed)
    resumed = await loop_queue._frozen_billing_identity(
        turn_id, changed.model_copy(update={"attempt": "attempt-2"})
    )

    assert frozen == first
    assert recovered == first
    assert resumed == changed.model_copy(update={"attempt": "attempt-2"})


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_recovery_cannot_move_the_frozen_model_to_another_payer(db: None) -> None:
    async with workspace_tx() as connection:
        _, turn_id = await _seed_turn(connection)

    class Registry:
        pricing = CORE_PRICING

        def __init__(self, payer: str) -> None:
            self.payer = payer
            self.requested: list[str] = []

        async def client_for(self, model: str) -> ResolvedModelClient:
            self.requested.append(model)
            return ResolvedModelClient(cast(ModelClient, object()), KEY_FUNDED, self.payer)

    first_registry = Registry("anthropic_api_key:member:first")
    billing, _, byok = await loop_queue._TurnBilling(
        registry=cast(ModelRegistry, first_registry),
        turn_id=turn_id,
        attempt="attempt-1",
        candidate_model="claude-opus-4-8",
    ).resolve()

    recovered_registry = Registry("anthropic_api_key")
    with pytest.raises(ModelFundingChanged, match="payer changed"):
        await loop_queue._TurnBilling(
            registry=cast(ModelRegistry, recovered_registry),
            turn_id=turn_id,
            attempt="attempt-1",
            candidate_model="gpt-5.4",
        ).resolve()

    assert (billing.funding, billing.payer, byok) == (
        KEY_FUNDED,
        "anthropic_api_key:member:first",
        True,
    )
    assert first_registry.requested == ["claude-opus-4-8"]
    assert recovered_registry.requested == ["claude-opus-4-8"]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_attempt_freezes_a_rate_for_every_account_it_may_move_onto(db: None) -> None:
    """A turn on the member's own account may move onto their other account mid-attempt. The rates
    of every model it may serve on are frozen with the identity, read off one card, so a moved
    round bills at the rate of the model that served it and a recovery re-prices nothing. Under a
    plan-funded grant that card is zero for every candidate, and the same identity resolves again
    on recovery."""
    async with workspace_tx() as connection:
        _, keyed_turn = await _seed_turn(connection)
        _, plan_turn = await _seed_turn(connection)

    class Registry:
        pricing = CORE_PRICING

        def __init__(self, funding: str) -> None:
            self.funding = funding

        async def client_for(self, model: str) -> ResolvedModelClient:
            return ResolvedModelClient(
                cast(ModelClient, object()),
                cast(Funding, self.funding),
                "openai_api_key:member:first",
            )

    keyed, _, keyed_byok = await loop_queue._TurnBilling(
        registry=cast(ModelRegistry, Registry(KEY_FUNDED)),
        turn_id=keyed_turn,
        attempt="attempt-1",
        candidate_model="gpt-5.6-sol",
        alternates=("claude-opus-5",),
    ).resolve()
    plan_registry = cast(ModelRegistry, Registry(PLAN_FUNDED))
    plan, _, plan_byok = await loop_queue._TurnBilling(
        registry=plan_registry,
        turn_id=plan_turn,
        attempt="attempt-1",
        candidate_model="gpt-5.6-sol",
        alternates=("claude-opus-5",),
    ).resolve()
    recovered, _, _ = await loop_queue._TurnBilling(
        registry=plan_registry,
        turn_id=plan_turn,
        attempt="attempt-1",
        candidate_model="gpt-5.6-sol",
        alternates=("claude-opus-5",),
    ).resolve()

    assert keyed.pricing().prices == {
        "gpt-5.6-sol": CORE_PRICES["gpt-5.6-sol"],
        "claude-opus-5": CORE_PRICES["claude-opus-5"],
    }
    assert keyed.pricing().digest == PRICE_DIGEST
    assert plan.pricing().prices == {
        "gpt-5.6-sol": loop_queue.PLAN_SERVED_PRICE,
        "claude-opus-5": loop_queue.PLAN_SERVED_PRICE,
    }
    assert plan.pricing().digest != keyed.pricing().digest
    assert recovered == plan
    assert (keyed_byok, plan_byok) == (True, True)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_resumed_attempt_decides_against_the_key_that_will_serve_it(db: None) -> None:
    """The money keys on the attempt: a parked turn's resume re-runs every round for real under a
    fresh workflow id and bills each burn under that attempt. A verdict frozen against the turn
    would bill the whole re-run under the situation that held when the turn first started — adding
    a key during the pause charges for calls the workspace's own key paid, removing one makes the
    entire re-run free, and either is repeatable with ordinary workspace permissions."""
    async with workspace_tx() as connection:
        _workspace_id, turn_id = await _seed_turn(connection)
    parked = await loop_queue._frozen_byok(turn_id, False, "attempt-1")
    resumed = await loop_queue._frozen_byok(turn_id, True, "attempt-2")
    resumed_again = await loop_queue._frozen_byok(turn_id, False, "attempt-3")
    assert parked is False
    assert resumed is True
    assert resumed_again is False


async def test_metered_workspaces_names_only_workspaces_with_ledger_rows(db: None) -> None:
    async with workspace_tx() as connection:
        metered, _ = await _seed_turn(connection)
        await _seed_turn(connection)
        await record_workspace_usage(connection, metered, "claude-opus-4-8", Usage(input_tokens=10))
    assert await accounting.metered_workspaces()() == (metered,)
