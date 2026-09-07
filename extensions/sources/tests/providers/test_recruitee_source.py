"""The Recruitee connector over a mock transport: the page-number loop, the stream-named envelope
(`{"candidates": [...]}`), and a refusal surfacing as `StreamSkipped`. The class base URL is empty
(per-tenant), so the test binds the tenant URL through `SourceAuth.base_url` — the real
per-tenant path. Offline — a canned transport, no token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.recruitee import PAGE_SIZE, RecruiteeConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

BASE_URL = "https://api.recruitee.com/c/acme"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(stream: str, handler: Callable[[httpx.Request], httpx.Response]) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler), base_url=BASE_URL)
    return await ConnectorBackend(connector=RecruiteeConnector()).fetch(
        ConnectorSourceConfig(stream=stream), None, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_candidates_paginate_the_named_envelope() -> None:
    seen_pages: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.recruitee.com"
        assert request.url.path == "/c/acme/candidates"
        assert request.url.params.get("limit") == str(PAGE_SIZE)
        page = request.url.params["page"]
        seen_pages.append(page)
        if page == "1":
            candidates = [{"id": candidate_id} for candidate_id in range(1, PAGE_SIZE + 1)]
        else:
            assert page == "2"
            candidates = [{"id": PAGE_SIZE + 1}]
        return httpx.Response(200, json={"candidates": candidates})

    result = await _fetch("candidates", handle)
    assert seen_pages == ["1", "2"]
    assert _refs(result) == {
        f"candidates/{candidate_id}" for candidate_id in range(1, PAGE_SIZE + 2)
    }
    assert result.snapshot is False
    assert result.next_cursor is None


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(StreamSkipped):
        await _fetch("candidates", handle)
