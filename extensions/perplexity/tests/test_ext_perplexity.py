import json
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_perplexity as perplexity
from cryptography.fernet import Fernet

from ufo.db import workspace_tx
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.ext.context import context_for
from ufo.runtime.workspace import init_workspace_credentials, ws
from ufo.schema import tables
from ufo.sdk.search import FetchRequest, SearchQuery

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

PERPLEXITY_KEY = "pplx-live-secret-0xdeadbeef"


class _Recorder:
    def __init__(self, responses: list[tuple[object, int]]) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = responses

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        payload, status = self._responses[len(self.requests) - 1]
        return httpx.Response(status, json=payload)


async def _keyed_provider(
    recorder: _Recorder,
) -> tuple[perplexity.PerplexitySearchProvider, UUID]:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    await store.put(workspace_id, perplexity.PERPLEXITY_SLOT, PERPLEXITY_KEY)
    credentials = context_for(perplexity.NAME, frozenset({perplexity.PERPLEXITY_SLOT})).credentials
    provider = perplexity.PerplexitySearchProvider(
        credentials=credentials,
        transport=httpx.MockTransport(recorder.handle),
    )
    return provider, workspace_id


def _body(request: httpx.Request) -> dict[str, object]:
    return json.loads(request.content)


def _search_response() -> object:
    return {
        "results": [
            {
                "url": "https://x.test/page",
                "title": "Page",
                "snippet": "page text",
                "date": "2026-08-01",
                "last_updated": "2026-08-09",
            }
        ],
        "id": "search-id",
        "server_time": None,
    }


def test_manifest_declares_host_side_slot_and_perplexity_search_backend() -> None:
    manifest = perplexity.manifest()
    assert manifest.name == "perplexity"
    (slot,) = manifest.credentials
    assert slot.name == "perplexity_api_key"
    assert slot.injection is None
    (spec,) = manifest.search_providers
    assert spec.backend == "perplexity"
    assert not manifest.tools
    assert not manifest.subagents
    assert not manifest.prompt_sections
    assert spec.build(None).supports_fetch is True


async def test_search_reads_platform_perplexity_api_key(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    init_workspace_credentials(None)
    monkeypatch.setenv("PERPLEXITY_API_KEY", PERPLEXITY_KEY)
    recorder = _Recorder([({"results": []}, 200)])
    provider = perplexity.PerplexitySearchProvider(
        credentials=context_for(
            perplexity.NAME, frozenset({perplexity.PERPLEXITY_SLOT})
        ).credentials,
        transport=httpx.MockTransport(recorder.handle),
    )

    with ws(workspace_id):
        await provider.search(SearchQuery(query="alpha", num_results=5))

    assert recorder.requests[-1].headers["authorization"] == f"Bearer {PERPLEXITY_KEY}"


async def test_search_caps_result_count_to_provider_limit(db: None) -> None:
    recorder = _Recorder([({"results": []}, 200)])
    provider, workspace_id = await _keyed_provider(recorder)
    with ws(workspace_id):
        await provider.search(SearchQuery(query="broad question", num_results=25))

    assert _body(recorder.requests[-1]) == {
        "query": "broad question",
        "max_results": 20,
        "max_tokens": 10_000,
        "max_tokens_per_page": 1_000,
    }


async def test_fetch_searches_the_domain_and_returns_only_the_exact_url(db: None) -> None:
    recorder = _Recorder([(_search_response(), 200)])
    provider, workspace_id = await _keyed_provider(recorder)
    with ws(workspace_id):
        page = await provider.fetch(
            FetchRequest(
                url="https://x.test/page",
                prompt="What changed?",
                max_chars=4,
            )
        )

    assert _body(recorder.requests[-1]) == {
        "query": "https://x.test/page\nWhat changed?",
        "search_domain_filter": ["x.test"],
        "max_results": 10,
        "max_tokens": 1,
        "max_tokens_per_page": 1,
    }
    assert (page.url, page.text, page.summary) == (
        "https://x.test/page",
        "page",
        "page",
    )


async def test_fetch_without_prompt_returns_no_summary(db: None) -> None:
    recorder = _Recorder([(_search_response(), 200)])
    provider, workspace_id = await _keyed_provider(recorder)
    with ws(workspace_id):
        page = await provider.fetch(FetchRequest(url="https://x.test/page"))

    body = _body(recorder.requests[-1])
    assert body["query"] == "https://x.test/page"
    assert body["max_tokens"] == 5_000
    assert page.summary is None


async def test_fetch_rejects_a_different_search_result(db: None) -> None:
    recorder = _Recorder(
        [
            (
                {"results": [{"url": "https://x.test/other", "title": "Other", "snippet": "text"}]},
                200,
            )
        ]
    )
    provider, workspace_id = await _keyed_provider(recorder)
    with ws(workspace_id), pytest.raises(perplexity.PerplexityError, match="requested URL"):
        await provider.fetch(FetchRequest(url="https://x.test/page"))


async def test_non_2xx_status_raises_with_bounded_body(db: None) -> None:
    recorder = _Recorder([({"message": "unauthorized"}, 401)])
    provider, workspace_id = await _keyed_provider(recorder)
    with ws(workspace_id), pytest.raises(perplexity.PerplexityError, match="unauthorized"):
        await provider.search(SearchQuery(query="alpha", num_results=5))


async def test_invalid_response_shape_fails_loud(db: None) -> None:
    recorder = _Recorder([({"results": "wrong"}, 200)])
    provider, workspace_id = await _keyed_provider(recorder)
    with ws(workspace_id), pytest.raises(perplexity.PerplexityError, match="invalid response"):
        await provider.search(SearchQuery(query="alpha", num_results=5))


async def test_payload_limits_fail_before_request(db: None) -> None:
    recorder = _Recorder([])
    provider, workspace_id = await _keyed_provider(recorder)
    with ws(workspace_id), pytest.raises(perplexity.PerplexityError, match="2000 characters"):
        await provider.search(SearchQuery(query="x" * 2_001, num_results=5))
    with ws(workspace_id), pytest.raises(perplexity.PerplexityError, match="at least 1"):
        await provider.search(SearchQuery(query="alpha", num_results=0))
    with ws(workspace_id), pytest.raises(perplexity.PerplexityError, match="prompt"):
        await provider.fetch(FetchRequest(url="https://x.test", prompt="x" * 2_001))
    with ws(workspace_id), pytest.raises(perplexity.PerplexityError, match="non-negative"):
        await provider.fetch(FetchRequest(url="https://x.test", max_chars=-1))
    with ws(workspace_id), pytest.raises(perplexity.PerplexityError, match="absolute HTTP URL"):
        await provider.fetch(FetchRequest(url="not-a-url"))
    with ws(workspace_id), pytest.raises(perplexity.PerplexityError, match="absolute HTTP URL"):
        await provider.fetch(FetchRequest(url="ftp://x.test/page"))
    assert not recorder.requests
