"""The Airtable connector over a mock transport: the base and table catalog each read by the row
that owns it, the tables and records fanned out over their parent's landed pages, the record walk
paged by the body's `offset` token, and the default titled-JSON render. A record id is unique across
the account, so a record page keeps the address it had before the tree — `records/<id>`, no scope.
Offline — a canned transport, no DB, no token, no broker."""

import json
from collections.abc import Callable, Mapping
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.airtable import AirtableConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
    syncing_streams,
)

Landed = Mapping[str, tuple[ParentRecord, ...]]
ParentsReader = Callable[[Landed], ParentPages]
ACCOUNT = "acct-1"
LANDED: Landed = {
    "bases": (ParentRecord(ref="bases/b1", fields={"id": "b1", "name": "Product"}),),
    "tables": (
        ParentRecord(ref="tables/b1/t1", fields={"base_id": "b1", "id": "t1", "name": "Tasks"}),
    ),
}
RECORD_DIGEST = "sha256:5ca33dafcc0afdeea420b1c0b56962f27eee2eb16d3444bebf3a1eda6b8fe650"


def _flat(result: SyncResult, ref: str) -> dict:
    body = next(page.body for page in result.pages if page.source_ref == ref)
    return json.loads(body.split("\n\n", 1)[1])


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    reader: ParentsReader,
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    cursor: str | None = None,
    landed: Landed = LANDED,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler), parents=reader(landed))
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


async def test_bases_render_titled_json(parents_reader: ParentsReader) -> None:
    result = await _fetch(parents_reader, "bases", _base_handler())
    assert {page.source_ref for page in result.pages} == {"bases/b1"}
    assert result.snapshot is False
    assert "Product" in result.pages[0].body


async def test_tables_fan_out_over_the_bases_that_landed(parents_reader: ParentsReader) -> None:
    calls: list[str] = []
    handler = _base_handler()

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return handler(request)

    result = await _fetch(parents_reader, "tables", handle)

    assert calls == ["/v0/meta/bases/b1/tables"]
    assert {page.source_ref for page in result.pages} == {"tables/b1/t1"}


async def test_records_follow_the_offset_token_and_keep_their_bare_address(
    parents_reader: ParentsReader,
) -> None:
    """An Airtable record id is unique across the account, so `records` declares
    `key_scope="global"` and a record page is addressed `records/<id>` exactly as it was before the
    tree — the base and table still key the partition's cursor and compose its path, they just do
    not enter the address. The base and table listings are gone from the walk: the two catalog rows
    own them."""
    calls: list[str] = []
    handler = _base_handler()

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return handler(request)

    result = await _fetch(parents_reader, "records", handle)

    assert calls == ["/v0/b1/t1", "/v0/b1/t1"]
    assert {page.source_ref for page in result.pages} == {"records/r1", "records/r2"}
    assert {page.source_identity for page in result.pages} == {"records/r1", "records/r2"}


async def test_a_record_carries_the_base_and_table_it_was_read_from(
    parents_reader: ParentsReader,
) -> None:
    record = _flat(await _fetch(parents_reader, "records", _base_handler()), "records/r1")
    assert record["base_id"] == "b1"
    assert record["table_id"] == "t1"
    assert record["table_name"] == "Tasks"


async def test_a_record_page_renders_what_it_rendered_before_the_tree(
    parents_reader: ParentsReader,
) -> None:
    """A record's body names the base and table it sits in, all three riding the edge, none being a
    value the page's address carries. The digest pins the rendered body, so a field that stops
    reaching a record fails here rather than re-deriving every record page on the next deploy."""

    def handle(request: httpx.Request) -> httpx.Response:
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

    result = await _fetch(parents_reader, "records", handle)

    assert result.pages[0].digest == RECORD_DIGEST


async def test_a_table_page_projects_the_fields_the_records_under_it_read(
    parents_reader: ParentsReader,
) -> None:
    result = await _fetch(parents_reader, "tables", _base_handler())
    assert result.pages[0].parent_fields == {"base_id": "b1", "id": "t1", "name": "Tasks"}


def test_the_catalog_registers_the_tables_and_bases_that_records_fan_from() -> None:
    streams = {stream.name: stream for stream in AirtableConnector().streams()}

    assert syncing_streams(list(streams.values())) == {"records", "tables", "bases"}
    assert streams["records"].key_scope == "global"
    assert streams["tables"].key_scope == "local"
    assert streams["bases"].parents == ()
    assert not streams["bases"].indexed and not streams["tables"].indexed
    assert streams["records"].indexed


async def test_bases_and_tables_flatten_derive_name_and_api_url(
    parents_reader: ParentsReader,
) -> None:
    base = _flat(await _fetch(parents_reader, "bases", _base_handler()), "bases/b1")
    assert base["name"] == "Product"
    assert base["api_url"] == "https://api.airtable.com/v0/meta/bases/b1"

    table = _flat(await _fetch(parents_reader, "tables", _base_handler()), "tables/b1/t1")
    assert table["api_url"] == "https://api.airtable.com/v0/b1/t1"


async def test_records_flatten_lifts_created_at_and_fields(parents_reader: ParentsReader) -> None:
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

    record = _flat(await _fetch(parents_reader, "records", handle), "records/r1")
    assert record["created_at"] == "2026-01-02T00:00:00Z"
    assert record["fields"] == {"Name": "Do"}


async def test_a_refused_base_listing_is_skipped_rather_than_failed(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "NOT_AUTHORIZED"})

    with pytest.raises(StreamSkipped):
        await _fetch(parents_reader, "bases", handle)
