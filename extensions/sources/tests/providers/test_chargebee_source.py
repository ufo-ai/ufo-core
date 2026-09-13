"""The Chargebee connector over a mock transport: the `{list: [{<resource>: {...}}], next_offset}`
walk with `flatten` lifting the per-record envelope (so `id`/`updated_at` sit at the top level and
the watermark advances), the `<field>[after]` incremental param, the substreams read under the
parent record that holds them, the HTTP Basic auth built from a direct key (empty password), and a
refusal surfacing as `StreamSkipped`. The class base URL is empty (per-tenant), so the tenant host
is bound through `SourceAuth.base_url`. Offline — a canned transport, no token."""

import base64
from collections.abc import Callable, Mapping
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.chargebee import CHARGEBEE_STREAMS, ChargebeeConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
    no_parents,
)

ParentsReader = Callable[[Mapping[str, tuple[ParentRecord, ...]]], ParentPages]

BASE_URL = "https://acme.chargebee.com/api/v2"
LANDED: Mapping[str, tuple[ParentRecord, ...]] = {
    "item": (ParentRecord(ref="item/item1", fields={"id": "item1"}),),
    "customer": (ParentRecord(ref="customer/cust1", fields={"id": "cust1"}),),
    "quote": (ParentRecord(ref="quote/q1", fields={"id": "q1"}),),
    "subscription": (ParentRecord(ref="subscription/sub1", fields={"id": "sub1"}),),
}


def _spec(name: str):
    return next(spec for spec in CHARGEBEE_STREAMS if spec.name == name)


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    cursor: str | None = None,
    parents: ParentPages = no_parents,
) -> SyncResult:
    auth = SourceAuth(
        workspace_id=uuid4(),
        auth_proxy=_MockProxy(handler),
        base_url=BASE_URL,
        parents=parents,
    )
    return await ConnectorBackend(connector=ChargebeeConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_customers_lift_envelope_and_advance_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "acme.chargebee.com"
        assert request.url.path == "/api/v2/customers"
        return httpx.Response(
            200,
            json={
                "list": [
                    {
                        "customer": {
                            "id": "c1",
                            "created_at": 1767225600,
                            "updated_at": 1769904000,
                        }
                    }
                ],
                "next_offset": None,
            },
        )

    result = await _fetch("customer", handle)
    assert _refs(result) == {"customer/c1"}
    assert result.next_cursor == "1769904000"
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"
    body = result.pages[0].body
    assert "c1" in body


async def test_incremental_after_param_sent_when_a_cursor_is_stored() -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("updated_at[after]"))
        return httpx.Response(200, json={"list": [], "next_offset": None})

    await _fetch("customer", handle, cursor="1767225600")
    assert seen and seen[0] == "1767225600"


async def test_integer_watermark_round_trips_to_the_incremental_filter() -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("updated_at[after]"))
        return httpx.Response(
            200,
            json={
                "list": [
                    {"customer": {"id": str(value), "updated_at": value}} for value in (999, 1000)
                ]
            },
        )

    first = await _fetch("customer", handle, cursor="998")
    assert first.next_cursor == "1000"
    await _fetch("customer", handle, cursor=first.next_cursor)
    assert seen == ["998", "1000"]


@pytest.mark.parametrize(
    ("stream", "path", "record", "source_ref", "created_at"),
    [
        (
            "event",
            "/api/v2/events",
            {"id": "ev1", "occurred_at": 100},
            "event/ev1",
            "1970-01-01T00:01:40.000000+00:00",
        ),
        (
            "comment",
            "/api/v2/comments",
            {"id": "co1", "created_at": 100},
            "comment/co1",
            "1970-01-01T00:01:40.000000+00:00",
        ),
        (
            "promotional_credit",
            "/api/v2/promotional_credits",
            {"id": "pc1", "created_at": 100},
            "promotional_credit/pc1",
            "1970-01-01T00:01:40.000000+00:00",
        ),
        (
            "site_migration_detail",
            "/api/v2/site_migration_details",
            {"entity_id": "sm1", "migrated_at": 100},
            "site_migration_detail/sm1",
            "1970-01-01T00:01:40.000000+00:00",
        ),
    ],
)
async def test_occurrence_streams_project_provider_time_as_creation(
    stream: str,
    path: str,
    record: dict,
    source_ref: str,
    created_at: str,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == path
        return httpx.Response(200, json={"list": [{stream: record}], "next_offset": None})

    result = await _fetch(stream, handle)
    assert _refs(result) == {source_ref}
    assert result.pages[0].created_at == created_at
    assert result.pages[0].updated_at is None


async def test_basic_auth_built_from_a_direct_key() -> None:
    client = ChargebeeConnector()._make_client(BASE_URL, Credential(bearer="key-123"))
    authed = next(client.auth.auth_flow(httpx.Request("GET", BASE_URL)))
    expected = "Basic " + base64.b64encode(b"key-123:").decode()
    assert authed.headers["Authorization"] == expected


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch("customer", handle)


@pytest.mark.parametrize(
    ("stream", "path", "identity"),
    [
        ("attached_item", "/api/v2/items/item1/attached_items", "attached_item/item1/ai1"),
        ("contact", "/api/v2/customers/cust1/contacts", "contact/cust1/cont1"),
        ("quote_line_group", "/api/v2/quotes/q1/quote_line_groups", "quote_line_group/q1/qlg1"),
    ],
)
async def test_a_substream_reads_only_its_parents_collection(
    stream: str, path: str, identity: str, parents_reader: ParentsReader
) -> None:
    """The parent collection walk each substream ran for itself is gone: the parent's own row
    already landed those records. None of the three is canonical, so no page has ever landed under
    the unscoped key the parent now prefixes."""
    seen: list[str] = []
    key = {"attached_item": "ai1", "contact": "cont1", "quote_line_group": "qlg1"}[stream]

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"list": [{stream: {"id": key}}], "next_offset": None})

    result = await _fetch(stream, handle, parents=parents_reader(LANDED))

    assert seen == [path]
    assert [page.source_identity for page in result.pages] == [identity]
    assert _spec(stream).canonical is False


async def test_a_parent_that_has_landed_nothing_yet_spends_no_request(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.url}")

    result = await _fetch("contact", handle, parents=no_parents)
    assert result.pages == ()


async def test_scheduled_changes_lift_the_subscription_envelope(
    parents_reader: ParentsReader,
) -> None:
    """`/subscriptions/{id}/retrieve_with_scheduled_changes` answers `{"subscription": {…}}`, so
    the record is that envelope's own value. Lifted by the stream name instead, the page carried
    Chargebee's wrapper as its body and neither timestamp reached the row."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "subscription": {
                    "id": "sub1",
                    "status": "active",
                    "created_at": 1767225600,
                    "updated_at": 1769904000,
                }
            },
        )

    result = await _fetch(
        "subscription_with_scheduled_changes", handle, parents=parents_reader(LANDED)
    )

    assert seen == ["/api/v2/subscriptions/sub1/retrieve_with_scheduled_changes"]
    assert [page.source_identity for page in result.pages] == [
        "subscription_with_scheduled_changes/sub1/sub1"
    ]
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"
    assert '"status": "active"' in result.pages[0].body
    assert '"subscription":' not in result.pages[0].body
