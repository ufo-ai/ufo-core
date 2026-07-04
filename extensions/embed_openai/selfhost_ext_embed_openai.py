"""The base-pinned default embed backend: OpenAI text-embedding-3-large over the async SDK.

Every deploy needs an embed client, so this extension is base-pinned and registers
`EmbedBackendSpec` name `"default"` — the backend core resolves when `memory.embed_backend` is
unset. Index derivation runs in the jobs/serve role on the deploy key (`OPENAI_API_KEY`), off the
user write path, never through the sandbox proxy. `plan_embed_batches` bounds the request payload:
it clips each item and packs batches under the item/char ceilings before the call.
"""

import os
from dataclasses import dataclass

import openai

from selfhost.sdk.context import ExtensionContext
from selfhost.sdk.index import EmbedClient
from selfhost.sdk.manifest import EmbedBackendSpec, Manifest

NAME = "embed-openai"
VERSION = "0.1.0"
EMBED_BACKEND = "default"
API_KEY_ENV = "OPENAI_API_KEY"
EMBED_MODEL = "text-embedding-3-large"
EMBED_DIM = 3072
EMBED_BATCH_MAX_ITEMS = 2048
EMBED_BATCH_MAX_CHARS = 600_000
EMBED_MAX_ITEM_CHARS = 24_000
PROVIDER_TIMEOUT_SECONDS = 60.0


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
    """text-embedding-3-large over the async OpenAI SDK. The deploy `OPENAI_API_KEY` is read from
    the environment on each embed call, not at boot: a zero-config dev serve with no key boots, and
    an embed call without one fails loud — the OpenAI SDK raises on an empty key at construction, so
    the client is built here on use (retries disabled; embedding runs off the write path)."""

    model: str = EMBED_MODEL

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        key = os.environ.get(API_KEY_ENV, "")
        if not key:
            raise RuntimeError(f"{API_KEY_ENV} required to embed")
        client = openai.AsyncOpenAI(api_key=key, max_retries=0, timeout=PROVIDER_TIMEOUT_SECONDS)
        vectors: list[tuple[float, ...]] = []
        for batch in plan_embed_batches(texts):
            response = await client.embeddings.create(model=self.model, input=list(batch))
            ordered = sorted(response.data, key=lambda row: row.index)
            vectors.extend(tuple(float(value) for value in row.embedding) for row in ordered)
        return tuple(vectors)


def build(ctx: ExtensionContext) -> EmbedClient:
    """The deploy embed client core builds at boot. Construction is key-free — the OpenAI SDK client
    is built on each embed call, keyed by the deploy `OPENAI_API_KEY` — so a zero-config dev serve
    boots without one and embedding without a key fails loud on use. `ctx` is the workspace scope
    the seam threads; this deploy-key backend reads no slot."""
    return OpenAIEmbedClient()


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        embeds=(EmbedBackendSpec(name=EMBED_BACKEND, factory=build),),
    )
