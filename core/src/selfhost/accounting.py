"""Token pricing and the one billing write per turn."""

from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from selfhost.o11y import log
from selfhost.schema import tables
from selfhost.schema.records import Usage, ledger_id_for

TOKENS_PER_MTOK = 1_000_000


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Micro-USD per million tokens, one rate per token class."""

    input: int
    output: int
    cache_read: int
    cache_write: int


MODEL_TOKEN_PRICE: dict[str, ModelPrice] = {
    "claude-fable-5": ModelPrice(10_000_000, 50_000_000, 1_000_000, 12_500_000),
    "claude-opus-4-8": ModelPrice(5_000_000, 25_000_000, 500_000, 6_250_000),
    "claude-opus-4-7": ModelPrice(5_000_000, 25_000_000, 500_000, 6_250_000),
    "claude-opus-4-6": ModelPrice(5_000_000, 25_000_000, 500_000, 6_250_000),
    "claude-sonnet-5": ModelPrice(3_000_000, 15_000_000, 300_000, 3_750_000),
    "claude-sonnet-4-6": ModelPrice(3_000_000, 15_000_000, 300_000, 3_750_000),
    "claude-haiku-4-5": ModelPrice(1_000_000, 5_000_000, 100_000, 1_250_000),
    "gpt-5.5": ModelPrice(5_000_000, 30_000_000, 500_000, 5_000_000),
    "gpt-5.4": ModelPrice(2_500_000, 15_000_000, 250_000, 2_500_000),
    "gpt-5.4-mini": ModelPrice(750_000, 4_500_000, 75_000, 750_000),
    "gpt-5.4-nano": ModelPrice(200_000, 1_250_000, 20_000, 200_000),
}


def usage_priced_micro_usd(model: str, usage: Usage) -> int:
    """Micro-USD for a usage split: integer dot product, floored at micro-dollar precision.

    An unknown model warns loudly and prices at zero — never a silent fallback to another
    model's price, and never a raise: pricing runs inside the turn's terminal commit, so raising
    would wedge the commit-retry loop instead of ending the client's wait."""
    price = MODEL_TOKEN_PRICE.get(model)
    if price is None:
        log("pricing.unknown_model", model=model)
        return 0
    micro_usd_mtok = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.cache_write
    )
    return micro_usd_mtok // TOKENS_PER_MTOK


async def record_turn_usage(
    connection: AsyncConnection,
    workspace_id: UUID,
    turn_id: UUID,
    model: str,
    usage: Usage,
) -> None:
    """One billing write per turn; select-then-insert is replay-safe because DBOS
    re-executes a turn sequentially, never concurrently with itself."""
    total = (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_read_tokens
        + usage.cache_write_tokens
    )
    if total == 0:
        return
    ledger_id = ledger_id_for(workspace_id, turn_id, "tokens")
    billed = await connection.execute(
        sa.select(tables.ledger.c.id).where(tables.ledger.c.id == ledger_id)
    )
    if billed.one_or_none() is not None:
        return
    await connection.execute(
        sa.insert(tables.ledger).values(
            id=ledger_id,
            workspace_id=workspace_id,
            turn_id=turn_id,
            dimension="tokens",
            amount=total,
            priced_micro_usd=usage_priced_micro_usd(model, usage),
            model=model,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )


async def read_turn_cost(
    connection: AsyncConnection, turn_id: UUID
) -> tuple[int, int, str] | None:
    """The billed tokens, micro-USD, and model for a turn; None when nothing was billed."""
    row = (
        await connection.execute(
            sa.select(
                tables.ledger.c.amount, tables.ledger.c.priced_micro_usd, tables.ledger.c.model
            ).where(
                (tables.ledger.c.turn_id == turn_id) & (tables.ledger.c.dimension == "tokens")
            )
        )
    ).one_or_none()
    if row is None:
        return None
    return int(row.amount), int(row.priced_micro_usd), row.model
