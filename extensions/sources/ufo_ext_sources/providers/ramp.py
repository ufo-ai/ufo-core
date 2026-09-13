"""The Ramp connector — spend objects (transactions, transfers, bills, reimbursements, cards, users,
departments, locations, limits, spend programs, vendors, receipts) synced as recallable pages.

Ramp pages uniformly: every list endpoint under `/developer/v1/` answers `{data: [...], page: {next:
"<absolute url>"}}` and the next page is the absolute `page.next` URL, followed until it is missing.
`transactions` is the one stream Ramp filters server-side on time (`from_date` over
`user_transaction_time`, walked ascending with `order_by_date_asc`), so it is the only incremental
stream; every other collection re-walks each run and the driver's digest skip absorbs the repeats.
A transaction's title is its merchant, which no title-like key carries, so `render` names it. A
refusal (401/403) raises `StreamSkipped`: the audited grant reads transactions, transfers, cards,
users, limits, departments, receipts, vendors and spend programs, so a deploy whose grant omits
bills or reimbursements records a skip rather than a failed run.

Ramp publishes real signed webhooks (`POST /developer/v1/webhooks`, `X-Ramp-Signature`), which would
deliver a cleared transaction instantly. The source seam is polled — core drives `fetch` on the sync
interval and no surface receives provider callbacks — so this connector polls. Auth is the OAuth
bearer the resolved `Credential` carries (the Pipedream brokered grant, proxied). The write path is
intentionally absent — the source seam only reads."""

import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

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
_TRANSACTION_CURSOR = "user_transaction_time"

_LIST_PATHS: dict[str, str] = {
    "transactions": "/developer/v1/transactions",
    "transfers": "/developer/v1/transfers",
    "reimbursements": "/developer/v1/reimbursements",
    "bills": "/developer/v1/bills",
    "cards": "/developer/v1/cards",
    "users": "/developer/v1/users",
    "departments": "/developer/v1/departments",
    "locations": "/developer/v1/locations",
    "limits": "/developer/v1/limits",
    "spend_programs": "/developer/v1/spend-programs",
    "vendors": "/developer/v1/vendors",
    "receipts": "/developer/v1/receipts",
}


def _stream(
    name: str,
    *,
    cursor_field: str | None = None,
    created_at_field: str | None = "created_at",
    updated_at_field: str | None = "updated_at",
    canonical: bool = False,
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=name,
        primary_key="id",
        cursor_field=cursor_field,
        created_at_field=created_at_field,
        updated_at_field=updated_at_field,
        canonical=canonical,
    )


RAMP_STREAMS: list[StreamSpec] = [
    _stream(
        "transactions",
        cursor_field=_TRANSACTION_CURSOR,
        created_at_field=_TRANSACTION_CURSOR,
        updated_at_field=None,
        canonical=True,
    ),
    _stream("transfers", canonical=True),
    _stream("reimbursements", canonical=True),
    _stream("bills", canonical=True),
    _stream("cards"),
    _stream("users"),
    _stream("departments"),
    _stream("vendors", canonical=True),
    _stream("locations"),
    _stream("limits"),
    _stream("spend_programs"),
    _stream("receipts"),
]


class RampConnector(RestConnector):
    name = "ramp"
    base_url = "https://api.ramp.com"
    streams_list = RAMP_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    @staticmethod
    def _next_path(next_link: Any) -> str | None:
        """Ramp's `page.next` absolute URL as the path+query the base-bound client needs."""
        if not isinstance(next_link, str) or not next_link:
            return None
        parsed = urlparse(next_link)
        if not parsed.path:
            return None
        return f"{parsed.path}?{parsed.query}" if parsed.query else parsed.path

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]]]:
        path = _LIST_PATHS.get(stream.name)
        if path is None:
            raise NotImplementedError(f"ramp: no list endpoint for stream {stream.name!r}")
        params: dict[str, Any] | None = {"page_size": PAGE_SIZE}
        if stream.name == "transactions":
            params = {"page_size": PAGE_SIZE, "order_by_date_asc": "true"}
            if run.cursor:
                params["from_date"] = run.cursor
        try:
            while True:
                data = await self._get(client, path, params=params)
                records = list_or_empty(data.get("data"))
                if records:
                    yield records
                page = data.get("page")
                following = self._next_path(page.get("next") if isinstance(page, dict) else None)
                if following is None:
                    return
                path, params = following, None
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"ramp: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks the scope for it"
                ) from error
            raise

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        """A transaction recalls by the merchant it was made at; Ramp carries that as
        `merchant_name`, which no title-like key names, so it is titled here. Every other stream
        takes the default titled-JSON render."""
        merchant = record.get("merchant_name")
        if stream.name != "transactions" or not isinstance(merchant, str) or not merchant:
            return super().render(record, stream)
        return (
            merchant,
            f"# ramp {stream.name}: {merchant}\n\n{json.dumps(record, sort_keys=True)}",
        )
