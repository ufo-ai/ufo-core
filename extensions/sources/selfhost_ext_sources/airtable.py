"""The Airtable connector — bases, their tables, and the records inside each table synced into
recallable pages.

Airtable exposes no flat collection: the connector walks the metadata API (`/meta/bases`, then
`/meta/bases/{base_id}/tables`) and fans record reads out over the discovered base/table set, so a
new table lands on the next sync with no manual config. Records page by an opaque `offset` token the
response body carries (`?offset=<token>&pageSize=100`). Each record is stamped with its `base_id` /
`table_id` context so a downstream reader can resolve its origin. Auth is the OAuth bearer the
resolved `Credential` carries. The write path is intentionally absent — the source seam only
reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from selfhost.sdk.sources import (
    RestConnector,
    StreamSkipped,
    StreamSpec,
    records_at,
    with_context,
)

PAGE_SIZE = 100

AIRTABLE_STREAMS: list[StreamSpec] = [
    StreamSpec(name="bases", source_object="bases", primary_key="id", canonical=True),
    StreamSpec(name="tables", source_object="tables", primary_key="id", canonical=False),
    StreamSpec(name="records", source_object="records", primary_key="id", canonical=False),
]


class AirtableConnector(RestConnector):
    name = "airtable"
    base_url = "https://api.airtable.com/v0"
    streams_list = AIRTABLE_STREAMS

    async def _bases(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        data = await self._get(client, "/meta/bases")
        return records_at(data, "bases")

    async def _tables_for_base(
        self, client: httpx.AsyncClient, base: dict[str, Any]
    ) -> list[dict[str, Any]]:
        base_id = base.get("id")
        if not isinstance(base_id, str) or not base_id:
            return []
        data = await self._get(client, f"/meta/bases/{base_id}/tables")
        return with_context(records_at(data, "tables"), base_id=base_id, base_name=base.get("name"))

    async def _records_for_table(
        self, client: httpx.AsyncClient, *, base_id: str, table: dict[str, Any]
    ) -> AsyncIterator[list[dict[str, Any]]]:
        table_id = table.get("id")
        if not isinstance(table_id, str) or not table_id:
            return
        async for records in self._get_cursor_pages(
            client,
            f"/{base_id}/{table_id}",
            records_path="records",
            next_cursor_path="offset",
            cursor_param="offset",
            page_size_param="pageSize",
            page_size=PAGE_SIZE,
        ):
            if records:
                yield with_context(
                    records, base_id=base_id, table_id=table_id, table_name=table.get("name")
                )

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        if stream.name == "bases":
            bases = await self._bases(client)
            if bases:
                yield bases
            return
        if stream.name == "tables":
            page: list[dict[str, Any]] = []
            for base in await self._bases(client):
                page.extend(await self._tables_for_base(client, base))
                if len(page) >= PAGE_SIZE:
                    yield page
                    page = []
            if page:
                yield page
            return
        if stream.name == "records":
            for base in await self._bases(client):
                base_id = base.get("id")
                if not isinstance(base_id, str) or not base_id:
                    continue
                for table in await self._tables_for_base(client, base):
                    async for records in self._records_for_table(
                        client, base_id=base_id, table=table
                    ):
                        yield records
            return
        raise StreamSkipped(f"airtable stream {stream.name!r} is not implemented")
