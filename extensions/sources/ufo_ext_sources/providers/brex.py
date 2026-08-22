"""The Brex connector — spend-management objects (transactions, expenses, users, vendors, budgets,
departments) synced into recallable pages.

Brex paginates uniformly: every list endpoint returns `{items: [...], next_cursor: "<token>"}` and
the next page is requested with `?cursor=<token>&limit=100`; the loop stops when `next_cursor` is
null. Records arrive flat, so `flatten` stays the identity passthrough. Most endpoints expose no
`updated_at` filter, so the sync is full-refresh; `transactions` and `expenses` carry a cursor field
(`posted_at_date` / `purchased_at`) the adapter advances a watermark over, without a server-side
filter. Auth is the OAuth bearer the resolved `Credential` carries. The write path is intentionally
absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.sources import RestConnector, StreamSpec, list_or_empty

PAGE_SIZE = 100

# Stream-name → list-endpoint path. Streams sit under `/v1/` (vendors, expenses) or `/v2/`
# (everything else); tracking the path per-stream keeps the mapping explicit.
_LIST_PATHS: dict[str, str] = {
    "transactions": "/v2/transactions/card/primary",
    "users": "/v2/users",
    "departments": "/v2/departments",
    "vendors": "/v1/vendors",
    "expenses": "/v1/expenses/card",
    "budgets": "/v2/budgets",
}


def _stream(
    name: str, *, primary_key: str = "id", cursor_field: str | None = None, canonical: bool = False
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        created_at_field=cursor_field,
        updated_at_field=None,
        canonical=canonical,
    )


# Stream set mirrors Airbyte's source-brex catalog (6 streams).
BREX_STREAMS: list[StreamSpec] = [
    _stream("budgets", primary_key="budget_id"),
    _stream("departments"),
    _stream("expenses", cursor_field="purchased_at", canonical=True),
    _stream("transactions", cursor_field="posted_at_date", canonical=True),
    _stream("users"),
    _stream("vendors", canonical=True),
]


class BrexConnector(RestConnector):
    name = "brex"
    base_url = "https://platform.brexapis.com"
    streams_list = BREX_STREAMS

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        path = _LIST_PATHS.get(stream.name)
        if path is None:
            raise NotImplementedError(f"brex: no list endpoint for stream {stream.name!r}")
        next_cursor: str | None = None
        while True:
            params: dict[str, Any] = {"limit": PAGE_SIZE}
            if next_cursor:
                params["cursor"] = next_cursor
            data = await self._get(client, path, params=params)
            records = list_or_empty(data.get("items"))
            if records:
                yield records
            next_cursor = data.get("next_cursor")
            if not next_cursor:
                return
