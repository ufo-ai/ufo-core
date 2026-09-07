"""The Chargebee v2 connector — the subscription-billing surface (customers, subscriptions, items,
invoices, transactions, and their siblings and substreams) synced as recallable pages.

Chargebee is uniform: every list endpoint returns `{list: [{<resource>: {...}}, ...], next_offset}`,
each record wrapped in a per-resource envelope. `flatten` lifts that envelope so the record's `id`
and `updated_at` cursor sit at the top level. Pagination is the opaque `?next_offset=<token>` walk,
threaded until the token is absent. An incremental stream seeds `<cursor_field>[after]=<value>` from
the run cursor. Four substreams (`attached_item`, `contact`, `quote_line_group`,
`subscription_with_scheduled_changes`) fan out over a parent stream and stamp the parent id on each
child. Auth is HTTP Basic with the API key as the username and an empty password: when the resolved
`Credential` carries a direct key, `_make_client` sends it as Basic auth; a broker's proxying
transport is honored unchanged. The base URL is per-tenant (`https://<site>.chargebee.com/api/v2`),
so the class default is empty and a run without a resolved host fails loud. A refusal (401/403)
raises `StreamSkipped`. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec
from ufo_ext_sources.watermark import integer_checkpoint

PAGE_SIZE = 100
TIMEOUT_CONNECT_SECONDS = 30.0
TIMEOUT_READ_SECONDS = 60.0
_REFUSAL_STATUS = frozenset({401, 403})

# Stream-name → list-endpoint path. Substreams (a parent loop) are handled separately.
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
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        created_at_field=created_at_field,
        updated_at_field=updated_at_field,
        canonical=canonical,
    )


CHARGEBEE_STREAMS: list[StreamSpec] = [
    _stream("customer", canonical=True),
    _stream("subscription", canonical=True),
    _stream("item_price"),
    _stream("invoice", canonical=True),
    _stream("transaction", canonical=True),
    _stream("addon"),
    _stream("attached_item", cursor_field=None),
    _stream("comment", cursor_field="created_at", updated_at_field=None, canonical=True),
    _stream("contact", cursor_field=None),
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
    _stream("quote_line_group", cursor_field=None),
    _stream(
        "site_migration_detail",
        primary_key="entity_id",
        cursor_field="migrated_at",
        created_at_field="migrated_at",
        updated_at_field=None,
    ),
    _stream(
        "subscription_with_scheduled_changes", primary_key="subscription_id", cursor_field=None
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
        key matches the stream's `source_object`. A parent-id stamp a substream paginator added at
        the top level is preserved by merging it onto the inner record."""
        envelope = record.get(stream.source_object)
        if isinstance(envelope, dict):
            merged = dict(envelope)
            for key, value in record.items():
                if key == stream.source_object:
                    continue
                merged.setdefault(key, value)
            return merged
        return record

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            if stream.name == "attached_item":
                async for page in self._paginate_attached_items(client, cursor=cursor):
                    yield page
                return
            if stream.name == "contact":
                async for page in self._paginate_contacts(client, cursor=cursor):
                    yield page
                return
            if stream.name == "quote_line_group":
                async for page in self._paginate_quote_line_groups(client, cursor=cursor):
                    yield page
                return
            if stream.name == "subscription_with_scheduled_changes":
                async for page in self._paginate_subscription_scheduled(client, cursor=cursor):
                    yield page
                return
            if stream.name in _LIST_PATHS:
                async for page in self._paginate_list(client, stream, cursor=cursor):
                    yield page
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

    async def _paginate_attached_items(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Per-item fanout: `/items/{item_id}/attached_items`, stamping the parent `item_id`."""
        item_stream = next(s for s in CHARGEBEE_STREAMS if s.name == "item")
        async for parent_page in self._paginate_list(client, item_stream, cursor=cursor):
            for parent in parent_page:
                inner = parent.get("item") if isinstance(parent.get("item"), dict) else parent
                item_id = inner.get("id") if isinstance(inner, dict) else None
                if not item_id:
                    continue
                async for child_page in self._paginate_substream(
                    client,
                    f"/items/{item_id}/attached_items",
                    parent_id_field="item_id",
                    parent_id_value=item_id,
                ):
                    yield child_page

    async def _paginate_contacts(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Per-customer fanout: `/customers/{customer_id}/contacts`."""
        customer_stream = next(s for s in CHARGEBEE_STREAMS if s.name == "customer")
        async for parent_page in self._paginate_list(client, customer_stream, cursor=cursor):
            for parent in parent_page:
                inner = (
                    parent.get("customer") if isinstance(parent.get("customer"), dict) else parent
                )
                customer_id = inner.get("id") if isinstance(inner, dict) else None
                if not customer_id:
                    continue
                async for child_page in self._paginate_substream(
                    client,
                    f"/customers/{customer_id}/contacts",
                    parent_id_field="customer_id",
                    parent_id_value=customer_id,
                ):
                    yield child_page

    async def _paginate_quote_line_groups(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Per-quote fanout: `/quotes/{quote_id}/quote_line_groups`."""
        quote_stream = next(s for s in CHARGEBEE_STREAMS if s.name == "quote")
        async for parent_page in self._paginate_list(client, quote_stream, cursor=cursor):
            for parent in parent_page:
                inner = parent.get("quote") if isinstance(parent.get("quote"), dict) else parent
                quote_id = inner.get("id") if isinstance(inner, dict) else None
                if not quote_id:
                    continue
                async for child_page in self._paginate_substream(
                    client,
                    f"/quotes/{quote_id}/quote_line_groups",
                    parent_id_field="quote_id",
                    parent_id_value=quote_id,
                ):
                    yield child_page

    async def _paginate_subscription_scheduled(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Per-subscription fanout to `/subscriptions/{id}/retrieve_with_scheduled_changes` — a
        single-record endpoint per parent, yielded one envelope at a time."""
        subscription_stream = next(s for s in CHARGEBEE_STREAMS if s.name == "subscription")
        async for parent_page in self._paginate_list(client, subscription_stream, cursor=cursor):
            for parent in parent_page:
                inner = (
                    parent.get("subscription")
                    if isinstance(parent.get("subscription"), dict)
                    else parent
                )
                sub_id = inner.get("id") if isinstance(inner, dict) else None
                if not sub_id:
                    continue
                detail = await self._get(
                    client, f"/subscriptions/{sub_id}/retrieve_with_scheduled_changes"
                )
                envelope = detail.get("subscription")
                if not isinstance(envelope, dict):
                    continue
                yield [{"subscription": envelope, "subscription_id": sub_id}]

    async def _paginate_substream(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        parent_id_field: str,
        parent_id_value: str,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Generic offset-paginated substream loop (same `list[]` + `next_offset` shape), stamping
        the parent id since a substream carries no incremental cursor of its own."""
        async for records in self._get_cursor_pages(
            client,
            path,
            records_path="list",
            next_cursor_path="next_offset",
            params={"limit": PAGE_SIZE},
            cursor_param="next_offset",
            page_size_param=None,
        ):
            stamped = []
            for record in records:
                if isinstance(record, dict):
                    enriched = dict(record)
                    enriched[parent_id_field] = parent_id_value
                    stamped.append(enriched)
            if stamped:
                yield stamped
