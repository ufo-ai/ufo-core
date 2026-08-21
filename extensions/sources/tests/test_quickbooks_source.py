"""The QuickBooks connector over a mock transport: the company the row's address names in every
request path, the `/query` loop, the `QueryResponse.<Entity>` envelope unwrap, the nested
`MetaData.LastUpdatedTime` cursor lifted to a flat watermark, the incremental `WHERE` clause, and a
refusal as `StreamSkipped`. Offline — a canned transport, no DB, no token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.quickbooks import QuickBooksConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"
REALM = "9130347596"
COMPANY_PATH = f"/v3/company/{REALM}"
COMPANY_BASE_URL = f"https://quickbooks.api.intuit.com{COMPANY_PATH}"


class _MockProxy:
    """The broker's answer for one connected account: a proxying transport, holding no token."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    cursor: str | None = None,
    base_url: str = COMPANY_BASE_URL,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=QuickBooksConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream, base_url=base_url), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


def _handler(seen: list[str]) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"{COMPANY_PATH}/query"
        seen.append(request.url.params.get("query") or "")
        return httpx.Response(
            200,
            json={
                "QueryResponse": {
                    "Customer": [
                        {
                            "Id": "1",
                            "DisplayName": "Acme",
                            "MetaData": {
                                "CreateTime": "2026-01-01T00:00:00Z",
                                "LastUpdatedTime": "2026-02-01T00:00:00Z",
                            },
                        }
                    ]
                }
            },
        )

    return handle


async def test_query_unwrap_and_nested_cursor_lift() -> None:
    seen: list[str] = []
    result = await _fetch("customers", _handler(seen))
    assert _refs(result) == {"customers/1"}
    assert result.snapshot is False
    assert result.deletes == ()
    assert result.next_cursor == "2026-02-01T00:00:00Z"
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"
    assert "FROM Customer" in seen[0]
    assert "WHERE" not in seen[0]


async def test_incremental_sends_where_clause() -> None:
    seen: list[str] = []
    await _fetch("customers", _handler(seen), cursor="2026-01-01T00:00:00Z")
    assert "WHERE MetaData.LastUpdatedTime > '2026-01-01T00:00:00Z'" in seen[0]


async def test_full_refresh_reference_entity_preserves_metadata_timestamps() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "QueryResponse": {
                    "PaymentMethod": [
                        {
                            "Id": "1",
                            "MetaData": {
                                "CreateTime": "2026-01-01T00:00:00Z",
                                "LastUpdatedTime": "2026-02-01T00:00:00Z",
                            },
                        }
                    ]
                }
            },
        )

    result = await _fetch("payment_methods", handle)
    assert result.next_cursor is None
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"fault": "AUTHENTICATION"})

    with pytest.raises(StreamSkipped):
        await _fetch("customers", handle)


async def test_query_addresses_the_company_the_row_names() -> None:
    """QBO answers a query only under the company file it is addressed to, and the whole address
    including that company is what the row carries — a realm-less `/v3/company/query` names no
    company and can never return a record."""
    paths: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"QueryResponse": {}})

    await _fetch("customers", handle)
    assert paths == [f"{COMPANY_PATH}/query"]


async def test_a_row_naming_no_company_fails_before_any_request() -> None:
    """The connector declares no host of its own, so a row without the company address fails loud
    rather than dial one: no request it could make names a company."""
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"QueryResponse": {}})

    with pytest.raises(RuntimeError, match="resolved no base_url"):
        await _fetch("customers", handle, base_url="")
    assert requests == []
