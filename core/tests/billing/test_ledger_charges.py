from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from test_accounting import _seed_turn
from ufo_ext_sample.spend import ALLOWANCE_TABLE, CHARGE_TABLE, AllowanceRaised, SampleGate, allow

from ufo.db import workspace_tx
from ufo.harness.models.catalog import ANTHROPIC_KEY_SLOT
from ufo.runtime.billing.accounting import (
    IMAGES_DIMENSION,
    SANDBOX_TOKENS_ATTEMPT,
    SANDBOX_TOKENS_DIMENSION,
    TOKENS_DIMENSION,
    VIDEOS_DIMENSION,
    Ledger,
    record_egress_request,
    record_probe_egress_request,
)
from ufo.runtime.billing.spend import GateDeploy
from ufo.schema import tables
from ufo.schema.records import Usage, ledger_id_for

LEDGER = Ledger(gates=(SampleGate(GateDeploy(public_base_url=None, home_surface=None)),))
MODEL = "claude-opus-4-8"
DOLLAR = 1_000_000

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


async def test_in_sandbox_and_media_spend_charge_each_increment_and_egress_charges_nothing(
    db: None,
) -> None:
    usage = Usage(input_tokens=5_000, output_tokens=50)
    async with workspace_tx() as connection:
        workspace_id, turn_id = await _seed_turn(connection)
        await allow(connection, workspace_id, DOLLAR, "park")
        for _ in range(2):
            await LEDGER.record_sandbox_tokens(connection, workspace_id, turn_id, MODEL, usage)
        await LEDGER.record_image_usage(connection, workspace_id, turn_id, "image", 1, 40_000)
        await LEDGER.record_video_usage(connection, workspace_id, turn_id, "video", 1, 60_000)
        await record_egress_request(connection, workspace_id, turn_id)
        await record_probe_egress_request(connection, workspace_id)
        remaining = (
            await connection.execute(
                sa.select(ALLOWANCE_TABLE.c.remaining_micro_usd).where(
                    ALLOWANCE_TABLE.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
    sandbox = ledger_id_for(workspace_id, turn_id, SANDBOX_TOKENS_DIMENSION, SANDBOX_TOKENS_ATTEMPT)
    charges = await _charges(workspace_id)
    assert [(charge[0], charge[2], charge[4]) for charge in charges] == [
        (sandbox, SANDBOX_TOKENS_DIMENSION, True),
        (sandbox, SANDBOX_TOKENS_DIMENSION, True),
        (ledger_id_for(workspace_id, turn_id, IMAGES_DIMENSION), IMAGES_DIMENSION, True),
        (ledger_id_for(workspace_id, turn_id, VIDEOS_DIMENSION), VIDEOS_DIMENSION, True),
    ]
    assert sum(charge[3] for charge in charges) == await _priced(workspace_id)
    assert remaining == DOLLAR - await _priced(workspace_id)


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
