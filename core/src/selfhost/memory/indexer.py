"""The derivation jobs: turn memory items and source pages due for indexing into chunks, off the
write path.

These are the sole producers of chunks — `MemoryService.commit` and the sync driver each write a
row and derive nothing, so a memory item carries no chunk and a synced page carries no chunk until
one of these jobs runs (batch-at-interval, in the jobs role, on the deploy embed key). A run
atomically claims a batch of rows whose `embedding_digest` is NULL and whose claim is unset or
lease-expired — stamping `embedding_claimed_at` (Postgres `FOR UPDATE SKIP LOCKED`, SQLite the
single writer) so an overlapping tick skips them and never double-embeds — chunks and embeds each
body through `chunk_embed_upsert`, then writes the content digest and clears the claim so the row is
no longer due. A tombstoned page instead has its chunks deleted before the stamp, so a removed
document stops being recalled.
"""

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa

from selfhost.blob import BlobStore
from selfhost.db import workspace_tx
from selfhost.memory.chunk import IndexScope, TextChunker
from selfhost.memory.embed import EmbedClient
from selfhost.memory.index import IndexBackend
from selfhost.memory.service import OWNER_KIND_MEMORY_ITEM, OWNER_KIND_PAGE
from selfhost.schema import tables
from selfhost.schema.records import MemoryItem

DUE_BATCH_MAX_ITEMS = 200
EMBED_CLAIM_LEASE_SECONDS = 300


async def chunk_embed_upsert(
    index: IndexBackend,
    embed: EmbedClient,
    chunker: TextChunker,
    owner_kind: str,
    owner_id: str,
    subject: str,
    body: str,
) -> None:
    """Chunk one body, embed each chunk, and upsert them under the owner — the derivation step both
    indexers share. Upsert is idempotent on chunk_digest, so a re-run over unchanged content
    rewrites the same rows rather than duplicating them."""
    chunks = chunker.chunk(body, owner_kind, owner_id, subject)
    if not chunks:
        return
    vectors = await embed.embed(tuple(chunk.text for chunk in chunks))
    await index.upsert(
        tuple(
            replace(chunk, embedding=vector)
            for chunk, vector in zip(chunks, vectors, strict=True)
        )
    )


@dataclass(frozen=True)
class MemoryIndexer:
    index: IndexBackend
    embed: EmbedClient
    chunker: TextChunker
    postgres: bool

    async def run(self) -> None:
        for item in await self._claim_due():
            await self._index_item(item)

    async def _claim_due(self) -> tuple[MemoryItem, ...]:
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=EMBED_CLAIM_LEASE_SECONDS)
        due = (
            sa.select(
                tables.memory_item.c.id,
                tables.memory_item.c.subject,
                tables.memory_item.c.body,
                tables.memory_item.c.item_class,
                tables.memory_item.c.source_ref,
                tables.memory_item.c.embedding_digest,
                tables.memory_item.c.superseded_by,
            )
            .where(
                tables.memory_item.c.embedding_digest.is_(None),
                sa.or_(
                    tables.memory_item.c.embedding_claimed_at.is_(None),
                    tables.memory_item.c.embedding_claimed_at < cutoff,
                ),
            )
            .limit(DUE_BATCH_MAX_ITEMS)
        )
        if self.postgres:
            due = due.with_for_update(skip_locked=True)
        async with workspace_tx() as connection:
            rows = (await connection.execute(due)).mappings().all()
            if rows:
                await connection.execute(
                    sa.update(tables.memory_item)
                    .values(embedding_claimed_at=now, updated_at=sa.func.now())
                    .where(tables.memory_item.c.id.in_([row["id"] for row in rows]))
                )
        return tuple(MemoryItem.model_validate(dict(row)) for row in rows)

    async def _index_item(self, item: MemoryItem) -> None:
        await chunk_embed_upsert(
            self.index,
            self.embed,
            self.chunker,
            OWNER_KIND_MEMORY_ITEM,
            str(item.id),
            item.subject,
            item.body,
        )
        await self._stamp(item)

    async def _stamp(self, item: MemoryItem) -> None:
        digest = "sha256:" + hashlib.sha256(item.body.encode()).hexdigest()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.memory_item)
                .values(
                    embedding_digest=digest,
                    embedding_claimed_at=None,
                    updated_at=sa.func.now(),
                )
                .where(tables.memory_item.c.id == item.id)
            )


@dataclass(frozen=True)
class DuePage:
    id: UUID
    subject: str
    body_ref: str
    digest: str
    tombstone: bool


@dataclass(frozen=True)
class PageIndexer:
    index: IndexBackend
    embed: EmbedClient
    chunker: TextChunker
    blob: BlobStore
    postgres: bool

    async def run(self) -> None:
        for page in await self._claim_due():
            await self._index_page(page)

    async def _claim_due(self) -> tuple[DuePage, ...]:
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=EMBED_CLAIM_LEASE_SECONDS)
        due = (
            sa.select(
                tables.page.c.id,
                tables.page.c.subject,
                tables.page.c.body_ref,
                tables.page.c.digest,
                tables.page.c.tombstone,
            )
            .where(
                tables.page.c.embedding_digest.is_(None),
                sa.or_(
                    tables.page.c.embedding_claimed_at.is_(None),
                    tables.page.c.embedding_claimed_at < cutoff,
                ),
            )
            .limit(DUE_BATCH_MAX_ITEMS)
        )
        if self.postgres:
            due = due.with_for_update(skip_locked=True)
        async with workspace_tx() as connection:
            rows = (await connection.execute(due)).mappings().all()
            if rows:
                await connection.execute(
                    sa.update(tables.page)
                    .values(embedding_claimed_at=now, updated_at=sa.func.now())
                    .where(tables.page.c.id.in_([row["id"] for row in rows]))
                )
        return tuple(
            DuePage(
                id=row["id"],
                subject=row["subject"],
                body_ref=row["body_ref"],
                digest=row["digest"],
                tombstone=bool(row["tombstone"]),
            )
            for row in rows
        )

    async def _index_page(self, page: DuePage) -> None:
        if page.tombstone:
            await self.index.delete(IndexScope(OWNER_KIND_PAGE, str(page.id)))
        else:
            body = (await self.blob.get(page.body_ref)).decode()
            await chunk_embed_upsert(
                self.index,
                self.embed,
                self.chunker,
                OWNER_KIND_PAGE,
                str(page.id),
                page.subject,
                body,
            )
        await self._stamp(page)

    async def _stamp(self, page: DuePage) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.page)
                .values(
                    embedding_digest=page.digest,
                    embedding_claimed_at=None,
                    updated_at=sa.func.now(),
                )
                .where(tables.page.c.id == page.id)
            )
