"""The ActiveCampaign connector over a mock transport: the resource-keyed envelope + offset walk
with the watermark advancing over `udate`, the `filters[<field>_after]` incremental param, the
`Api-Token` header built from a direct key, and a refusal surfacing as `StreamSkipped`. The class
base URL is empty (per-tenant), so the tenant host is bound through the config's `base_url`.
Offline — a canned transport, no token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.active_campaign import ActiveCampaignConnector

from ufo.access.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"
BASE_URL = "https://acme.api-us1.com"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=ActiveCampaignConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream, base_url=BASE_URL), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_contacts_offset_walk_hits_the_tenant_host_and_advances_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "acme.api-us1.com"
        assert request.url.path == "/api/3/contacts"
        return httpx.Response(
            200,
            json={
                "contacts": [
                    {
                        "id": "1",
                        "cdate": "2026-01-01T00:00:00Z",
                        "udate": "2026-02-01T00:00:00Z",
                    }
                ],
                "meta": {"total": "1"},
            },
        )

    result = await _fetch("contacts", handle)
    assert _refs(result) == {"contacts/1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-01T00:00:00Z"
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"


async def test_incremental_filter_param_sent_when_a_cursor_is_stored() -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("filters[udate_after]"))
        return httpx.Response(200, json={"contacts": [], "meta": {"total": "0"}})

    await _fetch("contacts", handle, cursor="2026-01-01T00:00:00Z")
    assert seen and seen[0] == "2026-01-01T00:00:00Z"


async def test_full_refresh_stream_has_no_false_update_timestamp() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "segments": [{"id": "1", "cdate": "2026-01-01T00:00:00Z"}],
                "meta": {"total": "1"},
            },
        )

    result = await _fetch("segments", handle)
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None
    stream = next(
        stream for stream in ActiveCampaignConnector.streams_list if stream.name == "segments"
    )
    assert stream.updated_at_field is None


async def test_api_token_header_built_from_a_direct_key() -> None:
    client = ActiveCampaignConnector()._make_client(BASE_URL, Credential(bearer="key-123"))
    assert client.headers["api-token"] == "key-123"
    assert "authorization" not in client.headers


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch("contacts", handle)
