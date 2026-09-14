"""The tenant's page store, searched directly over the deploy's index backend.

A workspace's synced pages are already chunked and embedded in whatever index the deploy selected —
Turbopuffer on the hosted pack, the local index on a stock one — so grounding a question in the
tenant's own documents is a hybrid read of that index and nothing more: the lexical leg for the
terms a member used verbatim, the vector leg for the passage that says it in other words, fused by
reciprocal rank. Hybrid lexical-plus-dense retrieval is the configuration the BEIR benchmark found
robust across domains where either leg alone is not, and the index seam offers exactly those two
legs.

What the index answers is then fenced against the database: a chunk is served only where its page
is live, carries the subject the chunk was stamped with, belongs to a source this reader may read,
and was derived from the body the page holds now. The index filters by subject; whether the reader
reaches the source behind the page is core's to answer, so `readable_page_states` is what decides
it and this never queries a table itself.

The body claim is the chunk's own identity: `chunk_digest` stamps the page's content digest into
every chunk, so recomputing it over a hit under the live page's digest tells a chunk of the live
body from a chunk the indexer has not replaced yet. A page edited between the sync and the next
index job therefore contributes nothing, rather than answering with the figure it used to hold.

An embedding the deploy cannot produce costs the vector leg alone: the lexical leg still answers,
which is what a fresh workspace whose embedder is unkeyed has."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from ufo.sdk.context import PageState, SourceReader
from ufo.sdk.index import OWNER_KIND_PAGE, EmbedClient, Hit, IndexBackend, chunk_digest
from ufo.sdk.o11y import log
from ufo_ext_rag.fusion import fused_scores

PAGE_OBJECT_KIND = "page"
PAGE_SEARCH_EVENT = "rag.page_search"

type ReadablePages = Callable[[tuple[UUID, ...], SourceReader], Awaitable[dict[UUID, PageState]]]


@dataclass(frozen=True)
class PageHit:
    """One passage of one synced page, with the ref a reply opens it by and the date the page
    states itself as of, so a reply weighing two records can tell the newer from the older."""

    page_id: UUID
    title: str
    text: str
    dated: str
    provider: str

    @property
    def ref(self) -> str:
        return f"{PAGE_OBJECT_KIND}/{self.page_id}"


@dataclass(frozen=True)
class PageStore:
    """Search this workspace's synced pages and return what the reader may read."""

    index: IndexBackend
    embed: EmbedClient | None
    readable: ReadablePages

    async def hits(self, query: str, reader: SourceReader, limit: int) -> tuple[PageHit, ...]:
        """The best passages for `query`, ranked, fenced, and bounded to `limit`."""
        if not reader.subjects or not query.strip():
            return ()
        legs = await self._legs(query, reader, limit)
        scores = fused_scores([[hit.chunk_digest for hit in leg] for leg in legs])
        held: dict[str, Hit] = {}
        for leg in legs:
            for hit in leg:
                held.setdefault(hit.chunk_digest, hit)
        ranked = sorted(
            held.values(), key=lambda hit: (-scores[hit.chunk_digest], hit.chunk_digest)
        )
        return await self._readable(ranked, reader, limit)

    async def _legs(
        self, query: str, reader: SourceReader, limit: int
    ) -> tuple[tuple[Hit, ...], ...]:
        embedding = await self._embedded(query)
        lexical, vector = await asyncio.gather(
            self.index.lexical(query, reader.subjects, OWNER_KIND_PAGE, limit),
            self._vector(embedding, reader, limit),
        )
        return (lexical, vector)

    async def _embedded(self, query: str) -> tuple[float, ...]:
        if self.embed is None:
            return ()
        try:
            vectors = await self.embed.embed((query,))
        except Exception as error:
            log(PAGE_SEARCH_EVENT, leg="vector", error_class=type(error).__name__)
            return ()
        return vectors[0] if vectors else ()

    async def _vector(
        self, embedding: tuple[float, ...], reader: SourceReader, limit: int
    ) -> tuple[Hit, ...]:
        if not embedding:
            return ()
        return await self.index.vector(embedding, reader.subjects, OWNER_KIND_PAGE, limit)

    async def _readable(
        self, ranked: list[Hit], reader: SourceReader, limit: int
    ) -> tuple[PageHit, ...]:
        page_ids = tuple(dict.fromkeys(UUID(hit.owner_id) for hit in ranked))
        states = await self.readable(page_ids, reader)
        return tuple(
            PageHit(
                page_id=page_id,
                title=state.title,
                text=hit.text,
                dated=state.as_of,
                provider=state.backend,
            )
            for hit in ranked
            if (state := states.get(page_id := UUID(hit.owner_id))) is not None
            and state.subject == hit.subject
            and chunk_digest(OWNER_KIND_PAGE, str(page_id), state.digest, hit.ordinal, hit.text)
            == hit.chunk_digest
        )[:limit]
