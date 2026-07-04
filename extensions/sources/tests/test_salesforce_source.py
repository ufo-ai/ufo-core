"""The Salesforce connector over a mock transport: the SObject describe feeding the SOQL `SELECT`,
the `nextRecordsUrl` walk, the `attributes` envelope dropped by `flatten`, the `/deleted/` window
producing delete tombstones with the window-end cursor, and a refusal surfacing as `StreamSkipped`.
The class base URL is empty (per-instance), so the test binds an instance URL through a subclass.
Offline — a canned transport, no DB, no token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from selfhost_ext_sources.salesforce import SalesforceConnector

from selfhost.connectors import Credential
from selfhost.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from selfhost.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"


class _Salesforce(SalesforceConnector):
    base_url = "https://acme.my.salesforce.com"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=_Salesforce()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


def _handler(seen: list[str]) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/sobjects/Account/describe"):
            return httpx.Response(
                200, json={"fields": [{"name": "Id"}, {"name": "Name"}, {"name": "SystemModstamp"}]}
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
    assert "SELECT Id, Name, SystemModstamp FROM Account" in seen[0]
    body = next(page.body for page in result.pages if page.source_ref == "accounts/a1")
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
