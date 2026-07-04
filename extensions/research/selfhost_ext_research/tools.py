"""The research tools: `search_web`, `fetch_url`, and `search_vertical` over the selected backend.

Each tool validates its arguments, calls the turn's selected `SearchProvider` (host-side, so the
backend reads its key in the serve process and the sandbox never sees it), and serializes the
seam's `SearchResults`/`FetchedPage` back to the model. `search_web` runs one provider search per
query and merges; `search_vertical` folds its content type into the query's `vertical`; `fetch_url`
gates on the provider's `supports_fetch` so a backend that only answers (never fetches) tells the
agent to reach for the browser or bash instead. A turn with no search backend fails loud."""

import json
from typing import Literal

from pydantic import BaseModel, Field

from selfhost.sdk.search import FetchRequest, SearchHit, SearchProvider, SearchQuery
from selfhost.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

SEARCH_WEB_TOOL = "search_web"
FETCH_URL_TOOL = "fetch_url"
SEARCH_VERTICAL_TOOL = "search_vertical"
MAX_SEARCH_QUERIES = 5
DEFAULT_SEARCH_RESULTS = 5
FETCH_UNSUPPORTED_MESSAGE = (
    "the configured search provider can't fetch a URL — use search_web, the browser tools, "
    "or bash with curl"
)

SEARCH_WEB_DESCRIPTION = (
    "Searches the web for current and factual information. Returns results with titles, "
    "URLs, and content snippets. Best for news, prices, and time-sensitive data. Use "
    "short, keyword-focused queries — max 3-5 per call. Run parallel queries for "
    "different topics rather than one combined query."
)
FETCH_URL_DESCRIPTION = (
    "Fetches content from an HTTP/HTTPS URL. Optionally extracts specific information via LLM "
    "prompt. Use to read web pages, documentation, articles, or any publicly accessible URL. "
    "Results are cached — use force_fetch=true if content appears stale."
)
SEARCH_VERTICAL_DESCRIPTION = (
    "Search specialized content verticals. Use instead of search_web when you need a specific "
    "content type: images, professional profiles, academic papers, videos, or product listings."
)


class SearchWebInput(BaseModel):
    queries: tuple[str, ...] = Field(max_length=MAX_SEARCH_QUERIES)
    recency_filter: Literal["day", "week", "month"] | None = None
    allowed_domains: tuple[str, ...] | None = None


class FetchUrlInput(BaseModel):
    url: str
    prompt: str | None = None
    max_length: int | None = None
    force_fetch: bool | None = None
    user_description: str


class SearchVerticalInput(BaseModel):
    vertical: Literal["image", "people", "academic", "video", "shopping"]
    query: str
    user_description: str


def _provider(ctx: ToolContext) -> SearchProvider:
    if ctx.search_provider is None:
        raise RuntimeError("no search provider is configured for this turn")
    return ctx.search_provider


def _results_json(hits: list[SearchHit], answer: str | None) -> str:
    results = [
        {
            "url": hit.url,
            "title": hit.title,
            "text": hit.text,
            "published_date": hit.published_date,
            "highlights": list(hit.highlights),
        }
        for hit in hits
    ]
    payload: dict[str, object] = {"results": results}
    if answer is not None:
        payload["answer"] = answer
    return json.dumps(payload)


async def _search_web(ctx: ToolContext, args: SearchWebInput) -> ToolResult:
    provider = _provider(ctx)
    hits: list[SearchHit] = []
    answer: str | None = None
    for query in args.queries:
        results = await provider.search(
            SearchQuery(
                query=query,
                num_results=DEFAULT_SEARCH_RESULTS,
                recency=args.recency_filter,
                allowed_domains=args.allowed_domains or (),
            )
        )
        hits.extend(results.hits)
        answer = answer or results.answer
    return ToolResult(content=(TextContent(text=_results_json(hits, answer)),))


async def _fetch_url(ctx: ToolContext, args: FetchUrlInput) -> ToolResult:
    provider = _provider(ctx)
    if not provider.supports_fetch:
        return ToolResult(content=(TextContent(text=FETCH_UNSUPPORTED_MESSAGE),), is_error=True)
    page = await provider.fetch(
        FetchRequest(
            url=args.url,
            prompt=args.prompt,
            max_chars=args.max_length,
            force=bool(args.force_fetch),
        )
    )
    reply: dict[str, object] = {"url": page.url, "text": page.text}
    if page.summary is not None:
        reply["summary"] = page.summary
    return ToolResult(content=(TextContent(text=json.dumps(reply)),))


async def _search_vertical(ctx: ToolContext, args: SearchVerticalInput) -> ToolResult:
    provider = _provider(ctx)
    results = await provider.search(
        SearchQuery(query=args.query, num_results=DEFAULT_SEARCH_RESULTS, vertical=args.vertical)
    )
    return ToolResult(
        content=(TextContent(text=_results_json(list(results.hits), results.answer)),)
    )


RESEARCH_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name=SEARCH_WEB_TOOL,
        description=SEARCH_WEB_DESCRIPTION,
        input_model=SearchWebInput,
        handler=_search_web,
        untrusted=True,
    ),
    ToolDef(
        name=FETCH_URL_TOOL,
        description=FETCH_URL_DESCRIPTION,
        input_model=FetchUrlInput,
        handler=_fetch_url,
        untrusted=True,
    ),
    ToolDef(
        name=SEARCH_VERTICAL_TOOL,
        description=SEARCH_VERTICAL_DESCRIPTION,
        input_model=SearchVerticalInput,
        handler=_search_vertical,
        untrusted=True,
    ),
)
