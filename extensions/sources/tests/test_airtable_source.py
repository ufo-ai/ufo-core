"""The Airtable connector over a mock transport: the metadata walk (`/meta/bases` →
`/meta/bases/{id}/tables`), the record fan-out per table paged by the body's `offset` token, and the
default titled-JSON render. Offline — a canned transport, no DB, no token, no broker."""

import json
from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.airtable import AirtableConnector

from ufo.access.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, SyncResult

ACCOUNT = "acct-1"


def _flat(result: SyncResult, ref: str) -> dict:
    body = next(page.body for page in result.pages if page.source_ref == ref)
    return json.loads(body.split("\n\n", 1)[1])


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=AirtableConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _base_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.airtable.com"
        if request.url.path == "/v0/meta/bases":
            return httpx.Response(200, json={"bases": [{"id": "b1", "name": "Product"}]})
        if request.url.path == "/v0/meta/bases/b1/tables":
            return httpx.Response(200, json={"tables": [{"id": "t1", "name": "Tasks"}]})
        if request.url.path == "/v0/b1/t1":
            if request.url.params.get("offset") == "o2":
                return httpx.Response(200, json={"records": [{"id": "r2"}]})
            return httpx.Response(200, json={"records": [{"id": "r1"}], "offset": "o2"})
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_bases_render_titled_json() -> None:
    result = await _fetch("bases", _base_handler())
    assert {page.source_ref for page in result.pages} == {"bases/b1"}
    assert result.snapshot is False
    assert "Product" in result.pages[0].body


async def test_tables_fan_out_per_base() -> None:
    result = await _fetch("tables", _base_handler())
    assert {page.source_ref for page in result.pages} == {"tables/t1"}


async def test_records_follow_the_offset_token_across_pages() -> None:
    result = await _fetch("records", _base_handler())
    assert {page.source_ref for page in result.pages} == {"records/r1", "records/r2"}


async def test_bases_and_tables_flatten_derive_name_and_api_url() -> None:
    base = _flat(await _fetch("bases", _base_handler()), "bases/b1")
    assert base["name"] == "Product"
    assert base["api_url"] == "https://api.airtable.com/v0/meta/bases/b1"

    table = _flat(await _fetch("tables", _base_handler()), "tables/t1")
    assert table["api_url"] == "https://api.airtable.com/v0/b1/t1"


async def test_records_flatten_lifts_created_at_and_fields() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v0/meta/bases":
            return httpx.Response(200, json={"bases": [{"id": "b1", "name": "Product"}]})
        if request.url.path == "/v0/meta/bases/b1/tables":
            return httpx.Response(200, json={"tables": [{"id": "t1", "name": "Tasks"}]})
        if request.url.path == "/v0/b1/t1":
            return httpx.Response(
                200,
                json={
                    "records": [
                        {
                            "id": "r1",
                            "createdTime": "2026-01-02T00:00:00Z",
                            "fields": {"Name": "Do"},
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    record = _flat(await _fetch("records", handle), "records/r1")
    assert record["created_at"] == "2026-01-02T00:00:00Z"
    assert record["fields"] == {"Name": "Do"}


async def test_a_forbidden_base_listing_fails_the_run() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "NOT_AUTHORIZED"})

    with pytest.raises(httpx.HTTPStatusError):
        await _fetch("bases", handle)
