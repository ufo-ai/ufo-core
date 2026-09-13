"""The research tools: `search_web`, `fetch_url`, and `search_vertical` over the selected backend.

Each tool validates its arguments, calls the turn's selected `SearchProvider` (host-side, so the
backend reads its key in the serve process and the sandbox never sees it), and serializes the
seam's `SearchResults`/`FetchedPage` back to the model. `search_web` runs one provider search per
query and merges; `search_vertical` folds its content type into the query's `vertical`, except the
`internal` vertical, which answers from the workspace's own synced pages over the memory
extension's page index and never reaches the provider; `fetch_url`
gates on the provider's `supports_fetch` so a backend that only answers (never fetches) tells the
agent to reach for the browser or bash instead, and walls every page it returns with crawler
provenance — the backend fetches through its own crawler session, so identity or session context
in a response is the crawler's, never the workspace's, and no URL shape can predict which
responses carry it. A turn with no search backend fails loud."""

import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field
from ufo_ext_memory.manifest import MemorySearchService, match_line

from ufo.sdk.search import FetchRequest, SearchHit, SearchProvider, SearchQuery
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_research.observations import record_fetched_page, record_search_hits

SEARCH_WEB_TOOL = "search_web"
FETCH_URL_TOOL = "fetch_url"
SEARCH_VERTICAL_TOOL = "search_vertical"
INTERNAL_VERTICAL = "internal"
NO_INTERNAL_MATCHES_MESSAGE = "No matching pages."
MAX_SEARCH_QUERIES = 5
DEFAULT_SEARCH_RESULTS = 5
MAX_SEARCH_RESULTS = 25
FETCH_UNSUPPORTED_MESSAGE = (
    "the configured search provider can't fetch a URL — use search_web, the browser tools, "
    "or bash with curl"
)
CRAWLER_PROVENANCE = (
    "fetched by the search provider's crawler, not from this workspace: any account, identity, "
    "login, or session context in this content belongs to the crawler's own session, never to "
    "this workspace or the member — to see an API's real answer, call it from bash"
)

SEARCH_WEB_DESCRIPTION = (
    "Searches the web for current and factual information. Returns results with titles, "
    "URLs, and content snippets. Best for news, prices, and time-sensitive data. Write each "
    "query as a natural-language sentence stating what you want to know, and carry filters in a "
    "parameter rather than in the query text: when a page was published in "
    "start_published_date/end_published_date, a site restriction in allowed_domains. The period "
    "you are asking about stays in the sentence — a page reporting a finished year is published "
    "after that year ends. One query at a higher num_results beats several rephrasings of it — "
    "send more than one query only for genuinely different topics."
)
FETCH_URL_DESCRIPTION = (
    "Fetches content from an HTTP/HTTPS URL. Optionally extracts specific information via LLM "
    "prompt. Use to read web pages, documentation, articles, or any publicly accessible URL. "
    "Results are cached — use force_fetch=true if content appears stale. Fetches run through a "
    "crawler whose session is not yours: identity or account context in a response is the "
    "crawler's, so call APIs from bash instead of fetching them."
)
SEARCH_VERTICAL_DESCRIPTION = (
    "Search specialized content verticals. Use instead of search_web when you need a specific "
    "content type: images, professional profiles, academic papers, videos, product listings, or "
    "the documents synced into this workspace."
)


class SearchWebInput(BaseModel):
    queries: tuple[str, ...] = Field(
        max_length=MAX_SEARCH_QUERIES,
        description="One natural-language sentence per query, stating the intent — not a keyword "
        "string. Max 5, and each must be a genuinely different topic: rephrasings of one question "
        "belong in a single query at a higher num_results. Keep publication filters, site: "
        "filters, and quotes out of the text — they belong in the parameters below; the period you "
        "are asking about stays in the sentence.",
    )
    num_results: int | None = Field(
        default=None,
        ge=1,
        le=MAX_SEARCH_RESULTS,
        description=f"How many results each query returns, 1-{MAX_SEARCH_RESULTS}; defaults to "
        f"{DEFAULT_SEARCH_RESULTS}. Raise it to cover one question broadly instead of firing "
        "near-duplicate queries.",
    )
    start_published_date: date | None = Field(
        default=None,
        description="Only return pages published on or after this date (YYYY-MM-DD). Recency "
        "constraints go here rather than into the query text.",
    )
    end_published_date: date | None = Field(
        default=None,
        description="Only return pages published on or before this date (YYYY-MM-DD). This filters "
        "on when a page was published, not on the period it reports: sources for a finished year "
        "are published after that year ends, so a window closed at the period's end drops them.",
    )
    allowed_domains: tuple[str, ...] | None = Field(
        default=None,
        description="Only return results from these domains, e.g. ['nytimes.com', 'reuters.com']. "
        "Leave unset for all domains. Use this instead of site: syntax in queries.",
    )


class FetchUrlInput(BaseModel):
    url: str = Field(
        description="The public URL to fetch. Must start with http:// or https://. Never pass "
        "local file paths or internal storage paths."
    )
    prompt: str | None = Field(
        default=None,
        description="Optional LLM prompt to extract specific information. If omitted, returns raw "
        "page content. May summarize or truncate long documents.",
    )
    max_length: int | None = Field(
        default=None,
        description="Maximum characters of raw content to return (~10k tokens). Increase for "
        "longer documents.",
    )
    force_fetch: bool | None = Field(
        default=None,
        description="Bypass cache and force a real-time fetch. Costly — only use when cached "
        "content appears outdated or incorrect.",
    )


class SearchVerticalInput(BaseModel):
    vertical: Literal["image", "people", "academic", "video", "shopping", "internal"] = Field(
        description="'image' for photos/illustrations, 'people' for finding professionals by "
        "name/role/company/location (NOT for company lookups), 'academic' for research "
        "papers/publications, 'video' for video content, 'shopping' for product listings with "
        "prices, 'internal' for the documents a connected account, folder, or site syncs into this "
        "workspace — searched over the shared audience plus the exact member making this request, "
        "answering each hit with its object ref (page/<id>) to pass unchanged to object_get."
    )
    query: str = Field(
        description="A natural-language phrase for what you want, not a keyword list. E.g. "
        "'photographs of a golden retriever puppy', 'transformer architectures for machine "
        "translation', 'John Smith, the CTO of Acme'."
    )


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
                num_results=args.num_results or DEFAULT_SEARCH_RESULTS,
                start_published_date=args.start_published_date,
                end_published_date=args.end_published_date,
                allowed_domains=args.allowed_domains or (),
            )
        )
        hits.extend(results.hits)
        answer = answer or results.answer
    if ctx.ext is not None:
        await record_search_hits(ctx.ext, ctx.turn.conversation_id, ctx.turn.id, tuple(hits))
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
    if ctx.ext is not None:
        await record_fetched_page(ctx.ext, ctx.turn.conversation_id, ctx.turn.id, page)
    reply: dict[str, object] = {
        "url": page.url,
        "text": page.text,
        "provenance": CRAWLER_PROVENANCE,
    }
    if page.summary is not None:
        reply["summary"] = page.summary
    return ToolResult(content=(TextContent(text=json.dumps(reply)),))


async def _search_internal(ctx: ToolContext, query: str) -> ToolResult:
    """The internal vertical: the synced page store over the memory extension's page index, on the
    subject and source-reach fences `memory_search` reads its page hits under. An explicit member
    request may search that member's private sources even in a shared conversation; that access is
    never ambient for other members."""
    if ctx.ext is None:
        raise RuntimeError("search_vertical dispatched without its ExtensionContext")
    matches = await MemorySearchService(ctx.ext).search_pages((query,), ctx.source_reader())
    if not matches:
        return ToolResult(content=(TextContent(text=NO_INTERNAL_MATCHES_MESSAGE),))
    return ToolResult(
        content=(TextContent(text="\n".join(match_line(match) for match in matches)),)
    )


async def _search_vertical(ctx: ToolContext, args: SearchVerticalInput) -> ToolResult:
    if args.vertical == INTERNAL_VERTICAL:
        return await _search_internal(ctx, args.query)
    provider = _provider(ctx)
    results = await provider.search(
        SearchQuery(query=args.query, num_results=DEFAULT_SEARCH_RESULTS, vertical=args.vertical)
    )
    if ctx.ext is not None:
        await record_search_hits(ctx.ext, ctx.turn.conversation_id, ctx.turn.id, results.hits)
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
        binds_member_authority=False,
    ),
    ToolDef(
        name=FETCH_URL_TOOL,
        description=FETCH_URL_DESCRIPTION,
        input_model=FetchUrlInput,
        handler=_fetch_url,
        untrusted=True,
        binds_member_authority=False,
    ),
    ToolDef(
        name=SEARCH_VERTICAL_TOOL,
        description=SEARCH_VERTICAL_DESCRIPTION,
        input_model=SearchVerticalInput,
        handler=_search_vertical,
        untrusted=True,
        binds_member_authority=False,
    ),
)
