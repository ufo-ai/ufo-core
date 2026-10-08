"""The synced pages' passages on the deploy index: `PageIndex` writes them from the page feed and
mirrors each page's state into the memory service; `PagePassages` reads them back for a reader.

A passage is served only while its page is live, carries the subject the chunk was stamped with,
sits on a connection the reader reaches, and still holds the body the chunk was cut from: the
chunk's own digest names the page digest it was derived from, so a chunk the indexer has not yet
replaced answers nothing."""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import chain, zip_longest
from uuid import UUID

from ufo_ext_sources.pages import PAGE_KIND

from ufo.sdk.context import ExtensionContext, PageState, SourceReader
from ufo.sdk.index import (
    OWNER_KIND_PAGE,
    EmbedClient,
    Hit,
    IndexBackend,
    IndexScope,
    TextChunker,
    chunk_digest,
    chunk_embed_upsert,
)
from ufo.sdk.memory import MemoryMatch
from ufo.sdk.objects import ObjectRef
from ufo.sdk.sources import PageChange
from ufo_ext_memory.client import MemoryApi, PageMirror

RRF_K = 60
RECALL_COSINE_FLOOR = 0.52
"""How near something must be for a query the lexical legs matched nowhere to recall anything. A
vector search answers every query with its closest chunks however far away they are, so without a
floor a meaningless string recalls whatever it happens to sit nearest.

The bar judges the query, never a row inside a wordful query's pool. A cosine height means
something only within one corpus on one embedding model — measured across three corpora, correct
answers sit at 0.44 where garbage tops 0.16, and garbage reaches 0.50 where a terse question's
answer sits at 0.34 — so a constant held against each row cuts real answers wherever the corpus
runs cool, and the rows it cuts first are the ones worded unlike their question: a memory filed
under a full name, asked for by handle. What a constant can judge is total lexical silence, because
across those same corpora every real question matched some word and no random string matched any.
The value sits just above the highest garbage measured: 200 random strings in five shapes against
the largest live corpus (29k items, `text-embedding-3-large`) reached 0.5038 at the very top."""
RECALL_CANDIDATE_POOL = 200
MIRROR_TITLE_MAX_CHARS = 2_000

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Fused:
    owner_id: str
    score: float
    text: str


def fuse_legs(
    legs: tuple[tuple[Hit, ...], ...], cosine_leg: tuple[Hit, ...]
) -> dict[str, tuple[float, float, str]]:
    """Each owner's best reciprocal-rank score across `legs` with the text of the chunk that
    earned it, beside the owner's best cosine in `cosine_leg`."""
    ranks = tuple(
        {hit.chunk_digest: rank for rank, hit in enumerate(leg, start=1) if hit.score > 0}
        for leg in legs
    )
    cosine: dict[str, float] = {}
    for hit in cosine_leg:
        cosine[hit.owner_id] = max(cosine.get(hit.owner_id, 0.0), hit.score)
    best: dict[str, tuple[float, str]] = {}
    for hit in chain.from_iterable(legs):
        rrf = sum(1.0 / (RRF_K + leg[hit.chunk_digest]) for leg in ranks if hit.chunk_digest in leg)
        current = best.get(hit.owner_id)
        if current is None or rrf > current[0]:
            best[hit.owner_id] = (rrf, hit.text)
    return {
        owner_id: (rrf, cosine.get(owner_id, 0.0), text) for owner_id, (rrf, text) in best.items()
    }


def fuse_hits(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]:
    """Pure reciprocal-rank fusion collapsed to one score per owning row — source-page search's
    ranking, where the fused rank across the lexical and vector legs is the whole signal.

    A query whose lexical leg matched nothing is held to `RECALL_COSINE_FLOOR`, as recall is: this
    search answers the same box and the same tool, so a query with no meaning must come back empty
    here too. Fused rank cannot carry that bar, being relative to whatever the legs returned; and
    once any page is worded the query is a real question, so the fusion ranks everything — the page
    a member wants is not always the page carrying their words."""
    fused = fuse_legs((lexical, vector), vector)
    floor = 0.0 if lexical else RECALL_COSINE_FLOOR
    kept = {owner_id: held for owner_id, held in fused.items() if held[1] >= floor}
    ranked = sorted(kept.items(), key=lambda item: item[1][0], reverse=True)[:limit]
    return tuple(Fused(owner_id, rrf, text) for owner_id, (rrf, _cosine, text) in ranked)


@dataclass(frozen=True)
class PageIndex:
    """The `index_pages` consumer: one feed batch into the deploy index and the memory service's
    page mirror, idempotent so a replayed batch settles on the same state."""

    index: IndexBackend
    embed: EmbedClient
    memory: MemoryApi
    ctx: ExtensionContext

    async def apply(self, changes: Sequence[PageChange]) -> None:
        """Drop the chunks of each tombstoned or unindexed page and embed every other page's body
        under its subject and digest, pruning the chunks of its other revisions. Then read the
        embedded pages' live state once and withdraw the chunks of any whose subject or revision
        moved meanwhile, and mirror every change into the memory service. A refusal from the
        memory service raises, so the batch's cursor holds."""
        for change in changes:
            await self._write(change)
        await self._withdraw_moved(
            tuple(change for change in changes if change.indexed and not change.tombstone)
        )
        await self.memory.pages(
            [
                PageMirror(
                    page_id=change.page_id,
                    subject=change.subject,
                    revision=change.revision,
                    indexed=change.indexed,
                    tombstone=change.tombstone,
                    title=change.title[:MIRROR_TITLE_MAX_CHARS],
                    provider=change.provider,
                )
                for change in changes
            ]
        )

    async def _write(self, change: PageChange) -> None:
        if change.tombstone or not change.indexed:
            await self.index.delete(IndexScope(OWNER_KIND_PAGE, str(change.page_id)))
            return
        await chunk_embed_upsert(
            self.index,
            self.embed,
            TextChunker(),
            OWNER_KIND_PAGE,
            str(change.page_id),
            change.subject,
            change.body,
            change.digest,
        )

    async def _withdraw_moved(self, embedded: tuple[PageChange, ...]) -> None:
        states = await self.ctx.page_states(tuple(change.page_id for change in embedded))
        for change in embedded:
            state = states.get(change.page_id)
            if state is None or (state.subject, state.revision) != (
                change.subject,
                change.revision,
            ):
                await self.index.delete(IndexScope(OWNER_KIND_PAGE, str(change.page_id)))


@dataclass(frozen=True)
class PagePassages:
    """The synced pages' passages a reader may read, searched over the deploy index."""

    index: IndexBackend
    embed: EmbedClient | None
    ctx: ExtensionContext

    async def search(
        self,
        queries: tuple[str, ...],
        reader: SourceReader,
        limit: int,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[MemoryMatch, ...]:
        """Each query's best passages, at most `limit` of them, fused from the lexical and vector
        legs under the reader's subjects, then merged across queries rank by rank, one passage
        kept once. A page first synced outside `[start, end)` is left out."""
        if not reader.subjects:
            return ()
        legs = await asyncio.gather(
            *(self._leg(query, reader.subjects, limit) for query in queries)
        )
        states = await self.ctx.readable_page_states(
            tuple(dict.fromkeys(UUID(hit.owner_id) for fused, _ in legs for hit in fused)), reader
        )
        served = [self._served(fused, hits, states, limit, start, end) for fused, hits in legs]
        passages: dict[tuple[UUID | None, str], MemoryMatch] = {}
        for tier in zip_longest(*served):
            for match in tier:
                if match is not None:
                    passages.setdefault((match.created_from_page_id, match.text), match)
        return tuple(passages.values())[:limit]

    async def _leg(
        self, query: str, subjects: frozenset[str], limit: int
    ) -> tuple[tuple[Fused, ...], tuple[Hit, ...]]:
        pool = max(limit, RECALL_CANDIDATE_POOL)
        embedding = await self._embedded(query)
        lexical, vector = await asyncio.gather(
            self.index.lexical(query, subjects, OWNER_KIND_PAGE, pool),
            self._vector(embedding, subjects, pool),
        )
        return fuse_hits(lexical, vector, pool), lexical + vector

    async def _embedded(self, query: str) -> tuple[float, ...]:
        if self.embed is None or not query.strip():
            return ()
        try:
            vectors = await self.embed.embed((query,))
        except Exception:
            logger.warning("memory.recall.embed_query_failed", exc_info=True)
            return ()
        return vectors[0] if vectors else ()

    async def _vector(
        self, embedding: tuple[float, ...], subjects: frozenset[str], pool: int
    ) -> tuple[Hit, ...]:
        if not embedding:
            return ()
        return await self.index.vector(embedding, subjects, OWNER_KIND_PAGE, pool)

    def _served(
        self,
        fused: tuple[Fused, ...],
        hits: tuple[Hit, ...],
        states: dict[UUID, PageState],
        limit: int,
        start: datetime | None,
        end: datetime | None,
    ) -> tuple[MemoryMatch, ...]:
        return tuple(
            MemoryMatch(
                kind="source",
                text=passage.text,
                ref=ObjectRef(kind=PAGE_KIND, name=str(page_id)),
                created_at=state.created_at,
                subject=state.subject,
                page_provider=state.backend,
                page_title=state.title,
                created_from_page_id=page_id,
            )
            for passage in fused
            if (state := states.get(page_id := UUID(passage.owner_id))) is not None
            and (start is None or state.created_at >= start)
            and (end is None or state.created_at < end)
            and any(
                hit.owner_id == passage.owner_id
                and hit.text == passage.text
                and hit.subject == state.subject
                and hit.chunk_digest
                == chunk_digest(OWNER_KIND_PAGE, hit.owner_id, state.digest, hit.ordinal, hit.text)
                for hit in hits
            )
        )[:limit]
