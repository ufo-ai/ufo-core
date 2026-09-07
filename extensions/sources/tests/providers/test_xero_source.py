"""The Xero connector over a mock transport: the tenant resolved from the grant's connections, the
resource-plural envelope, page-number pagination, the typed `*ID` lifted to `id` by `flatten`, the
single-shot non-paged collections, the `If-Modified-Since` incremental header, the
multi-organisation fault, and a refusal as `StreamSkipped`. Offline — a canned transport, no DB, no
token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.xero import XeroConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamFault, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

TENANT = "11111111-2222-3333-4444-555555555555"


def _connections(*tenants: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json=list(tenants))


def _one_organisation() -> dict[str, object]:
    return {"id": "conn-1", "tenantId": TENANT, "tenantType": "ORGANISATION"}


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=XeroConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_the_tenant_header_is_resolved_from_the_grants_connections() -> None:
    seen: list[tuple[str, str | None]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("xero-tenant-id")))
        if request.url.path == "/connections":
            return _connections(_one_organisation())
        return httpx.Response(200, json={"Accounts": [{"AccountID": "A1"}]})

    result = await _fetch("accounts", handle)
    assert seen[0][0] == "https://api.xero.com/connections"
    assert seen[1][1] == TENANT
    assert _refs(result) == {"accounts/A1"}


async def test_a_grant_naming_several_organisations_is_a_fault_rather_than_a_guess() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return _connections(
            _one_organisation(),
            {"id": "conn-2", "tenantId": "other-tenant", "tenantType": "ORGANISATION"},
        )

    with pytest.raises(StreamFault, match="2 organisations"):
        await _fetch("accounts", handle)


async def test_a_connector_built_with_a_tenant_reads_no_connections() -> None:
    paths: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        assert request.headers.get("xero-tenant-id") == "pinned-tenant"
        return httpx.Response(200, json={"Accounts": [{"AccountID": "A1"}]})

    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handle))
    result = await ConnectorBackend(connector=XeroConnector(tenant_id="pinned-tenant")).fetch(
        ConnectorSourceConfig(stream="accounts"), None, auth
    )
    assert "/connections" not in paths
    assert _refs(result) == {"accounts/A1"}


async def test_accounts_lift_typed_id_and_advance_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/connections":
            return _connections(_one_organisation())
        assert request.url.path.endswith("/Accounts")
        assert request.url.params.get("page") == "1"
        return httpx.Response(
            200,
            json={
                "Accounts": [
                    {"AccountID": "A1", "Name": "Cash", "UpdatedDateUTC": "2026-02-01T00:00:00Z"}
                ]
            },
        )

    result = await _fetch("accounts", handle)
    assert _refs(result) == {"accounts/A1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-01T00:00:00Z"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"


async def test_accounts_incremental_sends_if_modified_since() -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/connections":
            return _connections(_one_organisation())
        seen.append(request.headers.get("If-Modified-Since"))
        return httpx.Response(200, json={"Accounts": [{"AccountID": "A1"}]})

    await _fetch("accounts", handle, cursor="2026-01-01T00:00:00Z")
    assert seen and seen[0] is not None and "GMT" in seen[0]


async def test_currencies_single_shot_no_page_param() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/connections":
            return _connections(_one_organisation())
        assert request.url.path.endswith("/Currencies")
        assert "page" not in request.url.params
        return httpx.Response(200, json={"Currencies": [{"Code": "USD"}]})

    result = await _fetch("currencies", handle)
    assert _refs(result) == {"currencies/USD"}


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"Message": "forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch("accounts", handle)
