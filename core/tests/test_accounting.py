import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo import accounting
from ufo.accounting import (
    IMAGES_DIMENSION,
    MEMBER_SCOPE,
    SANDBOX_TOKENS_DIMENSION,
    TOKENS_DIMENSION,
    VIDEOS_DIMENSION,
    SpendRollup,
    TurnCost,
    read_turn_cost,
    record_egress_request,
    record_image_usage,
    record_probe_egress_request,
    record_sandbox_tokens,
    record_turn_usage,
    record_video_usage,
    record_workspace_usage,
)
from ufo.balance import credit
from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.db import workspace_tx
from ufo.loop import queue as loop_queue
from ufo.models.catalog import CORE_MODEL_SPECS, CORE_PRICES, CORE_PRICING, PRICE_DIGEST
from ufo.models.interface import PROVIDER_ANTHROPIC
from ufo.models.pricing import (
    TOKENS_PER_MTOK,
    ModelPrice,
    price_digest,
    pricing_from,
    usage_priced_micro_usd,
)
from ufo.models.registry import model_registry
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


def test_openai_row_converted_from_usd_per_mtok() -> None:
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert CORE_PRICING.micro_usd("gpt-5.4", usage) == 17_500_000


def test_unknown_model_prices_zero_never_raises() -> None:
    assert CORE_PRICING.micro_usd("gpt-4o", FULL_USAGE) == 0


def test_the_gpt_5_6_rows_price_at_the_published_rates() -> None:
    """Terra bills $2/$12 per Mtok and Luna $0.20/$1.20, each with cache reads at 0.1x input and
    cache writes at 1.25x — the rate OpenAI bills a GPT-5.6 cache write at, which is why these two
    rows carry a write rate above their input rate."""
    usage = Usage(
        input_tokens=1000,
        output_tokens=2000,
        cache_read_tokens=3000,
        cache_write_30m_tokens=4000,
    )
    assert CORE_PRICING.micro_usd("gpt-5.6-terra", usage) == 36_600
    assert CORE_PRICING.micro_usd("gpt-5.6-luna", usage) == 3_660
    for model in ("gpt-5.6-terra", "gpt-5.6-luna"):
        price = CORE_PRICES[model]
        assert price.cache_write_5m == 0
        assert price.cache_write_30m == price.input * 5 // 4
        assert price.cache_write_1h == 0
        assert price.cache_read == price.input // 10


def test_sonnet_5_row_prices_at_the_published_rates() -> None:
    price = CORE_PRICES["claude-sonnet-5"]
    assert price == ModelPrice(2_000_000, 10_000_000, 200_000, 2_500_000, 4_000_000)


def test_price_digest_is_stable_sha256() -> None:
    assert PRICE_DIGEST.startswith("sha256:")
    assert len(PRICE_DIGEST) == len("sha256:") + 64
    assert price_digest(CORE_PRICES) == PRICE_DIGEST


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


def test_core_pricing_is_the_core_table() -> None:
    assert CORE_PRICING.digest == PRICE_DIGEST
    assert (
        CORE_PRICING.micro_usd("claude-opus-4-8", Usage(input_tokens=1000, output_tokens=2000))
        == 55_000
    )
    assert CORE_PRICING.micro_usd("vendor/model-x", FULL_USAGE) == 0


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
        cost = await read_turn_cost(connection, turn_id, TOKENS_DIMENSION)
    assert cost == TurnCost(
        tokens=10_000, micro_usd=96_500, model="claude-opus-4-8", cache_percent=38
    )


async def test_ledger_prices_five_minute_and_one_hour_cache_writes_separately(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_5m, turn_5m = await _seed_turn(connection)
        workspace_1h, turn_1h = await _seed_turn(connection)
        await record_turn_usage(
            connection,
            workspace_5m,
            turn_5m,
            "claude-opus-4-8",
            Usage(cache_write_5m_tokens=TOKENS_PER_MTOK),
        )
        await record_turn_usage(
            connection,
            workspace_1h,
            turn_1h,
            "claude-opus-4-8",
            Usage(cache_write_1h_tokens=TOKENS_PER_MTOK),
        )
    async with workspace_tx() as connection:
        cost_5m = await read_turn_cost(connection, turn_5m, TOKENS_DIMENSION)
        cost_1h = await read_turn_cost(connection, turn_1h, TOKENS_DIMENSION)
    assert cost_5m == TurnCost(
        tokens=TOKENS_PER_MTOK,
        micro_usd=6_250_000,
        model="claude-opus-4-8",
        cache_percent=0,
    )
    assert cost_1h == TurnCost(
        tokens=TOKENS_PER_MTOK,
        micro_usd=10_000_000,
        model="claude-opus-4-8",
        cache_percent=0,
    )


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
        assert await read_turn_cost(connection, turn_id, TOKENS_DIMENSION) is None


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


async def test_probe_egress_bills_the_workspace_on_a_turnless_row(db: None) -> None:
    """An off-turn probe's egress shares the turn path's dimension and its zero price, and names no
    turn: it lands in the workspace total and every workspace-scoped cap window, and drops out of
    the per-member and per-agent rollups, which reach a member or agent through `turn`. Each write
    is its own row, so a probe row can never accumulate onto a key a later probe reuses."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_egress_request(connection, workspace_id, turn_id)
        await record_probe_egress_request(connection, workspace_id, amount=3)
        await record_probe_egress_request(connection, workspace_id)
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                )
                .where(
                    tables.ledger.c.workspace_id == workspace_id,
                    tables.ledger.c.dimension == "egress",
                    tables.ledger.c.turn_id.is_(None),
                )
                .order_by(tables.ledger.c.amount)
            )
        ).all()
        report = await SpendRollup(workspace_id=workspace_id).read(connection, None)

    assert [(int(row.amount), int(row.priced_micro_usd)) for row in rows] == [(1, 0), (3, 0)]
    assert [
        (total.dimension, total.amount)
        for total in report.by_dimension
        if total.dimension == "egress"
    ] == [("egress", 5)]
    assert not [subject for subject in report.by_member if subject.subject_id is not None]


async def test_egress_never_double_counts_the_token_cost(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
        await record_egress_request(connection, workspace_id, turn_id)
        await record_egress_request(connection, workspace_id, turn_id)
    async with workspace_tx() as connection:
        cost = await read_turn_cost(connection, turn_id, TOKENS_DIMENSION)
    assert cost == TurnCost(
        tokens=10_000, micro_usd=96_500, model="claude-opus-4-8", cache_percent=38
    )


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


async def test_a_turn_cost_read_has_to_name_the_spend_it_reads(db: None) -> None:
    """One turn can carry spend under more than one dimension, so no default is right for every
    reader: a caller that names none is refused at the call site rather than reading whichever one
    the signature happened to prefer."""
    async with workspace_tx() as connection:
        _workspace_id, turn_id = await _seed_turn(connection)
        with pytest.raises(TypeError, match="dimension"):
            await read_turn_cost(connection, turn_id)  # type: ignore[call-arg]


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
        "tokens": (10_000, 96_500),
    }
    assert report.total_micro_usd == 96_500 + 55_000


async def test_generated_images_accumulate_into_one_unstamped_row(db: None) -> None:
    """Image spend is a count and a charge, not a token burn: the row carries the image count as its
    amount, the provider's charge as its price, no price digest (no pinned rate table describes an
    image model), and a second generation on the same turn accumulates into the same row."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
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


async def test_generated_videos_meter_under_their_own_dimension(db: None) -> None:
    """Video spend is priced per output second, so its charge is the provider's and its amount is a
    video count — a row of its own, keyed apart from the same turn's images and accumulating across
    generations exactly as they do."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_video_usage(connection, workspace_id, turn_id, "minimax/hailuo-3", 1, 650_000)
        await record_video_usage(
            connection, workspace_id, turn_id, "minimax/hailuo-3", 1, 1_300_000
        )
        await record_image_usage(
            connection, workspace_id, turn_id, "bytedance-seed/seedream-4.5", 1, 40_000
        )
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.id,
                    tables.ledger.c.dimension,
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.model,
                    tables.ledger.c.price_digest,
                ).where(tables.ledger.c.turn_id == turn_id)
            )
        ).all()
    assert len({row.id for row in rows}) == 2
    video = next(row for row in rows if row.dimension == VIDEOS_DIMENSION)
    assert (int(video.amount), int(video.priced_micro_usd)) == (2, 1_950_000)
    assert (video.model, video.price_digest) == ("minimax/hailuo-3", None)


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


async def test_spend_rollup_surfaces_generated_images(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
        await record_image_usage(
            connection, workspace_id, turn_id, "bytedance-seed/seedream-4.5", 2, 80_000
        )
    async with workspace_tx() as connection:
        report = await SpendRollup(workspace_id).read(connection, 3600)
    assert {d.dimension: (d.amount, d.priced_micro_usd) for d in report.by_dimension} == {
        "images": (2, 80_000),
        "tokens": (10_000, 96_500),
    }
    assert report.total_micro_usd == 96_500 + 80_000


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


async def test_spend_by_origin_counts_a_turn_once_per_ledger_row(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await connection.execute(
            sa.update(tables.conversation)
            .where(tables.conversation.c.workspace_id == workspace_id)
            .values(surface="slack", surface_label="#eng")
        )
        await record_turn_usage(connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE)
        await record_turn_usage(
            connection,
            workspace_id,
            turn_id,
            "claude-opus-4-8",
            FULL_USAGE,
            attempt="resumed",
        )
    async with workspace_tx() as connection:
        report = await SpendRollup(workspace_id).read(connection, 3600)
    assert [(o.label, o.tokens, o.priced_micro_usd) for o in report.by_origin] == [
        ("#eng", 20_000, 193_000)
    ]
    assert sum(o.priced_micro_usd for o in report.by_origin) == report.total_micro_usd


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
    assert (row.dimension, int(row.amount), int(row.priced_micro_usd)) == ("tokens", 10_000, 96_500)
    assert row.model == "claude-opus-4-8"
    assert row.price_digest == PRICE_DIGEST


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


async def test_member_spend_excludes_ledger_outside_the_window(db: None) -> None:
    old = datetime.now(UTC) - timedelta(hours=2)
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        member_id = (
            await connection.execute(
                sa.select(tables.conversation.c.member_id).where(
                    tables.conversation.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
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
        report = await SpendRollup(workspace_id).read_member(connection, member_id, 3600)
    assert report.total_micro_usd == 0
    assert report.by_dimension == ()


CONSUMER = "metronome"
PAST_EXPORT_MARGIN_SECONDS = accounting.EXPORT_SETTLE_MARGIN_SECONDS + 100


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


def _key_slot_for() -> Callable[[str], str | None]:
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite://"),
        blob=BlobConfig(backend="filesystem", root=Path()),
    )
    return model_registry(config, ()).key_slot_for


async def _pending(
    workspace_id: UUID, consumer: str = CONSUMER
) -> tuple[accounting.UsageExport, ...]:
    floor = datetime.now(UTC) - timedelta(days=7)
    async with workspace_tx() as connection:
        await accounting.mint_usage_exports(
            connection, workspace_id, consumer, floor, _key_slot_for()
        )
        return await accounting.read_pending_usage_exports(connection, workspace_id, consumer, 100)


async def test_usage_export_settlement_rules(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
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
    assert not any(export.dimension == "egress" for export in settled)


async def test_images_export_with_their_settled_turn_as_platform_served(db: None) -> None:
    """An `images` row accumulates while its turn runs, so it settles like `sandbox_tokens`: nothing
    mints until the turn is terminal and past the margin. It exports as platform-served, because the
    `byok` label resolves a key slot through the model registry and an image model is not in it."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
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


async def test_videos_export_with_their_settled_turn_as_platform_served(db: None) -> None:
    """A `videos` row accumulates while its turn runs and settles like an `images` row: nothing
    mints until the turn is terminal and past the margin, and it exports as platform-served because
    no video model is in the registry for a key slot to be resolved from."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_video_usage(connection, workspace_id, turn_id, "minimax/hailuo-3", 1, 650_000)
    assert await _pending(workspace_id) == ()

    async with workspace_tx() as connection:
        await _settle_turn(connection, turn_id, age_seconds=PAST_EXPORT_MARGIN_SECONDS)
    (export,) = await _pending(workspace_id)
    assert (export.dimension, export.amount, export.from_amount) == ("videos", 1, 0)
    assert (export.priced_micro_usd, export.model, export.turn_id) == (
        650_000,
        "minimax/hailuo-3",
        turn_id,
    )
    assert export.byok is False
    assert export.price_digest is None


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


async def test_metered_workspaces_names_only_workspaces_with_ledger_rows(db: None) -> None:
    async with workspace_tx() as connection:
        metered, _ = await _seed_turn(connection)
        await _seed_turn(connection)
        await record_workspace_usage(connection, metered, "claude-opus-4-8", Usage(input_tokens=10))
    assert await accounting.metered_workspaces()() == (metered,)


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


async def test_several_sandbox_calls_debit_each_increment(db: None) -> None:
    """One accumulating row, and the debits sum to it."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await credit(connection, workspace_id, 100_000_000, 100_000_000, "first")
        for _ in range(3):
            await record_sandbox_tokens(
                connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
            )
        billed = await _priced_of(connection, turn_id, SANDBOX_TOKENS_DIMENSION)
        left = await _balance_of(connection, workspace_id)
    assert left == 100_000_000 - billed


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


async def test_a_recovery_of_one_attempt_bills_the_key_that_served_it(db: None) -> None:
    """A crash recovery re-executes the same attempt and must bill the tokens already burned under
    the verdict they were burned under. Re-deciding there would make the whole attempt free because
    a key arrived after the crash, or charge for what the workspace's own key paid because one
    left."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
    first = await loop_queue._frozen_byok(workspace_id, turn_id, "anthropic_api_key", "attempt-1")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot="anthropic_api_key",
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    recovered = await loop_queue._frozen_byok(
        workspace_id, turn_id, "anthropic_api_key", "attempt-1"
    )
    assert first is False
    assert recovered is False


async def test_a_resumed_attempt_decides_against_the_key_that_will_serve_it(db: None) -> None:
    """The money keys on the attempt: a parked turn's resume re-runs every round for real under a
    fresh workflow id and bills each burn under that attempt. A verdict frozen against the turn
    would bill the whole re-run under the situation that held when the turn first started — adding
    a key during the pause charges for calls the workspace's own key paid, removing one makes the
    entire re-run free, and either is repeatable with ordinary workspace permissions."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
    parked = await loop_queue._frozen_byok(workspace_id, turn_id, "anthropic_api_key", "attempt-1")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot="anthropic_api_key",
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    resumed = await loop_queue._frozen_byok(workspace_id, turn_id, "anthropic_api_key", "attempt-2")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.credential).where(tables.credential.c.workspace_id == workspace_id)
        )
    resumed_again = await loop_queue._frozen_byok(
        workspace_id, turn_id, "anthropic_api_key", "attempt-3"
    )
    assert parked is False
    assert resumed is True
    assert resumed_again is False
