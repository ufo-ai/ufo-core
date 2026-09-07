"""The Google Sheets connector — spreadsheets, their sheet tabs, and each tab's cell values synced
as recallable content.

Reads are a Drive-list→Sheets-get fan-out: `GET /drive/v3/files` enumerates the grant's
spreadsheets (filtered to the spreadsheet mime type, untrashed, ordered by `modifiedTime`), and each
file id is read through the Sheets API (`GET /v4/spreadsheets/{id}`) for its title and tab list. The
three streams are incremental on the file's Drive `modifiedTime` — the Drive query filters
server-side at or past the stored value, each record carries that time as a flat `updated_at`, and
each page reports as its own cursor the highest `modifiedTime` the run has landed, never below the
stored one. `sheets` explodes each spreadsheet into one record per tab; `sheet_values` reads tab
grids in bounded `values:batchGet` calls so a synced sheet recalls as its rows, every range naming
its tab as a quoted A1 sheet reference with each apostrophe doubled — an unquoted title reads as a
cell reference against the first visible sheet, as a named range, or fails to parse. The batch
answers one `valueRange` per requested range in the order asked, so each grid lands on the tab at
its position; the echoed `range` is the resolved A1 bounds of what the request named, not the name,
so it identifies nothing and a count other than the one asked for is the whole fault. A batch
refusal naming one file falls back to individual tab reads to isolate the refused tab. Both derived
streams stamp their parent file's times onto every record, and a page reports its cursor even where
a file lands no derived record at all. The watermark is the file's, so a change Drive does not stamp
on `modifiedTime` leaves that file's tabs as they last synced.

A file the grant refuses, on its metadata or on one of its tabs, travels beside that watermark
rather than holding it down: the page reports a `_Checkpoint` — the watermark the listing reached,
plus the ids of the refused files — and a run that drains its listing then re-fetches carried ids
the listing did not reach, one Drive `files.get` and that file's Sheets reads each, so a grant
arriving later lands the tabs the refusal dropped. The watermark folds the listing's
`modifiedTime`s alone — a carried `files.get` stamps its own file's times onto that file's records
and never onto the cursor — so the reported value covers exactly the listing prefix the run drained.
A carried id the listing does reach is settled there, and the carry re-fetches it no second time.
The carried `files.get` projects `trashed`, so a carried id the member trashes leaves the set, as
one Drive answers `404` for does.

A carried retry's record count is a function of the grant, not of a stable enumeration, so it cannot
sit inside a skip count (`core/src/ufo/runtime/sources/backend.py`: "the connector must reproduce
the same record sequence for the skip count to be sound"). Hence `retried` and one page per carried
id: every
carried page reports a cursor the page before it did not, which is what ends a run past the cap at
that page rather than counting on through the tail, and the count such a run stores spans listed
records. A run resuming on a stored count re-drives the listing prefix it discards and lands the
carry behind it. One shape still spans a count — a carried file landing past the adapter's overrun
ceiling inside its own page — and there those records land in that run and the resume re-fetches and
discards them. A resume whose re-driven listing has shrunk below the stored count discards the
carried page with the prefix and settles the id on records the adapter dropped: `fetch` hands
`paginate` the origin cursor alone, so that run and the healed run whose cursor and requests it
matches byte for byte admit no connector-side distinction. That file's tabs land on the run Drive
next stamps its `modifiedTime`, which is where a refusal leaves them with no carry at all.

The listing is ordered by `modifiedTime` and files share values, so the filter's bound is inclusive:
a run whose record cap ends inside a group of files sharing one `modifiedTime` reports that value,
and the next run re-lists the whole group rather than dropping the files past the split. A re-listed
file settles on the page ref it already holds, so the repeat lands it once. Drive's `orderBy` takes
no file id and its `name` term takes no ordering operator, so no cursor names a position inside such
a group; the run pays for the inclusive bound by re-reading the files tied at the corpus maximum
every run.

A refusal is classified by what it names. One naming the grant or the API — a missing Drive or
Sheets scope, a Sheets API disabled for the project — yields `StreamSkipped` wherever it arrives, on
the Drive list or on any file's Sheets get, so the run records a skip with its stored cursor held.
Google names those two ways: a `google.rpc.ErrorInfo` detail in the `googleapis.com` domain, which
[Service Infrastructure reserves for the credential, the project and the service](https://github.com/googleapis/googleapis/blob/master/google/api/error_reason.proto)
and never for one file, or an `errors[].reason` of `accessNotConfigured` or
`insufficientPermissions`. One naming a single file — a `403` "The caller does not have permission",
a deleted file's `404` `notFound` — falls back to that file's Drive metadata instead, which is all
its tabs ever amount to, and on a tab's grid it drops that tab's rows while the rest of the stream
lands. Only a Google API error body earns that fallback: a refusal carrying anything else comes from
in front of the API, where nothing marks it as being about the one file the request named, so a
`403` skips the stream as any `403` naming nothing does, and a `404` raises — a misrouted path is
not a source the grant cannot read. A quota refusal is neither, though it arrives on the same
statuses: it names a usage-limit reason (`RESOURCE_EXHAUSTED`, or one of Google's `usageLimits` 403
reasons) and raises, failing the run with its stored cursor held.

`render` lifts a spreadsheet's tab titles, a tab's name, and a grid's rows into a readable body. The
credential is resolved through the auth proxy the runner threads — this connector holds no token.
The write path is intentionally absent — the source seam only reads."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from ufo.sdk.sources import (
    RestConnector,
    StreamFault,
    StreamPage,
    StreamSkipped,
    StreamSpec,
    list_or_empty,
)
from ufo_ext_sources.providers import google
from ufo_ext_sources.watermark import text_checkpoint

SHEET_MIME = "application/vnd.google-apps.spreadsheet"
SHEETS_API_URL = "https://sheets.googleapis.com/v4"
DRIVE_PAGE_SIZE = 1000
PAGE_SIZE = 100
REFUSED_LIMIT = 50
VALUES_BATCH_SIZE = 50
_METADATA_FALLBACK_STATUS = frozenset({403, 404})
_SERVICE_ERROR_DOMAIN = "googleapis.com"
_GRANT_REASONS = frozenset({"accessNotConfigured", "insufficientPermissions"})
DRIVE_FILE_FIELDS = "id,name,webViewLink,createdTime,modifiedTime,owners(emailAddress,displayName)"
DRIVE_FIELDS = f"nextPageToken,files({DRIVE_FILE_FIELDS})"
DRIVE_CARRIED_FIELDS = f"{DRIVE_FILE_FIELDS},trashed"

GOOGLE_SHEETS_STREAMS: list[StreamSpec] = [
    StreamSpec(
        name="spreadsheets",
        source_object="spreadsheets",
        primary_key="spreadsheetId",
        cursor_field="updated_at",
        updated_at_field="updated_at",
        canonical=True,
    ),
    StreamSpec(
        name="sheets",
        source_object="sheets",
        primary_key="id",
        cursor_field="updated_at",
    ),
    StreamSpec(
        name="sheet_values",
        source_object="values",
        primary_key="id",
        cursor_field="updated_at",
        canonical=True,
    ),
]


class _Checkpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    watermark: str
    refused: list[str]
    retried: str | None = None


@dataclass(frozen=True)
class _FileVisit:
    file_id: str
    record: dict[str, Any] | None
    refused: bool


class GoogleSheetsConnector(RestConnector):
    name = "googlesheets"
    base_url = "https://www.googleapis.com"
    streams_list = GOOGLE_SHEETS_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[StreamPage]:
        watermark, carried, retried = _decode_cursor(cursor)
        page: list[dict[str, Any]] = []
        reported = watermark
        refused_ids = set(carried)
        listed: set[str] = set()
        last = cursor
        try:
            async for visit in self._spreadsheet_visits(client, watermark=watermark):
                listed.add(visit.file_id)
                records, refused = await self._visit_records(client, stream, visit)
                page.extend(records)
                refused_ids = _settled(refused_ids, visit.file_id, refused)
                modified = (visit.record or {}).get("updated_at")
                if isinstance(modified, str):
                    reported = modified if reported is None else max(reported, modified)
                if len(page) >= PAGE_SIZE:
                    last = _encode_cursor(reported, refused_ids, retried)
                    yield StreamPage(records=page, next_cursor=last)
                    page = []
            if page:
                last = _encode_cursor(reported, refused_ids, retried)
                yield StreamPage(records=page, next_cursor=last)
                page = []
            for file_id in carried:
                if retried is not None and file_id <= retried:
                    continue
                retried = file_id
                if file_id in listed:
                    continue
                visit = await self._carried_visit(client, file_id)
                records, refused = await self._visit_records(client, stream, visit)
                page.extend(records)
                refused_ids = _settled(refused_ids, file_id, refused)
                last = _encode_cursor(reported, refused_ids, retried)
                yield StreamPage(records=page, next_cursor=last)
                page = []
            retried = None
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if google.refused_for_scope(error):
                raise StreamSkipped(
                    f"googlesheets: {stream.name!r} refused ({status}); the grant cannot read "
                    "Drive or Sheets"
                ) from error
            raise
        checkpoint = _encode_cursor(reported, refused_ids, retried)
        if checkpoint != last:
            yield StreamPage(next_cursor=checkpoint)

    async def _iter_spreadsheet_files(
        self, client: httpx.AsyncClient, *, watermark: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        token: str | None = None
        query = f"mimeType = '{SHEET_MIME}' and trashed = false"
        if watermark:
            query = f"{query} and modifiedTime >= '{watermark}'"
        while True:
            params: dict[str, Any] = {
                "pageSize": DRIVE_PAGE_SIZE,
                "q": query,
                "orderBy": "modifiedTime",
                "fields": DRIVE_FIELDS,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "corpora": "allDrives",
            }
            if token:
                params["pageToken"] = token
            data = await self._get(client, "/drive/v3/files", params=params)
            records = list_or_empty(data.get("files"))
            if records:
                yield records
            token = data.get("nextPageToken")
            if not isinstance(token, str) or not token:
                return

    async def _spreadsheet_visits(
        self, client: httpx.AsyncClient, *, watermark: str | None
    ) -> AsyncIterator[_FileVisit]:
        async for files in self._iter_spreadsheet_files(client, watermark=watermark):
            for file in files:
                spreadsheet_id = file.get("id")
                if not isinstance(spreadsheet_id, str) or not spreadsheet_id:
                    continue
                yield await self._file_visit(client, spreadsheet_id, file)

    async def _carried_visit(self, client: httpx.AsyncClient, file_id: str) -> _FileVisit:
        try:
            file = await self._get(
                client,
                f"/drive/v3/files/{file_id}",
                params={"fields": DRIVE_CARRIED_FIELDS, "supportsAllDrives": "true"},
            )
        except httpx.HTTPStatusError as error:
            if not _is_per_file_refusal(error.response.status_code, google.error_detail(error)):
                raise
            gone = error.response.status_code == 404
            return _FileVisit(file_id=file_id, record=None, refused=not gone)
        if file.get("trashed"):
            return _FileVisit(file_id=file_id, record=None, refused=False)
        return await self._file_visit(client, file_id, file)

    async def _file_visit(
        self, client: httpx.AsyncClient, file_id: str, file: dict[str, Any]
    ) -> _FileVisit:
        refused = False
        try:
            meta = await self._get(
                client,
                f"{SHEETS_API_URL}/spreadsheets/{file_id}",
                params={"includeGridData": "false"},
            )
        except httpx.HTTPStatusError as error:
            if not _is_per_file_refusal(error.response.status_code, google.error_detail(error)):
                raise
            refused = True
            meta = {"spreadsheetId": file_id, "properties": {"title": file.get("name")}}
        return _FileVisit(
            file_id=file_id,
            record={
                **meta,
                "id": file_id,
                "spreadsheetId": meta.get("spreadsheetId") or file_id,
                "title": (meta.get("properties") or {}).get("title") or file.get("name"),
                "url": meta.get("spreadsheetUrl") or file.get("webViewLink"),
                "created_at": file.get("createdTime"),
                "updated_at": file.get("modifiedTime"),
            },
            refused=refused,
        )

    async def _visit_records(
        self, client: httpx.AsyncClient, stream: StreamSpec, visit: _FileVisit
    ) -> tuple[list[dict[str, Any]], bool]:
        if visit.record is None:
            return [], visit.refused
        match stream.name:
            case "spreadsheets":
                return [visit.record], visit.refused
            case "sheets":
                return _sheet_records(visit.record), visit.refused
            case "sheet_values":
                records, tab_refused = await self._sheet_value_records(client, visit.record)
                return records, visit.refused or tab_refused
            case _:
                raise NotImplementedError(
                    f"googlesheets: stream {stream.name!r} has no paginate dispatch"
                )

    async def _sheet_value_records(
        self, client: httpx.AsyncClient, spreadsheet: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], bool]:
        spreadsheet_id = spreadsheet["spreadsheetId"]
        records: list[dict[str, Any]] = []
        tab_refused = False
        tabs: list[tuple[str, Any]] = []
        for sheet in spreadsheet.get("sheets") or []:
            if not isinstance(sheet, dict):
                continue
            properties = sheet.get("properties") or {}
            title = properties.get("title")
            sheet_id = properties.get("sheetId")
            if not isinstance(title, str) or sheet_id is None:
                continue
            tabs.append((title, sheet_id))
        for start in range(0, len(tabs), VALUES_BATCH_SIZE):
            chunk = tabs[start : start + VALUES_BATCH_SIZE]
            try:
                data = await self._get(
                    client,
                    f"{SHEETS_API_URL}/spreadsheets/{spreadsheet_id}/values:batchGet",
                    params={
                        "ranges": [_quoted_sheet_range(title) for title, _ in chunk],
                        "majorDimension": "ROWS",
                    },
                )
            except httpx.HTTPStatusError as error:
                if not _is_per_file_refusal(error.response.status_code, google.error_detail(error)):
                    raise
                for title, sheet_id in chunk:
                    grid = (
                        f"{SHEETS_API_URL}/spreadsheets/{spreadsheet_id}/values/"
                        f"{quote(_quoted_sheet_range(title), safe='')}"
                    )
                    try:
                        value_range = await self._get(
                            client, grid, params={"majorDimension": "ROWS"}
                        )
                    except httpx.HTTPStatusError as tab_error:
                        if not _is_per_file_refusal(
                            tab_error.response.status_code, google.error_detail(tab_error)
                        ):
                            raise
                        tab_refused = True
                        continue
                    records.append(_sheet_value_record(spreadsheet, title, sheet_id, value_range))
                continue
            returned = list_or_empty(data.get("valueRanges"))
            if len(returned) != len(chunk):
                raise StreamFault(
                    f"googlesheets: values:batchGet on spreadsheet {spreadsheet_id} asked for "
                    f"{len(chunk)} ranges and returned {len(returned)}"
                )
            for (title, sheet_id), value_range in zip(chunk, returned, strict=True):
                records.append(_sheet_value_record(spreadsheet, title, sheet_id, value_range))
        return records, tab_refused

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        match stream.name:
            case "spreadsheets":
                title = _str(record.get("title"))
                tabs = ", ".join(
                    _str((sheet.get("properties") or {}).get("title"))
                    for sheet in record.get("sheets") or []
                    if isinstance(sheet, dict)
                ).strip(", ")
                body = f"sheets: {tabs}" if tabs else ""
            case "sheets":
                title = _str(record.get("title"))
                body = f"spreadsheet: {_str(record.get('spreadsheet_title'))}".rstrip(": ")
            case "sheet_values":
                title = _str(record.get("sheet_title"))
                body = _grid_text(record.get("values"))
            case _:
                return super().render(record, stream)
        heading = f"# googlesheets {stream.name}: {title}".rstrip()
        return title, f"{heading}\n\n{body}".rstrip()


def _decode_cursor(cursor: str | None) -> tuple[str | None, tuple[str, ...], str | None]:
    if not cursor:
        return None, (), None
    try:
        parsed = json.loads(cursor)
    except ValueError:
        return cursor, (), None
    if not isinstance(parsed, dict):
        return cursor, (), None
    try:
        checkpoint = _Checkpoint.model_validate(parsed)
    except ValidationError as error:
        raise RuntimeError(f"googlesheets: malformed cursor {cursor!r}") from error
    return checkpoint.watermark, tuple(checkpoint.refused), checkpoint.retried


def _encode_cursor(watermark: str | None, refused: set[str], retried: str | None) -> str | None:
    if watermark is None or not refused:
        return watermark
    return _Checkpoint(
        watermark=watermark, refused=sorted(refused)[:REFUSED_LIMIT], retried=retried
    ).model_dump_json(exclude_none=True)


def _settled(refused: set[str], file_id: str, still_refused: bool) -> set[str]:
    return refused | {file_id} if still_refused else refused - {file_id}


def _is_per_file_refusal(status: int, detail: dict[str, Any]) -> bool:
    return (
        status in _METADATA_FALLBACK_STATUS
        and bool(detail)
        and not google.is_quota_refusal(detail)
        and not _is_grant_refusal(detail)
    )


def _is_grant_refusal(detail: dict[str, Any]) -> bool:
    if any(item.get("reason") in _GRANT_REASONS for item in list_or_empty(detail.get("errors"))):
        return True
    return any(
        item.get("domain") == _SERVICE_ERROR_DOMAIN for item in list_or_empty(detail.get("details"))
    )


def _sheet_records(spreadsheet: dict[str, Any]) -> list[dict[str, Any]]:
    spreadsheet_id = spreadsheet["spreadsheetId"]
    records: list[dict[str, Any]] = []
    for sheet in spreadsheet.get("sheets") or []:
        if not isinstance(sheet, dict):
            continue
        properties = sheet.get("properties") or {}
        sheet_id = properties.get("sheetId")
        if sheet_id is None:
            continue
        records.append(
            {
                **sheet,
                "id": f"{spreadsheet_id}:{sheet_id}",
                "spreadsheet_id": spreadsheet_id,
                "spreadsheet_title": spreadsheet.get("title"),
                "title": properties.get("title"),
                "created_at": spreadsheet.get("created_at"),
                "updated_at": spreadsheet.get("updated_at"),
            }
        )
    return records


def _sheet_value_record(
    spreadsheet: dict[str, Any], title: str, sheet_id: Any, value_range: dict[str, Any]
) -> dict[str, Any]:
    spreadsheet_id = spreadsheet["spreadsheetId"]
    return {
        **value_range,
        "id": f"{spreadsheet_id}:{sheet_id}:values",
        "spreadsheet_id": spreadsheet_id,
        "spreadsheet_title": spreadsheet.get("title"),
        "sheet_id": sheet_id,
        "sheet_title": title,
        "created_at": spreadsheet.get("created_at"),
        "updated_at": spreadsheet.get("updated_at"),
    }


def _quoted_sheet_range(title: str) -> str:
    escaped = title.replace("'", "''")
    return f"'{escaped}'"


def _grid_text(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    return "\n".join(
        " | ".join(str(cell) for cell in row) for row in values if isinstance(row, list)
    )


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""
