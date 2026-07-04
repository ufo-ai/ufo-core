"""The Google Calendar connector — the primary calendar's events synced as recallable content.

Reads walk `GET /calendar/v3/calendars/primary/events`. The stream is a delta over Google's
`syncToken`, carried opaquely as the run cursor: the first run has no token, so it bootstraps from
`timeMin` (a lookback window) with `showDeleted` and captures the `nextSyncToken`; a subsequent run
passes the stored token, upserts changed events, and tombstones cancelled ones. A `410` means the
token expired, so the connector raises `CursorExpired` and core refetches fresh; a grant that lacks
the scope (`401`/`403`) yields `StreamSkipped` so the run records a skip, not a failure. An event's
attendees are folded onto its record, and `render` lifts the title, time range, location, attendees,
and description into a readable body. The credential is resolved through the auth proxy the runner
threads — this connector holds no token. The write path is intentionally absent — the source seam
only reads."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from selfhost.sdk.sources import CursorExpired, StreamSkipped
from selfhost_ext_sources.connector import StreamPage, StreamSpec
from selfhost_ext_sources.rest import RestConnector

EVENTS_PATH = "/calendar/v3/calendars/primary/events"
LIST_PAGE_SIZE = 250
BOOTSTRAP_LOOKBACK_DAYS = 90
_REFUSAL_STATUS = frozenset({401, 403})
_RESPONSE_MAP = {
    "accepted": "accepted",
    "declined": "declined",
    "tentative": "tentative",
    "needsAction": "needs_action",
}

GOOGLE_CALENDAR_STREAMS: list[StreamSpec] = [
    StreamSpec(name="calendar_events", source_object="events", primary_key="id"),
]


class GoogleCalendarConnector(RestConnector):
    name = "google_calendar"
    base_url = "https://www.googleapis.com"
    streams_list = GOOGLE_CALENDAR_STREAMS

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[StreamPage]:
        if stream.name != "calendar_events":
            raise NotImplementedError(
                f"google_calendar: stream {stream.name!r} has no paginate dispatch"
            )
        params: dict[str, Any] = {"maxResults": LIST_PAGE_SIZE, "singleEvents": "true"}
        if cursor:
            params["syncToken"] = cursor
        else:
            params["timeMin"] = (
                datetime.now(UTC) - timedelta(days=BOOTSTRAP_LOOKBACK_DAYS)
            ).isoformat()
            params["showDeleted"] = "true"
        page_token: str | None = None
        try:
            while True:
                if page_token:
                    params["pageToken"] = page_token
                data = await self._get(client, EVENTS_PATH, params=params)
                records: list[dict[str, Any]] = []
                deletes: list[str] = []
                for raw in data.get("items") or []:
                    if not isinstance(raw, dict):
                        continue
                    event_id = raw.get("id")
                    if not isinstance(event_id, str) or not event_id:
                        continue
                    if raw.get("status") == "cancelled":
                        deletes.append(event_id)
                        continue
                    records.append(_flatten_event(raw))
                page_token = data.get("nextPageToken")
                if isinstance(page_token, str) and page_token:
                    yield StreamPage(records=records, deletes=tuple(deletes))
                    continue
                next_sync = data.get("nextSyncToken")
                yield StreamPage(
                    records=records,
                    deletes=tuple(deletes),
                    next_cursor=next_sync if isinstance(next_sync, str) else None,
                )
                return
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 410:
                raise CursorExpired(f"google_calendar syncToken {cursor} expired") from error
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"google_calendar: {stream.name!r} refused ({error.response.status_code}); "
                    "the grant lacks the Calendar scope"
                ) from error
            raise

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        if stream.name != "calendar_events":
            return super().render(record, stream)
        title = _str(record.get("title"))
        lines = [f"# google_calendar calendar_events: {title}".rstrip()]
        starts = record.get("starts_at")
        ends = record.get("ends_at")
        if isinstance(starts, str) and starts:
            lines.append(
                f"when: {starts} - {ends}" if isinstance(ends, str) and ends else f"when: {starts}"
            )
        location = record.get("location")
        if isinstance(location, str) and location:
            lines.append(f"location: {location}")
        handles = ", ".join(
            attendee["handle"]
            for attendee in record.get("attendees") or []
            if isinstance(attendee, dict) and isinstance(attendee.get("handle"), str)
        )
        if handles:
            lines.append(f"attendees: {handles}")
        description = record.get("description")
        if isinstance(description, str) and description.strip():
            lines.append("")
            lines.append(description.strip())
        return title, "\n".join(lines).rstrip()


def _flatten_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Project one `events.list` item into the flat record the sync writes, folding the attendee
    handles onto it so a synced event recalls with who was invited."""
    start = raw.get("start")
    organizer = raw.get("organizer") or {}
    organizer_handle = organizer.get("email")
    attendees = [
        _attendee(attendee)
        for attendee in raw.get("attendees") or []
        if isinstance(attendee, dict) and isinstance(attendee.get("email"), str)
    ]
    return {
        "id": raw["id"],
        "title": raw.get("summary"),
        "description": raw.get("description"),
        "location": raw.get("location"),
        "starts_at": _parse_when(start),
        "ends_at": _parse_when(raw.get("end")),
        "is_all_day": isinstance(start, dict) and "date" in start,
        "organizer_handle": organizer_handle.lower() if isinstance(organizer_handle, str) else None,
        "organizer_display_name": organizer.get("displayName"),
        "status": raw.get("status"),
        "ical_uid": raw.get("iCalUID"),
        "series_external_id": raw.get("recurringEventId"),
        "attendees": attendees,
    }


def _attendee(attendee: dict[str, Any]) -> dict[str, Any]:
    response = attendee.get("responseStatus")
    return {
        "handle": attendee["email"].lower(),
        "display_name": attendee.get("displayName"),
        "response": _RESPONSE_MAP.get(response) if isinstance(response, str) else None,
    }


def _parse_when(when: Any) -> str | None:
    """`events.list` gives `{"dateTime": ...}` for a timed event and `{"date": "YYYY-MM-DD"}` for an
    all-day one; normalise both to an ISO timestamp."""
    if not isinstance(when, dict):
        return None
    if "dateTime" in when:
        return str(when["dateTime"])
    if "date" in when:
        return f"{when['date']}T00:00:00+00:00"
    return None


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""
