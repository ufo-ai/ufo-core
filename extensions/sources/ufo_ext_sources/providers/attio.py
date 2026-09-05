"""The Attio connector — CRM objects (companies, people, deals), tasks, notes, meetings, and call
recordings synced into recallable pages.

Attio splits its API across endpoint families the connector picks per stream. Standard/custom
objects read through `POST /v2/objects/<slug>/records/query` with offset paging; tasks and notes are
workspace-level resources at `/v2/tasks` and `/v2/notes` (offset-paged); meetings and call
recordings use the newer cursor-paged endpoints, and call recordings fan out per meeting with each
recording's transcript fetched inline. Attio exposes no uniform "last modified" slug, so every
stream is a full
snapshot (`delete_missing`) keyed idempotently by the record's composite id.

`flatten` is where this connector does its real work: an Attio record nests its identity under `id`
and every attribute under a `values` array of value-cells, so `flatten` lifts the id (`record_id` /
`task_id` / `note_id` / `meeting_id` / `call_recording_id`) to the top level and reduces each
value-cell to its natural primitive — without it the record has no top-level primary key. A disabled
standard object (`standard_object_disabled`) or a missing OAuth scope (`403 unauthorized`) yields
`StreamSkipped`. Auth is the OAuth bearer the resolved `Credential` carries. The write path is
intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec
from ufo_ext_sources.watermark import text_checkpoint

PAGE_LIMIT = 50  # Attio records-query limit cap
TASKS_PAGE_LIMIT = 500  # /v2/tasks default + max
NOTES_PAGE_LIMIT = 50  # /v2/notes max
MEETINGS_PAGE_LIMIT = 200  # /v2/meetings max
CALL_RECORDINGS_PAGE_LIMIT = 200  # /v2/meetings/{id}/call_recordings max


def _records_stream(name: str, *, object_slug: str, canonical: bool = True) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=object_slug,
        primary_key="record_id",
        cursor_field=None,
        delete_missing=True,
        canonical=canonical,
    )


ATTIO_STREAMS: list[StreamSpec] = [
    _records_stream("companies", object_slug="companies"),
    _records_stream("people", object_slug="people"),
    _records_stream("deals", object_slug="deals"),
    StreamSpec(name="tasks", source_object="tasks", primary_key="task_id", delete_missing=True),
    StreamSpec(
        name="notes",
        source_object="notes",
        primary_key="note_id",
        delete_missing=True,
        canonical=False,
    ),
    StreamSpec(
        name="meetings", source_object="meetings", primary_key="meeting_id", delete_missing=True
    ),
    StreamSpec(
        name="call_recordings",
        source_object="call_recordings",
        primary_key="call_recording_id",
        delete_missing=True,
    ),
]


def _nested_id(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


class AttioConnector(RestConnector):
    name = "attio"
    base_url = "https://api.attio.com"
    streams_list = ATTIO_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    @staticmethod
    def _build_query_body(offset: int) -> dict[str, Any]:
        return {"limit": PAGE_LIMIT, "offset": offset}

    @staticmethod
    def _value_primitive(item: dict[str, Any]) -> Any:
        """Pick the natural primitive out of an Attio value-cell — attribute types put the data on
        differently-named keys (text/number → `value`, select → `option.title`, reference →
        `target_record_id`, and so on)."""
        if "value" in item:
            return item["value"]
        option = item.get("option")
        if isinstance(option, dict):
            return option.get("title") or _nested_id(option.get("id"), "option_id")
        status = item.get("status")
        if isinstance(status, dict):
            return status.get("title") or _nested_id(status.get("id"), "status_id")
        if "email_address" in item:
            return item["email_address"]
        if "phone_number" in item:
            return item["phone_number"]
        if "domain" in item:
            return item["domain"] or item.get("root_domain")
        if "currency_value" in item:
            return item["currency_value"]
        if "target_record_id" in item:
            rid = item["target_record_id"]
            return f"attio:{rid}" if rid else None
        if "interacted_at" in item:
            return item["interacted_at"]
        if "referenced_actor_type" in item:
            actor_id = item.get("referenced_actor_id")
            actor_type = item.get("referenced_actor_type")
            if actor_id and actor_type:
                return f"{actor_type}:{actor_id}"
            return actor_id or actor_type
        if item.get("attribute_type") == "location":
            parts = [
                item.get("line_1"),
                item.get("line_2"),
                item.get("line_3"),
                item.get("line_4"),
                item.get("locality"),
                item.get("region"),
                item.get("postcode"),
                item.get("country_code"),
            ]
            return ", ".join(str(part) for part in parts if part) or None
        return None

    @classmethod
    def _flatten_cell(cls, cell: Any) -> Any:
        if isinstance(cell, list):
            primitives = [
                cls._value_primitive(item) if isinstance(item, dict) else item for item in cell
            ]
            primitives = [p for p in primitives if p is not None]
            if not primitives:
                return None
            if len(primitives) > 1 and all(
                isinstance(item, dict) and isinstance(item.get("option"), dict) for item in cell
            ):
                return primitives
            return primitives[0]
        if isinstance(cell, dict):
            return cls._value_primitive(cell)
        return cell

    @classmethod
    def _flatten_list_cell(cls, cell: Any) -> list[Any]:
        if not isinstance(cell, list):
            value = cls._flatten_cell(cell)
            return [] if value is None else [value]
        primitives = [
            cls._value_primitive(item) if isinstance(item, dict) else item for item in cell
        ]
        return [p for p in primitives if p is not None]

    @classmethod
    def _flatten_values(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Walk every attribute Attio returned and lift the primitive out of its value-cell."""
        out: dict[str, Any] = {}
        for slug, cell in values.items():
            if not cell:
                out[slug] = None
                continue
            if slug in {"domains", "categories", "email_addresses", "phone_numbers"}:
                out[slug] = cls._flatten_list_cell(cell)
            else:
                out[slug] = cls._flatten_cell(cell)

        cell = values.get("name")
        if cell:
            first = cell[0] if isinstance(cell, list) and cell else cell
            if isinstance(first, dict):
                if first.get("first_name"):
                    out["first_name"] = first["first_name"]
                if first.get("last_name"):
                    out["last_name"] = first["last_name"]
                if first.get("full_name"):
                    out["name"] = first["full_name"]
        domains = out.get("domains")
        if isinstance(domains, list) and domains:
            out["domain"] = domains[0]
        categories = out.get("categories")
        if isinstance(categories, list) and categories:
            out["category"] = categories[0]
        emails = out.get("email_addresses")
        if isinstance(emails, list) and emails:
            out["email"] = emails[0]
        phones = out.get("phone_numbers")
        if isinstance(phones, list) and phones:
            out["phone"] = phones[0]
        return out

    @classmethod
    def _flatten_record(cls, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        ident = record.get("id") or {}
        flat: dict[str, Any] = {
            "record_id": ident.get("record_id") if isinstance(ident, dict) else ident,
            "object_id": ident.get("object_id") if isinstance(ident, dict) else None,
            "workspace_id": ident.get("workspace_id") if isinstance(ident, dict) else None,
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
        }
        if stream.cursor_field:
            flat[stream.cursor_field] = record.get(stream.cursor_field) or record.get(
                "last_modified"
            )
        flat.update(cls._flatten_values(record.get("values") or {}))
        return flat

    @staticmethod
    def _flatten_task(record: dict[str, Any]) -> dict[str, Any]:
        ident = record.get("id") or {}
        out = dict(record)
        out["task_id"] = ident.get("task_id") if isinstance(ident, dict) else ident
        return out

    @staticmethod
    def _flatten_note(record: dict[str, Any]) -> dict[str, Any]:
        ident = record.get("id") or {}
        out = dict(record)
        out["note_id"] = ident.get("note_id") if isinstance(ident, dict) else ident
        return out

    @classmethod
    def _flatten_meeting(cls, record: dict[str, Any]) -> dict[str, Any]:
        ident = record.get("id") or {}
        out = dict(record)
        out["meeting_id"] = ident.get("meeting_id") if isinstance(ident, dict) else ident
        return out

    @classmethod
    def _flatten_call_recording(cls, record: dict[str, Any]) -> dict[str, Any]:
        """Lift `id.call_recording_id`, join the inline transcript segments into `transcript_text`,
        and fall `recording_url` back to `web_url` when no signed asset URL is on the row."""
        ident = record.get("id") or {}
        out = dict(record)
        out["call_recording_id"] = (
            ident.get("call_recording_id") if isinstance(ident, dict) else ident
        )
        if "recording_url" not in out and out.get("web_url"):
            out["recording_url"] = out["web_url"]
        segments = record.get("transcript") or []
        if isinstance(segments, list) and segments:
            parts: list[str] = []
            for seg in segments:
                if not isinstance(seg, dict):
                    continue
                speaker_obj = seg.get("speaker") if isinstance(seg.get("speaker"), dict) else None
                speaker = speaker_obj.get("name") if speaker_obj else None
                speech = seg.get("speech") or ""
                parts.append(f"{speaker}: {speech}" if speaker else str(speech))
            out["transcript_text"] = "\n".join(p for p in parts if p)
        return out

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        if stream.name == "tasks":
            return self._flatten_task(record)
        if stream.name == "notes":
            return self._flatten_note(record)
        if stream.name == "meetings":
            return self._flatten_meeting(record)
        if stream.name == "call_recordings":
            return self._flatten_call_recording(record)
        return self._flatten_record(record, stream)

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            async for page in self._pages(client, stream):
                yield page
        except httpx.HTTPStatusError as error:
            if self._is_scope_unauthorized(error):
                raise StreamSkipped(self._scope_skip_reason(error)) from error
            raise

    def _pages(
        self, client: httpx.AsyncClient, stream: StreamSpec
    ) -> AsyncIterator[list[dict[str, Any]]]:
        match stream.name:
            case "tasks":
                return self._paginate_simple(client, "/v2/tasks", page_size=TASKS_PAGE_LIMIT)
            case "notes":
                return self._paginate_simple(client, "/v2/notes", page_size=NOTES_PAGE_LIMIT)
            case "meetings":
                return self._paginate_cursor(client, "/v2/meetings", page_size=MEETINGS_PAGE_LIMIT)
            case "call_recordings":
                return self._paginate_call_recordings(client)
            case _:
                return self._paginate_records(client, stream)

    async def _paginate_records(
        self, client: httpx.AsyncClient, stream: StreamSpec
    ) -> AsyncIterator[list[dict[str, Any]]]:
        path = f"/v2/objects/{stream.source_object}/records/query"
        offset = 0
        while True:
            body = self._build_query_body(offset)
            try:
                data = await self._post(client, path, json=body)
            except httpx.HTTPStatusError as error:
                if self._is_object_disabled(error):
                    raise StreamSkipped(
                        f"standard object {stream.source_object!r} is disabled"
                    ) from error
                raise
            records = data.get("data", []) or []
            if not records:
                return
            yield records
            if len(records) < PAGE_LIMIT:
                return
            offset += len(records)

    async def _paginate_simple(
        self, client: httpx.AsyncClient, path: str, *, page_size: int
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Offset-paged GET for `/v2/tasks` + `/v2/notes` (`limit` + `offset` → `{data: [...]}`)."""
        async for page in self._get_offset_pages(
            client, path, records_path="data", limit=page_size
        ):
            yield page

    async def _paginate_cursor(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        page_size: int,
        params: dict[str, Any] | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Cursor-paged GET for `/v2/meetings` + `/v2/.../call_recordings` (`{data, pagination:
        {next_cursor}}`, fed back as `?cursor=`)."""
        async for page in self._get_cursor_pages(
            client,
            path,
            records_path="data",
            next_cursor_path="pagination.next_cursor",
            params=params,
            page_size=page_size,
        ):
            yield page

    async def _paginate_call_recordings(
        self, client: httpx.AsyncClient
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Per-meeting fan-out: walk every meeting, list its recordings, fetch each recording's
        transcript inline, and stamp parent-meeting context on each row."""
        async for meeting_page in self._paginate_cursor(
            client, "/v2/meetings", page_size=MEETINGS_PAGE_LIMIT
        ):
            for meeting in meeting_page:
                meeting_id = self._meeting_id(meeting)
                if not meeting_id:
                    continue
                title = meeting.get("title")
                start_at = self._datetime_of(meeting.get("start"))
                end_at = self._datetime_of(meeting.get("end"))
                duration = self._duration_seconds(start_at, end_at)
                async for rec_page in self._paginate_cursor(
                    client,
                    f"/v2/meetings/{meeting_id}/call_recordings",
                    page_size=CALL_RECORDINGS_PAGE_LIMIT,
                ):
                    for rec in rec_page:
                        rec["parent_meeting_id"] = meeting_id
                        rec.setdefault("title", title)
                        rec.setdefault("starts_at", start_at)
                        rec.setdefault("ends_at", end_at)
                        if duration is not None:
                            rec.setdefault("duration", duration)
                        rec_id = self._call_recording_id(rec)
                        if rec_id:
                            transcript = await self._fetch_transcript(
                                client, meeting_id=meeting_id, recording_id=rec_id
                            )
                            if transcript is not None:
                                rec["transcript"] = transcript.get("transcript") or []
                                rec["raw_transcript"] = transcript.get("raw_transcript")
                    yield rec_page

    async def _fetch_transcript(
        self, client: httpx.AsyncClient, *, meeting_id: str, recording_id: str
    ) -> dict[str, Any] | None:
        """GET a recording's transcript; None when the recording isn't ready (404 / 409)."""
        path = f"/v2/meetings/{meeting_id}/call_recordings/{recording_id}/transcript"
        try:
            data = await self._get(client, path)
        except httpx.HTTPStatusError as error:
            if error.response.status_code in (404, 409):
                return None
            raise
        body = data.get("data")
        return body if isinstance(body, dict) else None

    @staticmethod
    def _meeting_id(meeting: dict[str, Any]) -> str | None:
        ident = meeting.get("id") or {}
        if isinstance(ident, dict):
            return ident.get("meeting_id")
        return ident if isinstance(ident, str) else None

    @staticmethod
    def _call_recording_id(rec: dict[str, Any]) -> str | None:
        ident = rec.get("id") or {}
        if isinstance(ident, dict):
            return ident.get("call_recording_id")
        return ident if isinstance(ident, str) else None

    @staticmethod
    def _datetime_of(timeshape: Any) -> str | None:
        """Attio's meeting start/end is `{datetime, timezone}` (timed) or `{date}` (all-day)."""
        if not isinstance(timeshape, dict):
            return None
        return timeshape.get("datetime") or timeshape.get("date") or None

    @staticmethod
    def _duration_seconds(start_at: str | None, end_at: str | None) -> float | None:
        """Coarse duration in seconds from ISO 8601 start/end; None if either is missing or
        unparseable."""
        if not start_at or not end_at:
            return None
        try:
            start = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        return max(0.0, (end - start).total_seconds())

    @staticmethod
    def _is_object_disabled(error: httpx.HTTPStatusError) -> bool:
        """True if Attio rejected the request because the standard object isn't enabled in this
        workspace (`standard_object_disabled`)."""
        if error.response.status_code != 400:
            return False
        try:
            body = error.response.json()
        except ValueError:
            return False
        return isinstance(body, dict) and body.get("code") == "standard_object_disabled"

    @staticmethod
    def _is_scope_unauthorized(error: httpx.HTTPStatusError) -> bool:
        """True if Attio rejected the request for a missing OAuth scope (`403` with `code:
        unauthorized`)."""
        if error.response.status_code != 403:
            return False
        try:
            body = error.response.json()
        except ValueError:
            return False
        return isinstance(body, dict) and body.get("code") == "unauthorized"

    @staticmethod
    def _scope_skip_reason(error: httpx.HTTPStatusError) -> str:
        try:
            body = error.response.json()
        except ValueError:
            body = {}
        message = body.get("message") if isinstance(body, dict) else None
        return f"OAuth grant missing required scope ({message or 'see upstream response'})"
