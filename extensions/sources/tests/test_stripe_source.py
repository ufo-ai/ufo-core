"""The Stripe connector over a mock transport: the `has_more`/`starting_after` cursor walk, the
`?created[gte]` incremental filter, the `events` stream (which ufo syncs as plain records — it
does not route Stripe's cross-object `*.deleted` events to other streams), and a refusal as
`StreamSkipped`. Stripe's `created` cursor is a unix integer, which the adapter's string watermark
does not advance, so an incremental stream full-refreshes each run (correct: `snapshot=False` +
digest-skip). Offline — a canned transport, no DB, no token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.stripe import STRIPE_VERSION, StripeConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"


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
    assert {page.updated_at for page in result.pages} == {
        "1970-01-01T00:01:40.000000+00:00",
        "1970-01-01T00:03:20.000000+00:00",
    }
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


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"type": "authentication_error"}})

    with pytest.raises(StreamSkipped):
        await _fetch("charges", handle)
