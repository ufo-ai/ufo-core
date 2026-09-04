"""The Stripe connector over a mock transport: the `has_more`/`starting_after` cursor walk, the
`?created[gte]` incremental filter, the `events` stream (which ufo syncs as plain records — it
does not route Stripe's cross-object `*.deleted` events to other streams), the two-level
`usage_records` fan-out asserted request by request with its parameters (Stripe rejects
`/v1/subscription_items` without `subscription`), the per-request summary id kept out of the record
and still walked as the wire cursor, a parent Stripe no longer resolves skipped with
the rest of the walk intact, a refusal as `StreamSkipped`, and any other reason a client error
names reaching the run as a `StreamFault`. Stripe's `created` cursor is a unix integer, which the
adapter's string watermark does not advance, so an incremental stream full-refreshes each run
(correct: `snapshot=False` + digest-skip). Offline — a canned transport, no DB, no token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.stripe import (
    STRIPE_VERSION,
    StripeConnector,
    _refusal_reason,
)

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamFault, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

ACCOUNT = "acct-1"
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


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=StripeConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
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

    result = await _fetch("charges", handle)
    assert _refs(result) == {"charges/ch_1", "charges/ch_2"}
    assert result.snapshot is False
    assert seen and all(version == STRIPE_VERSION for version in seen)
    assert {page.updated_at for page in result.pages} == {None}
    assert {page.created_at for page in result.pages} == {
        "1970-01-01T00:01:40.000000+00:00",
        "1970-01-01T00:03:20.000000+00:00",
    }


async def test_charges_incremental_sends_created_filter() -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("created[gte]"))
        return httpx.Response(
            200, json={"data": [{"id": "ch_1", "created": 100}], "has_more": False}
        )

    await _fetch("charges", handle, cursor="1700000000")
    assert seen == ["1700000000"]


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
                "timestamp": 100,
            },
            "1970-01-01T00:01:40.000000+00:00",
            None,
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


def _summary(summary_id: str) -> dict:
    return {
        "id": summary_id,
        "subscription_item": "si_1",
        "period": {"start": 100, "end": 200},
        "total_usage": 7,
        "timestamp": 100,
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
