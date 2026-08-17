"""Perplexity web search and page extraction for the `search_providers` seam."""

from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from ufo.sdk.context import CredentialAccess
from ufo.sdk.manifest import CredentialSlot, Manifest, SearchProviderSpec
from ufo.sdk.search import FetchedPage, FetchRequest, SearchHit, SearchQuery, SearchResults

type Json = str | int | float | bool | None | list["Json"] | dict[str, "Json"]

NAME = "perplexity"
VERSION = "0.1.0"
PERPLEXITY_BACKEND = "perplexity"
PERPLEXITY_SLOT = "perplexity_api_key"
PERPLEXITY_HOST = "api.perplexity.ai"
PERPLEXITY_AUTH_HEADER = "authorization"
PERPLEXITY_TIMEOUT_SECONDS = 30
SEARCH_PATH = "/search"
MIN_RESULTS = 1
MAX_RESULTS = 20
FETCH_RESULTS = 10
SEARCH_TOKENS_PER_PAGE = 1_000
MAX_SEARCH_TOKENS = 10_000
MAX_FETCH_TOKENS = 5_000
CHARS_PER_TOKEN = 4
MAX_FETCH_CHARS = 20_000
MAX_QUERY_CHARS = 2_000
MAX_PROMPT_CHARS = 2_000
MAX_URL_CHARS = 2_048
MAX_ERROR_CHARS = 2_000
VERTICAL_QUALIFIER = {
    "academic": "research papers and publications",
    "image": "images and photographs",
    "video": "videos",
    "shopping": "product listings",
}
CANONICAL_PAGE_SUFFIXES = (".html", ".htm", ".txt")


class PerplexityError(RuntimeError):
    """Perplexity refused a request or returned an invalid response."""


class _PerplexitySearchItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    url: str
    title: str
    snippet: str
    date: str | None = None


class _PerplexitySearchResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: tuple[_PerplexitySearchItem, ...]


@dataclass(frozen=True)
class PerplexitySearchProvider:
    """Host-side Perplexity Search API discovery and exact-URL content extraction."""

    credentials: CredentialAccess
    transport: httpx.AsyncBaseTransport | None = None
    supports_fetch: bool = True

    async def search(self, query: SearchQuery) -> SearchResults:
        payload = await self._post(self._search_body(query))
        response = self._response(payload)
        return SearchResults(
            hits=tuple(
                SearchHit(
                    url=item.url,
                    title=item.title,
                    text=item.snippet,
                    published_date=item.date,
                )
                for item in response.results
            )
        )

    async def fetch(self, request: FetchRequest) -> FetchedPage:
        if len(request.url) > MAX_URL_CHARS:
            raise PerplexityError("Perplexity fetch URL cannot exceed 2048 characters")
        if request.prompt is not None and len(request.prompt) > MAX_PROMPT_CHARS:
            raise PerplexityError("Perplexity extraction prompt cannot exceed 2000 characters")
        if request.max_chars is not None and request.max_chars < 0:
            raise PerplexityError("Perplexity max_chars must be non-negative")
        parsed_url = urlsplit(request.url)
        domain = parsed_url.hostname
        if parsed_url.scheme not in {"http", "https"} or domain is None:
            raise PerplexityError("Perplexity fetch requires an absolute HTTP URL")
        query = request.url
        if request.prompt is not None:
            query = f"{query}\n{request.prompt}"
        limit = min(
            request.max_chars if request.max_chars is not None else MAX_FETCH_CHARS,
            MAX_FETCH_CHARS,
        )
        tokens = max(
            MIN_RESULTS,
            min((limit + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN, MAX_FETCH_TOKENS),
        )
        payload = await self._post(
            {
                "query": query,
                "search_domain_filter": [domain],
                "max_results": FETCH_RESULTS,
                "max_tokens": tokens,
                "max_tokens_per_page": tokens,
            }
        )
        response = self._response(payload)
        requested_page = _canonical_page(request.url)
        item = next(
            (item for item in response.results if _canonical_page(item.url) == requested_page),
            None,
        )
        if item is None:
            raise PerplexityError(f"Perplexity did not return the requested URL: {request.url}")
        text = item.snippet[:limit]
        return FetchedPage(
            url=item.url,
            text=text,
            summary=text if request.prompt is not None else None,
        )

    @staticmethod
    def _search_body(query: SearchQuery) -> dict[str, Json]:
        if query.num_results < MIN_RESULTS:
            raise PerplexityError("Perplexity num_results must be at least 1")
        result_count = min(query.num_results, MAX_RESULTS)
        parts = [query.query]
        qualifier = VERTICAL_QUALIFIER.get(query.vertical or "")
        if qualifier is not None:
            parts.append(qualifier)
        search_text = " ".join(parts)
        if len(search_text) > MAX_QUERY_CHARS:
            raise PerplexityError("Perplexity search query cannot exceed 2000 characters")
        body: dict[str, Json] = {
            "query": search_text,
            "max_results": result_count,
            "max_tokens": min(result_count * SEARCH_TOKENS_PER_PAGE, MAX_SEARCH_TOKENS),
            "max_tokens_per_page": SEARCH_TOKENS_PER_PAGE,
        }
        if query.vertical == "people":
            body["search_type"] = "people"
        if query.allowed_domains:
            body["search_domain_filter"] = list(query.allowed_domains)
        if query.start_published_date is not None:
            body["search_after_date_filter"] = _api_date(query.start_published_date)
        if query.end_published_date is not None:
            body["search_before_date_filter"] = _api_date(query.end_published_date)
        return body

    @staticmethod
    def _response(payload: object) -> _PerplexitySearchResponse:
        try:
            return _PerplexitySearchResponse.model_validate(payload)
        except ValidationError as error:
            raise PerplexityError("Perplexity /search returned an invalid response") from error

    async def _post(self, body: dict[str, Json]) -> object:
        key = await self.credentials.get(PERPLEXITY_SLOT)
        async with httpx.AsyncClient(
            base_url=f"https://{PERPLEXITY_HOST}",
            timeout=PERPLEXITY_TIMEOUT_SECONDS,
            transport=self.transport,
        ) as http:
            response = await http.post(
                SEARCH_PATH,
                json=body,
                headers={PERPLEXITY_AUTH_HEADER: f"Bearer {key}"},
            )
        if response.status_code >= 400:
            raise PerplexityError(
                f"Perplexity {SEARCH_PATH} failed ({response.status_code}): "
                f"{response.text[:MAX_ERROR_CHARS]}"
            )
        try:
            return response.json()
        except ValueError as error:
            raise PerplexityError("Perplexity /search returned invalid JSON") from error


def _api_date(value: date) -> str:
    return value.strftime("%m/%d/%Y")


def _canonical_page(value: str) -> tuple[str | None, str, str]:
    parsed = urlsplit(value)
    path = parsed.path.rstrip("/")
    for suffix in CANONICAL_PAGE_SUFFIXES:
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return parsed.hostname, path, parsed.query


def manifest() -> Manifest:
    """Register the Perplexity credential slot and search provider."""

    return Manifest(
        name=NAME,
        version=VERSION,
        credentials=(
            CredentialSlot(
                name=PERPLEXITY_SLOT,
                description=(
                    "BYOK Perplexity API key; the search backend reads it in-process, host-side."
                ),
            ),
        ),
        search_providers=(
            SearchProviderSpec(
                backend=PERPLEXITY_BACKEND,
                build=lambda credentials: PerplexitySearchProvider(credentials),
            ),
        ),
    )
