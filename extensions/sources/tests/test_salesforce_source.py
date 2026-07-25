"""The Salesforce connector over a mock transport: the SObject describe feeding the SOQL `SELECT`,
the `nextRecordsUrl` walk, the `attributes` envelope dropped by `flatten`, the `/deleted/` window
producing delete tombstones with the window-end cursor, and a refusal surfacing as `StreamSkipped`.
The class base URL is empty (per-instance), so the test binds the instance URL through
`ConnectorSourceConfig.base_url` — the real per-tenant path. Offline — a canned transport, no DB,
no token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.salesforce import SalesforceConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"
BASE_URL = "https://acme.my.salesforce.com"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=SalesforceConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream, base_url=BASE_URL), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


def _handler(seen: list[str]) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "acme.my.salesforce.com"
        path = request.url.path
        if path.endswith("/sobjects/Account/describe"):
            return httpx.Response(
                200,
                json={
                    "fields": [
                        {"name": "Id"},
                        {"name": "Name"},
                        {"name": "CreatedDate"},
                        {"name": "SystemModstamp"},
                    ]
                },
            )
        if path.endswith("/query"):
            seen.append(request.url.params.get("q") or "")
            return httpx.Response(
                200,
                json={
                    "done": True,
                    "records": [
                        {
                            "attributes": {"type": "Account"},
                            "Id": "a1",
                            "Name": "Acme",
                            "CreatedDate": "2026-01-01T00:00:00Z",
                            "SystemModstamp": "2026-02-01T00:00:00Z",
                        }
                    ],
                },
            )
        if path.endswith("/sobjects/Account/deleted/"):
            return httpx.Response(
                200,
                json={
                    "deletedRecords": [{"id": "a9"}],
                    "latestDateCovered": "2026-03-01T00:00:00Z",
                },
            )
        return httpx.Response(404, json={"path": path})

    return handle


async def test_query_flattens_attributes_and_advances_watermark() -> None:
    seen: list[str] = []
    result = await _fetch("accounts", _handler(seen))
    assert _refs(result) == {"accounts/a1"}
    assert result.snapshot is False
    assert result.deletes == ()
    assert result.next_cursor == "2026-02-01T00:00:00Z"
    assert "SELECT Id, Name, CreatedDate, SystemModstamp FROM Account" in seen[0]
    page = next(page for page in result.pages if page.source_ref == "accounts/a1")
    assert page.created_at == "2026-01-01T00:00:00.000000+00:00"
    assert page.updated_at == "2026-02-01T00:00:00.000000+00:00"
    body = page.body
    assert "attributes" not in body
    assert "Acme" in body


async def test_deleted_window_tombstones_and_carries_end_cursor() -> None:
    result = await _fetch("accounts", _handler([]), cursor="2026-01-01T00:00:00Z")
    assert result.deletes == ("accounts/a9",)
    assert result.next_cursor == "2026-03-01T00:00:00Z"


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "INVALID_SESSION_ID"})

    with pytest.raises(StreamSkipped):
        await _fetch("accounts", handle)
