import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection
from ufo_ext_sample.spend import CHARGE_TABLE, SampleGate

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
    EGRESS_DIMENSION,
    GIB_DIMENSION,
    IMAGES_DIMENSION,
    JOB_DAY_SETTLE_SECONDS,
    MEMBER_SCOPE,
    MODELS_SERVICE,
    PROXY_SERVICE,
    REQUESTS_DIMENSION,
    SANDBOX_TOKENS_ATTEMPT,
    SANDBOX_TOKENS_DIMENSION,
    SERVICE_CLOCK_SKEW_SECONDS,
    SERVICE_OF_DIMENSION,
    SERVICE_UNITS,
    TOKENS_DIMENSION,
    TURN_LABEL,
    UNGATED_LEDGER,
    USAGE_KEYS,
    JobDayRollup,
    Ledger,
    ServiceBackfill,
    SpendRollup,
    TurnCost,
    TurnUsageConflict,
    UsageExport,
    UsageLine,
    job_day_candidates,
    ledger_service_backfill_candidates,
    read_turn_cost,
    record_egress_request,
    rolled_days,
    session_spend,
    token_spend,
    usage_lines,
)
from ufo.runtime.billing.spend import GateDeploy
from ufo.runtime.ext.context import CredentialAccess, ExtensionContext, ScopedStore
from ufo.runtime.workspace import (
    KEY_FUNDED,
    PLAN_FUNDED,
    PLATFORM_FUNDED,
    PLATFORM_PAYER,
    ModelFundingChanged,
    ModelPayer,
    ResolvedModelClient,
    ws,
)
from ufo.schema import tables
from ufo.schema.records import Usage, ledger_id_for, service_ledger_id_for
from ufo.sdk.accounting import ServiceTotal, SpendTotals

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
UNCACHED_USAGE = Usage(input_tokens=50_000, output_tokens=200)


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
        assert usage_priced_micro_usd(spec.id, read, CORE_PRICES) == spec.price.cache_read


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
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "gpt-4o", FULL_USAGE
        )
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


async def test_a_large_turn_that_cached_nothing_warns(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        with caplog.at_level(logging.WARNING, logger="ufo"):
            await UNGATED_LEDGER.record_turn_usage(
                connection, workspace_id, turn_id, "claude-opus-4-8", UNCACHED_USAGE
            )

    record = caplog.records[-1]
    assert record.message == "accounting.uncached_prompt"
    assert record.ufo["model"] == "claude-opus-4-8"
    assert record.ufo["prompt_tokens"] == 50_000


@pytest.mark.parametrize(
    ("model", "usage"),
    [
        ("claude-opus-4-8", UNCACHED_USAGE.model_copy(update={"cache_write_5m_tokens": 50_000})),
        ("claude-opus-4-8", Usage(input_tokens=1_000, output_tokens=10)),
        ("gpt-4o", UNCACHED_USAGE),
    ],
)
def test_a_turn_with_nothing_to_report_stays_quiet(
    model: str, usage: Usage, caplog: pytest.LogCaptureFixture
) -> None:
    with ws(uuid4()), caplog.at_level(logging.WARNING, logger="ufo"):
        accounting._warn_uncached(model, usage, CORE_PRICING)

    assert caplog.records == []


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
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
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
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
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
        await UNGATED_LEDGER.record_turn_usage(
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
            await UNGATED_LEDGER.record_turn_usage(
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
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
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
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
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
                    tables.ledger.c.service,
                    tables.ledger.c.dimension,
                    tables.ledger.c.labels,
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                ).where(tables.ledger.c.turn_id == turn_id)
            )
        ).one()
    assert (row.service, row.dimension, row.labels) == ("proxy", "requests", {"turn": str(turn_id)})
    assert (int(row.amount), int(row.priced_micro_usd)) == (10, 0)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_sandbox_tokens_row_is_disjoint_from_the_host_token_row(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
        await UNGATED_LEDGER.record_sandbox_tokens(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.id,
                    tables.ledger.c.service,
                    tables.ledger.c.dimension,
                    tables.ledger.c.labels,
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.price_digest,
                ).where(tables.ledger.c.turn_id == turn_id)
            )
        ).all()
        cost = await read_turn_cost(connection, turn_id, TOKENS_DIMENSION)
    host = ledger_id_for(workspace_id, turn_id, TOKENS_DIMENSION)
    sandbox = ledger_id_for(workspace_id, turn_id, SANDBOX_TOKENS_DIMENSION, SANDBOX_TOKENS_ATTEMPT)
    assert {
        row.id: (
            row.service,
            row.dimension,
            row.labels,
            int(row.amount),
            int(row.priced_micro_usd),
            row.price_digest,
        )
        for row in rows
    } == {
        host: ("models", "tokens", {}, 10_000, 96_500, PRICE_DIGEST),
        sandbox: (
            "models",
            "tokens",
            {"via": "proxy", "turn": str(turn_id)},
            10_000,
            96_500,
            PRICE_DIGEST,
        ),
    }
    assert cost == TurnCost(
        tokens=20_000, micro_usd=193_000, model="claude-opus-4-8", cache_percent=38
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_sandbox_tokens_accumulate_into_one_row(db: None) -> None:
    usage = Usage(input_tokens=1000, output_tokens=2000)
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await UNGATED_LEDGER.record_sandbox_tokens(
            connection, workspace_id, turn_id, "claude-opus-4-8", usage
        )
        await UNGATED_LEDGER.record_sandbox_tokens(
            connection, workspace_id, turn_id, "claude-opus-4-8", usage
        )
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
    assert (row.dimension, int(row.amount), int(row.priced_micro_usd)) == ("tokens", 6000, 110_000)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_sandbox_rows_from_both_proxy_shapes_roll_up_and_export_once(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await UNGATED_LEDGER.record_sandbox_tokens(
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
                sa.select(tables.ledger.c.id, tables.ledger.c.amount, tables.ledger.c.labels).where(
                    tables.ledger.c.turn_id == turn_id,
                    tables.ledger.c.dimension == TOKENS_DIMENSION,
                )
            )
        ).all()
        cost = await read_turn_cost(connection, turn_id, TOKENS_DIMENSION)
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
    assert all(row.labels == {"via": "proxy", "turn": str(turn_id)} for row in rows)
    assert cost is not None and cost.tokens == 175
    assert [(total.dimension, total.amount) for total in report.by_dimension] == [("tokens", 175)]
    assert sorted(export.amount for export in exports) == [75, 100]
    assert not any(export.byok for export in exports)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_sandbox_tokens_priced_and_stamped_by_the_merged_pricing(db: None) -> None:
    pricing = pricing_from(
        {**CORE_PRICES, "vendor/model-x": ModelPrice(1_000_000, 2_000_000, 0, 0, 0)}
    )
    usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await UNGATED_LEDGER.record_sandbox_tokens(
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
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await connection.execute(
            sa.update(tables.turn).where(tables.turn.c.id == turn_id).values(byok=True)
        )
        await UNGATED_LEDGER.record_image_usage(
            connection, workspace_id, turn_id, "bytedance-seed/seedream-4.5", 2, 80_000
        )
        await UNGATED_LEDGER.record_image_usage(
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
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
        await UNGATED_LEDGER.record_image_usage(
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
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
        await record_egress_request(connection, workspace_id, turn_id)
        await record_egress_request(connection, workspace_id, turn_id)
    async with workspace_tx() as connection:
        report = await SpendRollup(workspace_id).read(connection, 3600)
    assert report.total_micro_usd == 96_500
    assert {d.dimension: (d.amount, d.priced_micro_usd) for d in report.by_dimension} == {
        "requests": (2, 0),
        "tokens": (10_000, 96_500),
    }
    assert [(s.label, s.priced_micro_usd) for s in report.by_member] == [("a@b.c", 96_500)]
    assert [(s.label, s.priced_micro_usd) for s in report.by_agent] == [("assistant", 96_500)]
    assert [(p.price_digest, p.priced_micro_usd) for p in report.by_price_digest] == [
        (PRICE_DIGEST, 96_500)
    ]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_spend_rollup_totals_each_service_over_the_dimensions_it_meters(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await record_egress_request(connection, workspace_id, turn_id)
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
        await UNGATED_LEDGER.record_image_usage(
            connection, workspace_id, turn_id, "openai/gpt-image-2", 1, 130_000
        )
    async with workspace_tx() as connection:
        report = await SpendRollup(workspace_id).read(connection, None)
    assert report.by_service == (
        ServiceTotal("models", 96_500 + 130_000),
        ServiceTotal("proxy", 0),
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_spend_rollup_lists_a_costlier_service_first(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
        await connection.execute(
            sa.insert(tables.ledger).values(
                id=uuid4(),
                workspace_id=workspace_id,
                turn_id=turn_id,
                dimension="egress",
                amount=1,
                priced_micro_usd=100_000,
                model="",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    async with workspace_tx() as connection:
        report = await SpendRollup(workspace_id).read(connection, None)
    assert report.by_service == (ServiceTotal("proxy", 100_000), ServiceTotal("models", 96_500))


def test_every_ledger_dimension_belongs_to_a_service() -> None:
    check = next(
        c
        for c in tables.ledger.constraints
        if isinstance(c, sa.CheckConstraint) and c.name == "ledger_dimension"
    )
    assert set(SERVICE_OF_DIMENSION) == set(re.findall(r"'([a-z_]+)'", str(check.sqltext)))


def test_every_service_unit_pair_is_in_the_check() -> None:
    check = next(
        c
        for c in tables.ledger.constraints
        if isinstance(c, sa.CheckConstraint) and c.name == "ledger_service_dimension"
    )
    admitted = {
        (service, unit)
        for service, units in re.findall(
            r"service = '([a-z_]+)' and dimension in \(([^)]*)\)", str(check.sqltext)
        )
        for unit in re.findall(r"'([a-z_]+)'", units)
    }
    pairs = {(service, unit) for service, units in SERVICE_UNITS.items() for unit in units}
    assert pairs <= admitted


GIB = 2**30
GIB_MICRO_USD = 168_750
CARD_DIGEST = "sha256:card"


async def _record(
    ledger: Ledger, connection: AsyncConnection, workspace_id: UUID, **changes: object
) -> bool:
    record: dict[str, object] = {
        "service": PROXY_SERVICE,
        "dimension": GIB_DIMENSION,
        "backend": None,
        "amount": GIB,
        "token_id": None,
        "session_id": None,
        "labels": {},
        "resource_id": "session/one",
        "attempt": "flush-1",
        "occurred_at": datetime.now(UTC),
        "byok": False,
        "priced_micro_usd": GIB_MICRO_USD,
        "price_digest": CARD_DIGEST,
    }
    return await ledger.record_service_usage(connection, workspace_id, **(record | changes))


@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
async def test_record_service_usage_writes_once_and_refuses_a_changed_replay(db: None) -> None:
    ledger = Ledger(gates=(SampleGate(GateDeploy(public_base_url=None, home_surface=None)),))
    session_id, token_id = uuid4(), uuid4()
    occurred_at = datetime.now(UTC) - timedelta(minutes=5)
    record = {
        "session_id": session_id,
        "token_id": token_id,
        "resource_id": None,
        "backend": "nat",
        "labels": {"team": "platform"},
        "occurred_at": occurred_at,
    }
    async with workspace_tx() as connection:
        workspace_id, _turn_id = await _seed_turn(connection)
        written = await _record(ledger, connection, workspace_id, **record)
        replayed = await _record(ledger, connection, workspace_id, **record)
    with pytest.raises(TurnUsageConflict, match="changed under its idempotency key"):
        async with workspace_tx() as connection:
            await _record(ledger, connection, workspace_id, **record, amount=GIB + 1)
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.id,
                    tables.ledger.c.service,
                    tables.ledger.c.dimension,
                    tables.ledger.c.backend,
                    tables.ledger.c.token_id,
                    tables.ledger.c.session_id,
                    tables.ledger.c.labels,
                    tables.ledger.c.resource_id,
                    tables.ledger.c.attempt,
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.price_digest,
                    tables.ledger.c.byok,
                    tables.ledger.c.turn_id,
                    tables.ledger.c.created_at,
                ).where(tables.ledger.c.workspace_id == workspace_id)
            )
        ).all()
        charges = (
            await connection.execute(
                sa.select(CHARGE_TABLE.c.ledger_id, CHARGE_TABLE.c.delta_micro_usd).where(
                    CHARGE_TABLE.c.workspace_id == workspace_id
                )
            )
        ).all()
    ledger_id = service_ledger_id_for(
        workspace_id, PROXY_SERVICE, str(session_id), GIB_DIMENSION, "flush-1"
    )
    assert (written, replayed) == (True, False)
    ((row_id, *row, created_at),) = rows
    assert row_id == ledger_id
    assert tuple(row) == (
        "proxy",
        "gib",
        "nat",
        token_id,
        session_id,
        {"team": "platform"},
        None,
        "flush-1",
        GIB,
        GIB_MICRO_USD,
        CARD_DIGEST,
        False,
        None,
    )
    assert created_at.replace(tzinfo=UTC) == occurred_at
    assert [tuple(charge) for charge in charges] == [(ledger_id, GIB_MICRO_USD)]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_tokens_record_books_its_classes_on_the_turn_it_names(db: None) -> None:
    usage = Usage(input_tokens=600, output_tokens=100, cache_read_tokens=300)
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await _record(
            UNGATED_LEDGER,
            connection,
            workspace_id,
            service=MODELS_SERVICE,
            dimension=TOKENS_DIMENSION,
            amount=1_000,
            labels={TURN_LABEL: str(turn_id)},
            priced_micro_usd=5_000,
            model="claude-opus-4-8",
            usage=usage,
        )
        row = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.prompt_tokens,
                    tables.ledger.c.input_tokens,
                    tables.ledger.c.output_tokens,
                    tables.ledger.c.cache_read_tokens,
                    tables.ledger.c.token_classes_complete,
                    tables.ledger.c.model,
                ).where(tables.ledger.c.workspace_id == workspace_id)
            )
        ).one()
        cost = await read_turn_cost(connection, turn_id, TOKENS_DIMENSION)
    assert tuple(row) == (900, 600, 100, 300, True, "claude-opus-4-8")
    assert cost == TurnCost(
        tokens=1_000, micro_usd=5_000, model="claude-opus-4-8", cache_percent=33
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_record_service_usage_binds_the_turn_its_label_names(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        _neighbor, neighbor_turn_id = await _seed_turn(connection)
        named = {
            "own": str(turn_id),
            "unknown": str(uuid4()),
            "neighbor": str(neighbor_turn_id),
            "malformed": "turn-one",
        }
        for attempt, turn in named.items():
            await _record(
                UNGATED_LEDGER, connection, workspace_id, attempt=attempt, labels={TURN_LABEL: turn}
            )
        rows = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.attempt, tables.ledger.c.turn_id, tables.ledger.c.labels
                ).where(tables.ledger.c.workspace_id == workspace_id)
            )
        ).all()
    assert {row.attempt: row.turn_id for row in rows} == {
        "own": turn_id,
        "unknown": None,
        "neighbor": None,
        "malformed": None,
    }
    assert {row.attempt: row.labels for row in rows} == {
        attempt: {TURN_LABEL: turn} for attempt, turn in named.items()
    }


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
@pytest.mark.parametrize(
    ("service", "unit"),
    [(PROXY_SERVICE, TOKENS_DIMENSION), (PROXY_SERVICE, EGRESS_DIMENSION), ("ledger", "gib")],
)
async def test_record_service_usage_refuses_a_pair_the_check_lacks(
    db: None, service: str, unit: str
) -> None:
    async with workspace_tx() as connection:
        workspace_id, _turn_id = await _seed_turn(connection)
        with pytest.raises(ValueError, match="meters no"):
            await _record(UNGATED_LEDGER, connection, workspace_id, service=service, dimension=unit)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_record_service_usage_refuses_seventeen_labels(db: None) -> None:
    labels = {f"key{index}": "value" for index in range(17)}
    async with workspace_tx() as connection:
        workspace_id, _turn_id = await _seed_turn(connection)
        with pytest.raises(ValueError, match="at most 16 labels"):
            await _record(UNGATED_LEDGER, connection, workspace_id, labels=labels)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_record_service_usage_requires_usage_for_tokens(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, _turn_id = await _seed_turn(connection)
        with pytest.raises(ValueError, match="carries its usage"):
            await _record(
                UNGATED_LEDGER,
                connection,
                workspace_id,
                service=MODELS_SERVICE,
                dimension=TOKENS_DIMENSION,
                amount=100,
                model="claude-opus-4-8",
            )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
@pytest.mark.parametrize(
    ("changes", "refusal"),
    [
        ({"resource_id": None}, "names a resource or a session"),
        ({"resource_id": ""}, "names a resource or a session"),
        ({"amount": 0}, "amount is positive"),
        ({"priced_micro_usd": -1}, "price is not negative"),
        ({"labels": {"Team": "platform"}}, "label key"),
        ({"labels": {"team": "p" * 65}}, "label key"),
        ({"usage": Usage(input_tokens=1)}, "sum to its amount"),
    ],
)
async def test_record_service_usage_refuses_a_malformed_record(
    db: None, changes: dict[str, object], refusal: str
) -> None:
    async with workspace_tx() as connection:
        workspace_id, _turn_id = await _seed_turn(connection)
        with pytest.raises(ValueError, match=refusal):
            await _record(UNGATED_LEDGER, connection, workspace_id, **changes)
        written = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.ledger)
                .where(tables.ledger.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert written == 0


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
@pytest.mark.parametrize(
    "offset",
    [
        timedelta(seconds=-JOB_DAY_SETTLE_SECONDS - 60),
        timedelta(seconds=SERVICE_CLOCK_SKEW_SECONDS + 60),
        timedelta(days=-2),
        timedelta(days=30),
    ],
)
async def test_record_service_usage_refuses_a_stale_or_future_dated_record(
    db: None, offset: timedelta
) -> None:
    async with workspace_tx() as connection:
        workspace_id, _turn_id = await _seed_turn(connection)
        with pytest.raises(ValueError, match="occurred at most 900 seconds ago"):
            await _record(
                UNGATED_LEDGER, connection, workspace_id, occurred_at=datetime.now(UTC) + offset
            )
        written = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.ledger)
                .where(tables.ledger.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert written == 0


@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
async def test_the_service_backfill_moves_a_batch_and_stops(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(accounting, "LEDGER_SERVICE_BACKFILL_BATCH", 1)
    now = datetime.now(UTC)
    tokens, sandbox, egress, probe = uuid4(), uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        for ledger_id, turn, dimension in (
            (tokens, None, TOKENS_DIMENSION),
            (sandbox, turn_id, SANDBOX_TOKENS_DIMENSION),
            (egress, turn_id, EGRESS_DIMENSION),
            (probe, None, EGRESS_DIMENSION),
        ):
            await connection.execute(
                sa.insert(tables.ledger).values(
                    id=ledger_id,
                    workspace_id=workspace_id,
                    turn_id=turn,
                    service=SERVICE_OF_DIMENSION[dimension],
                    dimension=dimension,
                    amount=1,
                    priced_micro_usd=0,
                    model="",
                    created_at=now,
                    updated_at=now,
                )
            )
        await connection.execute(
            sa.update(tables.ledger)
            .where(tables.ledger.c.workspace_id == workspace_id)
            .values(service=None)
        )
    assert await ledger_service_backfill_candidates()() == (workspace_id,)
    moved = []
    for _tick in range(5):
        async with workspace_tx() as connection:
            moved.append(await ServiceBackfill(workspace_id).roll(connection, now))
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.id,
                    tables.ledger.c.service,
                    tables.ledger.c.dimension,
                    tables.ledger.c.labels,
                    tables.ledger.c.byok,
                ).where(tables.ledger.c.workspace_id == workspace_id)
            )
        ).all()
    assert moved == [1, 1, 1, 1, 0]
    assert await ledger_service_backfill_candidates()() == ()
    assert {row.id: tuple(row)[1:] for row in rows} == {
        tokens: ("models", "tokens", {}, None),
        sandbox: ("models", "tokens", {"via": "proxy", "turn": str(turn_id)}, False),
        egress: ("proxy", "requests", {"turn": str(turn_id)}, None),
        probe: ("proxy", "requests", {}, None),
    }


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


async def _seed_job_spend(
    connection: AsyncConnection, workspace_id: UUID, now: datetime, ages: tuple[timedelta, ...]
) -> None:
    for offset, age in enumerate(ages, start=1):
        await UNGATED_LEDGER.record_workspace_usage(
            connection, workspace_id, "claude-opus-4-8", Usage(input_tokens=offset * 100)
        )
        await connection.execute(
            sa.update(tables.ledger)
            .where(
                tables.ledger.c.workspace_id == workspace_id,
                tables.ledger.c.turn_id.is_(None),
                tables.ledger.c.amount == offset * 100,
            )
            .values(created_at=now - age)
        )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_folding_job_days_leaves_every_usage_answer_unchanged(db: None) -> None:
    """The fold is an index over rows the ledger still holds, so reading through it has to answer
    exactly what reading the ledger answered."""
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
        await _seed_job_spend(
            connection,
            workspace_id,
            now,
            (
                timedelta(days=1),
                timedelta(days=5),
                timedelta(days=30, hours=-1),
                timedelta(days=30, hours=1),
                timedelta(days=59, hours=23),
                timedelta(days=60, hours=1),
            ),
        )
    async with workspace_tx() as connection:
        before = await SpendRollup(workspace_id).read(connection, 30 * 86_400)
    async with workspace_tx() as connection:
        folded = await JobDayRollup(workspace_id).roll(connection, now)
    async with workspace_tx() as connection:
        after = await SpendRollup(workspace_id).read(connection, 30 * 86_400)
    assert folded
    assert after == before


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_folding_job_days_leaves_today_in_the_ledger_and_repeats_clean(db: None) -> None:
    """Today is still being written, so it stays in the ledger and the read takes it from there.
    A second pass finds nothing left to fold, which is what lets the job run every ten minutes."""
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        workspace_id, _turn_id = await _seed_turn(connection)
        await _seed_job_spend(
            connection, workspace_id, now, (timedelta(days=2), timedelta(minutes=5))
        )
    async with workspace_tx() as connection:
        first = await JobDayRollup(workspace_id).roll(connection, now)
    async with workspace_tx() as connection:
        second = await JobDayRollup(workspace_id).roll(connection, now)
        days = (
            await connection.execute(
                sa.select(tables.ledger_job_day.c.day, tables.ledger_job_day.c.amount).where(
                    tables.ledger_job_day.c.workspace_id == workspace_id
                )
            )
        ).all()
    assert first == ((now - timedelta(days=2)).date(),)
    assert second == ()
    assert [(row.day, row.amount) for row in days] == [((now - timedelta(days=2)).date(), 100)]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_fold_is_bounded_by_the_watermark_the_skip_was_built_from(db: None) -> None:
    """The read takes a snapshot per statement, so the job can commit another day between the
    watermark read and the fold read."""
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        workspace_id, _turn_id = await _seed_turn(connection)
        await _seed_job_spend(connection, workspace_id, now, (timedelta(days=3), timedelta(days=2)))
        await JobDayRollup(workspace_id).roll(connection, now)
    stale = (now - timedelta(days=3)).date()
    async with workspace_tx() as connection:
        bounded = (
            await connection.execute(
                rolled_days(workspace_id, stale, now - timedelta(days=30), None).totals
            )
        ).one()
    assert int(bounded.tokens) == 100


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_day_stays_open_until_a_transaction_stamped_inside_it_must_have_committed(
    db: None,
) -> None:
    """`record_workspace_usage` stamps `created_at` with the transaction's start, so one that
    began before midnight commits onto yesterday after midnight."""
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    async with workspace_tx() as connection:
        workspace_id, _turn_id = await _seed_turn(connection)
        await _seed_job_spend(connection, workspace_id, midnight, (timedelta(minutes=1),))
    async with workspace_tx() as connection:
        early = await JobDayRollup(workspace_id).roll(connection, midnight + timedelta(minutes=1))
    async with workspace_tx() as connection:
        settled = await JobDayRollup(workspace_id).roll(connection, midnight + timedelta(hours=1))
    assert early == ()
    assert settled == ((midnight - timedelta(days=1)).date(),)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_fold_names_its_candidates_without_reading_the_ledger(db: None) -> None:
    """A workspace that has never booked a turn-less row is still named, because asking the ledger
    which workspaces are behind is a full scan of the table this fold exists to stop reading."""
    async with workspace_tx() as connection:
        workspace_id, _turn_id = await _seed_turn(connection)
    candidates = job_day_candidates()
    assert workspace_id in await candidates()


ALL_USAGE_KEYS = frozenset(USAGE_KEYS)
SERVICE_KEYS = frozenset({"service", "dimension"})
FIRST_TOKEN, SECOND_TOKEN = UUID(int=1), UUID(int=2)


async def _service_row(
    connection: AsyncConnection, workspace_id: UUID, at: datetime, **changes: object
) -> None:
    attempt = str(uuid4())
    await _record(UNGATED_LEDGER, connection, workspace_id, attempt=attempt, **changes)
    await connection.execute(
        sa.update(tables.ledger)
        .where(tables.ledger.c.workspace_id == workspace_id, tables.ledger.c.attempt == attempt)
        .values(created_at=at)
    )


def _tokens_record(backend: str, **changes: object) -> dict[str, object]:
    return {
        "service": MODELS_SERVICE,
        "dimension": TOKENS_DIMENSION,
        "backend": backend,
        "amount": 1_000,
        "usage": Usage(input_tokens=1_000),
        "model": "claude-opus-4-8",
        "priced_micro_usd": 5_000,
    } | changes


@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
async def test_folding_job_days_keeps_service_backend_token_and_byok(db: None) -> None:
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    first, second = midnight - timedelta(days=3, hours=-12), midnight - timedelta(days=2, hours=-12)
    requests = {"dimension": REQUESTS_DIMENSION, "amount": 5, "priced_micro_usd": 5}
    async with workspace_tx() as connection:
        workspace_id, _turn_id = await _seed_turn(connection)
        for at, token_id, changes in (
            (first, FIRST_TOKEN, {}),
            (first, FIRST_TOKEN, requests),
            (first, SECOND_TOKEN, {"byok": True}),
            (second, FIRST_TOKEN, _tokens_record("openrouter")),
            (second, SECOND_TOKEN, {}),
            (second, SECOND_TOKEN, {}),
        ):
            await _service_row(connection, workspace_id, at, token_id=token_id, **changes)
    since = midnight - timedelta(days=5)
    async with workspace_tx() as connection:
        until = datetime.now(UTC)
        by_key = await usage_lines(connection, workspace_id, since, until, keys=ALL_USAGE_KEYS)
        by_token = await usage_lines(
            connection, workspace_id, since, until, keys=frozenset({"token"})
        )
    async with workspace_tx() as connection:
        folded = await JobDayRollup(workspace_id).roll(connection, until)
        kept = (
            await connection.execute(
                sa.select(
                    tables.ledger_job_day.c.day,
                    tables.ledger_job_day.c.service,
                    tables.ledger_job_day.c.dimension,
                    tables.ledger_job_day.c.backend,
                    tables.ledger_job_day.c.token_id,
                    tables.ledger_job_day.c.byok,
                    tables.ledger_job_day.c.amount,
                    tables.ledger_job_day.c.priced_micro_usd,
                ).where(tables.ledger_job_day.c.workspace_id == workspace_id)
            )
        ).all()
    async with workspace_tx() as connection:
        folded_by_key = await usage_lines(
            connection, workspace_id, since, until, keys=ALL_USAGE_KEYS
        )
        folded_by_token = await usage_lines(
            connection, workspace_id, since, until, keys=frozenset({"token"})
        )
    assert folded == (first.date(), second.date())
    assert {tuple(row) for row in kept} == {
        (first.date(), "proxy", "gib", None, FIRST_TOKEN, False, GIB, GIB_MICRO_USD),
        (first.date(), "proxy", "requests", None, FIRST_TOKEN, False, 5, 5),
        (first.date(), "proxy", "gib", None, SECOND_TOKEN, True, GIB, GIB_MICRO_USD),
        (second.date(), "models", "tokens", "openrouter", FIRST_TOKEN, False, 1_000, 5_000),
        (second.date(), "proxy", "gib", None, SECOND_TOKEN, False, 2 * GIB, 2 * GIB_MICRO_USD),
    }
    assert by_key == (
        UsageLine(first.date(), "proxy", "gib", None, False, FIRST_TOKEN, {}, GIB, GIB_MICRO_USD),
        UsageLine(first.date(), "proxy", "gib", None, True, SECOND_TOKEN, {}, GIB, GIB_MICRO_USD),
        UsageLine(first.date(), "proxy", "requests", None, False, FIRST_TOKEN, {}, 5, 5),
        UsageLine(
            second.date(), "models", "tokens", "openrouter", False, FIRST_TOKEN, {}, 1_000, 5_000
        ),
        UsageLine(
            second.date(), "proxy", "gib", None, False, SECOND_TOKEN, {}, 2 * GIB, 2 * GIB_MICRO_USD
        ),
    )
    assert by_token == (
        UsageLine(first.date(), "", "", None, False, FIRST_TOKEN, {}, GIB + 5, GIB_MICRO_USD + 5),
        UsageLine(first.date(), "", "", None, False, SECOND_TOKEN, {}, GIB, GIB_MICRO_USD),
        UsageLine(second.date(), "", "", None, False, FIRST_TOKEN, {}, 1_000, 5_000),
        UsageLine(second.date(), "", "", None, False, SECOND_TOKEN, {}, 2 * GIB, 2 * GIB_MICRO_USD),
    )
    assert (folded_by_key, folded_by_token) == (by_key, by_token)


@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
async def test_usage_lines_group_by_label_over_raw_rows(db: None) -> None:
    now = datetime.now(UTC)
    platform = {"team": "platform"}
    async with workspace_tx() as connection:
        workspace_id, _turn_id = await _seed_turn(connection)
        for at, labels in (
            (now - timedelta(days=2), platform),
            (now, platform),
            (now, platform),
            (now, {"team": "infra", "env": "prod"}),
            (now, {}),
        ):
            await _service_row(connection, workspace_id, at, labels=labels)
    async with workspace_tx() as connection:
        await JobDayRollup(workspace_id).roll(connection, now)
    since, until = now - timedelta(days=5), now + timedelta(minutes=1)
    async with workspace_tx() as connection:
        teams = await usage_lines(
            connection,
            workspace_id,
            since,
            until,
            keys=SERVICE_KEYS,
            label_keys=frozenset({"team"}),
        )
        chosen = await usage_lines(
            connection, workspace_id, since, until, keys=SERVICE_KEYS, labels=platform
        )
        every_day = await usage_lines(connection, workspace_id, since, until, keys=SERVICE_KEYS)
    today = now.date()
    assert teams == (
        UsageLine(today, "proxy", "gib", None, False, None, {}, GIB, GIB_MICRO_USD),
        UsageLine(today, "proxy", "gib", None, False, None, {"team": "infra"}, GIB, GIB_MICRO_USD),
        UsageLine(today, "proxy", "gib", None, False, None, platform, 2 * GIB, 2 * GIB_MICRO_USD),
    )
    assert chosen == (
        UsageLine(today, "proxy", "gib", None, False, None, {}, 2 * GIB, 2 * GIB_MICRO_USD),
    )
    assert [(line.day, line.amount) for line in every_day] == [
        ((now - timedelta(days=2)).date(), GIB),
        (today, 4 * GIB),
    ]


@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
async def test_usage_lines_filters_backend_and_byok(db: None) -> None:
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await _service_row(connection, workspace_id, now, **_tokens_record("anthropic"))
        await _service_row(connection, workspace_id, now, **_tokens_record("openrouter", byok=True))
        await record_egress_request(connection, workspace_id, turn_id)
    keys = frozenset({"service", "dimension", "backend", "byok"})
    today = now.date()
    own_key = UsageLine(today, "models", "tokens", "openrouter", True, None, {}, 1_000, 5_000)
    platform = UsageLine(today, "models", "tokens", "anthropic", False, None, {}, 1_000, 5_000)
    egress = UsageLine(today, "proxy", "requests", None, False, None, {}, 1, 0)
    since = now - timedelta(hours=1)
    async with workspace_tx() as connection:
        until = datetime.now(UTC) + timedelta(minutes=1)
        by_backend = await usage_lines(
            connection, workspace_id, since, until, keys=keys, backend="openrouter"
        )
        paid_by_key = await usage_lines(
            connection, workspace_id, since, until, keys=keys, byok=True
        )
        paid_by_us = await usage_lines(
            connection, workspace_id, since, until, keys=keys, byok=False
        )
        neither = await usage_lines(
            connection, workspace_id, since, until, keys=keys, backend="anthropic", byok=True
        )
    assert by_backend == paid_by_key == (own_key,)
    assert paid_by_us == (platform, egress)
    assert neither == ()


@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
async def test_usage_lines_buckets_by_day(db: None) -> None:
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    first, middle, last = (midnight - timedelta(days=days) for days in (3, 2, 1))
    since, until = first + timedelta(hours=6), last + timedelta(hours=18)
    async with workspace_tx() as connection:
        workspace_id, _turn_id = await _seed_turn(connection)
        for at in (
            since - timedelta(hours=1),
            since + timedelta(hours=1),
            first + timedelta(hours=23),
            middle + timedelta(hours=12),
            until - timedelta(hours=1),
            until + timedelta(hours=1),
        ):
            await _service_row(connection, workspace_id, at)
    async with workspace_tx() as connection:
        before = await usage_lines(connection, workspace_id, since, until, keys=SERVICE_KEYS)
    async with workspace_tx() as connection:
        folded = await JobDayRollup(workspace_id).roll(connection, datetime.now(UTC))
    async with workspace_tx() as connection:
        after = await usage_lines(connection, workspace_id, since, until, keys=SERVICE_KEYS)
    assert {first.date(), middle.date()} <= set(folded)
    assert before == (
        UsageLine(first.date(), "proxy", "gib", None, False, None, {}, 2 * GIB, 2 * GIB_MICRO_USD),
        UsageLine(middle.date(), "proxy", "gib", None, False, None, {}, GIB, GIB_MICRO_USD),
        UsageLine(last.date(), "proxy", "gib", None, False, None, {}, GIB, GIB_MICRO_USD),
    )
    assert after == before


@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
async def test_token_spend_counts_platform_paid_rows_in_the_window_across_the_fold(
    db: None,
) -> None:
    midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    since = midnight - timedelta(days=2, hours=-12)
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        now = datetime.now(UTC)
        for at, changes in (
            (since - timedelta(days=1), {"priced_micro_usd": 100_000}),
            (since - timedelta(hours=6), {"priced_micro_usd": 10}),
            (since + timedelta(hours=6), {"priced_micro_usd": 100}),
            (since + timedelta(hours=8), {"priced_micro_usd": 999, "byok": True}),
            (since + timedelta(days=1), {"priced_micro_usd": 1_000}),
            (
                since + timedelta(days=1),
                {"priced_micro_usd": 30, "labels": {TURN_LABEL: str(turn_id)}},
            ),
            (now, {"priced_micro_usd": 7}),
            (now, {"priced_micro_usd": 50_000, "token_id": SECOND_TOKEN}),
        ):
            await _service_row(
                connection, workspace_id, at, **({"token_id": FIRST_TOKEN} | changes)
            )
    async with workspace_tx() as connection:
        before = await token_spend(connection, workspace_id, FIRST_TOKEN, since)
    async with workspace_tx() as connection:
        await JobDayRollup(workspace_id).roll(connection, datetime.now(UTC))
    async with workspace_tx() as connection:
        after = await token_spend(connection, workspace_id, FIRST_TOKEN, since)
    assert before == 100 + 1_000 + 30 + 7
    assert after == before + 10


@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
async def test_session_spend_sums_one_session(db: None) -> None:
    now = datetime.now(UTC)
    session_id = uuid4()
    async with workspace_tx() as connection:
        workspace_id, _turn_id = await _seed_turn(connection)
        neighbor, _neighbor_turn = await _seed_turn(connection)
        for at, changes in (
            (now - timedelta(days=2), {}),
            (now, {"dimension": REQUESTS_DIMENSION, "amount": 3, "priced_micro_usd": 3}),
            (now, {"byok": True}),
            (now, {"session_id": uuid4()}),
        ):
            await _service_row(
                connection,
                workspace_id,
                at,
                **({"session_id": session_id, "resource_id": None} | changes),
            )
        await _service_row(connection, neighbor, now, session_id=session_id, resource_id=None)
    async with workspace_tx() as connection:
        await JobDayRollup(workspace_id).roll(connection, now)
    async with workspace_tx() as connection:
        spent = await session_spend(connection, workspace_id, session_id)
    assert spent == GIB_MICRO_USD + 3


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_usage_lines_refuses_an_unknown_key_and_an_inverted_window(db: None) -> None:
    now = datetime.now(UTC)
    hour_ago = now - timedelta(hours=1)
    async with workspace_tx() as connection:
        workspace_id, _turn_id = await _seed_turn(connection)
        with pytest.raises(ValueError, match="groups by service, dimension, backend"):
            await usage_lines(connection, workspace_id, hour_ago, now, keys=frozenset({"session"}))
        for since in (now, now + timedelta(hours=1)):
            with pytest.raises(ValueError, match="starts before it ends"):
                await usage_lines(connection, workspace_id, since, now, keys=SERVICE_KEYS)
        with pytest.raises(ValueError, match="label key"):
            await usage_lines(
                connection,
                workspace_id,
                hour_ago,
                now,
                keys=SERVICE_KEYS,
                label_keys=frozenset({"Team"}),
            )
        with pytest.raises(ValueError, match="label key"):
            await usage_lines(
                connection, workspace_id, hour_ago, now, keys=SERVICE_KEYS, labels={"te am": "x"}
            )


@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
async def test_usage_lines_reads_in_two_statements(db: None) -> None:
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        workspace_id, _turn_id = await _seed_turn(connection)
        for at in (now - timedelta(days=2), now):
            await _service_row(connection, workspace_id, at, labels={"team": "platform"})

    async def read(**options: frozenset[str]) -> tuple[tuple[UsageLine, ...], list[str]]:
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

        async with workspace_tx() as connection:
            sa.event.listen(connection.sync_connection, "before_cursor_execute", record)
            try:
                lines = await usage_lines(
                    connection,
                    workspace_id,
                    now - timedelta(days=5),
                    now + timedelta(minutes=1),
                    **options,
                )
            finally:
                sa.event.remove(connection.sync_connection, "before_cursor_execute", record)
        return lines, statements

    unfolded, unfolded_statements = await read(keys=ALL_USAGE_KEYS)
    async with workspace_tx() as connection:
        await JobDayRollup(workspace_id).roll(connection, now)
    folded, folded_statements = await read(keys=ALL_USAGE_KEYS)
    labelled, labelled_statements = await read(keys=SERVICE_KEYS, label_keys=frozenset({"team"}))
    assert (len(unfolded_statements), len(folded_statements), len(labelled_statements)) == (2, 2, 2)
    assert "ledger_job_day" in folded_statements[1]
    assert "ledger_job_day" not in labelled_statements[1]
    assert folded == unfolded
    assert [line.labels for line in labelled] == [{"team": "platform"}]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_spend_by_origin_gathers_a_subagents_burn_under_the_channel(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await connection.execute(
            sa.update(tables.conversation)
            .where(tables.conversation.c.workspace_id == workspace_id)
            .values(surface="slack", surface_label="#eng")
        )
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
        child_turn = await _spawn_child_turn(connection, workspace_id, turn_id)
        grandchild = await _spawn_child_turn(connection, workspace_id, child_turn)
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, child_turn, "claude-opus-4-8", FULL_USAGE
        )
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, grandchild, "claude-opus-4-8", FULL_USAGE
        )
    async with workspace_tx() as connection:
        report = await SpendRollup(workspace_id).read(connection, 3600)
    assert [(o.surface, o.label, o.tokens, o.priced_micro_usd) for o in report.by_origin] == [
        ("slack", "#eng", 30_000, 289_500)
    ]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_spend_by_origin_names_an_unlabelled_surface_and_a_turnless_job(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
        await UNGATED_LEDGER.record_workspace_usage(
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
            await UNGATED_LEDGER.record_turn_usage(
                connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE, attempt
            )
        child_turn = await _spawn_child_turn(connection, workspace_id, turn_id)
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, child_turn, "claude-opus-4-8", FULL_USAGE
        )
        for _ in range(2):
            await UNGATED_LEDGER.record_workspace_usage(
                connection, workspace_id, "claude-opus-4-8", FULL_USAGE
            )
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
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
        child_turn = await _spawn_child_turn(connection, workspace_id, turn_id)
        await UNGATED_LEDGER.record_turn_usage(
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
async def test_spend_totals_answer_what_the_rollup_reports_for_the_workspace(db: None) -> None:
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
        child_turn = await _spawn_child_turn(connection, workspace_id, turn_id)
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, child_turn, "gpt-5.6-terra", OPENAI_FULL_USAGE
        )
        await UNGATED_LEDGER.record_image_usage(
            connection, workspace_id, turn_id, "openai/gpt-image-2", 1, 130_000
        )
        await record_egress_request(connection, workspace_id, turn_id)
        await _seed_job_spend(
            connection, workspace_id, now, (timedelta(minutes=90), timedelta(days=2))
        )
    async with workspace_tx() as connection:
        folded = await JobDayRollup(workspace_id).roll(connection, now)
    assert folded
    for window_seconds in (3600, None):
        async with workspace_tx() as connection:
            totals = await SpendRollup(workspace_id).read_totals(connection, window_seconds)
            report = await SpendRollup(workspace_id).read(connection, window_seconds)
        assert totals == SpendTotals(
            window_seconds,
            report.total_micro_usd,
            report.by_dimension,
            report.by_service,
            report.usage,
        )
        assert [(row.label, row.tokens) for row in totals.usage.by_execution] == [
            ("", 10_000),
            ("research", 10_000),
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
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
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
            statements.clear()
            totals = await SpendRollup(workspace_id).read_totals(connection, 3600)
            totals_statements = len(statements)
            statements.clear()
            await SpendRollup(workspace_id).read_totals(connection, None)
            all_time_totals_statements = len(statements)
        finally:
            sa.event.remove(connection.sync_connection, "before_cursor_execute", record)
    assert workspace_statements == 6
    assert member_statements == 3
    assert all_time_statements == 5
    assert totals_statements == 3
    assert all_time_totals_statements == 2
    assert totals.usage == workspace.usage
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
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_USAGE
        )
        await UNGATED_LEDGER.record_workspace_usage(
            connection, workspace_id, "claude-opus-4-8", FULL_USAGE
        )
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
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, mine, "claude-opus-4-8", FULL_USAGE
        )
        await record_egress_request(connection, workspace_id, mine)
        await UNGATED_LEDGER.record_turn_usage(
            connection, workspace_id, theirs, "claude-opus-4-8", FULL_USAGE
        )
        await UNGATED_LEDGER.record_turn_usage(
            connection,
            workspace_id,
            subagent_turn,
            "claude-opus-4-8",
            FULL_USAGE,
        )
        await UNGATED_LEDGER.record_workspace_usage(
            connection, workspace_id, "claude-opus-4-8", FULL_USAGE
        )
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
        "requests": (1, 0),
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
        await UNGATED_LEDGER.record_turn_usage(
            connection,
            workspace_id,
            turn_id,
            "claude-opus-4-8",
            Usage(input_tokens=1000),
        )
        await UNGATED_LEDGER.record_workspace_usage(
            connection, workspace_id, "claude-opus-4-8", Usage(input_tokens=250)
        )
        await UNGATED_LEDGER.record_sandbox_tokens(
            connection, workspace_id, turn_id, "claude-opus-4-8", Usage(input_tokens=175)
        )
        await record_egress_request(connection, workspace_id, turn_id)
        await _record(UNGATED_LEDGER, connection, workspace_id)
        await _record(
            UNGATED_LEDGER,
            connection,
            workspace_id,
            dimension=REQUESTS_DIMENSION,
            amount=3,
            priced_micro_usd=0,
        )

    settled = await _pending(workspace_id)
    assert {export.dimension for export in settled} == {"tokens", "gib"}
    assert all(export.from_amount == 0 and export.price_digest for export in settled)
    assert {export.amount for export in settled} == {1000, 250, 175, GIB}
    sandbox = next(export for export in settled if export.amount == 175)
    assert (sandbox.turn_id, sandbox.byok) == (turn_id, False)

    async with workspace_tx() as connection:
        await _settle_turn(connection, turn_id, age_seconds=PAST_EXPORT_MARGIN_SECONDS)
    assert await _pending(workspace_id) == settled


@pytest.mark.parametrize("database_url", ["sqlite", "postgres"], indirect=True)
async def test_proxy_rows_mint_at_once_and_carry_their_service_fields(db: None) -> None:
    token_id, session_id = uuid4(), uuid4()
    record = {
        "token_id": token_id,
        "session_id": session_id,
        "resource_id": None,
        "backend": "nat",
        "labels": {"team": "platform"},
    }
    async with workspace_tx() as connection:
        workspace_id, _turn_id = await _seed_turn(connection)
        await _record(UNGATED_LEDGER, connection, workspace_id, **record)
        await _record(UNGATED_LEDGER, connection, workspace_id, **record, attempt="own", byok=True)
        await _record(
            UNGATED_LEDGER,
            connection,
            workspace_id,
            **record,
            dimension=REQUESTS_DIMENSION,
            amount=4,
            priced_micro_usd=0,
        )
    exports = {export.byok: export for export in await _pending(workspace_id)}
    assert exports == {
        byok: UsageExport(
            ledger_id=service_ledger_id_for(
                workspace_id, PROXY_SERVICE, str(session_id), GIB_DIMENSION, attempt
            ),
            from_amount=0,
            amount=GIB,
            priced_micro_usd=GIB_MICRO_USD,
            dimension=GIB_DIMENSION,
            model="",
            price_digest=CARD_DIGEST,
            turn_id=None,
            byok=byok,
            occurred_at=exports[byok].occurred_at,
            service=PROXY_SERVICE,
            backend="nat",
            token_id=token_id,
            session_id=session_id,
            labels={"team": "platform"},
        )
        for byok, attempt in ((False, "flush-1"), (True, "own"))
    }


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_images_export_with_their_settled_turn_as_platform_served(db: None) -> None:
    """An `images` row accumulates while its turn runs, so nothing mints until the turn is
    terminal and past the margin."""
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await connection.execute(
            sa.update(tables.turn).where(tables.turn.c.id == turn_id).values(byok=True)
        )
        await UNGATED_LEDGER.record_image_usage(
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
        await UNGATED_LEDGER.record_sandbox_tokens(
            connection, workspace_id, turn_id, "claude-opus-4-8", Usage(input_tokens=175)
        )
        await _settle_turn(connection, turn_id, age_seconds=PAST_EXPORT_MARGIN_SECONDS)
    (first,) = await _pending(workspace_id)
    assert (first.from_amount, first.amount) == (0, 175)

    async with workspace_tx() as connection:
        await UNGATED_LEDGER.record_sandbox_tokens(
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
        await UNGATED_LEDGER.record_turn_usage(
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
        await UNGATED_LEDGER.record_turn_usage(
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
        await UNGATED_LEDGER.record_workspace_usage(
            connection, workspace_id, "claude-opus-4-8", Usage(input_tokens=100)
        )
        await UNGATED_LEDGER.record_workspace_usage(
            connection, workspace_id, "claude-opus-4-8", Usage(input_tokens=999)
        )
        await UNGATED_LEDGER.record_workspace_usage(
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_recovery_of_one_attempt_bills_the_key_that_served_it(db: None) -> None:
    """A crash recovery re-executes the same attempt and must bill the tokens already burned
    under the verdict they were burned under."""
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
        funding=PLATFORM_FUNDED,
        payer=PLATFORM_PAYER,
        alternates={
            model: loop_queue._Rates(
                input=1,
                output=2,
                cache_read=3,
                cache_write_5m=4,
                cache_write_30m=5,
                cache_write_1h=6,
            )
            for model in ("claude-sonnet-5", "claude-opus-5")
        },
        alternate_order=("claude-sonnet-5", "claude-opus-5"),
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
    assert tuple(route.model for route in recovered.routes()) == (
        "claude-opus-4-8",
        "claude-sonnet-5",
        "claude-opus-5",
    )
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
        candidates=("claude-opus-4-8",),
    ).resolve()

    recovered_registry = Registry("anthropic_api_key")
    with pytest.raises(ModelFundingChanged, match="payer changed"):
        await loop_queue._TurnBilling(
            registry=cast(ModelRegistry, recovered_registry),
            turn_id=turn_id,
            attempt="attempt-1",
            candidates=("gpt-5.4",),
        ).resolve()

    assert (billing.funding, billing.payer, byok) == (
        KEY_FUNDED,
        "anthropic_api_key:member:first",
        True,
    )
    assert first_registry.requested == ["claude-opus-4-8"]
    assert recovered_registry.requested == ["claude-opus-4-8"]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_attempt_freezes_a_rate_and_payer_for_every_model_route(db: None) -> None:
    async with workspace_tx() as connection:
        _, keyed_turn = await _seed_turn(connection)
        _, plan_turn = await _seed_turn(connection)
        _, mixed_turn = await _seed_turn(connection)

    class Registry:
        pricing = CORE_PRICING

        def __init__(self, payers: dict[str, ModelPayer]) -> None:
            self.payers = payers

        async def client_for(self, model: str) -> ResolvedModelClient:
            payer = self.payers[model]
            return ResolvedModelClient(
                cast(ModelClient, object()),
                payer.funding,
                payer.payer,
            )

        async def payer_for(self, model: str) -> ModelPayer:
            return self.payers[model]

    models = ("gpt-5.6-sol", "claude-opus-5")
    keyed_registry = Registry({model: ModelPayer(KEY_FUNDED, "workspace-key") for model in models})
    keyed, _, keyed_byok = await loop_queue._TurnBilling(
        registry=cast(ModelRegistry, keyed_registry),
        turn_id=keyed_turn,
        attempt="attempt-1",
        candidates=models,
    ).resolve()
    plan_registry = cast(
        ModelRegistry,
        Registry({model: ModelPayer(PLAN_FUNDED, f"member/{model}") for model in models}),
    )
    plan, _, plan_byok = await loop_queue._TurnBilling(
        registry=plan_registry,
        turn_id=plan_turn,
        attempt="attempt-1",
        candidates=models,
    ).resolve()
    recovered, _, _ = await loop_queue._TurnBilling(
        registry=plan_registry,
        turn_id=plan_turn,
        attempt="attempt-1",
        candidates=models,
    ).resolve()
    mixed, _, mixed_byok = await loop_queue._TurnBilling(
        registry=cast(
            ModelRegistry,
            Registry(
                {
                    "gpt-5.6-sol": ModelPayer(PLAN_FUNDED, "member/openai"),
                    "claude-opus-5": ModelPayer(PLATFORM_FUNDED, PLATFORM_PAYER),
                }
            ),
        ),
        turn_id=mixed_turn,
        attempt="attempt-1",
        candidates=models,
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
    assert mixed.pricing().prices == {
        "gpt-5.6-sol": loop_queue.PLAN_SERVED_PRICE,
        "claude-opus-5": CORE_PRICES["claude-opus-5"],
    }
    assert [(route.model, route.funding, route.payer) for route in mixed.routes()] == [
        ("gpt-5.6-sol", PLAN_FUNDED, "member/openai"),
        ("claude-opus-5", PLATFORM_FUNDED, PLATFORM_PAYER),
    ]
    assert recovered == plan
    assert (keyed_byok, plan_byok, mixed_byok) == (True, True, True)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_resumed_attempt_decides_against_the_key_that_will_serve_it(db: None) -> None:
    """The money keys on the attempt: a parked turn's resume re-runs every round for real under a
    fresh workflow id and bills each burn under that attempt."""
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
        await UNGATED_LEDGER.record_workspace_usage(
            connection, metered, "claude-opus-4-8", Usage(input_tokens=10)
        )
    assert await accounting.metered_workspaces()() == (metered,)


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
@pytest.mark.parametrize("byok", [False, True])
async def test_background_provider_call_id_deduplicates_and_refuses_changed_usage(
    db: None, byok: bool
) -> None:
    async with workspace_tx() as connection:
        workspace_id, _ = await _seed_turn(connection)
    ctx = ExtensionContext(
        ScopedStore("provider"),
        CredentialAccess(frozenset()),
        ledger=Ledger(gates=(SampleGate(GateDeploy(public_base_url=None, home_surface=None)),)),
    )
    price = ModelPrice(42_000, 0, 0, 0, 0)
    usage = Usage(input_tokens=1000, output_tokens=20)
    call_id = uuid4()
    with ws(workspace_id):
        await ctx.meter_tokens(call_id, "provider", usage, price, byok=byok)
        await ctx.meter_tokens(call_id, "provider", usage, price, byok=byok)
        with pytest.raises(accounting.TurnUsageConflict):
            await ctx.meter_tokens(call_id, "provider", Usage(input_tokens=1001), price, byok=byok)
        await ctx.meter_tokens(uuid4(), "provider", usage, price, byok=byok)
        async with workspace_tx() as connection:
            rows = (await connection.execute(sa.select(tables.ledger))).mappings().all()
        assert len(rows) == 2
        assert all(row["turn_id"] is None and row["workspace_id"] == workspace_id for row in rows)
        assert all(row["input_tokens"] == 1000 and row["output_tokens"] == 20 for row in rows)
        assert all(row["priced_micro_usd"] == 42 and row["byok"] is byok for row in rows)
        async with workspace_tx() as connection:
            charges = (await connection.execute(sa.select(CHARGE_TABLE))).mappings().all()
    assert sorted(charge["ledger_id"] for charge in charges) == sorted(row["id"] for row in rows)
    assert all(
        charge["turn_id"] is None
        and charge["delta_micro_usd"] == 42
        and charge["platform_paid"] is not byok
        for charge in charges
    )
