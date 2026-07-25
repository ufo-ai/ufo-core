"""The Square connector — customers, locations, payments, catalog, and orders synced as recallable
pages.

Square carries three read shapes behind one `paginate` dispatch: a body-cursor list (`customers`,
`payments`, `refunds`) walked through the shared `_get_cursor_pages` with an optional `begin_time`
filter; a POST search (`catalog_items`/`catalog_categories` via `/catalog/search`, `orders` via
`/orders/search` fanned over the account's locations) whose body carries the continuation `cursor`;
and single-shot collections (`locations`, `inventory_counts`). The pinned API version rides the
`Square-Version` header. A refusal (401/403) raises `StreamSkipped`; an unimplemented stream raises
it too. The credential is resolved through the auth proxy the runner threads; this connector holds
token. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec, records_at

SQUARE_VERSION = "2026-04-16"
PAGE_SIZE = 100
_REFUSAL_STATUS = frozenset({401, 403})
_BEGIN_TIME_STREAMS = frozenset({"payments", "refunds"})
_CATALOG_OBJECT_TYPES = {"catalog_items": "ITEM", "catalog_categories": "CATEGORY"}

SQUARE_STREAMS: list[StreamSpec] = [
    StreamSpec(
        name="customers",
        source_object="customers",
        primary_key="id",
        cursor_field="updated_at",
        updated_at_field="updated_at",
    ),
    StreamSpec(name="locations", source_object="locations", primary_key="id"),
    StreamSpec(
        name="payments",
        source_object="payments",
        primary_key="id",
        cursor_field="created_at",
    ),
    StreamSpec(
        name="refunds",
        source_object="refunds",
        primary_key="id",
        cursor_field="created_at",
        canonical=False,
    ),
    StreamSpec(
        name="catalog_items",
        source_object="catalog_items",
        primary_key="id",
        cursor_field="updated_at",
        updated_at_field="updated_at",
        canonical=False,
    ),
    StreamSpec(
        name="catalog_categories",
        source_object="catalog_categories",
        primary_key="id",
        cursor_field="updated_at",
        updated_at_field="updated_at",
        canonical=False,
    ),
    StreamSpec(
        name="orders",
        source_object="orders",
        primary_key="id",
        cursor_field="created_at",
        canonical=False,
    ),
    StreamSpec(
        name="inventory_counts",
        source_object="inventory_counts",
        primary_key="catalog_object_id",
        canonical=False,
    ),
]


class SquareConnector(RestConnector):
    name = "square"
    base_url = "https://connect.squareup.com/v2"
    streams_list = SQUARE_STREAMS

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        client = super()._make_client(base_url, credential)
        client.headers["Square-Version"] = SQUARE_VERSION
        return client

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            if stream.name == "locations":
                locations = await self._locations(client)
                if locations:
                    yield locations
                return
            if stream.name in {"customers", "payments", "refunds"}:
                async for page in self._cursor_get(client, stream, cursor=cursor):
                    yield page
                return
            if stream.name in _CATALOG_OBJECT_TYPES:
                async for page in self._catalog(client, stream, cursor=cursor):
                    yield page
                return
            if stream.name == "orders":
                async for page in self._orders(client, cursor=cursor):
                    yield page
                return
            if stream.name == "inventory_counts":
                data = await self._post(client, "/inventory/counts/batch-retrieve", json={})
                counts = records_at(data, "counts")
                if counts:
                    yield counts
                return
            raise StreamSkipped(f"square stream {stream.name!r} is not implemented")
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"square: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks scope or the key is invalid"
                ) from error
            raise

    async def _cursor_get(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        params: dict[str, Any] = {}
        if cursor and stream.name in _BEGIN_TIME_STREAMS:
            params["begin_time"] = cursor
        async for records in self._get_cursor_pages(
            client,
            f"/{stream.source_object}",
            records_path=stream.source_object,
            next_cursor_path="cursor",
            params=params,
            page_size=PAGE_SIZE,
        ):
            if cursor and stream.cursor_field and stream.name not in _BEGIN_TIME_STREAMS:
                records = [r for r in records if str(r.get(stream.cursor_field) or "") > cursor]
            if records:
                yield records

    async def _catalog(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        object_type = _CATALOG_OBJECT_TYPES[stream.name]
        token: str | None = None
        while True:
            body: dict[str, Any] = {"object_types": [object_type], "limit": PAGE_SIZE}
            if token:
                body["cursor"] = token
            data = await self._post(client, "/catalog/search", json=body)
            records = records_at(data, "objects")
            if cursor and stream.cursor_field:
                records = [r for r in records if str(r.get(stream.cursor_field) or "") > cursor]
            if records:
                yield records
            token = data.get("cursor")
            if not isinstance(token, str) or not token:
                return

    async def _locations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        data = await self._get(client, "/locations")
        return records_at(data, "locations")

    async def _orders(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        locations = await self._locations(client)
        location_ids = [
            location["id"] for location in locations if isinstance(location.get("id"), str)
        ]
        if not location_ids:
            return
        token: str | None = None
        while True:
            body: dict[str, Any] = {"location_ids": location_ids, "limit": PAGE_SIZE}
            if cursor:
                body["query"] = {
                    "filter": {"date_time_filter": {"created_at": {"start_at": cursor}}}
                }
            if token:
                body["cursor"] = token
            data = await self._post(client, "/orders/search", json=body)
            records = records_at(data, "orders")
            if records:
                yield records
            token = data.get("cursor")
            if not isinstance(token, str) or not token:
                return
