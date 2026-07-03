"""The derivation job: turn memory items due for indexing into index chunks, off the write path.

This is the sole producer of chunks — `MemoryService.commit` writes a row and derives nothing, so
a memory item carries no chunk or embedding until this job runs (batch-at-interval, in the jobs
role, on the deploy embed key). A run claims the items whose `embedding_digest` is NULL, chunks and
embeds each body, upserts the chunks through the index backend, then stamps the content digest so
the item is no longer due.
"""

import hashlib
from dataclasses import dataclass, replace

import sqlalchemy as sa

from selfhost.db import workspace_tx
from selfhost.memory.chunk import TextChunker
from selfhost.memory.embed import EmbedClient
from selfhost.memory.index import IndexBackend
from selfhost.memory.service import OWNER_KIND_MEMORY_ITEM
from selfhost.schema import tables
from selfhost.schema.records import MemoryItem

DUE_BATCH_MAX_ITEMS = 200


@dataclass(frozen=True)
class MemoryIndexer:
    index: IndexBackend
    embed: EmbedClient
    chunker: TextChunker

    async def run(self) -> None:
        for item in await self._due_items():
            await self._index_item(item)

    async def _due_items(self) -> tuple[MemoryItem, ...]:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.memory_item.c.id,
                        tables.memory_item.c.subject,
                        tables.memory_item.c.body,
                        tables.memory_item.c.item_class,
                        tables.memory_item.c.source_ref,
                        tables.memory_item.c.embedding_digest,
                        tables.memory_item.c.superseded_by,
                    )
                    .where(tables.memory_item.c.embedding_digest.is_(None))
                    .limit(DUE_BATCH_MAX_ITEMS)
                )
            ).mappings().all()
        return tuple(MemoryItem.model_validate(dict(row)) for row in rows)

    async def _index_item(self, item: MemoryItem) -> None:
        chunks = self.chunker.chunk(
            item.body, OWNER_KIND_MEMORY_ITEM, str(item.id), item.subject
        )
        if chunks:
            vectors = await self.embed.embed(tuple(chunk.text for chunk in chunks))
            await self.index.upsert(
                tuple(
                    replace(chunk, embedding=vector)
                    for chunk, vector in zip(chunks, vectors, strict=True)
                )
            )
        await self._stamp(item)

    async def _stamp(self, item: MemoryItem) -> None:
        digest = "sha256:" + hashlib.sha256(item.body.encode()).hexdigest()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.memory_item)
                .values(embedding_digest=digest, updated_at=sa.func.now())
                .where(tables.memory_item.c.id == item.id)
            )
