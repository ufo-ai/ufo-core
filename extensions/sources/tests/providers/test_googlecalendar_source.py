"""Google Calendar connector over a mock transport: the bootstrap events walk that captures a
`nextSyncToken` cursor, the delta run that threads the stored `syncToken` and tombstones cancelled
events, `CursorExpired` on a 410 stale token, the `render` override that lifts an event's title,
time range, location, attendees, and description, the `event_attendees` stream that explodes each
event into one per-attendee row, and `StreamSkipped` on a scope refusal. Offline — a canned
transport, no DB, no token, no broker."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
from ufo_ext_sources.providers.googlecalendar import GoogleCalendarConnector

from ufo.access.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import CursorExpired, SourceAuth, StreamSkipped

ACCOUNT = "acct-1"
EVENTS_PATH = "/calendar/v3/calendars/primary/events"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(handler: Callable[[httpx.Request], httpx.Response]) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))


async def _fetch(
    handler: Callable[[httpx.Request], httpx.Response],
    cursor: str | None = None,
    stream: str = "calendar_events",
):
    return await ConnectorBackend(connector=GoogleCalendarConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, _auth(handler)
    )


EVENT = {
    "id": "e1",
    "status": "confirmed",
    "created": "2026-01-15T10:00:00Z",
    "updated": "2026-01-20T11:00:00Z",
    "summary": "Sprint review",
    "description": "Demo the build.",
    "location": "Room 4",
    "start": {"dateTime": "2026-02-01T10:00:00Z"},
    "end": {"dateTime": "2026-02-01T11:00:00Z"},
    "organizer": {"email": "lead@example.com", "displayName": "Lead"},
    "attendees": [
        {"email": "A@example.com", "displayName": "A", "responseStatus": "accepted"},
        {"email": "b@example.com"},
    ],
}
CANCELLED = {"id": "e2", "status": "cancelled"}


async def test_bootstrap_lists_events_captures_sync_token_and_renders() -> None:
    seen: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.googleapis.com" and request.url.path == EVENTS_PATH
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"items": [EVENT, CANCELLED], "nextSyncToken": "sync-1"})

    result = await _fetch(handle)

    assert {page.source_ref for page in result.pages} == {"calendar_events/e1"}
    assert result.deletes == ("calendar_events/e2",)
    assert result.next_cursor == "sync-1"
    assert result.snapshot is False
    assert result.pages[0].created_at == "2026-01-15T10:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-01-20T11:00:00.000000+00:00"
    assert seen and "timeMin" in seen[0] and seen[0].get("showDeleted") == "true"
    assert "syncToken" not in seen[0]
    assert "singleEvents" not in seen[0]

    body = result.pages[0].body
    assert "Sprint review" in body
    assert "when: 2026-02-01T10:00:00Z - 2026-02-01T11:00:00Z" in body
    assert "location: Room 4" in body
    assert "attendees: a@example.com, b@example.com" in body
    assert "Demo the build." in body


async def test_event_attendees_stream_explodes_events_into_per_attendee_rows() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.googleapis.com" and request.url.path == EVENTS_PATH
        return httpx.Response(200, json={"items": [EVENT, CANCELLED], "nextSyncToken": "sync-1"})

    result = await _fetch(handle, stream="event_attendees")

    assert {page.source_ref for page in result.pages} == {
        "event_attendees/e1:a@example.com",
        "event_attendees/e1:b@example.com",
    }
    # a cancelled event carries no attendees list, so it emits neither rows nor tombstones
    assert result.deletes == ()
    assert result.next_cursor == "sync-1"
    assert result.snapshot is False
    assert {page.created_at for page in result.pages} == {"2026-01-15T10:00:00.000000+00:00"}
    assert {page.updated_at for page in result.pages} == {"2026-01-20T11:00:00.000000+00:00"}

    body = next(
        page.body for page in result.pages if page.source_ref == "event_attendees/e1:a@example.com"
    )
    assert '"handle": "a@example.com"' in body
    assert '"event_id": "e1"' in body
    assert '"response": "accepted"' in body
    assert '"role": "required"' in body
    assert '"is_self": false' in body


async def test_delta_run_threads_the_sync_token_and_advances_it() -> None:
    seen: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": "e3", "status": "confirmed", "summary": "Retro"},
                    {"id": "e1", "status": "cancelled"},
                ],
                "nextSyncToken": "sync-2",
            },
        )

    result = await _fetch(handle, cursor="sync-1")
    assert seen and seen[0].get("syncToken") == "sync-1"
    assert {page.source_ref for page in result.pages} == {"calendar_events/e3"}
    assert result.deletes == ("calendar_events/e1",)
    assert result.next_cursor == "sync-2"


async def test_stale_sync_token_raises_cursor_expired() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            410, json={"error": {"code": 410, "message": "Sync token is no longer valid"}}
        )

    try:
        await _fetch(handle, cursor="stale")
    except CursorExpired:
        return
    raise AssertionError("a 410 on the syncToken must raise CursorExpired")


async def test_scope_refusal_yields_stream_skipped() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": 403, "message": "insufficientScopes"}})

    try:
        await _fetch(refuse)
    except StreamSkipped:
        return
    raise AssertionError("a 403 from Calendar must raise StreamSkipped")
