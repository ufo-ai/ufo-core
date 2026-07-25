"""The QuickBooks connector over a mock transport: the `/query` loop, the `QueryResponse.<Entity>`
envelope unwrap, the nested `MetaData.LastUpdatedTime` cursor lifted to a flat watermark, the
incremental `WHERE` clause, and a refusal as `StreamSkipped`. Offline — a canned transport,
no DB, no token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.quickbooks import QuickBooksConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=QuickBooksConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


def _handler(seen: list[str]) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/query")
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
