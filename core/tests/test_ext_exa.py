"""The web tool pack: `search_web` and `fetch_url` over Exa, egress-proxied by a BYOK slot.

The extension imports only `selfhost.sdk`; these tests source its Manifest and drive its handlers
the way core does — a real `ToolContext` over a recording carrier that stands in for the container
and answers with canned Exa JSON. They assert the verbatim tool surface, the exact Exa request
bodies the two tools shape, and — through core's REAL rule derivation and proxy header rewrite —
that the slot's declared sentinel is the one the tool emits and the one the proxy swaps for the
stored key. No live Exa key or network is touched: a live key would only change the bytes the proxy
substitutes, exercised here with a stored stand-in secret."""

import json
import shlex
from uuid import uuid4

import selfhost_ext_exa as exa
import sqlalchemy as sa
from cryptography.fernet import Fernet

from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.loop.prompts.render import render_system_prompt
from selfhost.sandbox.proxy.rules import (
    InjectionRule,
    MeterRule,
    ScopeRule,
    derive_credential_rules,
)
from selfhost.sandbox.proxy.server import _inject
from selfhost.sandbox.session import ExecResult, SandboxHandle, SandboxSession
from selfhost.schema import tables
from selfhost.schema.records import Agent, Turn
from selfhost.tools.context import ToolContext

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


class _RecordingCarrier:
    """Records the egress command each web tool ran — so a test reads the exact Exa request the
    tool put on the wire — and answers with a canned Exa response. A stand-in for the container,
    never the thing asserted; `create`/`destroy` stay unreachable."""

    def __init__(self, stdout: str, exit_code: int = 0) -> None:
        self.commands: list[str] = []
        self._stdout = stdout
        self._exit_code = exit_code

    async def create(self, spec: object) -> object:
        raise AssertionError("a web tool reaches the sandbox only through exec")

    async def exec(
        self, handle: object, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        self.commands.append(argv[-1])
        return ExecResult(stdout=self._stdout, stderr="unauthorized", exit_code=self._exit_code)

    async def destroy(self, handle: object) -> None:
        raise AssertionError("a web tool reaches the sandbox only through exec")


def _context(carrier: _RecordingCarrier) -> ToolContext:
    conversation_id = uuid4()
    return ToolContext(
        sandbox=SandboxSession(
            carrier=carrier,
            handle=SandboxHandle(conversation_id=conversation_id, container_id="test"),
        ),
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=conversation_id,
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="research this",
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        memory=None,
        member_id=None,
        artifact_token_secret="",
    )


def _tool(name: str) -> object:
    return next(tool for tool in exa.manifest().tools if tool.name == name)


def _body_of(command: str) -> dict[str, object]:
    argv = shlex.split(command)
    return json.loads(argv[argv.index("--data") + 1])


def _url_of(command: str) -> str:
    return shlex.split(command)[-1]


def test_manifest_declares_search_and_fetch_with_verbatim_descriptions() -> None:
    manifest = exa.manifest()
    assert manifest.name == "exa"
    by_name = {tool.name: tool for tool in manifest.tools}
    assert set(by_name) == {"search_web", "fetch_url", "search_vertical"}
    assert by_name["search_web"].description == SEARCH_WEB_DESCRIPTION
    assert by_name["fetch_url"].description == FETCH_URL_DESCRIPTION
    assert set(exa.SearchWebInput.model_fields) == {
        "queries",
        "recency_filter",
        "allowed_domains",
    }
    assert set(exa.FetchUrlInput.model_fields) == {
        "url",
        "prompt",
        "max_length",
        "force_fetch",
        "user_description",
    }


def test_manifest_contributes_the_web_prompt_section_into_the_rendered_shell() -> None:
    """Both ends of the contribution seam: the web pack declares a prompt section, and the same
    tuple the loop builds from `manifest.prompt_sections` renders into the shell's `{{sections}}`
    slot — so the search rules reach the agent's system prompt without core naming it."""
    (section,) = exa.manifest().prompt_sections
    assert section.name == "web"
    rendered = render_system_prompt("You are the assistant.", ((section.name, section.body),))
    assert "search_web" in rendered.content
    assert 'never say "scrape" or "crawl"' in rendered.content
    assert "{{" not in rendered.content


def test_web_results_are_marked_untrusted() -> None:
    by_name = {tool.name: tool for tool in exa.manifest().tools}
    assert by_name["search_web"].untrusted is True
    assert by_name["fetch_url"].untrusted is True


def test_manifest_declares_the_exa_injection_slot() -> None:
    (slot,) = exa.manifest().credentials
    assert slot.name == "exa_api"
    injection = slot.injection
    assert injection is not None
    assert (injection.host, injection.header, injection.sentinel, injection.dimension) == (
        "api.exa.ai",
        "x-api-key",
        exa.EXA_SENTINEL,
        "requests",
    )


async def test_search_web_posts_one_exa_search_per_query_and_merges_results() -> None:
    carrier = _RecordingCarrier(json.dumps({"results": [{"url": "https://x.test", "title": "X"}]}))
    tool = _tool("search_web")
    args = tool.input_model.model_validate(
        {"queries": ["alpha", "beta"], "recency_filter": "week", "allowed_domains": ["docs.x.test"]}
    )
    result = await tool.handler(_context(carrier), args)
    assert result.is_error is False
    assert len(carrier.commands) == 2
    assert _url_of(carrier.commands[0]) == "https://api.exa.ai/search"
    assert f"x-api-key: {exa.EXA_SENTINEL}" in shlex.split(carrier.commands[0])
    body = _body_of(carrier.commands[0])
    assert body["query"] == "alpha"
    assert body["numResults"] == 5
    assert body["contents"] == {"text": {"maxCharacters": 1000}, "highlights": True}
    assert body["includeDomains"] == ["docs.x.test"]
    assert "startPublishedDate" in body
    assert _body_of(carrier.commands[1])["query"] == "beta"
    merged = json.loads(result.content[0].text)
    assert len(merged["results"]) == 2


async def test_search_web_omits_domain_and_recency_filters_when_unset() -> None:
    carrier = _RecordingCarrier(json.dumps({"results": []}))
    tool = _tool("search_web")
    args = tool.input_model.model_validate({"queries": ["solo"]})
    await tool.handler(_context(carrier), args)
    body = _body_of(carrier.commands[0])
    assert "includeDomains" not in body
    assert "startPublishedDate" not in body


async def test_fetch_url_posts_contents_with_summary_livecrawl_and_clamped_length() -> None:
    canned = json.dumps({"results": [{"url": "u", "text": "page text"}]})
    carrier = _RecordingCarrier(canned)
    tool = _tool("fetch_url")
    args = tool.input_model.model_validate(
        {
            "url": "https://ex.test/a",
            "prompt": "summarize it",
            "max_length": 999_999,
            "force_fetch": True,
            "user_description": "read the page",
        }
    )
    result = await tool.handler(_context(carrier), args)
    assert result.is_error is False
    assert _url_of(carrier.commands[0]) == "https://api.exa.ai/contents"
    assert f"x-api-key: {exa.EXA_SENTINEL}" in shlex.split(carrier.commands[0])
    body = _body_of(carrier.commands[0])
    assert body["urls"] == ["https://ex.test/a"]
    assert body["text"] == {"maxCharacters": 20_000}
    assert body["summary"] == {"query": "summarize it"}
    assert body["livecrawl"] == "always"
    assert result.content[0].text == canned


async def test_fetch_url_defaults_omit_summary_and_livecrawl() -> None:
    carrier = _RecordingCarrier(json.dumps({"results": []}))
    tool = _tool("fetch_url")
    args = tool.input_model.model_validate({"url": "https://ex.test", "user_description": "x"})
    await tool.handler(_context(carrier), args)
    body = _body_of(carrier.commands[0])
    assert body["text"] == {"maxCharacters": 20_000}
    assert "summary" not in body
    assert "livecrawl" not in body


async def test_a_failed_egress_surfaces_as_an_error_result_carrying_the_body() -> None:
    carrier = _RecordingCarrier(json.dumps({"error": "unauthorized"}), exit_code=22)
    tool = _tool("fetch_url")
    args = tool.input_model.model_validate({"url": "https://ex.test", "user_description": "x"})
    result = await tool.handler(_context(carrier), args)
    assert result.is_error is True
    assert "unauthorized" in result.content[0].text


async def test_the_declared_injection_swaps_the_emitted_sentinel_for_the_stored_key(
    db: None,
) -> None:
    """End to end through core's own derivation and proxy rewrite: the slot's declared sentinel is
    exactly the one the tool emits, and the proxy swaps it for the workspace's stored BYOK key —
    proving both ends of the injection line up without a live Exa key."""
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    real_key = "exa-live-secret-0xdeadbeef"
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, "exa_api", real_key)

    rules = await derive_credential_rules((exa.manifest(),), workspace_id, store)
    assert any(isinstance(r, ScopeRule) and "api.exa.ai" in r.allowed_hosts for r in rules)
    assert MeterRule(host="api.exa.ai", dimension="requests") in rules
    candidates = [r for r in rules if isinstance(r, InjectionRule) and r.host == "api.exa.ai"]
    assert candidates

    carrier = _RecordingCarrier(json.dumps({"results": []}))
    tool = _tool("fetch_url")
    args = tool.input_model.model_validate({"url": "https://ex.test", "user_description": "x"})
    await tool.handler(_context(carrier), args)
    header = next(a for a in shlex.split(carrier.commands[0]) if a.startswith("x-api-key: "))
    emitted = header.partition("x-api-key: ")[2]

    upstream = _inject([f"x-api-key: {emitted}\r\n".encode()], candidates)
    assert real_key.encode() in upstream
    assert exa.EXA_SENTINEL.encode() not in upstream
