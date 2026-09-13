"""The Brex connector — spend-management objects (transactions, transfers, expenses, users, vendors,
budgets, departments) synced into recallable pages.

Brex paginates uniformly: every list endpoint returns `{items: [...], next_cursor: "<token>"}` and
the next page is requested with `?cursor=<token>&limit=100`; the loop stops when `next_cursor` is
null. Records arrive flat, so `flatten` stays the identity passthrough. Most endpoints expose no
`updated_at` filter, so the sync is full-refresh; `transactions` and `expenses` carry a cursor field
(`posted_at_date` / `purchased_at`) the provider computes a watermark over, without a server-side
filter. Auth is the OAuth bearer the resolved `Credential` carries.

`transfers` is the polled form of Brex's own `TRANSFER_PROCESSED`/`TRANSFER_FAILED` webhook: the
list endpoint returns the same transfer objects the webhook's follow-up `GET /v1/transfers/{id}`
hydrates, keyed by the same transfer id. The source seam is polled — core drives `fetch` on the sync
interval and no surface receives provider callbacks — so the list read is what lands the transfer.
A refusal (401/403) raises `StreamSkipped`. The write path is intentionally absent — the source seam
only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.sources import (
    RestConnector,
    Run,
    StreamSkipped,
    StreamSpec,
    list_or_empty,
)
from ufo_ext_sources.watermark import text_checkpoint

PAGE_SIZE = 100
_REFUSAL_STATUS = frozenset({401, 403})

_LIST_PATHS: dict[str, str] = {
    "transactions": "/v2/transactions/card/primary",
    "transfers": "/v1/transfers",
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


BREX_STREAMS: list[StreamSpec] = [
    _stream("budgets", primary_key="budget_id"),
    _stream("departments"),
    _stream("expenses", cursor_field="purchased_at", canonical=True),
    _stream("transactions", cursor_field="posted_at_date", canonical=True),
    _stream("transfers", canonical=True),
    _stream("users"),
    _stream("vendors", canonical=True),
]


class BrexConnector(RestConnector):
    name = "brex"
    base_url = "https://platform.brexapis.com"
    streams_list = BREX_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]]]:
        path = _LIST_PATHS.get(stream.name)
        if path is None:
            raise NotImplementedError(f"brex: no list endpoint for stream {stream.name!r}")
        next_cursor: str | None = None
        try:
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
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"brex: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks the scope"
                ) from error
            raise
