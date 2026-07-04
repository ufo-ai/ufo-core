"""Intercom connector over a mock transport: the search-API POST body + `pages.next.starting_after`
cursor, the scroll API, the `Intercom-Version` header, the integer→string cursor normalization the
adapter needs to advance a watermark, and the `StreamSkipped` a refusal raises. Offline — a canned
transport, no DB, no token, no broker."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from selfhost_ext_sources.intercom import INTERCOM_VERSION, IntercomConnector

from selfhost.connectors import Credential
from selfhost.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from selfhost.sources.sync import SourceAuth, StreamSkipped

ACCOUNT = "acct-1"

CONV1 = {
    "id": "c1",
    "updated_at": 1700000000,
    "title": "Login issue",
    "source": {"type": "conversation", "subject": "Help", "body": "<p>I can't log in</p>"},
}
CONV2 = {
    "id": "c2",
    "updated_at": 1700000100,
    "title": "Billing question",
    "source": {"type": "conversation", "subject": "Invoice", "body": "<p>Charged twice</p>"},
}


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(handler: Callable[[httpx.Request], httpx.Response]) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
):
    return await ConnectorBackend(connector=IntercomConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, _auth(handler)
    )


def _conversations_handler(
    seen_values: list[object],
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.intercom.io"
        assert request.headers.get("Intercom-Version") == INTERCOM_VERSION
        if request.method == "POST" and request.url.path == "/conversations/search":
            body = json.loads(request.content)
            assert body["sort"] == {"field": "updated_at", "order": "ascending"}
            seen_values.append(body["query"]["value"])
            after = body.get("pagination", {}).get("starting_after")
            if after == "sa1":
                return httpx.Response(200, json={"conversations": [CONV2], "pages": {}})
            return httpx.Response(
                200,
                json={"conversations": [CONV1], "pages": {"next": {"starting_after": "sa1"}}},
            )
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_search_paginates_and_normalizes_the_cursor() -> None:
    seen: list[object] = []
    result = await _fetch("conversations", _conversations_handler(seen))

    assert {page.source_ref for page in result.pages} == {"conversations/c1", "conversations/c2"}
    assert result.snapshot is False
    assert result.deletes == ()
    assert result.next_cursor == "1700000100"
    assert seen[0] == 0

    body = next(page.body for page in result.pages if page.source_ref == "conversations/c1")
    assert "Login issue" in body


async def test_incremental_passes_the_cursor_as_a_number() -> None:
    seen: list[object] = []
    await _fetch("conversations", _conversations_handler(seen), cursor="1700000000")
    assert seen[0] == 1700000000


async def test_companies_scroll_walks_scroll_param() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/companies/scroll":
            if request.url.params.get("scroll_param") == "s1":
                return httpx.Response(200, json={"data": []})
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "co1", "updated_at": 1700000000, "name": "Acme"}],
                    "scroll_param": "s1",
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("companies", handle)
    assert {page.source_ref for page in result.pages} == {"companies/co1"}
    assert result.next_cursor == "1700000000"
    assert result.snapshot is False


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errors": [{"code": "forbidden"}]})

    with pytest.raises(StreamSkipped):
        await _fetch("conversations", handle)
