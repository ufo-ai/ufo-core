"""The Granola connector — meeting notes as recallable prose, read over MCP tool executions.

Granola publishes no REST host: its whole surface is the MCP server Composio fronts as the
`granola_mcp` toolkit, so this connector is a `ToolConnector` rather than a `RestConnector` and
each read is a tool execution the broker runs server-side.

Two streams, `meetings` and `transcripts`. Both list the meeting notes in a time range
(`list_meetings`). `meetings` hydrates them in batches of `DETAIL_BATCH` through `get_meetings`,
which carries the attendees and the written notes the page recalls by, and lands a page for every
listed meeting, so the written notes sync through `list_meetings` and `get_meetings` alone and need
no paid tool. `transcripts` reads the spoken record instead, one call per meeting through
`get_meeting_transcript`, and lands a page only for a meeting that has one. An error naming
Granola's paid plans refuses every meeting on the account, so it raises `StreamSkipped` and holds
the transcript stream alone: `meetings` keeps syncing on a plan that answers no transcript. Any
other refusal is one meeting's own, so that meeting lands no record and the checkpoint passes it.

`list_meetings` takes a time range, never a page cursor, so the cursor is the created timestamp of
the newest meeting read: a first run reads the declared backfill window (`last_30_days`, or the
row's pinned floor as a custom range), and a later run lists from `RESYNC_LOOKBACK` behind the
cursor, because Granola writes the notes and the transcript after the meeting — a meeting read
before Granola finished it carries neither, and the re-read lands what was written on the same page
ref. Records are landed oldest first and each batch carries its own checkpoint, so a run capped
mid-window resumes where it stopped. A tool answer Composio reports unsuccessful raises
`StreamFault` naming the tool."""

import json
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from ufo.sdk.sources import (
    Run,
    StreamFault,
    StreamPage,
    StreamSkipped,
    StreamSpec,
    ToolConnector,
    ToolExecutor,
)

LIST_MEETINGS_TOOL = "GRANOLA_MCP_LIST_MEETINGS"
GET_MEETINGS_TOOL = "GRANOLA_MCP_GET_MEETINGS"
GET_TRANSCRIPT_TOOL = "GRANOLA_MCP_GET_MEETING_TRANSCRIPT"
MEETINGS_STREAM = "meetings"
TRANSCRIPTS_STREAM = "transcripts"
DETAIL_BATCH = 10
DEFAULT_TIME_RANGE = "last_30_days"
CUSTOM_TIME_RANGE = "custom"
BACKFILL_WINDOW_DAYS = 30
RESYNC_LOOKBACK = timedelta(days=1)

PLAN_GATE_MARKERS = ("plan", "upgrade", "subscription", "entitle")

RECORD_KEYS = ("meetings", "notes", "documents", "results", "items")
ID_FIELDS = ("id", "meeting_id", "document_id", "note_id")
TITLE_FIELDS = ("title", "name", "subject")
CREATED_FIELDS = ("created_at", "created", "date", "start_time", "started_at")
NOTES_FIELDS = ("notes", "summary", "summary_markdown", "notes_markdown", "content")
TRANSCRIPT_FIELDS = ("transcript", "transcript_markdown", "segments", "utterances", "text")
SPOKEN_FIELDS = ("text", "content", "value")
SPEAKER_FIELDS = ("speaker", "speaker_name", "name", "source")

GRANOLA_STREAMS: list[StreamSpec] = [
    StreamSpec(
        name=MEETINGS_STREAM,
        source_object="meetings",
        primary_key="id",
        cursor_field="created_at",
        created_at_field="created_at",
        updated_at_field=None,
        canonical=True,
        backfill_window_days=BACKFILL_WINDOW_DAYS,
    ),
    StreamSpec(
        name=TRANSCRIPTS_STREAM,
        source_object="transcripts",
        primary_key="id",
        cursor_field="created_at",
        created_at_field="created_at",
        updated_at_field=None,
        canonical=True,
        backfill_window_days=BACKFILL_WINDOW_DAYS,
    ),
]


class GranolaConnector(ToolConnector):
    name = "granola_mcp"
    streams_list = GRANOLA_STREAMS

    async def paginate(
        self, execute: ToolExecutor, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        if stream.name not in (MEETINGS_STREAM, TRANSCRIPTS_STREAM):
            raise NotImplementedError(f"granola_mcp: stream {stream.name!r} has no paginate")
        floor = _lookback(run.cursor)
        listed = await _call(execute, LIST_MEETINGS_TOOL, _range(floor, run.backfill_after))
        meetings = [meeting for record in _records(listed) if (meeting := _meeting(record))]
        fresh = sorted(
            (meeting for meeting in meetings if floor is None or meeting["created_at"] > floor),
            key=lambda meeting: meeting["created_at"],
        )
        if stream.name == TRANSCRIPTS_STREAM:
            async for page in self._transcribe(execute, fresh):
                yield page
            return
        for start in range(0, len(fresh), DETAIL_BATCH):
            batch = fresh[start : start + DETAIL_BATCH]
            records = await self._hydrate(execute, batch)
            yield StreamPage(records=records, next_cursor=batch[-1]["created_at"])

    async def _hydrate(
        self, execute: ToolExecutor, batch: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """One batch of listed meetings with their notes: `get_meetings` reads up to `DETAIL_BATCH`
        ids per call and answers attendees and summarized notes the listing does not carry. A
        meeting the detail call does not name keeps its listed fields, so one unreadable meeting
        does not hold back the page."""
        payload = await _call(
            execute, GET_MEETINGS_TOOL, {"meeting_ids": [meeting["id"] for meeting in batch]}
        )
        details = {
            detail["id"]: detail for record in _records(payload) if (detail := _meeting(record))
        }
        return [{**meeting, **details.get(meeting["id"], {})} for meeting in batch]

    async def _transcribe(
        self, execute: ToolExecutor, fresh: list[dict[str, Any]]
    ) -> AsyncIterator[StreamPage]:
        """The listed meetings as transcript pages: `get_meeting_transcript` reads one meeting per
        call, so `DETAIL_BATCH` calls is a checkpoint boundary rather than a single call. A meeting
        the tool refuses, and a meeting it answers with nothing said, land no record and the
        checkpoint passes them, as an unreadable meeting does in `_hydrate`; the lookback window is
        what reads a transcript Granola writes later. Only the plan gate, which refuses the first
        meeting of every run, skips the stream."""
        records: list[dict[str, Any]] = []
        checkpoint: str | None = None
        reads = 0
        for meeting in fresh:
            payload = await execute(GET_TRANSCRIPT_TOOL, {"meeting_id": meeting["id"]})
            if payload.get("successful") is False:
                error = str(payload.get("error") or "")
                if any(marker in error.lower() for marker in PLAN_GATE_MARKERS):
                    raise StreamSkipped(
                        f"granola_mcp: {GET_TRANSCRIPT_TOOL} is gated to Granola's paid plans: "
                        f"{error!r}"
                    )
            elif spoken := _transcript(payload):
                records.append({**meeting, "transcript": spoken})
            checkpoint = meeting["created_at"]
            reads += 1
            if reads == DETAIL_BATCH:
                yield StreamPage(records=records, next_cursor=checkpoint)
                records, checkpoint, reads = [], None, 0
        if checkpoint is not None:
            yield StreamPage(records=records, next_cursor=checkpoint)

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        if stream.name not in (MEETINGS_STREAM, TRANSCRIPTS_STREAM):
            return super().render(record, stream)
        title = str(record.get("title") or "")
        attendees = ", ".join(record.get("attendees") or [])
        content = "transcript" if stream.name == TRANSCRIPTS_STREAM else "notes"
        parts = [
            f"# granola_mcp {stream.name}: {title}".rstrip(),
            "\n".join(
                f"{label}: {value}"
                for label, value in (
                    ("created", str(record.get("created_at") or "")),
                    ("attendees", attendees),
                )
                if value
            ),
            str(record.get(content) or ""),
        ]
        return title, "\n\n".join(part for part in parts if part).strip()


def _range(cursor: str | None, backfill_after: datetime | None) -> dict[str, Any]:
    """The time range one run lists: from the cursor where there is one, else from the row's pinned
    floor, else the stream's declared window as Granola's own named range."""
    start = cursor or (None if backfill_after is None else _instant(backfill_after))
    if start is None:
        return {"time_range": DEFAULT_TIME_RANGE}
    return {
        "time_range": CUSTOM_TIME_RANGE,
        "custom_start": start,
        "custom_end": _instant(datetime.now(UTC)),
    }


def _lookback(cursor: str | None) -> str | None:
    """The floor a content stream lists from: `RESYNC_LOOKBACK` behind the cursor, so a meeting
    whose notes or transcript Granola wrote after the run that first read it is read again."""
    if cursor is None:
        return None
    return _instant(datetime.fromisoformat(cursor.replace("Z", "+00:00")) - RESYNC_LOOKBACK)


def _instant(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


async def _call(
    execute: ToolExecutor, tool: str, arguments: Mapping[str, Any]
) -> Mapping[str, Any]:
    payload = await execute(tool, arguments)
    if payload.get("successful") is False:
        raise StreamFault(f"granola_mcp: {tool} answered unsuccessfully: {payload.get('error')!r}")
    return payload


def _records(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every meeting object a tool answer carries. Composio wraps an MCP tool's result in `data`
    and the server names its own list differently per tool (and may hand it back as JSON text), so
    the reader takes the first list of objects under a known key at any depth rather than pinning
    one path."""
    queue: list[Any] = [payload]
    while queue:
        current = queue.pop(0)
        if isinstance(current, str):
            try:
                queue.append(json.loads(current))
            except ValueError:
                continue
        elif isinstance(current, Mapping):
            for key in RECORD_KEYS:
                value = current.get(key)
                if isinstance(value, list) and any(isinstance(item, dict) for item in value):
                    return [item for item in value if isinstance(item, dict)]
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)
    return []


def _transcript(payload: Mapping[str, Any]) -> str:
    """What was said in one meeting, as prose. Granola answers a transcript either as one block of
    text or as the speaker turns it was recorded in, under the same wrapping `data` envelope the
    listing arrives in, so the reader takes the first transcript value under a known key at any
    depth and renders speaker turns as `speaker: said` lines."""
    queue: list[Any] = [payload]
    while queue:
        current = queue.pop(0)
        if isinstance(current, str):
            try:
                queue.append(json.loads(current))
            except ValueError:
                continue
        elif isinstance(current, Mapping):
            for key in TRANSCRIPT_FIELDS:
                value = current.get(key)
                if isinstance(value, str) and value:
                    return value
                if isinstance(value, list) and (turns := _turns(value)):
                    return turns
            queue.extend(current.values())
        elif isinstance(current, list):
            queue.extend(current)
    return ""


def _turns(value: list[Any]) -> str:
    lines: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            lines.append(item)
        elif isinstance(item, Mapping):
            said = _field(item, SPOKEN_FIELDS)
            if not said:
                continue
            speaker = _field(item, SPEAKER_FIELDS)
            lines.append(f"{speaker}: {said}" if speaker else said)
    return "\n".join(lines)


def _meeting(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """One Granola note as this stream's record, or None where the answer carries no id or no
    created timestamp to order it by. Granola mints the id, so nothing here assumes its shape."""
    meeting_id = _field(record, ID_FIELDS)
    created = _field(record, CREATED_FIELDS)
    if not meeting_id or not created:
        return None
    meeting: dict[str, Any] = {
        "id": meeting_id,
        "title": _field(record, TITLE_FIELDS) or f"Granola meeting {meeting_id}",
        "created_at": created,
    }
    attendees = _attendees(record.get("attendees"))
    if attendees:
        meeting["attendees"] = attendees
    notes = _field(record, NOTES_FIELDS)
    if notes:
        meeting["notes"] = notes
    return meeting


def _field(record: Mapping[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = record.get(name)
        if isinstance(value, str) and value:
            return value
    return ""


def _attendees(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    people: list[str] = []
    for item in value:
        if isinstance(item, str) and item:
            people.append(item)
        elif isinstance(item, Mapping):
            named = _field(item, ("name", "display_name", "email"))
            if named:
                people.append(named)
    return people
