"""The Recurly v3 connector — accounts, subscriptions, plans, invoices and their siblings synced as
recallable pages.

Recurly speaks one envelope on every list endpoint: `{data: [...], has_more: bool, next: <path>}`.
`paginate` follows `next` (a Recurly-returned path carrying the cursor) until `has_more` is false;
an incremental stream seeds the first request with `?begin_time=<iso>` and sorts by its cursor
field. Five substreams live under a parent (`/accounts/{id}/notes`, `/coupons/{id}/
unique_coupon_codes`, …) and fan out: `paginate` walks the parent collection first, then the child
resource per parent, stamping the parent id onto each row. Auth is HTTP Basic with the API key as
username (not a bearer), and the API version is pinned via the `Accept` header — so `_make_client`
builds the client itself: the auth-proxy transport when the broker proxies the secret, else the
member key as Basic auth. A refusal (401/403) raises `StreamSkipped`. The write path is absent
absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec
from ufo_ext_sources.watermark import text_checkpoint

RECURLY_API_VERSION = "application/vnd.recurly.v2021-02-25"
PAGE_SIZE = 200
TIMEOUT_CONNECT_SECONDS = 30.0
TIMEOUT_READ_SECONDS = 60.0
_REFUSAL_STATUS = frozenset({401, 403})

_PER_PARENT_STREAMS: dict[str, tuple[str, str, str]] = {
    "account_coupon_redemptions": ("/accounts", "coupon_redemptions", "account_id"),
    "account_notes": ("/accounts", "notes", "account_id"),
    "billing_infos": ("/accounts", "billing_infos", "account_id"),
    "shipping_addresses": ("/accounts", "shipping_addresses", "account_id"),
    "unique_coupons": ("/coupons", "unique_coupon_codes", "coupon_id"),
}


def _stream(
    name: str,
    *,
    source_object: str | None = None,
    primary_key: str = "id",
    cursor_field: str | None = "updated_at",
    canonical: bool = False,
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        canonical=canonical,
    )


RECURLY_STREAMS: list[StreamSpec] = [
    _stream("accounts", canonical=True),
    _stream("subscriptions", canonical=True),
    _stream("plans"),
    _stream("invoices", canonical=True),
    _stream("transactions", canonical=True),
    _stream("account_coupon_redemptions"),
    _stream("account_notes", cursor_field="created_at"),
    _stream("billing_infos"),
    _stream("shipping_addresses"),
    _stream("add_ons"),
    _stream("coupons"),
    _stream("measured_units"),
    _stream("shipping_methods"),
    _stream("credit_payments", canonical=True),
    _stream("line_items"),
    _stream("unique_coupons"),
    _stream("unique_coupons_parent", source_object="coupons"),
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
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            if stream.name in _PER_PARENT_STREAMS:
                parent_path, child_path, stamp_field = _PER_PARENT_STREAMS[stream.name]
                async for page in self._paginate_per_parent(
                    client,
                    stream,
                    parent_path=parent_path,
                    child_path=child_path,
                    stamp_field=stamp_field,
                    cursor=cursor,
                ):
                    yield page
                return
            if stream.name == "unique_coupons_parent":
                async for page in self._paginate_top_level(
                    client, stream, "/coupons", cursor=cursor
                ):
                    bulk = [r for r in page if r.get("coupon_type") == "bulk"]
                    if bulk:
                        yield bulk
                return
            path = f"/{stream.source_object.lstrip('/')}"
            async for page in self._paginate_top_level(client, stream, path, cursor=cursor):
                yield page
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

    async def _account_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]:
        path: str | None = "/accounts"
        params: dict[str, Any] | None = {"limit": PAGE_SIZE, "sort": "created_at", "order": "asc"}
        while path:
            data = await self._get(client, path, params=params)
            params = None
            for row in data.get("data") or []:
                if isinstance(row, dict) and row.get("id"):
                    yield str(row["id"])
            if not data.get("has_more"):
                return
            path = self._next_path(data.get("next"))

    async def _coupon_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]:
        path: str | None = "/coupons"
        params: dict[str, Any] | None = {"limit": PAGE_SIZE, "sort": "created_at", "order": "asc"}
        while path:
            data = await self._get(client, path, params=params)
            params = None
            for row in data.get("data") or []:
                if not isinstance(row, dict) or not row.get("id"):
                    continue
                if row.get("coupon_type") != "bulk":
                    continue
                yield str(row["id"])
            if not data.get("has_more"):
                return
            path = self._next_path(data.get("next"))

    async def _paginate_per_parent(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        parent_path: str,
        child_path: str,
        stamp_field: str,
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        ids_iter = (
            self._coupon_ids(client) if parent_path == "/coupons" else self._account_ids(client)
        )
        async for parent_id in ids_iter:
            child_url = f"{parent_path}/{parent_id}/{child_path}"
            params: dict[str, Any] | None = self._initial_query(stream, cursor)
            next_path: str | None = child_url
            while next_path:
                data = await self._get(client, next_path, params=params)
                params = None
                records = data.get("data") or []
                if records:
                    for row in records:
                        if isinstance(row, dict):
                            row.setdefault(stamp_field, parent_id)
                    yield records
                if not data.get("has_more"):
                    break
                next_path = self._next_path(data.get("next"))
