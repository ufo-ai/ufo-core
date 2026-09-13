"""The Airtable connector — bases, their tables, and the records inside each table synced into
recallable pages.

Airtable exposes no flat collection: a table is published only under its base
(`/meta/bases/{id}/tables`) and a record only under its base and table (`/{base_id}/{table_id}`), so
a new table lands on the next sync with no manual config. Records page by an opaque `offset` token
the response body carries (`?offset=<token>&pageSize=100`). A record id is unique across the
account, so `records` declares `key_scope="global"` and a record page is addressed by that id alone.
The base and table listings are metadata a record joins to rather than content an account connects
for, so both are `indexed=False` and only their ids and names travel, onto each record. Auth is the
OAuth bearer the resolved `Credential` carries. A refusal (401/403) raises `StreamSkipped`. The
write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from functools import partial
from typing import Any

import httpx

from ufo.sdk.sources import (
    ParentEdge,
    Partition,
    PartitionBound,
    RestConnector,
    Run,
    StreamPage,
    StreamSkipped,
    StreamSpec,
    WalkPage,
    fanned_out,
    records_at,
)

PAGE_SIZE = 100
_REFUSAL_STATUS = frozenset({401, 403})

AIRTABLE_STREAMS: list[StreamSpec] = [
    StreamSpec(name="bases", source_object="bases", primary_key="id", indexed=False),
    StreamSpec(
        name="tables",
        source_object="tables",
        primary_key="id",
        indexed=False,
        parents=(
            ParentEdge(
                stream="bases",
                path="/meta/bases/{id}/tables",
                carry={"base_id": "id", "base_name": "name"},
            ),
        ),
    ),
    StreamSpec(
        name="records",
        source_object="records",
        primary_key="id",
        canonical=True,
        key_scope="global",
        parents=(
            ParentEdge(
                stream="tables",
                path="/{base_id}/{id}",
                carry={"base_id": "base_id", "table_id": "id", "table_name": "name"},
            ),
        ),
    ),
]


class AirtableConnector(RestConnector):
    name = "airtable"
    base_url = "https://api.airtable.com/v0"
    streams_list = AIRTABLE_STREAMS

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            if stream.name == "bases":
                bases = records_at(await self._get(client, "/meta/bases"), "bases")
                if bases:
                    yield bases
                return
            pages = self._table_pages if stream.name == "tables" else self._record_pages
            async for page in fanned_out(stream, run, partial(pages, client)):
                yield page
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"airtable: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks the scope"
                ) from error
            raise

    async def _table_pages(
        self, client: httpx.AsyncClient, partition: Partition, bound: PartitionBound
    ) -> AsyncIterator[WalkPage]:
        tables = records_at(await self._get(client, partition.path), "tables")
        if tables:
            yield WalkPage(records=tables)

    async def _record_pages(
        self, client: httpx.AsyncClient, partition: Partition, bound: PartitionBound
    ) -> AsyncIterator[WalkPage]:
        async for records in self._get_cursor_pages(
            client,
            partition.path,
            records_path="records",
            next_cursor_path="offset",
            cursor_param="offset",
            page_size_param="pageSize",
            page_size=PAGE_SIZE,
        ):
            if records:
                yield WalkPage(records=records)

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        if stream.name == "bases":
            return {
                **record,
                "name": record.get("name"),
                "api_url": f"https://api.airtable.com/v0/meta/bases/{record.get('id')}",
            }
        if stream.name == "tables":
            return {
                **record,
                "api_url": (
                    f"https://api.airtable.com/v0/{record.get('base_id')}/{record.get('id')}"
                ),
            }
        if stream.name == "records":
            fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
            return {
                **record,
                "id": record.get("id"),
                "created_at": record.get("createdTime"),
                "fields": fields,
            }
        return record
