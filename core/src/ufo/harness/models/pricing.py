"""Model pricing values and the immutable table used to bill model usage."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from ufo.harness.o11y import log
from ufo.schema.records import Usage

TOKENS_PER_MTOK = 1_000_000


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Micro-USD per million tokens, one rate per token class."""

    input: int
    output: int
    cache_read: int
    cache_write_5m: int
    cache_write_1h: int
    cache_write_30m: int = 0


def price_digest(prices: Mapping[str, ModelPrice]) -> str:
    """A deterministic sha256 version stamp over sorted per-model rates."""
    payload = json.dumps(
        {
            model: {
                "input": price.input,
                "output": price.output,
                "cache_read": price.cache_read,
                "cache_write_5m": price.cache_write_5m,
                "cache_write_30m": price.cache_write_30m,
                "cache_write_1h": price.cache_write_1h,
            }
            for model, price in sorted(prices.items())
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def usage_priced_micro_usd(model: str, usage: Usage, prices: Mapping[str, ModelPrice]) -> int:
    """Price a usage split, warning and returning zero for an unknown historical model."""
    price = prices.get(model)
    if price is None:
        log("pricing.unknown_model", model=model)
        return 0
    micro_usd_mtok = (
        usage.input_tokens * price.input
        + usage.output_tokens * price.output
        + usage.cache_read_tokens * price.cache_read
        + usage.cache_write_5m_tokens * price.cache_write_5m
        + usage.cache_write_30m_tokens * price.cache_write_30m
        + usage.cache_write_1h_tokens * price.cache_write_1h
    )
    return micro_usd_mtok // TOKENS_PER_MTOK


@dataclass(frozen=True, slots=True)
class Pricing:
    """A model price table and the digest stamped on each billed usage."""

    prices: Mapping[str, ModelPrice]
    digest: str

    def micro_usd(self, model: str, usage: Usage) -> int:
        return usage_priced_micro_usd(model, usage, self.prices)


def pricing_from(prices: Mapping[str, ModelPrice]) -> Pricing:
    """Build pricing and its digest from one model-price table."""
    table = dict(prices)
    return Pricing(prices=table, digest=price_digest(table))
