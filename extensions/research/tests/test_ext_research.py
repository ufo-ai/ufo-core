"""The research pack's proof: its tools marshal into the turn's selected `SearchProvider` and back,
its profiles register, and its web section renders.

A `_FakeSearchProvider` on the ToolContext stands in for the deploy's backend — it records the
`SearchQuery`/`FetchRequest` the tools build and answers with canned seam objects, so the tests
assert the tools' own marshalling (queries fanned and merged, the vertical folded in, the fetch
gate, the fail-loud on a missing provider), never the fake. The Perplexity backend keeps its
proof."""

import json
import re
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import ufo_ext_memory.manifest as memory_manifest
import ufo_ext_research.manifest as research_manifest
import ufo_ext_research.tools as research_tools
import ufo_ext_sources.manifest as sources_manifest
from cryptography.fernet import Fernet
from pydantic import ValidationError
from ufo_ext_research.subagent import (
    DEEP_RESEARCH_PROFILE,
    DEEP_RESEARCH_ROUND_LIMIT,
    RESEARCH_PROFILE,
    RESEARCH_TOOL_NAMES,
)
from ufo_ext_research.tools import RESEARCH_TOOLS

from ufo.host.ext.loader import skill_registry, turn_tools
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.queue import _subagent_actions, _subagent_tools, _with_action_verbs
from ufo.runtime.search import (
    FetchedPage,
    FetchRequest,
    SearchHit,
    SearchProvider,
    SearchQuery,
    SearchResults,
)
from ufo.runtime.tools.context import ToolContext
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import SHARED_AUDIENCE, conversation_audience

TOOL_NARRATION = "looking it up"

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
PERIOD_STAYS_IN_THE_SENTENCE = re.compile(r"period[^.]*stays in the sentence")
PUBLICATION_NOT_THE_REPORTED_PERIOD = re.compile(r"published, not on the period it reports")


class _FakeSearchProvider:
    """Records the queries and fetches the tools build and answers with canned seam objects. A
    dependency stand-in — the tests assert the tools' marshalling, never this fake."""

    def __init__(self, supports_fetch: bool = True, answer: str | None = None) -> None:
        self.supports_fetch = supports_fetch
        self.answer = answer
        self.queries: list[SearchQuery] = []
        self.fetches: list[FetchRequest] = []

    async def search(self, query: SearchQuery) -> SearchResults:
        self.queries.append(query)
        return SearchResults(
            hits=(
                SearchHit(
                    url=f"https://{query.query}.test",
                    title=query.query,
                    text="snippet",
                    published_date="2024-01-01",
                    highlights=("h1",),
                ),
            ),
            answer=self.answer,
        )

    async def fetch(self, request: FetchRequest) -> FetchedPage:
        self.fetches.append(request)
        return FetchedPage(
            url=request.url,
            text="page text",
            summary="the summary" if request.prompt else None,
        )


def _search_web_guidance_copies() -> tuple[str, ...]:
    (section,) = research_manifest.manifest().prompt_sections
    return (
        research_tools.SEARCH_WEB_DESCRIPTION,
        research_tools.SearchWebInput.model_fields["queries"].description or "",
        section.body,
        RESEARCH_PROFILE.prompt,
        DEEP_RESEARCH_PROFILE.prompt,
    )


def _no_spawn(profile: str, payload: dict[str, object], background: bool = False) -> object:
    raise AssertionError("the research web tools do not spawn subagents")


def _context(provider: SearchProvider | None) -> ToolContext:
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="research this",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_no_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
        search_provider=provider,
    )


def _tool(name: str):
    return next(tool for tool in RESEARCH_TOOLS if tool.name == name)


async def _run(name: str, provider: SearchProvider | None, **args: object):
    tool = _tool(name)
    return await tool.handler(
        _context(provider),
        tool.input_model.model_validate({**args}),
    )


def test_manifest_declares_the_tools_profiles_section_and_requires() -> None:
    manifest = research_manifest.manifest()
    assert manifest.name == "research"
    assert {tool.name for tool in manifest.tools} == {
        "search_web",
        "fetch_url",
        "search_vertical",
        "wide_research",
    }
    assert {profile.name for profile in manifest.subagents} == {"research", "deep_research"}
    assert {section.name for section in manifest.prompt_sections} == {"web"}
    assert manifest.requires == ("search_providers",)
    assert not manifest.credentials


async def test_search_web_runs_one_provider_search_per_query_and_merges() -> None:
    provider = _FakeSearchProvider(answer="direct answer")
    result = await _run(
        "search_web",
        provider,
        queries=["alpha", "beta"],
        allowed_domains=["docs.x.test"],
    )
    assert result.is_error is False
    assert [query.query for query in provider.queries] == ["alpha", "beta"]
    first = provider.queries[0]
    assert first.num_results == research_tools.DEFAULT_SEARCH_RESULTS
    assert first.allowed_domains == ("docs.x.test",)
    assert first.vertical is None
    payload = json.loads(result.content[0].text)
    assert [hit["url"] for hit in payload["results"]] == ["https://alpha.test", "https://beta.test"]
    assert payload["results"][0]["highlights"] == ["h1"]
    assert payload["results"][0]["published_date"] == "2024-01-01"
    assert payload["answer"] == "direct answer"


async def test_search_web_omits_answer_when_the_provider_gives_none() -> None:
    result = await _run("search_web", _FakeSearchProvider(), queries=["solo"])
    assert "answer" not in json.loads(result.content[0].text)


async def test_search_web_leaves_the_date_window_unset_when_the_agent_passes_none() -> None:
    provider = _FakeSearchProvider()
    await _run("search_web", provider, queries=["How does the seam work?"])
    (query,) = provider.queries
    assert query.start_published_date is None
    assert query.end_published_date is None


def test_search_web_bounds_the_result_count_and_rejects_a_malformed_date() -> None:
    with pytest.raises(ValidationError):
        research_tools.SearchWebInput.model_validate(
            {
                "queries": ["x"],
                "num_results": research_tools.MAX_SEARCH_RESULTS + 1,
            }
        )
    with pytest.raises(ValidationError):
        research_tools.SearchWebInput.model_validate(
            {
                "queries": ["x"],
                "start_published_date": "last week",
            }
        )


async def test_fetch_url_walls_every_page_with_crawler_provenance() -> None:
    provider = _FakeSearchProvider()
    result = await _run("fetch_url", provider, url="https://api.github.com/user")
    assert result.is_error is False
    reply = json.loads(result.content[0].text)
    assert reply["provenance"] == research_tools.CRAWLER_PROVENANCE
    assert "crawler's own session" in reply["provenance"]


async def test_the_internal_vertical_never_reaches_the_search_provider() -> None:
    """The internal vertical is answered from the workspace's own page store, so it asks the web
    backend nothing — and a turn holding no extension context fails loud rather than reporting an
    empty page store."""
    provider = _FakeSearchProvider()
    with pytest.raises(RuntimeError, match="without its ExtensionContext"):
        await _run(
            "search_vertical",
            provider,
            vertical=research_tools.INTERNAL_VERTICAL,
            query="the expense policy",
        )
    assert provider.queries == []


async def test_web_tools_fail_loud_without_a_search_provider() -> None:
    with pytest.raises(RuntimeError, match="no search provider is configured"):
        await _run("search_web", None, queries=["x"])
    with pytest.raises(RuntimeError, match="no search provider is configured"):
        await _run("fetch_url", None, url="https://ex.test")


def test_deep_research_lifts_its_round_budget_above_the_default() -> None:
    assert DEEP_RESEARCH_PROFILE.max_rounds == DEEP_RESEARCH_ROUND_LIMIT == 200
    assert RESEARCH_PROFILE.max_rounds < DEEP_RESEARCH_PROFILE.max_rounds


def test_research_skills_parse_and_index() -> None:
    index = dict(skill_registry((research_manifest.manifest(),)).index())
    for name in ("research-assistant", "research-report", "competitive-intel"):
        assert name in index


def test_a_research_child_keeps_memory_search_on_the_wire() -> None:
    tools, _, verbs = turn_tools(
        (memory_manifest.manifest(), sources_manifest.manifest(), research_manifest.manifest()),
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        audience=SHARED_AUDIENCE,
    )
    assert "memory_search" in RESEARCH_TOOL_NAMES
    assert not {"object_list", "object_get", "object_action"} & set(RESEARCH_TOOL_NAMES)
    for profile in (RESEARCH_PROFILE, DEEP_RESEARCH_PROFILE):
        granted = _subagent_actions(verbs.actions, profile, frozenset())
        assert granted == frozenset()
        selected = _with_action_verbs(_subagent_tools(tools, profile, frozenset()), tools, granted)
        names = {tool.name for tool in selected}
        assert "memory_search" in names
        assert not names & {
            "object_action",
            "object_list",
            "object_get",
            "object_apply",
            "object_delete",
        }
