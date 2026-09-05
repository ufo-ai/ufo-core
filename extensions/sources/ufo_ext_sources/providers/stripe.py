"""The Stripe connector — the billing, issuing, connect, and checkout surface synced as recallable
pages.

Stripe has one list shape across every top-level stream: `{data: [...], has_more: bool}` walked with
`?limit=100&starting_after=<last_id>`, filtered incrementally with `?created[gte]=<unix>` when the
stream's cursor is `created`. Stripe filters a list by creation only, and a charge, invoice, or
subscription keeps changing after it is created, so once per `SWEEP_INTERVAL_SECONDS` the filter
reaches `CREATED_LOOKBACK_SECONDS` behind the cursor and re-reads that window. The cursor of a
`created` stream is the instant of that sweep, stored as unix seconds: each run of the hour after it
filters from it, so a record created since the sweep lands again and a between-sweep run costs that
hour of records rather than the whole window. The run that finishes the next sweep stores its own
start instant. One decimal timestamp is also what the release this one replaces reads out of that
column — it filters `created[gte]` from it and advances it as a watermark — so through a rolling
deploy a pod of either image reads the row the other wrote, and neither skips a record. A capped run
stores no position of its own: the adapter's positional envelope resumes it, and Stripe returning
the newest record first makes the between-sweep filter a prefix of the sweep filter, so the skip
count lands on the same record either way. Substreams fan out: `paginate` walks the parent
collection, then for each parent record fetches the child collection either by path
(`/customers/{id}/payment_methods`) or by query param (`/subscription_items?subscription=<id>`),
stamping the parent id onto each row.
A parent that is itself a query substream is enumerated through that same fan-out, so
`usage_records` walks two levels: `/subscriptions` → `/subscription_items?subscription=<id>` →
`/subscription_items/{id}/usage_record_summaries`. A summary has period bounds and no timestamp,
so this complete fan-out is cursorless and projects those bounds as its page timestamps. An item on
Stripe's current meter system has no legacy usage summaries; that one child is outside this stream
and the remaining items continue.
The `external_account_*` streams fan over accounts with an `object=<type>` filter. The pinned API
version rides the `Stripe-Version` header. Records arrive flat, so `flatten` is the identity
passthrough. A refusal (401/403) raises `StreamSkipped`; a fan-out child request Stripe answers
`resource_missing` skips that one parent and the walk carries on; any other reason Stripe names
raises `StreamFault` so the failure record carries it; a stored `created` cursor this image cannot
read raises `CursorExpired`, which the driver clears rather than refusing forever. The credential is
resolved through the auth proxy the runner threads; this connector holds no token. The write path is
intentionally absent — source seam only reads."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import (
    CursorExpired,
    RestConnector,
    StreamFault,
    StreamPage,
    StreamSkipped,
    StreamSpec,
)
from ufo_ext_sources.watermark import integer_checkpoint

PAGE_SIZE = 100
STRIPE_VERSION = "2024-10-28.acacia"
_REFUSAL_STATUS = frozenset({401, 403})
VOLATILE_FIELDS = frozenset({"receipt_url", "hosted_invoice_url", "invoice_pdf"})
USAGE_PERIOD_KEY = "usage_period"
CREATED_LOOKBACK_SECONDS = 30 * 24 * 60 * 60
SWEEP_INTERVAL_SECONDS = 60 * 60
# Stripe stamps `created` off its own clock, so a record minted while a walk runs can carry an
# instant slightly behind the walk's start: the sweep is stored that far back, and the next filter
# still reaches such a record.
CLOCK_SKEW_SECONDS = 120
_MISSING_RESOURCE_STATUS = frozenset({400, 404})
_MISSING_RESOURCE_CODE = "resource_missing"
_CURRENT_METER_USAGE_PREFIX = "Cannot list usage record summaries for `"
_CURRENT_METER_USAGE_SUFFIX = (
    "` because it is not on the legacy metered billing system. "
    "Call /v1/billing/meters/:id/event_summaries instead."
)

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
_SUBSTREAM_PARENT_FIELDS: dict[str, dict[str, str]] = {
    "checkout_sessions_line_items": {
        "checkout_session_created": "created",
        "checkout_session_expires_at": "expires_at",
    },
    "invoice_line_items": {
        "invoice_created": "created",
    },
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


def _stripe_error(error: httpx.HTTPStatusError) -> dict[str, Any]:
    try:
        body = error.response.json()
    except ValueError:
        return {}
    detail = body.get("error") if isinstance(body, dict) else None
    return detail if isinstance(detail, dict) else {}


def _refusal_reason(error: httpx.HTTPStatusError) -> str:
    """The reason Stripe named for refusing a request, as `message [code]`, read from the single
    `error` object it answers with — the shape core's `response_fault` cannot read, since that
    reads the `errors` array a GraphQL endpoint answers with. Without this a refusal reaches the
    failure record as a status and a URL with its query dropped, which says a request was refused
    and never which subscription, customer or parameter it named. Only those two keys ride, never
    the body: an error body echoes the request that drew it, and a record is not where a credential
    lands. A 5xx names nothing about the request, so it keeps its status error and with it the
    error class, which is the whole signal when a provider is down."""
    if not error.response.is_client_error:
        return ""
    detail = _stripe_error(error)
    message = detail.get("message")
    if not isinstance(message, str) or not message:
        return ""
    code = detail.get("code")
    return f"{message} [{code}]" if isinstance(code, str) and code else message


def _stream(
    name: str,
    *,
    source_object: str | None = None,
    primary_key: str = "id",
    cursor_field: str | None = "created",
    created_at_field: str | None = "created",
    updated_at_field: str | None = None,
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


STRIPE_STREAMS: list[StreamSpec] = [
    _stream("customers", canonical=True),
    _stream("subscriptions", canonical=True),
    _stream("plans", canonical=True),
    _stream("invoices", canonical=True),
    _stream("charges", canonical=True),
    _stream(
        "usage_records",
        source_object="subscription_items",
        primary_key=USAGE_PERIOD_KEY,
        cursor_field=None,
        created_at_field="period.start",
        updated_at_field="period.end",
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
        cursor_field="checkout_session_created",
        created_at_field="checkout_session_created",
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
    _stream(
        "invoice_line_items",
        source_object="invoices",
        cursor_field="invoice_created",
        created_at_field="invoice_created",
    ),
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

    def checkpoint(
        self, stream: StreamSpec, records: list[dict[str, Any]], cursor: str | None
    ) -> str | None:
        """Advance record watermarks. A created walk emits its sweep checkpoint at completion."""
        if stream.cursor_field is None or stream.cursor_field == "created":
            return cursor
        timestamp = self._cursor_to_unix(cursor)
        if cursor is not None and timestamp is None:
            raise ValueError("stripe: invalid timestamp cursor")
        normalized = str(timestamp) if timestamp is not None else None
        result = integer_checkpoint(stream, records, normalized)
        return cursor if result == normalized else result

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
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
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
            if stream.cursor_field == "created":
                async for checkpointed in self._created_walk(client, stream, cursor=cursor):
                    yield checkpointed
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
            reason = _refusal_reason(error)
            if not reason:
                raise
            raise StreamFault(
                f"stripe: {error.response.status_code} {error.request.method} "
                f"{error.request.url.copy_with(query=None)}: {reason}"
            ) from error

    async def _created_walk(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        """The `created` walk of one stream, filtered from the stored sweep instant and, once the
        sweep is due, from `CREATED_LOOKBACK_SECONDS` behind it. The instant is stored only when the
        walk reaches the end of the collection, so a run the adapter caps mid-walk resumes through
        the adapter's positional envelope over an unchanged cursor and repeats this same filter.
        This list read covers recent records inside that bounded window; older records are outside
        its read set and remain available through Stripe's events stream.
        A stored value this image cannot read is `CursorExpired`, which the driver clears — the walk
        then restarts whole, where holding it would refuse the same value every run forever."""
        stored = self._cursor_to_unix(cursor)
        if cursor is not None and stored is None:
            raise CursorExpired(f"stripe: {stream.name!r} cursor {cursor!r} is not a timestamp")
        now = int(datetime.now(UTC).timestamp())
        if stored is None or now - stored >= SWEEP_INTERVAL_SECONDS:
            gte = None if stored is None else max(stored - CREATED_LOOKBACK_SECONDS, 0)
            swept = max(now - CLOCK_SKEW_SECONDS, 0)
        else:
            gte, swept = stored, stored
        path = self._list_path(stream)
        after: str | None = None
        while True:
            params: dict[str, Any] = {"limit": PAGE_SIZE, **_EXTRA_PARAMS.get(stream.name, {})}
            if after is not None:
                params["starting_after"] = after
            if gte is not None:
                params["created[gte]"] = gte
            data = await self._get(client, path, params=params)
            rows = data.get("data") or []
            if rows:
                yield [self._browse_record(record, stream) for record in rows]
            last_id = rows[-1].get("id") if rows else None
            if not data.get("has_more") or not isinstance(last_id, str) or not last_id:
                break
            after = last_id
        yield StreamPage(next_cursor=str(swept))

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
            rows = data.get("data") or []
            records = [self._browse_record(record, stream) for record in rows]
            if records:
                yield records
            if not data.get("has_more"):
                return
            last = rows[-1] if rows else None
            if not isinstance(last, dict) or not last.get("id"):
                return
            starting_after = str(last["id"])

    @staticmethod
    def _browse_record(record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        normalized = {key: value for key, value in record.items() if key not in VOLATILE_FIELDS}
        if stream.name == "usage_records":
            period = normalized.get("period")
            item = normalized.get("subscription_item")
            if isinstance(period, dict) and item:
                normalized[USAGE_PERIOD_KEY] = f"{item}:{period.get('start')}:{period.get('end')}"
            # Stripe mints a fresh summary id per request, so it is wire addressing and never
            # part of the record: kept, it moves the page body and digest on every run.
            normalized.pop("id", None)
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
        stamp_key = _SUBSTREAM_PARENT_STAMP.get(stream.name)
        async for parent_page in self._parent_pages(client, parent_stream):
            for parent in parent_page:
                pid = parent.get("id")
                if not pid:
                    continue
                child_path = child_path_template.format(id=pid)
                async for child_page in self._child_pages(client, child_path, stream):
                    parent_fields = {
                        target: parent.get(source)
                        for target, source in _SUBSTREAM_PARENT_FIELDS.get(stream.name, {}).items()
                    }
                    if stamp_key:
                        parent_fields[stamp_key] = str(pid)
                    if parent_fields:
                        yield [{**row, **parent_fields} for row in child_page]
                    else:
                        yield child_page

    def _parent_pages(
        self, client: httpx.AsyncClient, parent_stream: StreamSpec
    ) -> AsyncIterator[list[dict[str, Any]]]:
        if parent_stream.name in _SUBSTREAM_QUERY_PARENTS:
            return self._paginate_substream_query(client, parent_stream)
        return self._page_loop(client, self._list_path(parent_stream), parent_stream, cursor=None)

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
                async for child_page in self._child_pages(
                    client, child_path, stream, extra_params={query_field: str(pid)}
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
                async for child_page in self._child_pages(client, child_path, stream):
                    yield [{**row, "account_id": str(account_id)} for row in child_page]

    async def _child_pages(
        self,
        client: httpx.AsyncClient,
        path: str,
        stream: StreamSpec,
        *,
        extra_params: dict[str, str] | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """One parent's child collection, and nothing at all where Stripe answers that the parent
        is gone. A fan-out issues one request per parent an earlier page listed, so it spans the
        window in which a parent can vanish, and a subscription canceled between the two is the
        ordinary case rather than a broken request: the refusal is scoped to the one request that
        named the parent, so skipping it leaves the rest of the walk to sync, where letting the
        status escape ends the stream and ends it again every run, the parent staying gone. Stripe
        spells that `resource_missing` either way — 404 where the parent is the path
        (`/v1/customers/{id}/bank_accounts`), 400 where a parameter names it and the path is a real
        collection (`/v1/subscription_items?subscription=`) — so the code decides, never the status
        a request-shape fault shares with it. No Stripe stream is a `delete_missing` snapshot, so
        the pages a skipped parent does not contribute tombstone nothing."""
        try:
            async for page in self._page_loop(
                client, path, stream, cursor=None, extra_params=extra_params
            ):
                yield page
        except httpx.HTTPStatusError as error:
            detail = _stripe_error(error)
            missing = (
                error.response.status_code in _MISSING_RESOURCE_STATUS
                and detail.get("code") == _MISSING_RESOURCE_CODE
            )
            message = detail.get("message")
            current_meter = (
                stream.name == "usage_records"
                and error.response.status_code == 400
                and isinstance(message, str)
                and message.startswith(_CURRENT_METER_USAGE_PREFIX)
                and message.endswith(_CURRENT_METER_USAGE_SUFFIX)
            )
            if not missing and not current_meter:
                raise

    def _stream_spec(self, name: str) -> StreamSpec:
        return next(spec for spec in STRIPE_STREAMS if spec.name == name)
