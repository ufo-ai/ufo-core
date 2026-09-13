"""The Stripe connector over a mock transport: the `has_more`/`starting_after` cursor walk, the
`?created[gte]` incremental filter, the `events` stream (which ufo syncs as plain records — it
does not route Stripe's cross-object `*.deleted` events to other streams), a child stream reading
only its own collection over the parent records that landed, a query-param edge keeping its parent
filter beside the walk's own page size (Stripe rejects `/v1/subscription_items` without
`subscription`), the per-request summary
id kept out of the record and still walked as the wire cursor, period bounds projected without a
nonexistent record timestamp, a missing parent or current-meter item dropped with the rest of the
walk intact, a refusal as `StreamSkipped`, and any other reason a client error names reaching the
run as a `StreamFault`. Stripe's `created` cursor is the unix instant of the last sweep, emitted as
decimal text the replaced image reads too; the sweep filters from thirty days behind it, and a
capped run resumes on the adapter's positional envelope, so no record id is ever stored. Offline —
a canned transport, no DB, no token."""

import json
import time
from collections.abc import Callable, Mapping
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.stripe import (
    CLOCK_SKEW_SECONDS,
    CREATED_LOOKBACK_SECONDS,
    STRIPE_STREAMS,
    STRIPE_VERSION,
    SWEEP_INTERVAL_SECONDS,
    StripeConnector,
    _refusal_reason,
)

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources import backend
from ufo.runtime.sources.sync import (
    CursorExpired,
    SourceAuth,
    StreamFault,
    StreamSkipped,
    SyncResult,
)
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
    no_parents,
)

ParentsReader = Callable[[Mapping[str, tuple[ParentRecord, ...]]], ParentPages]

LANDED: Mapping[str, tuple[ParentRecord, ...]] = {
    "customers": (ParentRecord(ref="customers/cus_1", fields={"id": "cus_1"}),),
    "invoices": (ParentRecord(ref="invoices/in_1", fields={"id": "in_1", "created": 100}),),
    "checkout_sessions": (
        ParentRecord(
            ref="checkout_sessions/cs_1",
            fields={"id": "cs_1", "created": 100, "expires_at": 200},
        ),
    ),
    "accounts": (ParentRecord(ref="accounts/acct_1", fields={"id": "acct_1"}),),
    "payouts": (ParentRecord(ref="payouts/po_1", fields={"id": "po_1"}),),
    "subscriptions": (ParentRecord(ref="subscriptions/sub_1", fields={"id": "sub_1"}),),
    "subscription_items": (
        ParentRecord(ref="subscription_items/sub_1/si_1", fields={"id": "si_1"}),
    ),
}

MISSING_SUBSCRIPTION = {
    "error": {
        "code": "parameter_missing",
        "message": "Missing required param: subscription.",
        "type": "invalid_request_error",
    }
}
GONE_SUBSCRIPTION = {
    "error": {
        "code": "resource_missing",
        "message": "No such subscription: 'sub_gone'",
        "param": "subscription",
        "type": "invalid_request_error",
    }
}
CURRENT_METER_ITEM = {
    "error": {
        "message": (
            "Cannot list usage record summaries for `si_meter` because it is not on the legacy "
            "metered billing system. Call /v1/billing/meters/:id/event_summaries instead."
        ),
        "type": "invalid_request_error",
    }
}


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


def _spec(name: str):
    return next(spec for spec in STRIPE_STREAMS if spec.name == name)


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    cursor: str | None = None,
    parents: ParentPages = no_parents,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler), parents=parents)
    return await ConnectorBackend(connector=StripeConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_charges_walk_has_more_with_starting_after() -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Stripe-Version"))
        if request.url.params.get("starting_after") == "ch_1":
            return httpx.Response(
                200, json={"data": [{"id": "ch_2", "created": 200}], "has_more": False}
            )
        return httpx.Response(
            200, json={"data": [{"id": "ch_1", "created": 100}], "has_more": True}
        )

    now = int(time.time())
    result = await _fetch("charges", handle)
    assert _refs(result) == {"charges/ch_1", "charges/ch_2"}
    assert result.snapshot is False
    assert now - CLOCK_SKEW_SECONDS <= int(result.next_cursor or "") <= now
    assert seen and all(version == STRIPE_VERSION for version in seen)
    assert {page.updated_at for page in result.pages} == {None}
    assert {page.created_at for page in result.pages} == {
        "1970-01-01T00:01:40.000000+00:00",
        "1970-01-01T00:03:20.000000+00:00",
    }


def _one_charge(created: int) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"id": "ch_1", "created": created}], "has_more": False}
        )

    return handle


def _filters(handle: Callable[[httpx.Request], httpx.Response], seen: list[str | None]):
    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("created[gte]"))
        return handle(request)

    return wrapped


async def test_between_sweeps_the_filter_is_the_stored_sweep() -> None:
    swept = str(int(time.time()) - 600)
    seen: list[str | None] = []
    result = await _fetch("charges", _filters(_one_charge(1700000005), seen), cursor=swept)
    assert seen == [swept]
    assert result.next_cursor == swept


async def test_a_due_sweep_reaches_thirty_days_behind_the_stored_sweep() -> None:
    now = int(time.time())
    swept = now - SWEEP_INTERVAL_SECONDS
    seen: list[str | None] = []
    result = await _fetch("charges", _filters(_one_charge(1699999000), seen), cursor=str(swept))
    assert seen == [str(swept - CREATED_LOOKBACK_SECONDS)]
    assert now - CLOCK_SKEW_SECONDS <= int(result.next_cursor or "") <= now


@pytest.mark.parametrize("cursor", ["1700000000", "2023-11-14T22:13:20Z"])
async def test_a_stored_timestamp_of_either_form_sweeps_and_stores_decimal(cursor: str) -> None:
    now = int(time.time())
    seen: list[str | None] = []
    result = await _fetch("charges", _filters(_one_charge(1700000000), seen), cursor=cursor)
    assert seen == [str(1700000000 - CREATED_LOOKBACK_SECONDS)]
    assert now - CLOCK_SKEW_SECONDS <= int(result.next_cursor or "") <= now


async def test_an_unreadable_cursor_expires_so_the_driver_clears_it() -> None:
    with pytest.raises(CursorExpired):
        await _fetch("charges", _one_charge(1700000000), cursor='{"created":1700000000}')


async def test_a_capped_sweep_resumes_by_position_and_stores_no_record_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backend, "MAX_RECORDS_PER_RUN", 1)
    now = int(time.time())
    swept = now - SWEEP_INTERVAL_SECONDS
    window = str(swept - CREATED_LOOKBACK_SECONDS)
    requests: list[tuple[str | None, str | None]] = []
    charges = {None: ("ch_2", 1700000002), "ch_2": ("ch_1", 1700000001)}

    def handle(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("starting_after")
        requests.append((request.url.params.get("created[gte]"), after))
        charge_id, created = charges[after]
        return httpx.Response(
            200,
            json={"data": [{"id": charge_id, "created": created}], "has_more": after is None},
        )

    first = await _fetch("charges", handle, cursor=str(swept))
    assert _refs(first) == {"charges/ch_2"}
    assert json.loads(first.next_cursor or "")["ufo_backfill"] == {
        "origin": str(swept),
        "skip": 1,
        "watermark": str(swept),
    }
    assert "ch_" not in (first.next_cursor or "")

    second = await _fetch("charges", handle, cursor=first.next_cursor)
    assert _refs(second) == {"charges/ch_1"}
    assert requests[1] == (window, None)
    assert json.loads(second.next_cursor or "")["ufo_backfill"]["skip"] == 2

    third = await _fetch("charges", handle, cursor=second.next_cursor)
    assert third.pages == ()
    assert requests[3] == (window, None)
    assert now - CLOCK_SKEW_SECONDS <= int(third.next_cursor or "") <= now


async def test_a_first_capped_sweep_with_no_cursor_resumes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(backend, "MAX_RECORDS_PER_RUN", 1)
    requests: list[tuple[str | None, str | None]] = []
    charges = {None: ("ch_2", 1700000002), "ch_2": ("ch_1", 1700000001)}

    def handle(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("starting_after")
        requests.append((request.url.params.get("created[gte]"), after))
        charge_id, created = charges[after]
        return httpx.Response(
            200,
            json={"data": [{"id": charge_id, "created": created}], "has_more": after is None},
        )

    first = await _fetch("charges", handle)
    assert json.loads(first.next_cursor or "")["ufo_backfill"] == {
        "origin": None,
        "skip": 1,
        "watermark": None,
    }
    second = await _fetch("charges", handle, cursor=first.next_cursor)
    assert _refs(second) == {"charges/ch_1"}
    assert requests[1] == (None, None)


async def test_the_image_this_release_replaces_reads_the_stored_cursor() -> None:
    """A rolling deploy leaves outgoing pods claiming the same source row, so the cursor this
    release writes has to drive the two paths the replaced image runs on it — `_page_loop`'s filter
    and `checkpoint`'s parse, both unchanged by this release."""
    stored = (await _fetch("charges", _one_charge(1700000000))).next_cursor
    connector = StripeConnector()
    charges = _spec("charges")
    seen: list[str | None] = []
    transport = httpx.MockTransport(_filters(_one_charge(1700000000), seen))
    async with httpx.AsyncClient(base_url="https://api.stripe.com", transport=transport) as client:
        async for _ in connector._page_loop(client, "/v1/charges", charges, cursor=stored):
            pass
    assert seen == [stored]
    assert connector.checkpoint(charges, [{"created": 1700000000}], stored) == stored


async def test_a_child_reads_only_its_own_collection_over_the_parents_that_landed(
    parents_reader: ParentsReader,
) -> None:
    """The `/v1/customers` walk each child ran for itself is gone — the customers row already
    landed those records — and the customer the path reads addresses the transaction rather than
    being copied into its body."""
    paths: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(
            200, json={"data": [{"id": "cbtxn_1", "created": 100}], "has_more": False}
        )

    result = await _fetch("customer_balance_transactions", handle, parents=parents_reader(LANDED))
    assert paths == ["/v1/customers/cus_1/balance_transactions"]
    assert _refs(result) == {"customer_balance_transactions/cus_1/cbtxn_1"}
    assert '"customer_id"' not in result.pages[0].body


async def test_an_edge_that_names_its_parent_in_the_query_keeps_it_beside_the_page_size(
    parents_reader: ParentsReader,
) -> None:
    """`/v1/balance_transactions?payout={id}` names its parent in the query, and the walk hands its
    own `limit` to the same request — both have to ride, or the walk reads every payout's
    transactions under one payout's address. The two `external_account_*` streams are the sharper
    case: one path, told apart by the `object` their edges declare and nothing else."""
    seen: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"data": [{"id": "txn_1", "created": 100}]})

    result = await _fetch("payout_balance_transactions", handle, parents=parents_reader(LANDED))
    assert seen == [{"payout": "po_1", "limit": "100"}]
    assert _refs(result) == {"payout_balance_transactions/po_1/txn_1"}

    seen.clear()
    await _fetch("external_account_bank_accounts", handle, parents=parents_reader(LANDED))
    await _fetch("external_account_cards", handle, parents=parents_reader(LANDED))
    assert [params["object"] for params in seen] == ["bank_account", "card"]


async def test_a_line_item_reads_the_instant_its_parent_carries_onto_it(
    parents_reader: ParentsReader,
) -> None:
    """A checkout line item and an invoice line item carry no timestamp of their own, so the
    session's `created`/`expires_at` and the invoice's `created` are written onto each record under
    the names the stream declares — reaching `render`, so the body a member reads back names the
    instant, and `created_at_field`, so the page row does."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "li_1", "amount_total": 500}]})

    result = await _fetch("checkout_sessions_line_items", handle, parents=parents_reader(LANDED))
    assert '"checkout_session_created": 100' in result.pages[0].body
    assert '"checkout_session_expires_at": 200' in result.pages[0].body
    assert result.pages[0].created_at == "1970-01-01T00:01:40.000000+00:00"

    result = await _fetch("invoice_line_items", handle, parents=parents_reader(LANDED))
    assert '"invoice_created": 100' in result.pages[0].body
    assert result.pages[0].created_at == "1970-01-01T00:01:40.000000+00:00"


async def test_a_line_item_that_names_the_carried_field_itself_raises(
    parents_reader: ParentsReader,
) -> None:
    """Stripe sending its own `checkout_session_created` would be a second answer to the one the
    session gives, and which wins is a thing to say out loud rather than resolve by overwrite."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "li_1", "checkout_session_created": 999}]})

    with pytest.raises(RuntimeError, match="already carries it"):
        await _fetch("checkout_sessions_line_items", handle, parents=parents_reader(LANDED))


def test_no_child_stripe_declares_is_canonical() -> None:
    """Every one of the fourteen is non-canonical, so `ConnectedSources` has never registered a row
    for it and no page has ever landed under the key the parent now prefixes."""
    children = [spec for spec in STRIPE_STREAMS if spec.parents]
    assert len(children) == 14
    assert [spec.name for spec in children if spec.canonical] == []


async def test_events_sync_as_plain_records(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/events"
        return httpx.Response(
            200, json={"data": [{"id": "evt_1", "type": "customer.deleted"}], "has_more": False}
        )

    result = await _fetch("events", handle)
    assert _refs(result) == {"events/evt_1"}
    assert result.deletes == ()


@pytest.mark.parametrize(
    ("stream", "child_path", "record", "created_at", "updated_at"),
    [
        (
            "usage_records",
            "/v1/subscription_items/si_1/usage_record_summaries",
            {
                "id": "ur_1",
                "subscription_item": "si_1",
                "period": {"start": 100, "end": 200},
            },
            "1970-01-01T00:01:40.000000+00:00",
            "1970-01-01T00:03:20.000000+00:00",
        ),
        (
            "checkout_sessions_line_items",
            "/v1/checkout/sessions/cs_1/line_items",
            {"id": "li_1"},
            "1970-01-01T00:01:40.000000+00:00",
            None,
        ),
        (
            "invoice_line_items",
            "/v1/invoices/in_1/lines",
            {"id": "il_1"},
            "1970-01-01T00:01:40.000000+00:00",
            None,
        ),
    ],
)
async def test_child_record_timestamps(
    stream: str,
    child_path: str,
    record: dict,
    created_at: str | None,
    updated_at: str | None,
    parents_reader: ParentsReader,
) -> None:
    """A usage summary carries its own period bounds; a line item carries no instant at all, so its
    edge carries the session's or invoice's `created` onto it under the name the stream reads."""

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == child_path
        return httpx.Response(200, json={"data": [record], "has_more": False})

    result = await _fetch(stream, handle, parents=parents_reader(LANDED))
    assert result.pages[0].created_at == created_at
    assert result.pages[0].updated_at == updated_at


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"type": "authentication_error"}})

    with pytest.raises(StreamSkipped):
        await _fetch("charges", handle)


async def test_a_child_drops_a_parent_stripe_no_longer_resolves(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        match request.url.params.get("subscription"):
            case "sub_gone":
                return httpx.Response(400, json=GONE_SUBSCRIPTION)
            case "sub_1":
                return httpx.Response(
                    200, json={"data": [{"id": "si_1", "created": 100}], "has_more": False}
                )
            case other:
                raise AssertionError(f"unexpected subscription {other}")

    result = await _fetch(
        "subscription_items",
        handle,
        parents=parents_reader(
            {
                "subscriptions": (
                    ParentRecord(ref="subscriptions/sub_gone", fields={"id": "sub_gone"}),
                    ParentRecord(ref="subscriptions/sub_1", fields={"id": "sub_1"}),
                )
            }
        ),
    )
    assert _refs(result) == {"subscription_items/sub_1/si_1"}


async def test_usage_records_skips_an_item_on_the_current_meter_system(
    caplog: pytest.LogCaptureFixture,
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        match request.url.path:
            case "/v1/subscription_items/si_meter/usage_record_summaries":
                return httpx.Response(400, json=CURRENT_METER_ITEM)
            case "/v1/subscription_items/si_1/usage_record_summaries":
                return httpx.Response(200, json={"data": [_summary("sis_1")], "has_more": False})
            case path:
                raise AssertionError(f"unexpected request {path}")

    result = await _fetch(
        "usage_records",
        handle,
        parents=parents_reader(
            {
                "subscription_items": (
                    ParentRecord(
                        ref="subscription_items/sub_1/si_meter", fields={"id": "si_meter"}
                    ),
                    ParentRecord(ref="subscription_items/sub_1/si_1", fields={"id": "si_1"}),
                )
            }
        ),
    )
    assert _refs(result) == {"usage_records/si_1/si_1:100:200"}
    assert "source_sync.cursor_field_absent" not in caplog.messages


def _summary(summary_id: str) -> dict:
    return {
        "id": summary_id,
        "subscription_item": "si_1",
        "period": {"start": 100, "end": 200},
        "total_usage": 7,
    }


async def test_usage_summaries_key_on_item_and_period_not_the_per_request_id(
    parents_reader: ParentsReader,
) -> None:
    ids = iter(["sis_first", "sis_second"])

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [_summary(next(ids))], "has_more": False})

    first = await _fetch("usage_records", handle, parents=parents_reader(LANDED))
    second = await _fetch("usage_records", handle, parents=parents_reader(LANDED))
    assert first.pages[0].source_identity == second.pages[0].source_identity
    assert first.pages[0].source_identity == "usage_records/si_1/si_1:100:200"
    assert "sis_first" not in first.pages[0].body
    assert "sis_second" not in second.pages[0].body
    assert first.pages[0].digest == second.pages[0].digest


async def test_usage_summary_walk_pages_on_the_id_the_record_drops(
    parents_reader: ParentsReader,
) -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        after = request.url.params.get("starting_after")
        seen.append(after)
        if after == "sis_first":
            later = {**_summary("sis_second"), "period": {"start": 200, "end": 300}}
            return httpx.Response(200, json={"data": [later], "has_more": False})
        return httpx.Response(200, json={"data": [_summary("sis_first")], "has_more": True})

    result = await _fetch("usage_records", handle, parents=parents_reader(LANDED))
    assert seen == [None, "sis_first"]
    assert _refs(result) == {
        "usage_records/si_1/si_1:100:200",
        "usage_records/si_1/si_1:200:300",
    }


async def test_rotating_signed_links_leave_the_page_body_and_digest() -> None:
    links = iter(["https://pay.stripe.com/receipts/a", "https://pay.stripe.com/receipts/b"])

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [{"id": "ch_1", "created": 100, "receipt_url": next(links)}],
                "has_more": False,
            },
        )

    first = await _fetch("charges", handle)
    second = await _fetch("charges", handle)
    assert "receipt_url" not in first.pages[0].body
    assert first.pages[0].digest == second.pages[0].digest


async def test_stream_fault_carries_the_reason_stripe_named(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=MISSING_SUBSCRIPTION)

    with pytest.raises(StreamFault) as raised:
        await _fetch("subscription_items", handle, parents=parents_reader(LANDED))
    assert raised.value.reason == (
        "stripe: 400 GET https://api.stripe.com/v1/subscription_items: "
        "Missing required param: subscription. [parameter_missing]"
    )


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [
        (400, MISSING_SUBSCRIPTION, "Missing required param: subscription. [parameter_missing]"),
        (404, {"error": {"message": "No such customer: 'cus_1'"}}, "No such customer: 'cus_1'"),
        (500, {"error": {"message": "An unexpected error", "code": "api_error"}}, ""),
        (400, {"error": {"type": "invalid_request_error"}}, ""),
        (400, "<html>gateway</html>", ""),
    ],
)
def test_refusal_reason_reads_only_what_a_client_error_named(
    status: int, body: dict | str, reason: str
) -> None:
    request = httpx.Request("GET", "https://api.stripe.com/v1/charges")
    response = httpx.Response(
        status, request=request, **({"text": body} if isinstance(body, str) else {"json": body})
    )
    assert _refusal_reason(httpx.HTTPStatusError("", request=request, response=response)) == reason
