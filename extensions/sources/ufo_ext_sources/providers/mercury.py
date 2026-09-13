"""The Mercury connector — bank accounts and their transactions synced as recallable pages.

`accounts` is one flat read (`GET /api/v1/accounts` → `{accounts: [...]}`). `transactions` is flat
too: `GET /api/v1/transactions` lists every account's, cursor-paged by the `page.nextPage`
transaction id the response carries, which the next request sends as `start_after`. The per-account
collection under `/api/v1/account/{id}/transactions` reports only a `total` and would cost one walk
per account to read the same rows. The run's watermark rides Mercury's `postedStart` filter, which
is day-granular and names the `postedAt` field the cursor tracks, so an incremental run reads the
posting day it stopped on and everything after it. A pending transaction carries no `postedAt` yet,
so it advances nothing and lands again once Mercury posts it.

A transaction recalls by its counterparty, which Mercury carries as `counterpartyName` — no
title-like key names it, so `render` does.

Mercury publishes real signed webhooks (`POST /api/v1/webhooks`, `HMAC-SHA256(secret,
"{timestamp}.{raw_body}")`), which would deliver a transaction instantly, and are production-only.
The source seam is polled — core drives `fetch` on the sync interval and no surface receives
provider callbacks — so this connector polls, which is also the only surface Mercury's sandbox
offers. Auth is the member-added API key the resolved `Credential` carries as a bearer. A refusal
(401/403) raises `StreamSkipped`. The write path is intentionally absent — the source seam only
reads."""

import json
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


MERCURY_STREAMS: list[StreamSpec] = [
    StreamSpec(
        name="accounts",
        source_object="accounts",
        primary_key="id",
        cursor_field=None,
        created_at_field="createdAt",
        updated_at_field=None,
        canonical=True,
    ),
    StreamSpec(
        name="transactions",
        source_object="transactions",
        primary_key="id",
        cursor_field="postedAt",
        created_at_field="createdAt",
        updated_at_field="postedAt",
        canonical=True,
    ),
]


class MercuryConnector(RestConnector):
    name = "mercury"
    base_url = "https://api.mercury.com"
    streams_list = MERCURY_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            if stream.name == "accounts":
                data = await self._get(client, "/api/v1/accounts")
                accounts = list_or_empty(data.get("accounts"))
                if accounts:
                    yield accounts
                return
            if stream.name != "transactions":
                raise NotImplementedError(f"mercury: stream {stream.name!r} has no dispatch")
            async for page in self._transactions(client, cursor=run.cursor):
                yield page
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"mercury: {stream.name!r} refused ({error.response.status_code}); the key "
                    "lacks read access or is invalid"
                ) from error
            raise

    def _transactions(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Every account's transactions from the watermark's posting day onward. `postedStart` takes
        a date, so the day the last run stopped on is re-read whole and the repeats dedup
        downstream."""
        return self._get_cursor_pages(
            client,
            "/api/v1/transactions",
            records_path="transactions",
            next_cursor_path="page.nextPage",
            params={"postedStart": cursor[:10]} if cursor else None,
            cursor_param="start_after",
            page_size_param="limit",
            page_size=PAGE_SIZE,
        )

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        """A transaction recalls by its counterparty; every other stream takes the default
        titled-JSON render."""
        counterparty = record.get("counterpartyName")
        if stream.name != "transactions" or not isinstance(counterparty, str) or not counterparty:
            return super().render(record, stream)
        return (
            counterparty,
            f"# mercury {stream.name}: {counterparty}\n\n{json.dumps(record, sort_keys=True)}",
        )
