"""The Exa search backend: one implementation of core's `search_providers` seam.

`ExaSearchProvider` runs host-side in the serve process — it reads the workspace's BYOK Exa key in
process through the scoped `CredentialAccess`, shapes each Exa request body, and sends it to
`api.exa.ai` over async httpx, mapping the response into the seam's `SearchResults`/`FetchedPage`.
The raw key never enters the sandbox: the `exa_api_key` credential slot the Manifest declares is
a plain host-side slot with no wire-injection, so the egress proxy derives no rule for it. The
research extension's tools call this backend through the turn's `ToolContext`; a deploy selects
it with `[research] search_provider = "exa"`."""

from dataclasses import dataclass

import httpx

from ufo.sdk.context import CredentialAccess
from ufo.sdk.manifest import CredentialSlot, Manifest, SearchProviderSpec
from ufo.sdk.search import FetchedPage, FetchRequest, SearchHit, SearchQuery, SearchResults

type Json = str | int | float | bool | None | list["Json"] | dict[str, "Json"]

NAME = "exa"
VERSION = "0.1.0"
EXA_BACKEND = "exa"
EXA_SLOT = "exa_api_key"
EXA_HOST = "api.exa.ai"
EXA_API_KEY_HEADER = "x-api-key"
EXA_TIMEOUT_SECONDS = 30
SEARCH_PATH = "/search"
CONTENTS_PATH = "/contents"
SEARCH_TEXT_CHARS = 1000
VERTICAL_TEXT_CHARS = 500
MAX_FETCH_CHARS = 20_000
PUBLISHED_DAY_START = "T00:00:00Z"
PUBLISHED_DAY_END = "T23:59:59Z"
VERTICAL_CATEGORY = {"academic": "research paper", "people": "linkedin profile"}


class ExaError(RuntimeError):
    """Exa answered a non-2xx status or a body without a results list — surfaced to the turn as an
    error result carrying the status and body, never a silent empty answer."""


@dataclass(frozen=True)
class ExaSearchProvider:
    """Core's `search_providers` seam backed by Exa: `search` runs one Exa `/search`, `fetch` reads
    one URL through `/contents`, both host-side over async httpx with the BYOK key read in process.
    `supports_fetch` is True — Exa fetches page content. The `transport` field is the httpx
    testability seam a test injects a `MockTransport` on; production leaves it None."""

    credentials: CredentialAccess
    transport: httpx.AsyncBaseTransport | None = None
    supports_fetch: bool = True

    async def search(self, query: SearchQuery) -> SearchResults:
        payload = await self._post(SEARCH_PATH, self._search_body(query))
        return SearchResults(hits=tuple(self._hit(item) for item in _results(payload)))

    async def fetch(self, request: FetchRequest) -> FetchedPage:
        body: dict[str, Json] = {
            "urls": [request.url],
            "text": {"maxCharacters": min(request.max_chars or MAX_FETCH_CHARS, MAX_FETCH_CHARS)},
        }
        if request.prompt:
            body["summary"] = {"query": request.prompt}
        if request.force:
            body["livecrawl"] = "always"
        payload = await self._post(CONTENTS_PATH, body)
        results = _results(payload)
        item = results[0] if results else {}
        return FetchedPage(
            url=str(item.get("url") or request.url),
            text=str(item.get("text") or ""),
            summary=_opt_str(item.get("summary")),
        )

    @staticmethod
    def _search_body(query: SearchQuery) -> dict[str, Json]:
        body: dict[str, Json] = {"query": query.query, "numResults": query.num_results}
        if query.vertical is None:
            body["contents"] = {"text": {"maxCharacters": SEARCH_TEXT_CHARS}, "highlights": True}
            if query.allowed_domains:
                body["includeDomains"] = list(query.allowed_domains)
            if query.start_published_date is not None:
                start = query.start_published_date.isoformat()
                body["startPublishedDate"] = f"{start}{PUBLISHED_DAY_START}"
            if query.end_published_date is not None:
                end = query.end_published_date.isoformat()
                body["endPublishedDate"] = f"{end}{PUBLISHED_DAY_END}"
        else:
            body["contents"] = {"text": {"maxCharacters": VERTICAL_TEXT_CHARS}}
            category = VERTICAL_CATEGORY.get(query.vertical)
            if category is not None:
                body["category"] = category
        return body

    @staticmethod
    def _hit(item: dict[str, object]) -> SearchHit:
        highlights = item.get("highlights")
        return SearchHit(
            url=str(item.get("url") or ""),
            title=str(item.get("title") or ""),
            text=str(item.get("text") or ""),
            published_date=_opt_str(item.get("publishedDate")),
            highlights=tuple(h for h in highlights if isinstance(h, str))
            if isinstance(highlights, list)
            else (),
        )

    async def _post(self, path: str, body: dict[str, Json]) -> object:
        key = await self.credentials.get(EXA_SLOT)
        async with httpx.AsyncClient(
            base_url=f"https://{EXA_HOST}", timeout=EXA_TIMEOUT_SECONDS, transport=self.transport
        ) as http:
            response = await http.post(path, json=body, headers={EXA_API_KEY_HEADER: key})
        if response.status_code >= 400:
            raise ExaError(f"exa {path} failed ({response.status_code}): {response.text}")
        return response.json()


def _results(payload: object) -> list[dict[str, object]]:
    found = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(found, list):
        raise ExaError(f"exa response has no results list: {payload!r}")
    return [item for item in found if isinstance(item, dict)]


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        credentials=(
            CredentialSlot(
                name=EXA_SLOT,
                description="BYOK Exa API key; the search backend reads it in-process, host-side.",
            ),
        ),
        search_providers=(
            SearchProviderSpec(backend=EXA_BACKEND, build=lambda creds: ExaSearchProvider(creds)),
        ),
    )
