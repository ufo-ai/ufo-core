"""The research pack's proof: its tools marshal into the turn's selected `SearchProvider` and back,
its profiles register, and its web section renders.

A `_FakeSearchProvider` on the ToolContext stands in for the deploy's backend — it records the
`SearchQuery`/`FetchRequest` the tools build and answers with canned seam objects, so the tests
assert the tools' own marshalling (queries fanned and merged, the vertical folded in, the fetch
gate, the fail-loud on a missing provider), never the fake. The exa backend keeps its own proof."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import ufo_ext_research.manifest as research_manifest
import ufo_ext_research.tools as research_tools
from ufo_ext_research.subagent import (
    DEEP_RESEARCH_PROFILE,
    DEEP_RESEARCH_ROUND_LIMIT,
    RESEARCH_PROFILE,
    RESEARCH_TOOL_NAMES,
)
from ufo_ext_research.tools import RESEARCH_TOOLS

from ufo.ext.loader import skill_registry
from ufo.loop.prompts.render import render_system_prompt
from ufo.loop.subagents import subagent_system_prompt
from ufo.schema.records import Agent, Turn
from ufo.search import (
    FetchedPage,
    FetchRequest,
    SearchHit,
    SearchProvider,
    SearchQuery,
    SearchResults,
    SearchUnsupported,
)
from ufo.tools.context import ToolContext

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


class _FakeSearchProvider:
    """Records the queries and fetches the tools build and answers with canned seam objects. A
    dependency stand-in — the tests assert the tools' marshalling, never this fake. `supports_fetch`
    False honors the seam contract by raising `SearchUnsupported` from `fetch`, which the tool's
    gate ensures is never reached."""

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
        if not self.supports_fetch:
            raise SearchUnsupported("this fake cannot fetch")
        return FetchedPage(
            url=request.url,
            text="page text",
            summary="the summary" if request.prompt else None,
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
        audience_member_id=None,
        artifact_token_secret="",
        search_provider=provider,
    )


def _tool(name: str):
    return next(tool for tool in RESEARCH_TOOLS if tool.name == name)


async def _run(name: str, provider: SearchProvider | None, **args: object):
    tool = _tool(name)
    return await tool.handler(_context(provider), tool.input_model.model_validate(args))


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


def test_tool_descriptions_are_the_ported_verbatim_strings_and_untrusted() -> None:
    by_name = {tool.name: tool for tool in RESEARCH_TOOLS}
    assert by_name["search_web"].description == SEARCH_WEB_DESCRIPTION
    assert by_name["fetch_url"].description == FETCH_URL_DESCRIPTION
    assert by_name["search_vertical"].description == research_tools.SEARCH_VERTICAL_DESCRIPTION
    assert all(tool.untrusted for tool in RESEARCH_TOOLS)
    assert set(research_tools.SearchWebInput.model_fields) == {
        "queries",
        "recency_filter",
        "allowed_domains",
    }
    assert set(research_tools.FetchUrlInput.model_fields) == {
        "url",
        "prompt",
        "max_length",
        "force_fetch",
        "user_description",
    }
    schema = by_name["search_vertical"].schema().input_schema
    assert set(schema["properties"]["vertical"]["enum"]) == {
        "image",
        "people",
        "academic",
        "video",
        "shopping",
    }


async def test_search_web_runs_one_provider_search_per_query_and_merges() -> None:
    provider = _FakeSearchProvider(answer="direct answer")
    result = await _run(
        "search_web",
        provider,
        queries=["alpha", "beta"],
        recency_filter="week",
        allowed_domains=["docs.x.test"],
    )
    assert result.is_error is False
    assert [query.query for query in provider.queries] == ["alpha", "beta"]
    first = provider.queries[0]
    assert first.num_results == research_tools.DEFAULT_SEARCH_RESULTS
    assert first.recency == "week"
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


async def test_search_vertical_folds_the_vertical_into_the_query() -> None:
    provider = _FakeSearchProvider()
    result = await _run(
        "search_vertical",
        provider,
        vertical="academic",
        query="graph transformers",
        user_description="papers",
    )
    assert result.is_error is False
    assert provider.queries[-1].vertical == "academic"
    assert provider.queries[-1].query == "graph transformers"


async def test_fetch_url_maps_the_page_and_passes_the_extraction_prompt() -> None:
    provider = _FakeSearchProvider()
    result = await _run(
        "fetch_url",
        provider,
        url="https://ex.test/a",
        prompt="summarize it",
        max_length=999_999,
        force_fetch=True,
        user_description="read it",
    )
    assert result.is_error is False
    request = provider.fetches[-1]
    assert (request.url, request.prompt, request.max_chars, request.force) == (
        "https://ex.test/a",
        "summarize it",
        999_999,
        True,
    )
    reply = json.loads(result.content[0].text)
    assert reply == {"url": "https://ex.test/a", "text": "page text", "summary": "the summary"}


async def test_fetch_url_is_gated_when_the_provider_cannot_fetch() -> None:
    provider = _FakeSearchProvider(supports_fetch=False)
    result = await _run("fetch_url", provider, url="https://ex.test", user_description="read it")
    assert result.is_error is True
    assert result.content[0].text == research_tools.FETCH_UNSUPPORTED_MESSAGE
    assert provider.fetches == []


async def test_web_tools_fail_loud_without_a_search_provider() -> None:
    with pytest.raises(RuntimeError, match="no search provider is configured"):
        await _run("search_web", None, queries=["x"])
    with pytest.raises(RuntimeError, match="no search provider is configured"):
        await _run("fetch_url", None, url="https://ex.test", user_description="x")


def test_web_prompt_section_renders_into_the_shell() -> None:
    (section,) = research_manifest.manifest().prompt_sections
    assert section.name == "web"
    rendered = render_system_prompt(
        "You are the assistant.", ((section.name, section.body),), model="claude-opus-4-8"
    )
    assert "search_web" in rendered.content
    assert 'never say "scrape" or "crawl"' in rendered.content
    assert "{{" not in rendered.content


def test_registers_the_research_and_deep_research_profiles() -> None:
    assert {RESEARCH_PROFILE.name, DEEP_RESEARCH_PROFILE.name} == {"research", "deep_research"}
    assert RESEARCH_PROFILE.input_model.model_validate({"objective": "size the market"}).objective
    for tool_name in ("search_web", "search_vertical", "fetch_url"):
        assert tool_name in RESEARCH_TOOL_NAMES
    assert "ask_user" not in RESEARCH_TOOL_NAMES
    assert "spawn_subagent" not in RESEARCH_TOOL_NAMES


def test_deep_research_lifts_its_round_budget_above_the_default() -> None:
    assert DEEP_RESEARCH_PROFILE.max_rounds == DEEP_RESEARCH_ROUND_LIMIT == 200
    assert RESEARCH_PROFILE.max_rounds < DEEP_RESEARCH_PROFILE.max_rounds


def test_research_profile_prompt_wraps_with_citation_and_fills_the_skill_index() -> None:
    skills = (("extension-skill", "A turn-specific research workflow."),)
    for profile in (RESEARCH_PROFILE, DEEP_RESEARCH_PROFILE):
        prompt = subagent_system_prompt(profile, skills=skills)
        assert "{{skill_index}}" not in prompt
        assert "<available_skills>" in prompt
        assert "- extension-skill: A turn-specific research workflow." in prompt
        assert "<citation_instructions>" in prompt
        assert "search_vertical" in prompt
        assert "list_skills" not in profile.tool_names


def test_research_skills_parse_and_index() -> None:
    index = dict(skill_registry((research_manifest.manifest(),)).index())
    for name in ("research-assistant", "research-report"):
        assert name in index
