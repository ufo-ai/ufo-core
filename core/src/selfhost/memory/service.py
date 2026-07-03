"""The memory service: commit writes one row and derives nothing; recall and search fuse index legs.

`commit` persists a `memory_item` and stops — chunking and embedding are the derivation job's work,
never inline on a write (the load-bearing invariant). `recall` embeds the query once, asks the index
backend for its lexical and vector hits under the caller's subject filter, fuses them with
reciprocal-rank fusion (K=60), and reads the surviving items back as `Recalled` value objects.
`search_sources` runs the same fusion over source-page chunks, so a synced document is findable
alongside recalled facts. The query embedding is best-effort: if the embed provider is unreachable,
both degrade to the lexical leg rather than raising, and the turn-load caller degrades again to no
context.
"""

from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa

from selfhost.db import workspace_tx
from selfhost.memory.chunk import Hit
from selfhost.memory.embed import EmbedClient
from selfhost.memory.index import IndexBackend
from selfhost.o11y import log
from selfhost.schema import tables
from selfhost.schema.records import MemoryWrite

RRF_K = 60
SHARED_SUBJECT = "shared"
MEMBER_SUBJECT_PREFIX = "member:"
OWNER_KIND_MEMORY_ITEM = "memory_item"
OWNER_KIND_PAGE = "page"


def member_subject(member_id: UUID) -> str:
    return f"{MEMBER_SUBJECT_PREFIX}{member_id}"


def recall_subjects(member_id: UUID | None) -> frozenset[str]:
    """The subjects a turn recalls under: the member's own space plus the shared space, or shared
    alone when the conversation has no linked member."""
    if member_id is None:
        return frozenset({SHARED_SUBJECT})
    return frozenset({member_subject(member_id), SHARED_SUBJECT})


@dataclass(frozen=True)
class Fused:
    owner_id: str
    score: float
    text: str


def fuse_hits(
    lexical: tuple[Hit, ...], vector: tuple[Hit, ...], owner_kind: str, limit: int
) -> tuple[Fused, ...]:
    """Reciprocal-rank fusion (K=60) over the two legs, collapsed to one score per owning row of the
    given kind: each leg ranks its chunk hits, a chunk's RRF score sums 1/(K+rank) across the legs
    it placed in, and a row takes its best-scoring chunk — that chunk's text rides along as the
    matched snippet."""
    ranks = tuple(
        {hit.chunk_digest: rank for rank, hit in enumerate(leg, start=1) if hit.score > 0}
        for leg in (lexical, vector)
    )
    best: dict[str, tuple[float, str]] = {}
    for hit in (*lexical, *vector):
        if hit.owner_kind != owner_kind:
            continue
        rrf = sum(
            1.0 / (RRF_K + leg[hit.chunk_digest]) for leg in ranks if hit.chunk_digest in leg
        )
        current = best.get(hit.owner_id)
        if current is None or rrf > current[0]:
            best[hit.owner_id] = (rrf, hit.text)
    ranked = sorted(best.items(), key=lambda item: item[1][0], reverse=True)[:limit]
    return tuple(Fused(owner_id, score, text) for owner_id, (score, text) in ranked)


@dataclass(frozen=True)
class Recalled:
    memory_id: UUID
    subject: str
    item_class: str
    body: str
    source_ref: str | None
    score: float


@dataclass(frozen=True)
class SourceMatch:
    page_id: UUID
    subject: str
    text: str
    score: float


@dataclass(frozen=True)
class MemoryService:
    index: IndexBackend
    embed: EmbedClient

    async def commit(self, write: MemoryWrite) -> None:
        """Persist one memory_item with no derived state: embedding_digest stays NULL, marking the
        row due for the index job — the sole producer of chunks and embeddings."""
        async with workspace_tx() as connection:
            workspace_id = (
                await connection.execute(sa.select(tables.workspace.c.id))
            ).scalar_one()
            await connection.execute(
                sa.insert(tables.memory_item).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    subject=write.subject,
                    body=write.body,
                    item_class=write.item_class,
                    source_ref=write.source_ref,
                    superseded_by=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

    async def recall(
        self, query: str, subjects: frozenset[str], limit: int
    ) -> tuple[Recalled, ...]:
        fused = fuse_hits(*await self._legs(query, subjects, limit), OWNER_KIND_MEMORY_ITEM, limit)
        return await self._enrich(fused)

    async def search_sources(
        self, query: str, subjects: frozenset[str], limit: int
    ) -> tuple[SourceMatch, ...]:
        """Search synced source pages the same way recall searches facts: fuse the two index legs
        under the subject filter, then read the surviving (non-tombstoned) pages back with the
        matched snippet."""
        fused = fuse_hits(*await self._legs(query, subjects, limit), OWNER_KIND_PAGE, limit)
        return await self._enrich_pages(fused)

    async def _legs(
        self, query: str, subjects: frozenset[str], limit: int
    ) -> tuple[tuple[Hit, ...], tuple[Hit, ...]]:
        embedding = await self._embed_query(query)
        lexical = await self.index.lexical(query, subjects, limit)
        vector = await self.index.vector(embedding, subjects, limit) if embedding else ()
        return lexical, vector

    async def _embed_query(self, query: str) -> tuple[float, ...]:
        if not query.strip():
            return ()
        try:
            vectors = await self.embed.embed((query,))
        except Exception as error:
            log("recall.embed_query_failed", error_class=type(error).__name__)
            return ()
        return vectors[0] if vectors else ()

    async def _enrich(self, fused: tuple[Fused, ...]) -> tuple[Recalled, ...]:
        """Read the surviving (non-superseded) items back in fused order; a superseded item drops
        out here rather than being served stale."""
        if not fused:
            return ()
        ids = [UUID(hit.owner_id) for hit in fused]
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.memory_item.c.id,
                        tables.memory_item.c.subject,
                        tables.memory_item.c.item_class,
                        tables.memory_item.c.body,
                        tables.memory_item.c.source_ref,
                    ).where(
                        tables.memory_item.c.id.in_(ids),
                        tables.memory_item.c.superseded_by.is_(None),
                    )
                )
            ).mappings().all()
        by_id = {row["id"]: row for row in rows}
        return tuple(
            Recalled(
                memory_id=UUID(hit.owner_id),
                subject=by_id[UUID(hit.owner_id)]["subject"],
                item_class=by_id[UUID(hit.owner_id)]["item_class"],
                body=by_id[UUID(hit.owner_id)]["body"],
                source_ref=by_id[UUID(hit.owner_id)]["source_ref"],
                score=hit.score,
            )
            for hit in fused
            if UUID(hit.owner_id) in by_id
        )

    async def _enrich_pages(self, fused: tuple[Fused, ...]) -> tuple[SourceMatch, ...]:
        """Read the surviving (non-tombstoned) pages back in fused order, carrying the matched
        snippet; a tombstoned page drops out rather than being served after its document is gone."""
        if not fused:
            return ()
        ids = [UUID(hit.owner_id) for hit in fused]
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.page.c.id, tables.page.c.subject).where(
                        tables.page.c.id.in_(ids),
                        tables.page.c.tombstone.is_(False),
                    )
                )
            ).mappings().all()
        by_id = {row["id"]: row for row in rows}
        return tuple(
            SourceMatch(
                page_id=UUID(hit.owner_id),
                subject=by_id[UUID(hit.owner_id)]["subject"],
                text=hit.text,
                score=hit.score,
            )
            for hit in fused
            if UUID(hit.owner_id) in by_id
        )
