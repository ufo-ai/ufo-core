"""The Chargebee connector over a mock transport: the `{list: [{<resource>: {...}}], next_offset}`
walk with `flatten` lifting the per-record envelope (so `id`/`updated_at` sit at the top level and
the watermark advances), the `<field>[after]` incremental param, the HTTP Basic auth built from a
direct key (empty password), and a refusal surfacing as `StreamSkipped`. The class base URL is empty
(per-tenant), so the tenant host is bound through `ConnectorSourceConfig.base_url`. Offline — a
canned transport, no token."""

import base64
from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.chargebee import ChargebeeConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"
BASE_URL = "https://acme.chargebee.com/api/v2"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=ChargebeeConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream, base_url=BASE_URL), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_customers_lift_envelope_and_advance_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "acme.chargebee.com"
        assert request.url.path == "/api/v2/customers"
        return httpx.Response(
            200,
            json={
                "list": [{"customer": {"id": "c1", "updated_at": "2026-02-01T00:00:00Z"}}],
                "next_offset": None,
            },
        )

    result = await _fetch("customer", handle)
    assert _refs(result) == {"customer/c1"}
    assert result.next_cursor == "2026-02-01T00:00:00Z"
    body = result.pages[0].body
    assert "c1" in body


async def test_incremental_after_param_sent_when_a_cursor_is_stored() -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("updated_at[after]"))
        return httpx.Response(200, json={"list": [], "next_offset": None})

    await _fetch("customer", handle, cursor="2026-01-01T00:00:00Z")
    assert seen and seen[0] == "2026-01-01T00:00:00Z"


async def test_basic_auth_built_from_a_direct_key() -> None:
    client = ChargebeeConnector()._make_client(BASE_URL, Credential(bearer="key-123"))
    authed = next(client.auth.auth_flow(httpx.Request("GET", BASE_URL)))
    expected = "Basic " + base64.b64encode(b"key-123:").decode()
    assert authed.headers["Authorization"] == expected


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch("customer", handle)
