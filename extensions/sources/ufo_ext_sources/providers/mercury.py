"""The Mercury connector — bank accounts and their transactions synced as recallable pages.

`accounts` is one flat read (`GET /api/v1/accounts` → `{accounts: [...]}`). `transactions` fans out
over those accounts: each is paged by `?limit=100&offset=N` until a short page, and the run's
watermark rides Mercury's own `start` filter, day-granular, so an incremental run reads the posting
day it stopped on and everything after it. One watermark covers every account because the filter is
a date rather than a position: each run walks every account to the end, so no account can be left
behind by another account's newer transaction. A pending transaction carries no `postedAt` yet, so
it advances nothing and lands again once Mercury posts it.

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

from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec, list_or_empty, with_context
from ufo_ext_sources.watermark import text_checkpoint

PAGE_SIZE = 100
ACCOUNT_FIELD = "account_id"
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
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            if stream.name == "accounts":
                accounts = await self._accounts(client)
                if accounts:
                    yield accounts
                return
            if stream.name != "transactions":
                raise NotImplementedError(f"mercury: stream {stream.name!r} has no dispatch")
            for account_id in self._account_ids(await self._accounts(client)):
                async for page in self._transactions(client, account_id, cursor=cursor):
                    yield with_context(page, **{ACCOUNT_FIELD: account_id})
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"mercury: {stream.name!r} refused ({error.response.status_code}); the key "
                    "lacks read access or is invalid"
                ) from error
            raise

    async def _accounts(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        data = await self._get(client, "/api/v1/accounts")
        return list_or_empty(data.get("accounts"))

    @staticmethod
    def _account_ids(accounts: list[dict[str, Any]]) -> list[str]:
        return [
            account["id"]
            for account in accounts
            if isinstance(account.get("id"), str) and account["id"]
        ]

    async def _transactions(
        self, client: httpx.AsyncClient, account_id: str, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """One account's transactions, from the watermark's posting day onward. Mercury's `start`
        takes a date, so the day the last run stopped on is re-read whole and the repeats dedup
        downstream."""
        offset = 0
        while True:
            params: dict[str, Any] = {"limit": PAGE_SIZE, "offset": offset}
            if cursor:
                params["start"] = cursor[:10]
            data = await self._get(
                client, f"/api/v1/account/{account_id}/transactions", params=params
            )
            records = list_or_empty(data.get("transactions"))
            if records:
                yield records
            if len(records) < PAGE_SIZE:
                return
            offset += PAGE_SIZE

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
