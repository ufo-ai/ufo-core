"""Klaviyo connector over a mock transport: the JSON:API `attributes` lift that makes the cursor a
flat key, `links.next` cursor pagination, the events `include=metric` sidecar stamped onto each
record, the pinned `revision` header, and the `StreamSkipped` a refusal raises. Offline — a canned
transport, no DB, no token, no broker."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.klaviyo import KLAVIYO_REVISION, KlaviyoConnector

from ufo.access.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped

ACCOUNT = "acct-1"

PROFILE_1 = {
    "type": "profile",
    "id": "p1",
    "attributes": {
        "email": "ada@example.com",
        "created": "2023-01-01T00:00:00+00:00",
        "updated": "2024-01-01T00:00:00+00:00",
        "subscriptions": {"email": {"marketing": {"consent": "SUBSCRIBED"}}},
    },
}
PROFILE_2 = {
    "type": "profile",
    "id": "p2",
    "attributes": {
        "email": "grace@example.com",
        "created": "2023-02-01T00:00:00+00:00",
        "updated": "2024-02-01T00:00:00+00:00",
        "subscriptions": {"email": {"marketing": {"consent": "UNSUBSCRIBED"}}},
    },
}


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(handler: Callable[[httpx.Request], httpx.Response]) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
):
    return await ConnectorBackend(connector=KlaviyoConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, _auth(handler)
    )


async def test_profiles_lift_attributes_and_advance_the_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "a.klaviyo.com"
        assert request.headers.get("revision") == KLAVIYO_REVISION
        assert request.url.path == "/api/profiles"
        return httpx.Response(200, json={"data": [PROFILE_1, PROFILE_2], "links": {"next": None}})

    result = await _fetch("profiles", handle)

    assert {page.source_ref for page in result.pages} == {"profiles/p1", "profiles/p2"}
    assert result.snapshot is False
    assert result.next_cursor == "2024-02-01T00:00:00+00:00"

    page = next(page for page in result.pages if page.source_ref == "profiles/p1")
    assert page.created_at == "2023-01-01T00:00:00.000000+00:00"
    assert page.updated_at == "2024-01-01T00:00:00.000000+00:00"
    body = page.body
    assert "ada@example.com" in body
    assert "SUBSCRIBED" in body
    assert "attributes" not in body


async def test_links_next_pagination_follows_the_cursor() -> None:
    list_1 = {"type": "list", "id": "l1", "attributes": {"name": "News", "updated": "2024-01-01"}}
    list_2 = {"type": "list", "id": "l2", "attributes": {"name": "Promo", "updated": "2024-01-02"}}

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/lists"
        if request.url.params.get("page[cursor]") == "xyz":
            return httpx.Response(200, json={"data": [list_2], "links": {"next": None}})
        return httpx.Response(
            200,
            json={
                "data": [list_1],
                "links": {"next": "https://a.klaviyo.com/api/lists?page[cursor]=xyz"},
            },
        )

    result = await _fetch("lists", handle)
    assert {page.source_ref for page in result.pages} == {"lists/l1", "lists/l2"}
    assert result.next_cursor == "2024-01-02"


@pytest.mark.parametrize(
    ("stream", "resource_type", "created_field", "updated_field"),
    [
        ("lists", "list", "created", "updated"),
        ("segments", "segment", "created", "updated"),
        ("campaigns", "campaign", "created_at", "updated_at"),
        ("forms", "form", "created_at", "updated_at"),
        ("images", "image", "created_at", "updated_at"),
    ],
)
async def test_mutable_streams_project_provider_record_timestamps(
    stream: str,
    resource_type: str,
    created_field: str,
    updated_field: str,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/{stream}"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "type": resource_type,
                        "id": "r1",
                        "attributes": {
                            created_field: "2026-01-01T00:00:00Z",
                            updated_field: "2026-02-01T00:00:00Z",
                        },
                    }
                ],
                "links": {"next": None},
            },
        )

    result = await _fetch(stream, handle)
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"


@pytest.mark.parametrize(("stream", "resource_type"), [("lists", "list"), ("segments", "segment")])
async def test_list_membership_counts_do_not_change_digests(
    stream: str, resource_type: str
) -> None:
    async def fetch(profile_count: int):
        def handle(request: httpx.Request) -> httpx.Response:
            assert request.url.params.get(f"additional-fields[{resource_type}]") is None
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "type": resource_type,
                            "id": "group1",
                            "attributes": {
                                "name": "Customers",
                                "updated": "2024-01-01",
                                "profile_count": profile_count,
                            },
                        }
                    ],
                    "links": {"next": None},
                },
            )

        return await _fetch(stream, handle)

    first = await fetch(10)
    membership_changed = await fetch(11)

    assert first.pages[0].digest == membership_changed.pages[0].digest
    assert "profile_count" not in first.pages[0].body


async def test_events_stamp_metric_name_from_the_included_sidecar() -> None:
    event = {
        "type": "event",
        "id": "e1",
        "attributes": {"datetime": "2024-03-01T00:00:00+00:00"},
        "relationships": {
            "metric": {"data": {"id": "m1"}},
            "profile": {"data": {"id": "pr1"}},
        },
    }

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/events"
        return httpx.Response(
            200,
            json={
                "data": [event],
                "included": [
                    {"type": "metric", "id": "m1", "attributes": {"name": "Opened Email"}}
                ],
                "links": {"next": None},
            },
        )

    result = await _fetch("events", handle)
    assert {page.source_ref for page in result.pages} == {"events/e1"}
    assert result.next_cursor == "2024-03-01T00:00:00+00:00"
    assert result.pages[0].created_at == "2024-03-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None
    body = result.pages[0].body
    assert "Opened Email" in body
    assert "pr1" in body


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errors": [{"code": "not_authorized"}]})

    with pytest.raises(StreamSkipped):
        await _fetch("profiles", handle)
