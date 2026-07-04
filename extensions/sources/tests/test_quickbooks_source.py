"""The QuickBooks connector over a mock transport: the `/query` loop, the `QueryResponse.<Entity>`
envelope unwrap, the nested `Metadata.LastUpdatedTime` cursor lifted to a flat watermark, the
incremental `WHERE` clause, and a refusal as `StreamSkipped`. Offline — a canned transport,
no DB, no token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from selfhost_ext_sources.quickbooks import QuickBooksConnector

from selfhost.connectors import Credential
from selfhost.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from selfhost.sources.sync import SourceAuth, StreamSkipped, SyncResult

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
                            "Metadata": {"LastUpdatedTime": "2026-02-01T00:00:00Z"},
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
    assert "FROM Customer" in seen[0]
    assert "WHERE" not in seen[0]


async def test_incremental_sends_where_clause() -> None:
    seen: list[str] = []
    await _fetch("customers", _handler(seen), cursor="2026-01-01T00:00:00Z")
    assert "WHERE Metadata.LastUpdatedTime > '2026-01-01T00:00:00Z'" in seen[0]


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"fault": "AUTHENTICATION"})

    with pytest.raises(StreamSkipped):
        await _fetch("customers", handle)
