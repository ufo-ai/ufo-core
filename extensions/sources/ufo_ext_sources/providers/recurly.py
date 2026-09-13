"""The Recurly v3 connector — accounts, subscriptions, plans, invoices and their siblings synced as
recallable pages.

Recurly speaks one envelope on every list endpoint: `{data: [...], has_more: bool, next: <path>}`.
`paginate` follows `next` (a Recurly-returned path carrying the cursor) until `has_more` is false;
an incremental stream seeds the first request with `?begin_time=<iso>` and sorts by its cursor
field, which is what makes every per-parent collection `ascending` — the listing is asked in its
cursor's own order, so the watermark it resumes at is sound. Unique coupon codes exist only under a
bulk coupon, which is the only kind their edge hangs under. Auth is HTTP Basic with the API key as
username (not a bearer), and the API version is pinned via the `Accept` header — so `_make_client`
builds the client itself: the auth-proxy transport when the broker proxies the secret, else the
member key as Basic auth. A refusal (401/403) raises `StreamSkipped`. The write path is
intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator, Mapping
from functools import partial
from typing import Any
from urllib.parse import urlparse

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import (
    Ordering,
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
)
from ufo_ext_sources.watermark import text_checkpoint

RECURLY_API_VERSION = "application/vnd.recurly.v2021-02-25"
PAGE_SIZE = 200
TIMEOUT_CONNECT_SECONDS = 30.0
TIMEOUT_READ_SECONDS = 60.0
_REFUSAL_STATUS = frozenset({401, 403})


def _highest(records: list[dict[str, Any]], cursor_field: str | None) -> str | None:
    values = [
        record[cursor_field]
        for record in records
        if cursor_field and isinstance(record.get(cursor_field), str)
    ]
    return max(values) if values else None


def _stream(
    name: str,
    *,
    parent: str | None = None,
    path: str | None = None,
    where: Mapping[str, tuple[str, ...]] | None = None,
    source_object: str | None = None,
    primary_key: str = "id",
    cursor_field: str | None = "updated_at",
    canonical: bool = False,
) -> StreamSpec:
    if (parent is None) != (path is None):
        raise ValueError(f"recurly: stream {name!r} names a parent without a path, or the reverse")
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        canonical=canonical,
        ordering=Ordering.none if parent is None else Ordering.ascending,
        parents=()
        if parent is None or path is None
        else (ParentEdge(stream=parent, path=path, where=where or {}),),
    )


RECURLY_STREAMS: list[StreamSpec] = [
    _stream("accounts", canonical=True),
    _stream("subscriptions", canonical=True),
    _stream("plans"),
    _stream("invoices", canonical=True),
    _stream("transactions", canonical=True),
    _stream(
        "account_coupon_redemptions",
        parent="accounts",
        path="/accounts/{id}/coupon_redemptions",
    ),
    _stream(
        "account_notes", parent="accounts", path="/accounts/{id}/notes", cursor_field="created_at"
    ),
    _stream("billing_infos", parent="accounts", path="/accounts/{id}/billing_infos"),
    _stream("shipping_addresses", parent="accounts", path="/accounts/{id}/shipping_addresses"),
    _stream("add_ons"),
    _stream("coupons"),
    _stream("measured_units"),
    _stream("shipping_methods"),
    _stream("credit_payments", canonical=True),
    _stream("line_items"),
    _stream(
        "unique_coupons",
        parent="coupons",
        path="/coupons/{id}/unique_coupon_codes",
        where={"coupon_type": ("bulk",)},
    ),
    _stream("export_dates", primary_key="date", cursor_field=None),
]


class RecurlyConnector(RestConnector):
    name = "recurly"
    base_url = "https://v3.recurly.com"
    streams_list = RECURLY_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        timeout = httpx.Timeout(TIMEOUT_CONNECT_SECONDS, read=TIMEOUT_READ_SECONDS)
        base = base_url.rstrip("/")
        headers = {"Accept": RECURLY_API_VERSION, "Content-Type": "application/json"}
        if credential.transport is not None:
            return httpx.AsyncClient(
                base_url=base, transport=credential.transport, timeout=timeout, headers=headers
            )
        if credential.bearer is not None:
            return httpx.AsyncClient(
                base_url=base,
                timeout=timeout,
                auth=httpx.BasicAuth(credential.bearer, ""),
                headers=headers,
            )
        raise RuntimeError("recurly: credential carries no auth")

    @staticmethod
    def _next_path(next_link: str | None) -> str | None:
        if not next_link:
            return None
        parsed = urlparse(next_link)
        if parsed.scheme:
            if not parsed.path:
                return None
            path = parsed.path
            if parsed.query:
                path = f"{path}?{parsed.query}"
            return path
        return next_link

    @staticmethod
    def _initial_query(stream: StreamSpec, cursor: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "limit": PAGE_SIZE,
            "sort": stream.cursor_field or "created_at",
            "order": "asc",
        }
        if stream.cursor_field and cursor:
            params["begin_time"] = cursor
        return params

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            if stream.parents:
                pages = partial(self._partition_pages, client, stream)
                async for page in fanned_out(stream, run, pages):
                    yield page
                return
            path = f"/{stream.source_object.lstrip('/')}"
            async for records in self._paginate_top_level(client, stream, path, cursor=run.cursor):
                yield records
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"recurly: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks scope or the key is invalid"
                ) from error
            raise

    async def _paginate_top_level(
        self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        params: dict[str, Any] | None = self._initial_query(stream, cursor)
        next_path: str | None = path
        while next_path:
            data = await self._get(client, next_path, params=params)
            params = None
            records = data.get("data") or []
            if records:
                yield records
            if not data.get("has_more"):
                return
            next_path = self._next_path(data.get("next"))

    async def _partition_pages(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        partition: Partition,
        bound: PartitionBound,
    ) -> AsyncIterator[WalkPage]:
        """One parent's slice, resumed at the partition's own watermark: the listing is asked for
        `sort=<cursor_field>&order=asc`, so `begin_time` reads records strictly after it."""
        params: dict[str, Any] | None = self._initial_query(stream, bound.after)
        next_path: str | None = partition.path
        while next_path:
            data = await self._get(client, next_path, params=params)
            params = None
            records = data.get("data") or []
            if records:
                yield WalkPage(records=records, high=_highest(records, stream.cursor_field))
            if not data.get("has_more"):
                return
            next_path = self._next_path(data.get("next"))
