"""The Google Calendar connector — the primary calendar's events synced as recallable content, plus
a per-attendee stream.

Both streams walk `GET /calendar/v3/calendars/primary/events` as a delta over Google's `syncToken`,
carried opaquely as the run cursor: the first run has no token, so it bootstraps from `timeMin` (a
lookback window) with `showDeleted` and captures the `nextSyncToken`; a subsequent run passes the
stored token, upserts changed events, and tombstones cancelled ones. A `410` means the token
expired, so the connector raises `CursorExpired` and core refetches fresh; a grant that lacks the
scope (`401`/`403`) yields `StreamSkipped` so the run records a skip, not a failure; a refusal
naming a usage limit instead of the grant raises (`ufo_ext_sources.providers.google`).

`calendar_events` folds an event's attendees onto its record and `render` lifts the title, time
range, location, attendees, and description into a readable body. `event_attendees` explodes each
event into one row per attendee (keyed `{event_id}:{handle}`), carrying the invitee's role,
response, and self flag. The credential is resolved through the auth proxy the runner threads —
this connector holds no token. The write path is intentionally absent — the source seam only
reads."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ufo.sdk.sources import (
    CursorExpired,
    RestConnector,
    Run,
    StreamPage,
    StreamSkipped,
    StreamSpec,
)
from ufo_ext_sources.providers import google

EVENTS_PATH = "/calendar/v3/calendars/primary/events"
LIST_PAGE_SIZE = 250
BOOTSTRAP_LOOKBACK_DAYS = 90
_RESPONSE_MAP = {
    "accepted": "accepted",
    "declined": "declined",
    "tentative": "tentative",
    "needsAction": "needs_action",
}

GOOGLE_CALENDAR_STREAMS: list[StreamSpec] = [
    StreamSpec(name="calendar_events", source_object="events", primary_key="id", canonical=True),
    StreamSpec(
        name="event_attendees",
        source_object="event_attendees",
        primary_key="id",
    ),
]


class GoogleCalendarConnector(RestConnector):
    name = "googlecalendar"
    base_url = "https://www.googleapis.com"
    streams_list = GOOGLE_CALENDAR_STREAMS

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[StreamPage]:
        if stream.name not in ("calendar_events", "event_attendees"):
            raise NotImplementedError(
                f"googlecalendar: stream {stream.name!r} has no paginate dispatch"
            )
        attendees_stream = stream.name == "event_attendees"
        params: dict[str, Any] = {"maxResults": LIST_PAGE_SIZE}
        if run.cursor:
            params["syncToken"] = run.cursor
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
                    cancelled = raw.get("status") == "cancelled"
                    if attendees_stream:
                        if not cancelled:
                            records.extend(_flatten_attendees(raw))
                        continue
                    if cancelled:
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
                raise CursorExpired(f"googlecalendar syncToken {run.cursor} expired") from error
            if google.refused_for_scope(error):
                raise StreamSkipped(
                    f"googlecalendar: {stream.name!r} refused ({error.response.status_code}); "
                    "the grant lacks the Calendar scope"
                ) from error
            raise

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        if stream.name != "calendar_events":
            return super().render(record, stream)
        title = _str(record.get("title"))
        lines = [f"# googlecalendar calendar_events: {title}".rstrip()]
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
        "created_at": raw.get("created"),
        "updated_at": raw.get("updated"),
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


def _flatten_attendees(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Explode one `events.list` item into one row per attendee, keyed `{event_id}:{handle}`, so an
    event's invitees sync as their own relational rows carrying role, response, and self flag."""
    event_id = raw["id"]
    organizer = raw.get("organizer") or {}
    organizer_email = organizer.get("email")
    organizer_handle = organizer_email.lower() if isinstance(organizer_email, str) else None
    rows: list[dict[str, Any]] = []
    for attendee in raw.get("attendees") or []:
        if not isinstance(attendee, dict) or not isinstance(attendee.get("email"), str):
            continue
        handle = attendee["email"].lower()
        response = attendee.get("responseStatus")
        rows.append(
            {
                "id": f"{event_id}:{handle}",
                "created_at": raw.get("created"),
                "updated_at": raw.get("updated"),
                "event_id": event_id,
                "role": _attendee_role(attendee, is_organizer=handle == organizer_handle),
                "handle": handle,
                "display_name": attendee.get("displayName"),
                "response": _RESPONSE_MAP.get(response) if isinstance(response, str) else None,
                "is_self": bool(attendee.get("self")),
            }
        )
    return rows


def _attendee_role(attendee: dict[str, Any], *, is_organizer: bool) -> str:
    if is_organizer or attendee.get("organizer"):
        return "organizer"
    if attendee.get("resource"):
        return "resource"
    if attendee.get("optional"):
        return "optional"
    return "required"


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
