"""The Google Meet connector - generated meeting transcripts and AI summaries as recallable prose.

Google Meet exposes generated meeting artifacts through the Meet REST API: conference records,
transcript sessions, per-speaker transcript entries, and smart notes ("Take notes with Gemini")
metadata. A conference record is the meeting-shaped unit a manager wants to recall, so this source
emits one page per conference that has at least one generated transcript or smart-note session.

The stream lists `conferenceRecords` ordered by start time descending. Incremental sync filters by
`start_time` with a short lookback because transcript and smart-note files can be generated after
the conference ends; a refetched conference settles on the same page ref and digest. The cursor
still advances across conferences with no artifacts, so the driver does not rescan the full retained
window forever. Transcript entries are retained by the Meet API only for the current conference
record window, so the rendered page also carries the durable Google Docs destination for transcripts
and smart notes. When the same grant can read the smart-notes Google Doc, the connector inlines its
plain text; if Docs refuses or the file disappeared, the page keeps the link instead of failing the
run. A Meet API refusal (`401`/`403`) records the stream as skipped, not failed."""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from ufo.sdk.sources import RestConnector, StreamPage, StreamSkipped, StreamSpec, list_or_empty

MEET_API_BASE = "https://meet.googleapis.com"
DOCS_API_URL = "https://docs.googleapis.com/v1"
CONFERENCE_RECORDS_PATH = "/v2/conferenceRecords"
CONFERENCE_PAGE_SIZE = 100
ARTIFACT_PAGE_SIZE = 100
ENTRY_PAGE_SIZE = 100
RESYNC_LOOKBACK = timedelta(days=1)
_REFUSAL_STATUS = frozenset({401, 403})
_DOC_MISSING_STATUS = frozenset({403, 404})

GOOGLE_MEET_STREAMS: list[StreamSpec] = [
    StreamSpec(
        name="meeting_artifacts",
        source_object="conferenceRecords",
        primary_key="id",
        cursor_field="start_time",
    ),
]


class GoogleMeetConnector(RestConnector):
    name = "googlemeet"
    base_url = MEET_API_BASE
    streams_list = GOOGLE_MEET_STREAMS

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[StreamPage]:
        if stream.name != "meeting_artifacts":
            raise NotImplementedError(
                f"googlemeet: stream {stream.name!r} has no paginate dispatch"
            )
        params: dict[str, Any] = {"pageSize": CONFERENCE_PAGE_SIZE}
        if cursor:
            params["filter"] = f'start_time >= "{_lookback(cursor)}"'
        token: str | None = None
        try:
            while True:
                request_params = dict(params)
                if token:
                    request_params["pageToken"] = token
                data = await self._get(client, CONFERENCE_RECORDS_PATH, params=request_params)
                conferences = list_or_empty(data.get("conferenceRecords"))
                records: list[dict[str, Any]] = []
                next_cursor = _max_start_time(conferences, cursor)
                for conference in conferences:
                    record = await self._conference_record(client, conference)
                    if record["transcripts"] or record["smart_notes"]:
                        records.append(record)
                yield StreamPage(records=records, next_cursor=next_cursor)
                token = data.get("nextPageToken")
                if not isinstance(token, str) or not token:
                    return
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"googlemeet: {stream.name!r} refused ({error.response.status_code}); "
                    "the grant lacks the Google Meet scope or cannot read these artifacts"
                ) from error
            raise

    async def _conference_record(
        self, client: httpx.AsyncClient, conference: dict[str, Any]
    ) -> dict[str, Any]:
        name = _str(conference.get("name"))
        transcripts = [
            await self._transcript(client, transcript)
            for transcript in await self._artifacts(client, name, "transcripts")
        ]
        smart_notes = [
            await self._smart_note(client, note)
            for note in await self._artifacts(client, name, "smartNotes")
        ]
        conference_id = _resource_id(name)
        return {
            "id": conference_id,
            "title": f"Google Meet {conference_id}".rstrip(),
            "conference_name": name,
            "space": conference.get("space"),
            "start_time": conference.get("startTime"),
            "end_time": conference.get("endTime"),
            "expire_time": conference.get("expireTime"),
            "transcripts": transcripts,
            "smart_notes": smart_notes,
        }

    async def _artifacts(
        self, client: httpx.AsyncClient, parent: str, collection: str
    ) -> list[dict[str, Any]]:
        if not parent:
            return []
        path = f"/v2/{parent}/{collection}"
        key = "smartNotes" if collection == "smartNotes" else collection
        artifacts: list[dict[str, Any]] = []
        token: str | None = None
        while True:
            params: dict[str, Any] = {"pageSize": ARTIFACT_PAGE_SIZE}
            if token:
                params["pageToken"] = token
            data = await self._get(client, path, params=params)
            artifacts.extend(list_or_empty(data.get(key)))
            token = data.get("nextPageToken")
            if not isinstance(token, str) or not token:
                return artifacts

    async def _transcript(
        self, client: httpx.AsyncClient, transcript: dict[str, Any]
    ) -> dict[str, Any]:
        name = _str(transcript.get("name"))
        return {
            "id": _resource_id(name),
            "name": name,
            "state": transcript.get("state"),
            "start_time": transcript.get("startTime"),
            "end_time": transcript.get("endTime"),
            **_docs_destination(transcript),
            "entries": await self._transcript_entries(client, name),
        }

    async def _transcript_entries(
        self, client: httpx.AsyncClient, transcript_name: str
    ) -> list[dict[str, Any]]:
        if not transcript_name:
            return []
        entries: list[dict[str, Any]] = []
        token: str | None = None
        path = f"/v2/{transcript_name}/entries"
        try:
            while True:
                params: dict[str, Any] = {"pageSize": ENTRY_PAGE_SIZE}
                if token:
                    params["pageToken"] = token
                data = await self._get(client, path, params=params)
                for entry in list_or_empty(data.get("transcriptEntries")):
                    name = _str(entry.get("name"))
                    entries.append(
                        {
                            "id": _resource_id(name),
                            "name": name,
                            "participant": entry.get("participant"),
                            "text": entry.get("text"),
                            "language_code": entry.get("languageCode"),
                            "start_time": entry.get("startTime"),
                            "end_time": entry.get("endTime"),
                        }
                    )
                token = data.get("nextPageToken")
                if not isinstance(token, str) or not token:
                    return entries
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _DOC_MISSING_STATUS:
                return entries
            raise

    async def _smart_note(self, client: httpx.AsyncClient, note: dict[str, Any]) -> dict[str, Any]:
        name = _str(note.get("name"))
        record = {
            "id": _resource_id(name),
            "name": name,
            "state": note.get("state"),
            "start_time": note.get("startTime"),
            "end_time": note.get("endTime"),
            **_docs_destination(note),
        }
        doc = record.get("docs_document")
        if isinstance(doc, str) and doc:
            body = await self._document_text(client, doc)
            if body:
                record["body"] = body
        return record

    async def _document_text(self, client: httpx.AsyncClient, document_id: str) -> str:
        try:
            document = await self._get(
                client, f"{DOCS_API_URL}/documents/{quote(document_id, safe='')}"
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _DOC_MISSING_STATUS:
                return ""
            raise
        return _plain_text(document)

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        if stream.name != "meeting_artifacts":
            return super().render(record, stream)
        title = _str(record.get("title"))
        parts = [
            f"# googlemeet meeting_artifacts: {title}".rstrip(),
            _labeled(
                [
                    ("conference", _str(record.get("conference_name"))),
                    ("space", _str(record.get("space"))),
                    ("start", _str(record.get("start_time"))),
                    ("end", _str(record.get("end_time"))),
                ]
            ),
            _transcripts_section(record.get("transcripts")),
            _smart_notes_section(record.get("smart_notes")),
        ]
        return title, "\n\n".join(part for part in parts if part).strip()


def _transcripts_section(value: Any) -> str:
    transcripts = list_or_empty(value)
    if not transcripts:
        return ""
    sections = ["## Transcripts"]
    for transcript in transcripts:
        meta = _labeled(
            [
                ("state", _str(transcript.get("state"))),
                ("start", _str(transcript.get("start_time"))),
                ("end", _str(transcript.get("end_time"))),
                ("doc", _str(transcript.get("docs_url"))),
            ]
        )
        dialogue = _dialogue(transcript.get("entries"))
        sections.append("\n\n".join(part for part in (meta, dialogue) if part).strip())
    return "\n\n".join(section for section in sections if section).strip()


def _smart_notes_section(value: Any) -> str:
    notes = list_or_empty(value)
    if not notes:
        return ""
    sections = ["## AI summaries"]
    for note in notes:
        meta = _labeled(
            [
                ("state", _str(note.get("state"))),
                ("start", _str(note.get("start_time"))),
                ("end", _str(note.get("end_time"))),
                ("doc", _str(note.get("docs_url"))),
            ]
        )
        body = _str(note.get("body")).strip()
        sections.append("\n\n".join(part for part in (meta, body) if part).strip())
    return "\n\n".join(section for section in sections if section).strip()


def _dialogue(value: Any) -> str:
    lines: list[str] = []
    speaker: str | None = None
    for entry in list_or_empty(value):
        text = _str(entry.get("text")).strip()
        if not text:
            continue
        name = _speaker(entry.get("participant"))
        if name == speaker and lines:
            lines[-1] = f"{lines[-1]} {text}"
        else:
            lines.append(f"{name}: {text}")
            speaker = name
    return "\n".join(lines)


def _plain_text(record: dict[str, Any]) -> str:
    body = record.get("body")
    content = body.get("content") if isinstance(body, dict) else None
    chunks: list[str] = []
    for item in content if isinstance(content, list) else []:
        paragraph = item.get("paragraph") if isinstance(item, dict) else None
        if not isinstance(paragraph, dict):
            continue
        for element in paragraph.get("elements") or []:
            run = element.get("textRun") if isinstance(element, dict) else None
            text = run.get("content") if isinstance(run, dict) else None
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks).strip()


def _docs_destination(record: dict[str, Any]) -> dict[str, str]:
    destination = record.get("docsDestination")
    if not isinstance(destination, dict):
        return {}
    return {
        "docs_document": _str(destination.get("document")),
        "docs_url": _str(destination.get("exportUri")),
    }


def _max_start_time(conferences: list[dict[str, Any]], cursor: str | None) -> str | None:
    current = cursor
    for conference in conferences:
        start = conference.get("startTime")
        if isinstance(start, str) and (current is None or start > current):
            current = start
    return current


def _lookback(cursor: str) -> str:
    moment = datetime.fromisoformat(cursor.replace("Z", "+00:00")) - RESYNC_LOOKBACK
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _resource_id(name: str) -> str:
    return name.rsplit("/", 1)[-1] if name else ""


def _speaker(value: Any) -> str:
    name = _str(value)
    return _resource_id(name) or "Participant"


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _labeled(pairs: list[tuple[str, str]]) -> str:
    return "\n".join(f"{label}: {value}" for label, value in pairs if value)
