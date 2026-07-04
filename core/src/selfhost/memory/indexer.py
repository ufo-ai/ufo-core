"""The page derivation job: turn source pages due for indexing into chunks, off the write path.

This is the sole producer of page chunks — the sync driver writes a page row and derives nothing,
so a synced page carries no chunk until this job runs (batch-at-interval, in the jobs role, on the
deploy embed key). A run atomically claims a batch of pages whose `embedding_digest` is NULL and
whose claim is unset or lease-expired — stamping `embedding_claimed_at` (Postgres `FOR UPDATE SKIP
LOCKED`, SQLite the single writer) so an overlapping tick skips them and never double-embeds —
chunks and embeds each body through `chunk_embed_upsert`, then writes the content digest and clears
the claim so the row is no longer due. A tombstoned page instead has its chunks deleted before the
stamp, so a removed document stops being recalled.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import sqlalchemy as sa

from selfhost.blob import BlobStore
from selfhost.db import workspace_tx
from selfhost.indexing import (
    OWNER_KIND_PAGE,
    EmbedClient,
    IndexBackend,
    IndexScope,
    TextChunker,
    chunk_embed_upsert,
)
from selfhost.schema import tables

DUE_BATCH_MAX_ITEMS = 200
EMBED_CLAIM_LEASE_SECONDS = 300


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
