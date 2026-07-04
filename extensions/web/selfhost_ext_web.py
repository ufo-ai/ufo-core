"""The research web tool pack: `search_web` and `fetch_url`, backed by the Exa API.

Neither tool holds the Exa key. Each shapes an Exa request body and sends it to `api.exa.ai` through
the sandbox's egress proxy, carrying the sentinel `x-api-key` value in the header. The proxy admits
the host (an unconfigured slot opens no egress, so the CONNECT is refused), swaps the sentinel for
the workspace's stored BYOK key on the wire, and meters the request under `requests` — so the raw
secret never enters the sandbox and the call shows in `selfhost spend`. The `exa_api` credential
slot the Manifest declares is what drives that injection."""

import json
import shlex
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from selfhost.sdk.manifest import CredentialSlot, InjectionTarget, Manifest, PromptSection
from selfhost.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

type Json = str | int | float | bool | None | list["Json"] | dict[str, "Json"]

NAME = "web"
VERSION = "0.1.0"
SEARCH_WEB_TOOL = "search_web"
FETCH_URL_TOOL = "fetch_url"
EXA_SLOT = "exa_api"
EXA_HOST = "api.exa.ai"
EXA_API_KEY_HEADER = "x-api-key"
EXA_SENTINEL = "SELFHOST_SENTINEL_EXA_KEY"
EGRESS_DIMENSION = "requests"
SEARCH_PATH = "/search"
CONTENTS_PATH = "/contents"
MAX_SEARCH_QUERIES = 5
DEFAULT_SEARCH_RESULTS = 5
SEARCH_TEXT_CHARS = 1000
MAX_FETCH_CHARS = 20_000
RECENCY_DAYS = {"day": 1, "week": 7, "month": 30}

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

SECTION_NAME = "web"
SECTION_BODY = (Path(__file__).parent / "web_section.md").read_text().strip()


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


async def _search_web(ctx: ToolContext, args: SearchWebInput) -> ToolResult:
    start_date: str | None = None
    if args.recency_filter is not None:
        start = datetime.now(UTC) - timedelta(days=RECENCY_DAYS[args.recency_filter])
        start_date = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    results: list[Json] = []
    for query in args.queries:
        body: dict[str, Json] = {
            "query": query,
            "numResults": DEFAULT_SEARCH_RESULTS,
            "contents": {"text": {"maxCharacters": SEARCH_TEXT_CHARS}, "highlights": True},
        }
        if args.allowed_domains:
            body["includeDomains"] = list(args.allowed_domains)
        if start_date is not None:
            body["startPublishedDate"] = start_date
        result = await _exa_post(ctx, SEARCH_PATH, body)
        if result.exit_code != 0:
            return ToolResult(
                content=(TextContent(text=result.stdout or result.stderr),), is_error=True
            )
        payload = json.loads(result.stdout)
        found = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(found, list):
            raise ValueError("exa search response has no results list")
        results.extend(found)
    return ToolResult(content=(TextContent(text=json.dumps({"results": results})),))


async def _fetch_url(ctx: ToolContext, args: FetchUrlInput) -> ToolResult:
    body: dict[str, Json] = {
        "urls": [args.url],
        "text": {"maxCharacters": min(args.max_length or MAX_FETCH_CHARS, MAX_FETCH_CHARS)},
    }
    if args.prompt:
        body["summary"] = {"query": args.prompt}
    if args.force_fetch:
        body["livecrawl"] = "always"
    result = await _exa_post(ctx, CONTENTS_PATH, body)
    return ToolResult(
        content=(TextContent(text=result.stdout or result.stderr),),
        is_error=result.exit_code != 0,
    )


async def _exa_post(ctx: ToolContext, path: str, body: dict[str, Json]):
    """POST the Exa request body to `path` through the sandbox egress proxy, carrying the sentinel
    `x-api-key` the proxy swaps for the workspace's BYOK Exa key. The request originates inside the
    sandbox — the only route the proxy meters and injects — so the raw key never enters the box."""
    api_key = shlex.quote(f"{EXA_API_KEY_HEADER}: {EXA_SENTINEL}")
    content_type = shlex.quote("Content-Type: application/json")
    data = shlex.quote(json.dumps(body))
    url = shlex.quote(f"https://{EXA_HOST}{path}")
    command = (
        f"curl -sS --fail-with-body -X POST -H {api_key} -H {content_type} --data {data} {url}"
    )
    return await ctx.sandbox.bash(command)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(
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
        ),
        credentials=(
            CredentialSlot(
                name=EXA_SLOT,
                description="BYOK Exa API key; the egress proxy swaps it onto api.exa.ai for the "
                "sentinel the sandbox sends.",
                injection=InjectionTarget(
                    host=EXA_HOST,
                    header=EXA_API_KEY_HEADER,
                    sentinel=EXA_SENTINEL,
                    dimension=EGRESS_DIMENSION,
                ),
            ),
        ),
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
    )
