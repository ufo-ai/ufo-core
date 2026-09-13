"""The Calendly connector over a mock transport: the org-scoped fan-out (read `/users/me` for
`current_organization`, then filter every collection by it), the `collection` /
`pagination.next_page_token` envelope, the incremental `updated_since` filter, and the
`StreamSkipped` raised when the account exposes no organization. Offline — a canned transport, no
DB, no token, no broker."""

import json
from collections.abc import Callable, Mapping
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.calendly import CalendlyConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
    syncing_streams,
)

Landed = Mapping[str, tuple[ParentRecord, ...]]
ParentsReader = Callable[[Landed], ParentPages]

ORG = "https://api.calendly.com/organizations/ORG1"
EVENT_URI = "https://api.calendly.com/scheduled_events/EV1"
INVITEE_URI = "https://api.calendly.com/scheduled_events/EV1/invitees/IN1"
EVENT_REF = f"scheduled_events/{EVENT_URI}"
LANDED_EVENTS: Landed = {
    "scheduled_events": (ParentRecord(ref=EVENT_REF, fields={"uri": EVENT_URI}),)
}


def _flat(result: SyncResult, ref: str) -> dict:
    body = next(page.body for page in result.pages if page.source_ref == ref)
    return json.loads(body.split("\n\n", 1)[1])


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    cursor: str | None = None,
    *,
    parents_reader: ParentsReader,
    landed: Landed = LANDED_EVENTS,
) -> SyncResult:
    auth = SourceAuth(
        workspace_id=uuid4(), auth_proxy=_MockProxy(handler), parents=parents_reader(landed)
    )
    return await ConnectorBackend(connector=CalendlyConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


def _me() -> dict[str, object]:
    return {"resource": {"uri": "u1", "name": "Ada", "current_organization": ORG}}


async def test_api_user_reads_the_current_user(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.calendly.com"
        assert request.url.path == "/users/me"
        return httpx.Response(200, json=_me())

    result = await _fetch("api_user", handle, parents_reader=parents_reader)
    assert {page.source_ref for page in result.pages} == {"api_user/u1"}
    assert "Ada" in result.pages[0].body


async def test_event_types_scope_to_org_thread_updated_since_and_advance_watermark(
    parents_reader: ParentsReader,
) -> None:
    seen_updated_since: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/me":
            return httpx.Response(200, json=_me())
        assert request.url.path == "/event_types"
        assert request.url.params.get("organization") == ORG
        seen_updated_since.append(request.url.params.get("updated_since"))
        return httpx.Response(
            200,
            json={
                "collection": [{"uri": "et1", "name": "Intro", "updated_at": "2026-02-05"}],
                "pagination": {"next_page_token": None},
            },
        )

    result = await _fetch("event_types", handle, cursor="2026-02-01", parents_reader=parents_reader)
    assert seen_updated_since == ["2026-02-01"]
    assert {page.source_ref for page in result.pages} == {"event_types/et1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-05"
    assert result.pages[0].updated_at == "2026-02-05T00:00:00.000000+00:00"


async def test_scheduled_events_flatten_derives_title_times_and_location(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/me":
            return httpx.Response(200, json=_me())
        assert request.url.path == "/scheduled_events"
        return httpx.Response(
            200,
            json={
                "collection": [
                    {
                        "uri": "ev1",
                        "name": "Standup",
                        "description": "Daily sync",
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-02T00:00:00Z",
                        "start_time": "2026-02-05T09:00:00Z",
                        "end_time": "2026-02-05T09:15:00Z",
                        "location": {"type": "zoom", "location": "https://zoom.us/j/1"},
                    }
                ],
                "pagination": {"next_page_token": None},
            },
        )

    result = await _fetch("scheduled_events", handle, parents_reader=parents_reader)
    page = result.pages[0]
    assert page.created_at == "2026-01-01T00:00:00.000000+00:00"
    assert page.updated_at == "2026-01-02T00:00:00.000000+00:00"
    record = _flat(result, "scheduled_events/ev1")
    assert record["title"] == "Standup"
    assert record["start_at"] == "2026-02-05T09:00:00Z"
    assert record["end_at"] == "2026-02-05T09:15:00Z"
    assert record["location"] == "https://zoom.us/j/1"


async def test_event_types_flatten_derives_api_url(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/me":
            return httpx.Response(200, json=_me())
        return httpx.Response(
            200,
            json={
                "collection": [{"uri": "et1", "name": "Intro", "created_at": "2026-01-01"}],
                "pagination": {"next_page_token": None},
            },
        )

    record = _flat(
        await _fetch("event_types", handle, parents_reader=parents_reader), "event_types/et1"
    )
    assert record["api_url"] == "et1"
    assert record["name"] == "Intro"


async def test_organization_memberships_keep_only_user_name_and_email(
    parents_reader: ParentsReader,
) -> None:
    async def fetch(user_updated_at: str) -> SyncResult:
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/users/me":
                return httpx.Response(200, json=_me())
            assert request.url.path == "/organization_memberships"
            return httpx.Response(
                200,
                json={
                    "collection": [
                        {
                            "uri": "om1",
                            "created_at": "2026-01-03",
                            "user": {
                                "name": "Ada Lovelace",
                                "email": "ada@example.com",
                                "avatar_url": "https://example.com/avatar.png",
                                "updated_at": user_updated_at,
                            },
                        }
                    ],
                    "pagination": {"next_page_token": None},
                },
            )

        return await _fetch("organization_memberships", handle, parents_reader=parents_reader)

    first = await fetch("2026-01-01")
    profile_changed = await fetch("2026-01-02")
    record = _flat(first, "organization_memberships/om1")
    assert record["name"] == "Ada Lovelace"
    assert record["email"] == "ada@example.com"
    assert "user" not in record
    assert first.pages[0].digest == profile_changed.pages[0].digest


async def test_missing_organization_skips_the_stream(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/users/me"
        return httpx.Response(200, json={"resource": {"uri": "u1", "name": "Ada"}})

    with pytest.raises(StreamSkipped):
        await _fetch("scheduled_events", handle, parents_reader=parents_reader)


async def test_a_refused_stream_is_skipped_rather_than_failed(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Permission Denied"})

    with pytest.raises(StreamSkipped):
        await _fetch("event_types", handle, parents_reader=parents_reader)


def _invitee_page(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "collection": [
                {
                    "uri": INVITEE_URI,
                    "name": "Grace",
                    "email": "grace@example.com",
                    "created_at": "2026-01-03T00:00:00Z",
                }
            ],
            "pagination": {"next_page_token": None},
        },
    )


async def test_invitees_fan_out_over_landed_events_without_rewalking_the_calendar(
    parents_reader: ParentsReader,
) -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        assert request.url.params.get("sort") == "created_at:desc"
        return _invitee_page(request)

    result = await _fetch("event_invitees", handle, parents_reader=parents_reader)
    assert seen == ["/scheduled_events/EV1/invitees"]
    assert {page.source_ref for page in result.pages} == {
        f"event_invitees/{EVENT_URI}/{INVITEE_URI}"
    }
    assert {page.source_identity for page in result.pages} == {
        f"event_invitees/{EVENT_URI}/{INVITEE_URI}"
    }


async def test_the_invitee_address_moves_under_its_event_and_restamps_nothing() -> None:
    streams = {stream.name: stream for stream in CalendlyConnector().streams()}
    assert streams["event_invitees"].canonical is False
    assert "event_invitees" not in syncing_streams(list(streams.values()))


async def test_an_event_landed_without_its_uri_raises_at_the_fan_out(
    parents_reader: ParentsReader,
) -> None:
    landed = {"scheduled_events": (ParentRecord(ref=EVENT_REF, fields={}),)}
    with pytest.raises(RuntimeError, match="event_invitees"):
        await _fetch("event_invitees", _invitee_page, landed=landed, parents_reader=parents_reader)


async def test_a_calendar_that_landed_nothing_spends_no_request(
    parents_reader: ParentsReader,
) -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return _invitee_page(request)

    result = await _fetch("event_invitees", handle, landed={}, parents_reader=parents_reader)
    assert seen == []
    assert result.pages == ()


async def test_scheduled_events_project_the_uri_its_invitees_read(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/me":
            return httpx.Response(
                200,
                json={"resource": {"uri": "u1", "name": "Ada", "current_organization": ORG}},
            )
        return httpx.Response(
            200,
            json={
                "collection": [
                    {
                        "uri": EVENT_URI,
                        "name": "Standup",
                        "created_at": "2026-01-01T00:00:00Z",
                        "start_time": "2026-02-05T09:00:00Z",
                    }
                ],
                "pagination": {"next_page_token": None},
            },
        )

    result = await _fetch("scheduled_events", handle, parents_reader=parents_reader)
    assert result.pages[0].source_identity == EVENT_REF
    assert result.pages[0].parent_fields == {"uri": EVENT_URI}
