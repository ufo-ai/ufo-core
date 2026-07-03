"""The memory service: commit writes one row and derives nothing; recall fuses the two index legs.

`commit` persists a `memory_item` and stops — chunking and embedding are the derivation job's work,
never inline on a write (the load-bearing invariant). `recall` embeds the query once, asks the
index backend for its lexical and vector hits under the caller's subject filter, fuses them with
reciprocal-rank fusion (K=60), and reads the surviving items back as `Recalled` value objects. The
query embedding is best-effort: if the embed provider is unreachable, recall degrades to the lexical
leg rather than raising, and the caller (turn-load auto-inject) degrades again to no context.
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


def member_subject(member_id: UUID) -> str:
    return f"{MEMBER_SUBJECT_PREFIX}{member_id}"


def recall_subjects(member_id: UUID | None) -> frozenset[str]:
    """The subjects a turn recalls under: the member's own space plus the shared space, or shared
    alone when the conversation has no linked member."""
    if member_id is None:
        return frozenset({SHARED_SUBJECT})
    return frozenset({member_subject(member_id), SHARED_SUBJECT})


@dataclass(frozen=True)
class Recalled:
    memory_id: UUID
    subject: str
    item_class: str
    body: str
    source_ref: str | None
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
        embedding = await self._embed_query(query)
        lexical = await self.index.lexical(query, subjects, limit)
        vector = await self.index.vector(embedding, subjects, limit) if embedding else ()
        return await self._enrich(self._fuse(lexical, vector, limit))

    async def _embed_query(self, query: str) -> tuple[float, ...]:
        if not query.strip():
            return ()
        try:
            vectors = await self.embed.embed((query,))
        except Exception as error:
            log("recall.embed_query_failed", error_class=type(error).__name__)
            return ()
        return vectors[0] if vectors else ()

    def _fuse(
        self, lexical: tuple[Hit, ...], vector: tuple[Hit, ...], limit: int
    ) -> list[tuple[str, float]]:
        """Reciprocal-rank fusion (K=60) over the two legs, collapsed to one score per owning item:
        each leg ranks its chunk hits, a chunk's RRF score sums 1/(K+rank) across the legs it placed
        in, and an item takes the best score among its chunks."""
        ranks = tuple(
            {hit.chunk_digest: rank for rank, hit in enumerate(leg, start=1) if hit.score > 0}
            for leg in (lexical, vector)
        )
        scored: dict[str, float] = {}
        for hit in (*lexical, *vector):
            if hit.owner_kind != OWNER_KIND_MEMORY_ITEM:
                continue
            rrf = sum(
                1.0 / (RRF_K + leg[hit.chunk_digest])
                for leg in ranks
                if hit.chunk_digest in leg
            )
            scored[hit.owner_id] = max(scored.get(hit.owner_id, 0.0), rrf)
        return sorted(scored.items(), key=lambda item: item[1], reverse=True)[:limit]

    async def _enrich(self, ranked: list[tuple[str, float]]) -> tuple[Recalled, ...]:
        """Read the surviving (non-superseded) items back in fused order; a superseded item drops
        out here rather than being served stale."""
        if not ranked:
            return ()
        ids = [UUID(owner_id) for owner_id, _ in ranked]
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
                memory_id=item_id,
                subject=by_id[item_id]["subject"],
                item_class=by_id[item_id]["item_class"],
                body=by_id[item_id]["body"],
                source_ref=by_id[item_id]["source_ref"],
                score=score,
            )
            for (_, score), item_id in zip(ranked, ids, strict=True)
            if item_id in by_id
        )
