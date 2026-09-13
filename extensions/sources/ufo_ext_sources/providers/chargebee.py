"""The Chargebee v2 connector — the subscription-billing surface (customers, subscriptions, items,
invoices, transactions, and their siblings and substreams) synced as recallable pages.

Chargebee is uniform: every list endpoint returns `{list: [{<resource>: {...}}, ...], next_offset}`,
each record wrapped in a per-resource envelope named by the stream's `source_object`. `flatten`
lifts that envelope so the record's `id` and `updated_at` cursor sit at the top level. Pagination is
the opaque `?next_offset=<token>` walk, threaded until the token is absent. An incremental stream
seeds `<cursor_field>[after]=<value>` from the run cursor.
`subscription_with_scheduled_changes` is the one collection that answers a single record rather than
a list — `{"subscription": {...}}`, the envelope its `source_object` names. Auth is HTTP Basic with
the API key as the username and an empty password: when the resolved
`Credential` carries a direct key, `_make_client` sends it as Basic auth; a broker's proxying
transport is honored unchanged. The base URL is per-tenant (`https://<site>.chargebee.com/api/v2`),
so the class default is empty and a run without a resolved host fails loud. A refusal (401/403)
raises `StreamSkipped`. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from functools import partial
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
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
)
from ufo_ext_sources.watermark import integer_checkpoint

PAGE_SIZE = 100
TIMEOUT_CONNECT_SECONDS = 30.0
TIMEOUT_READ_SECONDS = 60.0
_REFUSAL_STATUS = frozenset({401, 403})
_SINGLE_RECORD_CHILDREN = frozenset({"subscription_with_scheduled_changes"})

_LIST_PATHS: dict[str, str] = {
    "addon": "/addons",
    "comment": "/comments",
    "coupon": "/coupons",
    "credit_note": "/credit_notes",
    "customer": "/customers",
    "differential_price": "/differential_prices",
    "event": "/events",
    "gift": "/gifts",
    "hosted_page": "/hosted_pages",
    "invoice": "/invoices",
    "item": "/items",
    "item_family": "/item_families",
    "item_price": "/item_prices",
    "order": "/orders",
    "payment_source": "/payment_sources",
    "plan": "/plans",
    "promotional_credit": "/promotional_credits",
    "quote": "/quotes",
    "site_migration_detail": "/site_migration_details",
    "subscription": "/subscriptions",
    "transaction": "/transactions",
    "unbilled_charge": "/unbilled_charges",
    "virtual_bank_account": "/virtual_bank_accounts",
}


def _stream(
    name: str,
    *,
    source_object: str | None = None,
    primary_key: str = "id",
    cursor_field: str | None = "updated_at",
    created_at_field: str | None = "created_at",
    updated_at_field: str | None = "updated_at",
    canonical: bool = False,
    parent: str | None = None,
    path: str | None = None,
) -> StreamSpec:
    if (parent is None) != (path is None):
        raise ValueError(
            f"chargebee: stream {name!r} names a parent without a path, or the reverse"
        )
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        created_at_field=created_at_field,
        updated_at_field=updated_at_field,
        canonical=canonical,
        parents=() if parent is None or path is None else (ParentEdge(stream=parent, path=path),),
    )


CHARGEBEE_STREAMS: list[StreamSpec] = [
    _stream("customer", canonical=True),
    _stream("subscription", canonical=True),
    _stream("item_price"),
    _stream("invoice", canonical=True),
    _stream("transaction", canonical=True),
    _stream("addon"),
    _stream(
        "attached_item",
        cursor_field=None,
        parent="item",
        path="/items/{id}/attached_items",
    ),
    _stream("comment", cursor_field="created_at", updated_at_field=None, canonical=True),
    _stream(
        "contact",
        cursor_field=None,
        parent="customer",
        path="/customers/{id}/contacts",
    ),
    _stream("coupon"),
    _stream("credit_note", canonical=True),
    _stream("differential_price"),
    _stream(
        "event",
        cursor_field="occurred_at",
        created_at_field="occurred_at",
        updated_at_field=None,
    ),
    _stream("gift"),
    _stream("hosted_page"),
    _stream("item"),
    _stream("item_family"),
    _stream("order"),
    _stream("payment_source"),
    _stream("plan"),
    _stream("promotional_credit", cursor_field="created_at", updated_at_field=None),
    _stream("quote"),
    _stream(
        "quote_line_group",
        cursor_field=None,
        parent="quote",
        path="/quotes/{id}/quote_line_groups",
    ),
    _stream(
        "site_migration_detail",
        primary_key="entity_id",
        cursor_field="migrated_at",
        created_at_field="migrated_at",
        updated_at_field=None,
    ),
    _stream(
        "subscription_with_scheduled_changes",
        source_object="subscription",
        cursor_field=None,
        parent="subscription",
        path="/subscriptions/{id}/retrieve_with_scheduled_changes",
    ),
    _stream("unbilled_charge"),
    _stream("virtual_bank_account"),
]


class ChargebeeConnector(RestConnector):
    name = "chargebee"
    base_url = ""
    streams_list = CHARGEBEE_STREAMS
    checkpoint = staticmethod(integer_checkpoint)

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        timeout = httpx.Timeout(TIMEOUT_CONNECT_SECONDS, read=TIMEOUT_READ_SECONDS)
        base = base_url.rstrip("/")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
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
        raise RuntimeError("chargebee: credential carries no auth")

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        """Lift Chargebee's per-record `<resource>` envelope so the record reads flat — the envelope
        key matches the stream's `source_object`."""
        envelope = record.get(stream.source_object)
        return dict(envelope) if isinstance(envelope, dict) else record

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            if stream.parents:
                pages = partial(self._partition_pages, client, stream)
                async for page in fanned_out(stream, run, pages):
                    yield page
                return
            if stream.name in _LIST_PATHS:
                async for records in self._paginate_list(client, stream, cursor=run.cursor):
                    yield records
                return
            raise NotImplementedError(f"chargebee: no pagination strategy for {stream.name!r}")
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"chargebee: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks scope or the key is invalid"
                ) from error
            raise

    @staticmethod
    def _build_list_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]:
        """`limit` caps the page; `<cursor_field>[after]=<value>` is Chargebee's strictly-greater
        incremental operator, seeded from the run cursor."""
        params: dict[str, Any] = {"limit": PAGE_SIZE}
        if cursor and stream.cursor_field:
            params[f"{stream.cursor_field}[after]"] = cursor
        return params

    async def _paginate_list(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for page in self._get_cursor_pages(
            client,
            _LIST_PATHS[stream.name],
            records_path="list",
            next_cursor_path="next_offset",
            params=self._build_list_params(stream, cursor),
            cursor_param="next_offset",
            page_size_param=None,
        ):
            yield page

    async def _partition_pages(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        partition: Partition,
        bound: PartitionBound,
    ) -> AsyncIterator[WalkPage]:
        """One collection in whichever of Chargebee's two shapes it answers: the `list[]` +
        `next_offset` walk, or the single enveloped record a retrieve answers."""
        if stream.name in _SINGLE_RECORD_CHILDREN:
            data = await self._get(client, partition.path)
            if isinstance(data.get(stream.source_object), dict):
                yield WalkPage(records=[data])
            return
        async for records in self._get_cursor_pages(
            client,
            partition.path,
            records_path="list",
            next_cursor_path="next_offset",
            params={"limit": PAGE_SIZE},
            cursor_param="next_offset",
            page_size_param=None,
        ):
            yield WalkPage(records=records)
