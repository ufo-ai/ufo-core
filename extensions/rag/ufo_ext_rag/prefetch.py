"""One search across both corpora, fused into the passages a turn is grounded on.

The two legs run together: the deploy's web backend (Perplexity in a stock deploy) and the
workspace's own page store, read over the deploy's index backend under the reader's subjects and
source reach. Neither leg's rank is comparable to the other's, and a score is not
comparable across queries either, so the passages are fused by reciprocal rank — the fusion
Cormack, Clarke and Buettcher measured as beating the individual systems and the learned
combinations they compared it against, with no tuning and no shared score scale. Every ranked list
a query and a leg produce contributes `1 / (RRF_K + rank)` to the passage it holds.

Selection then takes the best passages under a character budget, after seating the top passage of
each leg that returned one: an answer that needs the web and the workspace together is the case the
budget must not starve, and taking the head of one strong leg would do exactly that. What is
selected is rendered best-first, since a model reads the head of a context window more reliably
than its middle.

Each leg is bounded on its own — a web backend that hangs must not cost the workspace leg — and a
leg that fails contributes nothing while the other still grounds the turn."""

import asyncio
from dataclasses import dataclass
from typing import Literal

from ufo.sdk.context import SourceReader
from ufo.sdk.hub import SourceRef
from ufo.sdk.index import TextChunker
from ufo.sdk.o11y import log
from ufo.sdk.search import SearchHit, SearchProvider, SearchQuery
from ufo.sdk.untrusted import wall
from ufo_ext_rag.fusion import fused_scores
from ufo_ext_rag.pages import PageHit, PageStore

type Origin = Literal["web", "workspace"]

EXTERNAL_RESULTS = 5
INTERNAL_LABEL: Origin = "workspace"
EXTERNAL_LABEL: Origin = "web"
LEG_TIMEOUT_SECONDS = 3.0
PAGE_HITS = 6
PASSAGE_MAX_CHARS = 700
TOTAL_MAX_CHARS = 4_000
MAX_PASSAGES = 8
CHUNK_TARGET_WORDS = 90
CHUNK_OVERLAP_WORDS = 15
TRUNCATION_NOTICE = " [TRUNCATED, call search_web again for full results]"
WALL_SOURCE = "search run before this turn"
PREFETCH_EVENT = "rag.prefetch"
CHUNKER = TextChunker(target_words=CHUNK_TARGET_WORDS, overlap_words=CHUNK_OVERLAP_WORDS)


@dataclass(frozen=True)
class Passage:
    """One retrieved piece of text with the provenance a reply cites it by."""

    origin: Origin
    title: str
    reference: str
    dated: str
    text: str
    provider: str = ""
    truncated: bool = False

    @property
    def key(self) -> str:
        return f"{self.reference}\x00{self.text}"

    @property
    def source(self) -> SourceRef:
        if self.origin == EXTERNAL_LABEL:
            return SourceRef(kind=EXTERNAL_LABEL, title=self.title, url=self.reference)
        return SourceRef(
            kind=INTERNAL_LABEL, title=self.title, ref=self.reference, provider=self.provider
        )

    def rendered(self, number: int) -> str:
        """The numbered entry a reply cites: the number and the title the reply names the source
        by, the reference the reader opens — a web address or a workspace record ref — the
        publication date when the source carries one, then the text itself."""
        lines = [f"[{number}] Title: {self.title}", f"URL: {self.reference}"]
        if self.dated:
            lines.append(f"Published: {self.dated}")
        body = f"{self.text}{TRUNCATION_NOTICE}" if self.truncated else self.text
        lines.append(f"Content:\n{body}")
        return "\n".join(lines)


def _rank(fused: dict[str, float], passage: Passage) -> tuple[float, str]:
    """Best first, and one order for a given set of passages whatever order the legs arrived in:
    two passages of equal fused score are ranked by their own key, never by which leg answered
    first."""
    return (-fused[passage.key], passage.key)


@dataclass(frozen=True)
class Grounding:
    """What one prefetch found: the walled block the model reads, and each place it was drawn from
    once, in the order the block names them."""

    block: str
    sources: tuple[SourceRef, ...]


@dataclass(frozen=True)
class Prefetch:
    """Run both legs for one turn's queries and render what they found."""

    search: SearchProvider | None
    pages: PageStore | None
    chunker: TextChunker = CHUNKER

    async def passages(self, queries: tuple[str, ...], reader: SourceReader) -> Grounding:
        """The grounding for `queries`: an empty block and no sources when neither leg found
        anything."""
        ranked = await self._legs(queries, reader)
        chosen = self._chosen(ranked)
        log(
            PREFETCH_EVENT,
            queries=len(queries),
            web=sum(1 for passage in chosen if passage.origin == EXTERNAL_LABEL),
            workspace=sum(1 for passage in chosen if passage.origin == INTERNAL_LABEL),
        )
        if not chosen:
            return Grounding(block="", sources=())
        block = wall(
            WALL_SOURCE,
            "\n\n".join(passage.rendered(number) for number, passage in enumerate(chosen, start=1)),
        )
        sources = {passage.reference: passage.source for passage in chosen}
        return Grounding(block=block, sources=tuple(sources.values()))

    async def _legs(
        self, queries: tuple[str, ...], reader: SourceReader
    ) -> tuple[tuple[Passage, ...], ...]:
        legs = await asyncio.gather(
            *(self._web(query) for query in queries),
            *(self._workspace(query, reader) for query in queries),
            return_exceptions=True,
        )
        return tuple(leg for leg in legs if isinstance(leg, tuple))

    async def _web(self, query: str) -> tuple[Passage, ...]:
        if self.search is None:
            return ()
        try:
            async with asyncio.timeout(LEG_TIMEOUT_SECONDS):
                results = await self.search.search(
                    SearchQuery(query=query, num_results=EXTERNAL_RESULTS)
                )
        except Exception as error:
            log(PREFETCH_EVENT, leg=EXTERNAL_LABEL, error_class=type(error).__name__)
            return ()
        return tuple(passage for hit in results.hits for passage in self._chunked_hit(hit))

    def _chunked_hit(self, hit: SearchHit) -> tuple[Passage, ...]:
        body = "\n".join((*hit.highlights, hit.text)).strip()
        chunks = self.chunker.chunk(body, EXTERNAL_LABEL, hit.url, EXTERNAL_LABEL, "")
        return tuple(
            Passage(
                origin=EXTERNAL_LABEL,
                title=hit.title,
                reference=hit.url,
                dated=hit.published_date or "",
                text=chunk.text[:PASSAGE_MAX_CHARS],
                truncated=len(chunk.text) > PASSAGE_MAX_CHARS,
            )
            for chunk in chunks
        )

    async def _workspace(self, query: str, reader: SourceReader) -> tuple[Passage, ...]:
        if self.pages is None:
            return ()
        try:
            async with asyncio.timeout(LEG_TIMEOUT_SECONDS):
                hits = await self.pages.hits(query, reader, PAGE_HITS)
        except Exception as error:
            log(PREFETCH_EVENT, leg=INTERNAL_LABEL, error_class=type(error).__name__)
            return ()
        return tuple(self._page_passage(hit) for hit in hits)

    @staticmethod
    def _page_passage(hit: PageHit) -> Passage:
        return Passage(
            origin=INTERNAL_LABEL,
            title=hit.title,
            reference=hit.ref,
            dated=hit.dated,
            text=hit.text[:PASSAGE_MAX_CHARS],
            provider=hit.provider,
            truncated=len(hit.text) > PASSAGE_MAX_CHARS,
        )

    def _chosen(self, ranked: tuple[tuple[Passage, ...], ...]) -> tuple[Passage, ...]:
        fused = fused_scores([[passage.key for passage in leg] for leg in ranked])
        held: dict[str, Passage] = {}
        for leg in ranked:
            for passage in leg:
                held.setdefault(passage.key, passage)
        order = sorted(held.values(), key=lambda passage: _rank(fused, passage))
        heads = [
            next((passage for passage in order if passage.origin == origin), None)
            for origin in (EXTERNAL_LABEL, INTERNAL_LABEL)
        ]
        seated = [passage for passage in heads if passage is not None]
        selected: list[Passage] = []
        total = 0
        for passage in (*seated, *order):
            if passage in selected:
                continue
            if len(selected) == MAX_PASSAGES or total + len(passage.text) > TOTAL_MAX_CHARS:
                continue
            selected.append(passage)
            total += len(passage.text)
        return tuple(sorted(selected, key=lambda passage: _rank(fused, passage)))
