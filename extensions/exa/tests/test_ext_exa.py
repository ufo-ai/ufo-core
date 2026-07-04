"""The Exa search backend's proof: `ExaSearchProvider` runs host-side, reads the BYOK key through a
scoped `CredentialAccess`, shapes the Exa request bodies, and maps the responses into the seam's
`SearchResults`/`FetchedPage`.

Each test drives the provider over an `httpx.MockTransport` that records the request it emits and
answers with canned Exa JSON — no live Exa key or network — and reads the key through the REAL
credential store, so the host-side key read is exercised end to end (the key lands in the emitted
`x-api-key` header, never in a sandbox). The bodies are the verbatim shapes the backend puts on the
wire; the mapping is what the model ultimately sees."""

import json
from uuid import uuid4

import httpx
import pytest
import selfhost_ext_exa as exa
import sqlalchemy as sa
from cryptography.fernet import Fernet

from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.context import context_for
from selfhost.schema import tables
from selfhost.sdk.search import FetchRequest, SearchQuery

EXA_KEY = "exa-live-secret-0xdeadbeef"


class _Recorder:
    """Records each request the backend emits and answers with a canned Exa body — the stand-in for
    the Exa API, never the thing asserted."""

    def __init__(self, payload: object, status: int = 200) -> None:
        self.requests: list[httpx.Request] = []
        self._payload = payload
        self._status = status

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self._status, json=self._payload)


async def _keyed_provider(recorder: _Recorder) -> exa.ExaSearchProvider:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, exa.EXA_SLOT, EXA_KEY)
    credentials = context_for(workspace_id, exa.NAME, frozenset({exa.EXA_SLOT}), store).credentials
    return exa.ExaSearchProvider(
        credentials=credentials, transport=httpx.MockTransport(recorder.handle)
    )


def _body(recorder: _Recorder) -> dict[str, object]:
    return json.loads(recorder.requests[-1].content)


def test_manifest_declares_a_host_side_slot_and_the_exa_search_backend() -> None:
    manifest = exa.manifest()
    assert manifest.name == "exa"
    (slot,) = manifest.credentials
    assert slot.name == "exa_api"
    assert slot.injection is None
    (spec,) = manifest.search_providers
    assert spec.backend == "exa"
    assert not manifest.tools
    assert not manifest.subagents
    assert not manifest.prompt_sections
    assert spec.build(None).supports_fetch is True


async def test_search_posts_one_exa_search_body_and_maps_the_results(db: None) -> None:
    recorder = _Recorder(
        {
            "results": [
                {
                    "url": "https://x.test",
                    "title": "X",
                    "text": "snippet",
                    "publishedDate": "2024-01-01",
                    "highlights": ["h1", "h2"],
                }
            ]
        }
    )
    provider = await _keyed_provider(recorder)
    results = await provider.search(
        SearchQuery(query="alpha", num_results=5, recency="week", allowed_domains=("docs.x.test",))
    )
    request = recorder.requests[-1]
    assert request.url.path == "/search"
    assert request.headers["x-api-key"] == EXA_KEY
    body = _body(recorder)
    assert body["query"] == "alpha"
    assert body["numResults"] == 5
    assert body["contents"] == {"text": {"maxCharacters": 1000}, "highlights": True}
    assert body["includeDomains"] == ["docs.x.test"]
    assert "startPublishedDate" in body

    hit = results.hits[0]
    assert (hit.url, hit.title, hit.text, hit.published_date) == (
        "https://x.test",
        "X",
        "snippet",
        "2024-01-01",
    )
    assert hit.highlights == ("h1", "h2")
    assert results.answer is None


async def test_search_omits_domain_and_recency_filters_when_unset(db: None) -> None:
    recorder = _Recorder({"results": []})
    provider = await _keyed_provider(recorder)
    await provider.search(SearchQuery(query="solo", num_results=5))
    body = _body(recorder)
    assert "includeDomains" not in body
    assert "startPublishedDate" not in body


async def test_vertical_search_carries_its_category_and_narrower_snippet(db: None) -> None:
    recorder = _Recorder({"results": [{"title": "paper"}]})
    provider = await _keyed_provider(recorder)
    await provider.search(
        SearchQuery(query="graph transformers", num_results=5, vertical="academic")
    )
    body = _body(recorder)
    assert body["category"] == "research paper"
    assert body["contents"] == {"text": {"maxCharacters": 500}}


async def test_vertical_search_without_a_mapped_category_sends_none(db: None) -> None:
    recorder = _Recorder({"results": []})
    provider = await _keyed_provider(recorder)
    await provider.search(SearchQuery(query="how to knit", num_results=5, vertical="video"))
    body = _body(recorder)
    assert "category" not in body


async def test_fetch_posts_contents_with_summary_livecrawl_and_clamped_length(db: None) -> None:
    recorder = _Recorder(
        {"results": [{"url": "https://ex.test/a", "text": "page text", "summary": "the summary"}]}
    )
    provider = await _keyed_provider(recorder)
    page = await provider.fetch(
        FetchRequest(url="https://ex.test/a", prompt="summarize it", max_chars=999_999, force=True)
    )
    request = recorder.requests[-1]
    assert request.url.path == "/contents"
    assert request.headers["x-api-key"] == EXA_KEY
    body = _body(recorder)
    assert body["urls"] == ["https://ex.test/a"]
    assert body["text"] == {"maxCharacters": 20_000}
    assert body["summary"] == {"query": "summarize it"}
    assert body["livecrawl"] == "always"
    assert (page.url, page.text, page.summary) == ("https://ex.test/a", "page text", "the summary")


async def test_fetch_defaults_omit_summary_and_livecrawl(db: None) -> None:
    recorder = _Recorder({"results": [{"url": "u", "text": "t"}]})
    provider = await _keyed_provider(recorder)
    page = await provider.fetch(FetchRequest(url="https://ex.test"))
    body = _body(recorder)
    assert body["text"] == {"maxCharacters": 20_000}
    assert "summary" not in body
    assert "livecrawl" not in body
    assert page.summary is None


async def test_a_non_2xx_status_raises_carrying_the_body(db: None) -> None:
    recorder = _Recorder({"error": "unauthorized"}, status=401)
    provider = await _keyed_provider(recorder)
    with pytest.raises(exa.ExaError, match="unauthorized"):
        await provider.fetch(FetchRequest(url="https://ex.test"))
