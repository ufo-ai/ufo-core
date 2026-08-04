"""The search seam: where a turn's web search and page fetch come from, decoupled from the tools.

Core owns only the provider seam, never a search backend. A `SearchProvider` — built once at boot,
selected by `config.research.search_provider` — answers a `SearchQuery` with `SearchResults` and,
when `supports_fetch`, a `FetchRequest` with a `FetchedPage`. A backend reads its own BYOK key
in-process, host-side (the serve process), and reaches its API over async HTTP; core never holds the
key and the sandbox never sees it. Every backend is an extension registering a `SearchProviderSpec`
at the `search_providers` Manifest seam — there is no core default. The research extension's tools
call the selected provider through the turn's `ToolContext`; the research `fetch_url` tool gates
on `supports_fetch` before calling `fetch`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SearchHit:
    """One result of a web search: the page URL and title, a text snippet, an optional publish date,
    and any highlighted passages the backend returned."""

    url: str
    title: str
    text: str
    published_date: str | None = None
    highlights: tuple[str, ...] = ()


@dataclass(frozen=True)
class SearchResults:
    """What `SearchProvider.search` yields: the ranked hits, plus an optional synthesized `answer` a
    backend that answers directly (rather than only linking sources) provides."""

    hits: tuple[SearchHit, ...]
    answer: str | None = None


@dataclass(frozen=True)
class FetchedPage:
    """What `SearchProvider.fetch` yields for one URL: the extracted page text and, when the fetch
    carried an extraction prompt, the backend's `summary` of it."""

    url: str
    text: str
    summary: str | None = None


@dataclass(frozen=True)
class SearchQuery:
    """One web search the provider runs: the `query` text, how many results to return, an optional
    `recency` window keyword (`day`/`week`/`month`), domains to restrict to, and an optional
    `vertical` (academic, people, image, video, shopping) the backend maps to its own category."""

    query: str
    num_results: int
    recency: str | None = None
    allowed_domains: tuple[str, ...] = ()
    vertical: str | None = None


@dataclass(frozen=True)
class FetchRequest:
    """One URL fetch: the `url`, an optional extraction `prompt`, a `max_chars` cap on the returned
    text, and `force` to bypass the backend's cache and recrawl live."""

    url: str
    prompt: str | None = None
    max_chars: int | None = None
    force: bool = False


class SearchProvider(Protocol):
    """Where a turn's web search comes from: `search` answers a `SearchQuery`; `fetch` reads one URL
    when `supports_fetch` is True. Process-wide (built once at boot), reading its provider key
    in-process and reaching its API host-side — never in the sandbox."""

    @property
    def supports_fetch(self) -> bool: ...

    async def search(self, query: SearchQuery) -> SearchResults: ...

    async def fetch(self, request: FetchRequest) -> FetchedPage: ...
