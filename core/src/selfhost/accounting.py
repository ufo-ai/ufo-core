"""Token pricing and the one billing write per turn."""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from selfhost.schema.records import Usage, ledger_id_for

MICRO_USD_PER_USD = 1_000_000
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


def model_price(model: str) -> ModelPrice:
    """The price row for a model; unknown models fail loud."""
    price = MODEL_TOKEN_PRICE.get(model)
    if price is None:
        known = ", ".join(sorted(MODEL_TOKEN_PRICE))
        raise KeyError(f"no price for model {model!r}; priced models: {known}")
    return price


def usage_priced_usd(model: str, usage: Usage) -> Decimal:
    """Exact USD for a usage split: integer dot product, one Decimal division."""
    price = model_price(model)
    micro_usd_mtok = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_tokens * price.cache_write
    )
    return Decimal(micro_usd_mtok) / Decimal(MICRO_USD_PER_USD * TOKENS_PER_MTOK)


async def record_turn_usage(
    connection: AsyncConnection,
    workspace_id: UUID,
    turn_id: UUID,
    model: str,
    usage: Usage,
) -> None:
    """One billing write per turn, replay-idempotent; zero usage writes nothing."""
    total = (
        usage.input_tokens
        + usage.output_tokens
        + usage.cache_read_tokens
        + usage.cache_write_tokens
    )
    if total == 0:
        return
    await connection.execute(
        text(
            "insert into ledger"
            " (id, workspace_id, turn_id, dimension, amount, priced_usd, model,"
            " created_at, updated_at)"
            " values (:id, :ws, :turn, 'tokens', :amount, :priced, :model, now(), now())"
            " on conflict (id) do nothing"
        ),
        {
            "id": ledger_id_for(workspace_id, turn_id, "tokens"),
            "ws": workspace_id,
            "turn": turn_id,
            "amount": total,
            "priced": usage_priced_usd(model, usage),
            "model": model,
        },
    )


async def read_turn_cost(
    connection: AsyncConnection, turn_id: UUID
) -> tuple[int, Decimal, str] | None:
    """The billed tokens, USD, and model for a turn; None when nothing was billed."""
    row = (
        await connection.execute(
            text(
                "select amount, priced_usd, model from ledger"
                " where turn_id = :turn and dimension = 'tokens'"
            ),
            {"turn": turn_id},
        )
    ).one_or_none()
    if row is None:
        return None
    return int(row.amount), row.priced_usd, row.model
