"""Embedding behind one async seam: the deploy-key OpenAI client for the jobs role.

Index derivation runs in the jobs role with the deploy key — off the user write path, never
through the sandbox proxy. Token metering for embeddings arrives with the ledger work; nothing
here writes to it.
"""

from dataclasses import dataclass
from typing import Protocol

import openai

EMBED_MODEL = "text-embedding-3-large"
EMBED_DIM = 3072
EMBED_BATCH_MAX_ITEMS = 2048
EMBED_BATCH_MAX_CHARS = 600_000
EMBED_MAX_ITEM_CHARS = 24_000


class EmbedClient(Protocol):
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...


def plan_embed_batches(texts: tuple[str, ...]) -> tuple[tuple[str, ...], ...]:
    """Bound the request payload: clip each item and pack batches under the item/char ceilings."""
    batches: list[tuple[str, ...]] = []
    batch: list[str] = []
    chars = 0
    for text in texts:
        clipped = text[:EMBED_MAX_ITEM_CHARS]
        over_items = len(batch) >= EMBED_BATCH_MAX_ITEMS
        over_chars = chars + len(clipped) > EMBED_BATCH_MAX_CHARS
        if batch and (over_items or over_chars):
            batches.append(tuple(batch))
            batch, chars = [], 0
        batch.append(clipped)
        chars += len(clipped)
    if batch:
        batches.append(tuple(batch))
    return tuple(batches)


@dataclass(frozen=True)
class OpenAIEmbedClient:
    """text-embedding-3-large over the async OpenAI SDK; injected, never module-global."""

    client: openai.AsyncOpenAI
    model: str = EMBED_MODEL

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        vectors: list[tuple[float, ...]] = []
        for batch in plan_embed_batches(texts):
            response = await self.client.embeddings.create(model=self.model, input=list(batch))
            ordered = sorted(response.data, key=lambda row: row.index)
            vectors.extend(tuple(float(value) for value in row.embedding) for row in ordered)
        return tuple(vectors)
