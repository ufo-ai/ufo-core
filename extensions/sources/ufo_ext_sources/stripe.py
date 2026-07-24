"""The Stripe connector — the billing, issuing, connect, and checkout surface synced as recallable
pages.

Stripe has one list shape across every top-level stream: `{data: [...], has_more: bool}` walked with
`?limit=100&starting_after=<last_id>`, filtered incrementally with `?created[gte]=<unix>` when the
stream's cursor is `created`. Substreams fan out: `paginate` walks the parent collection, then for
each parent record fetches the child collection either by path (`/customers/{id}/payment_methods`)
or by query param (`/subscription_items?subscription=<id>`), stamping the parent id onto each row.
The `external_account_*` streams fan over accounts with an `object=<type>` filter. The pinned API
version rides the `Stripe-Version` header. Records arrive flat, so `flatten` is the identity
passthrough. A refusal (401/403) raises `StreamSkipped`. The credential is resolved through the auth
proxy the runner threads; this connector holds no token. The write path is intentionally absent —
source seam only reads."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec

PAGE_SIZE = 100
STRIPE_VERSION = "2024-10-28.acacia"
_REFUSAL_STATUS = frozenset({401, 403})

_SUBSTREAM_CHILD_PATHS: dict[str, str] = {
    "customer_balance_transactions": "/v1/customers/{id}/balance_transactions",
    "checkout_sessions_line_items": "/v1/checkout/sessions/{id}/line_items",
    "transfer_reversals": "/v1/transfers/{id}/reversals",
    "payment_methods": "/v1/customers/{id}/payment_methods",
    "persons": "/v1/accounts/{id}/persons",
    "invoice_line_items": "/v1/invoices/{id}/lines",
    "application_fees_refunds": "/v1/application_fees/{id}/refunds",
    "bank_accounts": "/v1/customers/{id}/bank_accounts",
    "usage_records": "/v1/subscription_items/{id}/usage_record_summaries",
}

_SUBSTREAM_PARENTS: dict[str, str] = {
    "customer_balance_transactions": "customers",
    "checkout_sessions_line_items": "checkout_sessions",
    "transfer_reversals": "transfers",
    "payment_methods": "customers",
    "persons": "accounts",
    "invoice_line_items": "invoices",
    "application_fees_refunds": "application_fees",
    "bank_accounts": "customers",
    "usage_records": "subscription_items",
}

_SUBSTREAM_PARENT_STAMP: dict[str, str] = {
    "application_fees_refunds": "application_fee_id",
    "bank_accounts": "customer_id",
    "checkout_sessions_line_items": "checkout_session_id",
    "customer_balance_transactions": "customer_id",
    "invoice_line_items": "invoice_id",
    "payment_methods": "customer_id",
    "persons": "account_id",
    "transfer_reversals": "transfer_id",
    "usage_records": "subscription_item_id",
}

_SUBSTREAM_QUERY_PARENTS: dict[str, tuple[str, str, str]] = {
    "subscription_items": ("subscriptions", "subscription", "/v1/subscription_items"),
    "payout_balance_transactions": ("payouts", "payout", "/v1/balance_transactions"),
    "setup_attempts": ("setup_intents", "setup_intent", "/v1/setup_attempts"),
}

_EXTRA_PARAMS: dict[str, dict[str, str]] = {
    "external_account_bank_accounts": {"object": "bank_account"},
    "external_account_cards": {"object": "card"},
    "subscriptions": {"status": "all"},
}


def _stream(
    name: str,
    *,
    source_object: str | None = None,
    primary_key: str = "id",
    cursor_field: str | None = "created",
    canonical: bool = False,
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        canonical=canonical,
    )


STRIPE_STREAMS: list[StreamSpec] = [
    _stream("customers", canonical=True),
    _stream("subscriptions", canonical=True),
    _stream("plans", canonical=True),
    _stream("invoices", canonical=True),
    _stream("charges", canonical=True),
    _stream(
        "usage_records",
        source_object="subscription_items",
        cursor_field="timestamp",
        canonical=True,
    ),
    _stream("accounts", cursor_field=None),
    _stream("application_fees"),
    _stream("application_fees_refunds", source_object="application_fees", cursor_field=None),
    _stream("authorizations", source_object="issuing/authorizations"),
    _stream("balance_transactions"),
    _stream("bank_accounts", source_object="customers", cursor_field=None),
    _stream("cardholders", source_object="issuing/cardholders"),
    _stream("cards", source_object="issuing/cards"),
    _stream("checkout_sessions", source_object="checkout/sessions"),
    _stream(
        "checkout_sessions_line_items",
        source_object="checkout/sessions",
        cursor_field="checkout_session_updated",
    ),
    _stream("coupons"),
    _stream("credit_notes"),
    _stream("customer_balance_transactions", source_object="customers"),
    _stream("disputes"),
    _stream("early_fraud_warnings", source_object="radar/early_fraud_warnings"),
    _stream("events"),
    _stream("external_account_bank_accounts", source_object="accounts", cursor_field=None),
    _stream("external_account_cards", source_object="accounts", cursor_field=None),
    _stream("file_links"),
    _stream("files"),
    _stream("invoice_items", source_object="invoiceitems"),
    _stream("invoice_line_items", source_object="invoices", cursor_field="invoice_updated"),
    _stream("payment_intents"),
    _stream("payment_methods", source_object="customers", cursor_field=None),
    _stream("payout_balance_transactions", source_object="balance_transactions"),
    _stream("payouts"),
    _stream("persons", source_object="accounts", cursor_field=None),
    _stream("prices"),
    _stream("products"),
    _stream("promotion_codes"),
    _stream("refunds"),
    _stream("reviews"),
    _stream("setup_attempts", source_object="setup_attempts"),
    _stream("setup_intents"),
    _stream("shipping_rates"),
    _stream("subscription_items", source_object="subscription_items", cursor_field=None),
    _stream("subscription_schedule", source_object="subscription_schedules"),
    _stream("top_ups", source_object="topups"),
    _stream("transactions", source_object="issuing/transactions"),
    _stream("transfer_reversals", source_object="transfers"),
    _stream("transfers"),
]


class StripeConnector(RestConnector):
    name = "stripe"
    base_url = "https://api.stripe.com"
    streams_list = STRIPE_STREAMS

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        client = super()._make_client(base_url, credential)
        client.headers["Stripe-Version"] = STRIPE_VERSION
        return client

    @staticmethod
    def _list_path(stream: StreamSpec) -> str:
        return f"/v1/{stream.source_object}"

    @staticmethod
    def _cursor_to_unix(cursor: str | None) -> int | None:
        if not cursor:
            return None
        text = str(cursor).strip()
        if text.isdigit():
            return int(text)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp())

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            if stream.name in {"external_account_bank_accounts", "external_account_cards"}:
                async for page in self._paginate_external_accounts(client, stream):
                    yield page
                return
            if stream.name in _SUBSTREAM_CHILD_PATHS:
                async for page in self._paginate_substream(client, stream):
                    yield page
                return
            if stream.name in _SUBSTREAM_QUERY_PARENTS:
                async for page in self._paginate_substream_query(client, stream):
                    yield page
                return
            async for page in self._page_loop(
                client, self._list_path(stream), stream, cursor=cursor
            ):
                yield page
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"stripe: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks scope or the key is invalid"
                ) from error
            raise

    async def _page_loop(
        self,
        client: httpx.AsyncClient,
        path: str,
        stream: StreamSpec,
        *,
        cursor: str | None,
        extra_params: dict[str, str] | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        starting_after: str | None = None
        cursor_unix = self._cursor_to_unix(cursor)
        while True:
            params: dict[str, Any] = {"limit": PAGE_SIZE}
            if starting_after is not None:
                params["starting_after"] = starting_after
            if cursor_unix is not None and stream.cursor_field == "created":
                params["created[gte]"] = cursor_unix
            stream_extras = _EXTRA_PARAMS.get(stream.name)
            if stream_extras:
                params.update(stream_extras)
            if extra_params:
                params.update(extra_params)
            data = await self._get(client, path, params=params)
            records = [self._browse_record(record, stream) for record in data.get("data") or []]
            if records:
                yield records
            if not data.get("has_more"):
                return
            last = records[-1] if records else None
            if not isinstance(last, dict) or not last.get("id"):
                return
            starting_after = str(last["id"])

    @staticmethod
    def _browse_record(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        normalized = dict(record)
        for field in ("created_at", "updated_at"):
            value = normalized.get(field)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                normalized[field] = datetime.fromtimestamp(value, UTC).isoformat()
        created_value = normalized.get("created")
        if (
            normalized.get("created_at") is None
            and isinstance(created_value, (int, float))
            and not isinstance(created_value, bool)
        ):
            normalized["created_at"] = datetime.fromtimestamp(created_value, UTC).isoformat()
        cursor_value = normalized.get(stream.cursor_field) if stream.cursor_field else None
        if (
            normalized.get("updated_at") is None
            and isinstance(cursor_value, (int, float))
            and not isinstance(cursor_value, bool)
        ):
            normalized["updated_at"] = datetime.fromtimestamp(cursor_value, UTC).isoformat()
        return normalized

    async def _paginate_substream(
        self, client: httpx.AsyncClient, stream: StreamSpec
    ) -> AsyncIterator[list[dict[str, Any]]]:
        parent_stream = self._stream_spec(_SUBSTREAM_PARENTS[stream.name])
        child_path_template = _SUBSTREAM_CHILD_PATHS[stream.name]
        parent_path = self._list_path(parent_stream)
        stamp_key = _SUBSTREAM_PARENT_STAMP.get(stream.name)
        async for parent_page in self._page_loop(client, parent_path, parent_stream, cursor=None):
            for parent in parent_page:
                pid = parent.get("id")
                if not pid:
                    continue
                child_path = child_path_template.format(id=pid)
                async for child_page in self._page_loop(client, child_path, stream, cursor=None):
                    if stamp_key:
                        yield [{**row, stamp_key: str(pid)} for row in child_page]
                    else:
                        yield child_page

    async def _paginate_substream_query(
        self, client: httpx.AsyncClient, stream: StreamSpec
    ) -> AsyncIterator[list[dict[str, Any]]]:
        parent_name, query_field, child_path = _SUBSTREAM_QUERY_PARENTS[stream.name]
        parent_stream = self._stream_spec(parent_name)
        parent_path = self._list_path(parent_stream)
        async for parent_page in self._page_loop(client, parent_path, parent_stream, cursor=None):
            for parent in parent_page:
                pid = parent.get("id")
                if not pid:
                    continue
                async for child_page in self._page_loop(
                    client, child_path, stream, cursor=None, extra_params={query_field: str(pid)}
                ):
                    yield [{**row, f"{query_field}_id": str(pid)} for row in child_page]

    async def _paginate_external_accounts(
        self, client: httpx.AsyncClient, stream: StreamSpec
    ) -> AsyncIterator[list[dict[str, Any]]]:
        accounts_stream = self._stream_spec("accounts")
        async for parent_page in self._page_loop(
            client, self._list_path(accounts_stream), accounts_stream, cursor=None
        ):
            for account in parent_page:
                account_id = account.get("id")
                if not account_id:
                    continue
                child_path = f"/v1/accounts/{account_id}/external_accounts"
                async for child_page in self._page_loop(client, child_path, stream, cursor=None):
                    yield [{**row, "account_id": str(account_id)} for row in child_page]

    def _stream_spec(self, name: str) -> StreamSpec:
        return next(spec for spec in STRIPE_STREAMS if spec.name == name)
