"""The Freshdesk connector over a mock transport: the page-number tickets loop (with `updated_since`
and the watermark advancing over `updated_at`) hitting the per-tenant host, the RFC 5988 link-header
default walk, the HTTP Basic auth built from a direct key (password `"X"`), and a refusal surfacing
as `StreamSkipped`. The class base URL is empty (per-tenant), so the tenant host is bound through
`ConnectorSourceConfig.base_url`. Offline — a canned transport, no token."""

import base64
from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.freshdesk import FreshdeskConnector

from ufo.access.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"
BASE_URL = "https://acme.freshdesk.com"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=FreshdeskConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream, base_url=BASE_URL), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_tickets_page_number_walk_hits_the_tenant_host_and_advances_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "acme.freshdesk.com"
        assert request.url.path == "/api/v2/tickets"
        assert request.url.params.get("page") == "1"
        return httpx.Response(200, json=[{"id": 1, "updated_at": "2026-02-01T00:00:00Z"}])

    result = await _fetch("tickets", handle)
    assert _refs(result) == {"tickets/1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-01T00:00:00Z"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"


async def test_groups_follow_the_link_header_default() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/groups"
        return httpx.Response(200, json=[{"id": 2, "name": "Support"}])

    result = await _fetch("groups", handle)
    assert _refs(result) == {"groups/2"}


async def test_basic_auth_built_from_a_direct_key() -> None:
    client = FreshdeskConnector()._make_client(BASE_URL, Credential(bearer="key-123"))
    authed = next(client.auth.auth_flow(httpx.Request("GET", BASE_URL)))
    expected = "Basic " + base64.b64encode(b"key-123:X").decode()
    assert authed.headers["Authorization"] == expected


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch("tickets", handle)
