from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from test_accounting import _seed_turn
from ufo_ext_sample.spend import ALLOWANCE_TABLE, CHARGE_TABLE, AllowanceRaised, SampleGate, allow

from ufo.db import workspace_tx
from ufo.harness.models.catalog import ANTHROPIC_KEY_SLOT
from ufo.runtime.billing.accounting import (
    GIB_DIMENSION,
    IMAGES_DIMENSION,
    MODELS_SERVICE,
    PROXY_SERVICE,
    TOKENS_DIMENSION,
    VIDEOS_DIMENSION,
    Ledger,
)
from ufo.runtime.billing.spend import GateDeploy
from ufo.schema import tables
from ufo.schema.records import Usage, ledger_id_for, service_ledger_id_for

LEDGER = Ledger(gates=(SampleGate(GateDeploy(public_base_url=None, home_surface=None)),))
MODEL = "claude-opus-4-8"
DOLLAR = 1_000_000
GIB_MICRO_USD = 168_750

pytestmark = pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)


async def _charges(workspace_id: UUID) -> list[tuple[UUID, UUID | None, str, int, bool]]:
    async with workspace_tx() as connection:
        rows = await connection.execute(
            sa.select(
                CHARGE_TABLE.c.ledger_id,
                CHARGE_TABLE.c.turn_id,
                CHARGE_TABLE.c.dimension,
                CHARGE_TABLE.c.delta_micro_usd,
                CHARGE_TABLE.c.platform_paid,
            ).where(CHARGE_TABLE.c.workspace_id == workspace_id)
        )
        return [tuple(row) for row in rows]


async def _priced(workspace_id: UUID) -> int:
    async with workspace_tx() as connection:
        return int(
            (
                await connection.execute(
                    sa.select(
                        sa.func.coalesce(sa.func.sum(tables.ledger.c.priced_micro_usd), 0)
                    ).where(tables.ledger.c.workspace_id == workspace_id)
                )
            ).scalar_one()
        )


@pytest.mark.parametrize("byok", [False, True])
async def test_a_turns_cumulative_snapshots_charge_each_delta_once(db: None, byok: bool) -> None:
    first = Usage(input_tokens=1_000, output_tokens=100)
    second = Usage(input_tokens=3_000, output_tokens=500)
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        for snapshot in (first, second, first, second):
            await LEDGER.record_turn_usage(
                connection, workspace_id, turn_id, MODEL, snapshot, "attempt", byok=byok
            )
    ledger_id = ledger_id_for(workspace_id, turn_id, TOKENS_DIMENSION, "attempt")
    charges = await _charges(workspace_id)
    assert [charge[:3] for charge in charges] == [(ledger_id, turn_id, TOKENS_DIMENSION)] * 2
    assert sum(charge[3] for charge in charges) == await _priced(workspace_id)
    assert all(charge[4] is not byok for charge in charges)


@pytest.mark.parametrize("byok", [False, True])
async def test_a_background_call_charges_once_per_call(db: None, byok: bool) -> None:
    call_id = uuid4()
    usage = Usage(input_tokens=2_000, output_tokens=200)
    async with workspace_tx() as connection:
        workspace_id, _ = await _seed_turn(connection)
        for _ in range(2):
            await LEDGER.record_workspace_usage(
                connection, workspace_id, MODEL, usage, byok=byok, call_id=call_id
            )
    assert await _charges(workspace_id) == [
        (call_id, None, TOKENS_DIMENSION, await _priced(workspace_id), not byok)
    ]


async def test_a_key_arriving_mid_turn_does_not_make_that_turn_free(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await allow(connection, workspace_id, DOLLAR, "park")
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot=ANTHROPIC_KEY_SLOT,
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await LEDGER.record_turn_usage(
            connection,
            workspace_id,
            turn_id,
            MODEL,
            Usage(input_tokens=4_000, output_tokens=400),
            "attempt",
            byok=False,
        )
        remaining = (
            await connection.execute(
                sa.select(ALLOWANCE_TABLE.c.remaining_micro_usd).where(
                    ALLOWANCE_TABLE.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
    priced = await _priced(workspace_id)
    assert await _charges(workspace_id) == [
        (
            ledger_id_for(workspace_id, turn_id, TOKENS_DIMENSION, "attempt"),
            turn_id,
            TOKENS_DIMENSION,
            priced,
            True,
        )
    ]
    assert remaining == DOLLAR - priced


async def test_media_spend_charges_each_increment(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await allow(connection, workspace_id, DOLLAR, "park")
        for _ in range(2):
            await LEDGER.record_image_usage(connection, workspace_id, turn_id, "image", 1, 40_000)
        await LEDGER.record_video_usage(connection, workspace_id, turn_id, "video", 1, 60_000)
        remaining = (
            await connection.execute(
                sa.select(ALLOWANCE_TABLE.c.remaining_micro_usd).where(
                    ALLOWANCE_TABLE.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
    images = ledger_id_for(workspace_id, turn_id, IMAGES_DIMENSION)
    charges = await _charges(workspace_id)
    assert [(charge[0], charge[2], charge[4]) for charge in charges] == [
        (images, IMAGES_DIMENSION, True),
        (images, IMAGES_DIMENSION, True),
        (ledger_id_for(workspace_id, turn_id, VIDEOS_DIMENSION), VIDEOS_DIMENSION, True),
    ]
    assert sum(charge[3] for charge in charges) == await _priced(workspace_id)
    assert remaining == DOLLAR - await _priced(workspace_id)


async def _remaining(workspace_id: UUID) -> int:
    async with workspace_tx() as connection:
        return int(
            (
                await connection.execute(
                    sa.select(ALLOWANCE_TABLE.c.remaining_micro_usd).where(
                        ALLOWANCE_TABLE.c.workspace_id == workspace_id
                    )
                )
            ).scalar_one()
        )


async def test_a_service_record_charges_once_per_idempotency_key(db: None) -> None:
    session_id = uuid4()
    async with workspace_tx() as connection:
        workspace_id, _ = await _seed_turn(connection)
        await allow(connection, workspace_id, DOLLAR, "park")
        for attempt in ("flush-1", "flush-1", "flush-2"):
            await LEDGER.record_service_usage(
                connection,
                workspace_id,
                service=PROXY_SERVICE,
                dimension=GIB_DIMENSION,
                backend=None,
                amount=2**30,
                token_id=None,
                session_id=session_id,
                labels={},
                resource_id=None,
                attempt=attempt,
                occurred_at=datetime.now(UTC),
                byok=False,
                priced_micro_usd=GIB_MICRO_USD,
                price_digest="sha256:card",
            )
    assert await _charges(workspace_id) == [
        (
            service_ledger_id_for(
                workspace_id, PROXY_SERVICE, str(session_id), GIB_DIMENSION, attempt
            ),
            None,
            GIB_DIMENSION,
            GIB_MICRO_USD,
            True,
        )
        for attempt in ("flush-1", "flush-2")
    ]
    assert await _remaining(workspace_id) == DOLLAR - 2 * GIB_MICRO_USD


async def test_a_byok_service_record_charges_nothing(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, _ = await _seed_turn(connection)
        await allow(connection, workspace_id, DOLLAR, "park")
        await LEDGER.record_service_usage(
            connection,
            workspace_id,
            service=MODELS_SERVICE,
            dimension=TOKENS_DIMENSION,
            backend="anthropic",
            amount=1_100,
            token_id=None,
            session_id=uuid4(),
            labels={},
            resource_id=None,
            attempt="flush-1",
            occurred_at=datetime.now(UTC),
            byok=True,
            priced_micro_usd=8_000,
            price_digest="sha256:card",
            model=MODEL,
            usage=Usage(input_tokens=1_000, output_tokens=100),
        )
    ((_, _, dimension, delta, platform_paid),) = await _charges(workspace_id)
    assert (dimension, delta, platform_paid) == (TOKENS_DIMENSION, 8_000, False)
    assert await _remaining(workspace_id) == DOLLAR


async def test_a_charge_the_gate_refuses_takes_the_ledger_row_back(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await allow(connection, workspace_id, DOLLAR, "raise")
    with pytest.raises(AllowanceRaised):
        async with workspace_tx() as connection:
            await LEDGER.record_turn_usage(
                connection, workspace_id, turn_id, MODEL, Usage(input_tokens=10), "attempt"
            )
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.ledger)
                .where(tables.ledger.c.workspace_id == workspace_id)
            )
        ).scalar_one()
    assert rows == 0
    assert await _charges(workspace_id) == []
