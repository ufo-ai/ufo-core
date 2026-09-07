"""The Stripe connector over a mock transport: the `has_more`/`starting_after` cursor walk, the
`?created[gte]` incremental filter, the `events` stream (which ufo syncs as plain records — it
does not route Stripe's cross-object `*.deleted` events to other streams), the two-level
`usage_records` fan-out asserted request by request with its parameters (Stripe rejects
`/v1/subscription_items` without `subscription`), the per-request summary id kept out of the record
and still walked as the wire cursor, period bounds projected without a nonexistent record
timestamp, a missing parent or current-meter item skipped with the rest of the walk intact, a
refusal as `StreamSkipped`, and any other reason a client error names reaching the run as a
`StreamFault`. Stripe's `created` cursor is the unix instant of the last sweep, emitted as decimal
text the replaced image reads too; the sweep filters from thirty days behind it, and a capped run
resumes on the adapter's positional envelope, so no record id is ever stored. Offline — a canned
transport, no DB, no token."""

import json
import time
from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.stripe import (
    CLOCK_SKEW_SECONDS,
    CREATED_LOOKBACK_SECONDS,
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
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

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


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
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
    charges = connector._stream_spec("charges")
    seen: list[str | None] = []
    transport = httpx.MockTransport(_filters(_one_charge(1700000000), seen))
    async with httpx.AsyncClient(base_url="https://api.stripe.com", transport=transport) as client:
        async for _ in connector._page_loop(client, "/v1/charges", charges, cursor=stored):
            pass
    assert seen == [stored]
    assert connector.checkpoint(charges, [{"created": 1700000000}], stored) == stored


async def test_a_created_substream_still_fans_out_from_its_parent() -> None:
    paths: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        match request.url.path:
            case "/v1/customers":
                return httpx.Response(
                    200, json={"data": [{"id": "cus_1", "created": 100}], "has_more": False}
                )
            case "/v1/customers/cus_1/balance_transactions":
                return httpx.Response(
                    200, json={"data": [{"id": "cbtxn_1", "created": 100}], "has_more": False}
                )
            case path:
                raise AssertionError(f"unexpected request {path}")

    result = await _fetch("customer_balance_transactions", handle)
    assert paths == ["/v1/customers", "/v1/customers/cus_1/balance_transactions"]
    assert _refs(result) == {"customer_balance_transactions/cbtxn_1"}
    assert '"customer_id": "cus_1"' in result.pages[0].body


async def test_events_sync_as_plain_records() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/events"
        return httpx.Response(
            200, json={"data": [{"id": "evt_1", "type": "customer.deleted"}], "has_more": False}
        )

    result = await _fetch("events", handle)
    assert _refs(result) == {"events/evt_1"}
    assert result.deletes == ()


@pytest.mark.parametrize(
    ("stream", "parents", "child_path", "record", "created_at", "updated_at"),
    [
        (
            "usage_records",
            {"/v1/subscriptions": "sub_1", "/v1/subscription_items": "si_1"},
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
            {"/v1/checkout/sessions": "cs_1"},
            "/v1/checkout/sessions/cs_1/line_items",
            {"id": "li_1"},
            "1970-01-01T00:01:40.000000+00:00",
            None,
        ),
        (
            "invoice_line_items",
            {"/v1/invoices": "in_1"},
            "/v1/invoices/in_1/lines",
            {"id": "il_1"},
            "1970-01-01T00:01:40.000000+00:00",
            None,
        ),
    ],
)
async def test_substream_record_timestamps(
    stream: str,
    parents: dict[str, str],
    child_path: str,
    record: dict,
    created_at: str,
    updated_at: str | None,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        parent_id = parents.get(request.url.path)
        if parent_id is not None:
            return httpx.Response(
                200,
                json={
                    "data": [{"id": parent_id, "created": 100}],
                    "has_more": False,
                },
            )
        assert request.url.path == child_path
        return httpx.Response(200, json={"data": [record], "has_more": False})

    result = await _fetch(stream, handle)
    assert result.pages[0].created_at == created_at
    assert result.pages[0].updated_at == updated_at


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"type": "authentication_error"}})

    with pytest.raises(StreamSkipped):
        await _fetch("charges", handle)


async def test_usage_records_skips_a_subscription_stripe_no_longer_resolves() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        match request.url.path, request.url.params.get("subscription"):
            case "/v1/subscriptions", _:
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {"id": "sub_gone", "created": 100},
                            {"id": "sub_1", "created": 100},
                        ],
                        "has_more": False,
                    },
                )
            case "/v1/subscription_items", "sub_gone":
                return httpx.Response(400, json=GONE_SUBSCRIPTION)
            case "/v1/subscription_items", "sub_1":
                return httpx.Response(
                    200, json={"data": [{"id": "si_1", "created": 100}], "has_more": False}
                )
            case "/v1/subscription_items/si_1/usage_record_summaries", _:
                return httpx.Response(200, json={"data": [_summary("sis_1")], "has_more": False})
            case path, _:
                raise AssertionError(f"unexpected request {path}")

    result = await _fetch("usage_records", handle)
    assert _refs(result) == {"usage_records/si_1:100:200"}


async def test_usage_records_skips_an_item_on_the_current_meter_system(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        match request.url.path:
            case "/v1/subscriptions":
                return httpx.Response(
                    200, json={"data": [{"id": "sub_1", "created": 100}], "has_more": False}
                )
            case "/v1/subscription_items":
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {"id": "si_meter", "created": 100},
                            {"id": "si_1", "created": 100},
                        ],
                        "has_more": False,
                    },
                )
            case "/v1/subscription_items/si_meter/usage_record_summaries":
                return httpx.Response(400, json=CURRENT_METER_ITEM)
            case "/v1/subscription_items/si_1/usage_record_summaries":
                return httpx.Response(200, json={"data": [_summary("sis_1")], "has_more": False})
            case path:
                raise AssertionError(f"unexpected request {path}")

    result = await _fetch("usage_records", handle)
    assert _refs(result) == {"usage_records/si_1:100:200"}
    assert result.next_cursor is None
    assert "source_sync.cursor_field_absent" not in caplog.messages


def _summary(summary_id: str) -> dict:
    return {
        "id": summary_id,
        "subscription_item": "si_1",
        "period": {"start": 100, "end": 200},
        "total_usage": 7,
    }


async def test_usage_summaries_key_on_item_and_period_not_the_per_request_id() -> None:
    ids = iter(["sis_first", "sis_second"])

    def handle(request: httpx.Request) -> httpx.Response:
        match request.url.path:
            case "/v1/subscriptions":
                return httpx.Response(
                    200, json={"data": [{"id": "sub_1", "created": 100}], "has_more": False}
                )
            case "/v1/subscription_items":
                return httpx.Response(
                    200, json={"data": [{"id": "si_1", "created": 100}], "has_more": False}
                )
            case _:
                return httpx.Response(200, json={"data": [_summary(next(ids))], "has_more": False})

    first = await _fetch("usage_records", handle)
    second = await _fetch("usage_records", handle)
    assert first.pages[0].source_identity == second.pages[0].source_identity
    assert first.pages[0].source_identity == "usage_records/si_1:100:200"
    assert "sis_first" not in first.pages[0].body
    assert "sis_second" not in second.pages[0].body
    assert first.pages[0].digest == second.pages[0].digest


async def test_usage_summary_walk_pages_on_the_id_the_record_drops() -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        match request.url.path:
            case "/v1/subscriptions":
                return httpx.Response(
                    200, json={"data": [{"id": "sub_1", "created": 100}], "has_more": False}
                )
            case "/v1/subscription_items":
                return httpx.Response(
                    200, json={"data": [{"id": "si_1", "created": 100}], "has_more": False}
                )
            case _:
                after = request.url.params.get("starting_after")
                seen.append(after)
                if after == "sis_first":
                    later = {**_summary("sis_second"), "period": {"start": 200, "end": 300}}
                    return httpx.Response(200, json={"data": [later], "has_more": False})
                return httpx.Response(200, json={"data": [_summary("sis_first")], "has_more": True})

    result = await _fetch("usage_records", handle)
    assert seen == [None, "sis_first"]
    assert _refs(result) == {"usage_records/si_1:100:200", "usage_records/si_1:200:300"}


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


async def test_stream_fault_carries_the_reason_stripe_named() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/subscriptions":
            return httpx.Response(
                200, json={"data": [{"id": "sub_1", "created": 100}], "has_more": False}
            )
        return httpx.Response(400, json=MISSING_SUBSCRIPTION)

    with pytest.raises(StreamFault) as raised:
        await _fetch("usage_records", handle)
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
