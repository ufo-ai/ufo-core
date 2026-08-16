"""The Stripe connector over a mock transport: the `has_more`/`starting_after` cursor walk, the
`?created[gte]` incremental filter, the `events` stream (which ufo syncs as plain records — it
does not route Stripe's cross-object `*.deleted` events to other streams), the two-level
`usage_records` fan-out asserted request by request with its parameters (Stripe rejects
`/v1/subscription_items` without `subscription`), and a refusal as `StreamSkipped`. Stripe's
`created` cursor is a unix integer, which the adapter's string watermark does not advance, so an
incremental stream full-refreshes each run (correct: `snapshot=False` + digest-skip). Offline — a
canned transport, no DB, no token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.stripe import STRIPE_VERSION, StripeConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"
MISSING_SUBSCRIPTION = {
    "error": {
        "code": "parameter_missing",
        "message": "Missing required param: subscription.",
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
            {"id": "ur_1", "timestamp": 100},
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


async def test_usage_records_fan_out_lists_items_per_subscription() -> None:
    seen: list[tuple[str, dict[str, str]]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, dict(request.url.params)))
        match request.url.path:
            case "/v1/subscriptions":
                return httpx.Response(200, json={"data": [{"id": "sub_1"}], "has_more": False})
            case "/v1/subscription_items":
                if "subscription" not in request.url.params:
                    return httpx.Response(400, json=MISSING_SUBSCRIPTION)
                if request.url.params.get("starting_after") == "si_1":
                    return httpx.Response(200, json={"data": [{"id": "si_2"}], "has_more": False})
                return httpx.Response(200, json={"data": [{"id": "si_1"}], "has_more": True})
            case "/v1/subscription_items/si_1/usage_record_summaries":
                return httpx.Response(
                    200, json={"data": [{"id": "ur_1", "timestamp": 100}], "has_more": False}
                )
            case _:
                assert request.url.path == "/v1/subscription_items/si_2/usage_record_summaries"
                return httpx.Response(
                    200, json={"data": [{"id": "ur_2", "timestamp": 200}], "has_more": False}
                )

    result = await _fetch("usage_records", handle)
    assert _refs(result) == {"usage_records/ur_1", "usage_records/ur_2"}
    assert seen == [
        ("/v1/subscriptions", {"limit": "100", "status": "all"}),
        ("/v1/subscription_items", {"limit": "100", "subscription": "sub_1"}),
        ("/v1/subscription_items/si_1/usage_record_summaries", {"limit": "100"}),
        (
            "/v1/subscription_items",
            {"limit": "100", "starting_after": "si_1", "subscription": "sub_1"},
        ),
        ("/v1/subscription_items/si_2/usage_record_summaries", {"limit": "100"}),
    ]
    assert '"subscription_item_id": "si_1"' in result.pages[0].body
    assert '"subscription_item_id": "si_2"' in result.pages[1].body


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"type": "authentication_error"}})

    with pytest.raises(StreamSkipped):
        await _fetch("charges", handle)
