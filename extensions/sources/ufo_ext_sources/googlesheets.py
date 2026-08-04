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
its tab as a quoted A1 sheet reference with each apostrophe doubled so the returned range matches
back to the tab that was asked for — an unquoted title is a different request, one carrying a space
or punctuation failing to parse at all, one that reads as a cell reference resolving against the
first visible sheet, one a named range also carries resolving to that range; both stamp their
parent file's times onto every derived record, and a page reports its cursor even where a file lands
no derived record at all — an empty tab list, a tab list the grant cannot read — so the reported
value covers that file and the corpus below it goes unlisted on the next run. A file the grant
refuses is covered by that cursor like any other: its `spreadsheets` record is the Drive metadata
fallback, the tabs the refusal drops land nothing. A batch refusal naming one file falls back to
individual tab reads to isolate the refused tab: the file's other tabs land, the reported cursor
covers the file as if that tab had landed too, and the tab's rows land on the next run that lists
the file — the run Drive stamps a fresh `modifiedTime` on it, or one re-listing the group it is tied
in. The watermark is the file's, so a change Drive does not stamp on `modifiedTime` leaves that
file's tabs as they last synced.

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

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import httpx

from ufo.sdk.sources import (
    RestConnector,
    StreamPage,
    StreamSkipped,
    StreamSpec,
    dict_or_empty,
    list_or_empty,
)

SHEET_MIME = "application/vnd.google-apps.spreadsheet"
SHEETS_API_URL = "https://sheets.googleapis.com/v4"
DRIVE_PAGE_SIZE = 1000
PAGE_SIZE = 100
VALUES_BATCH_SIZE = 50
_REFUSAL_STATUS = frozenset({401, 403})
_METADATA_FALLBACK_STATUS = frozenset({403, 404})
_QUOTA_STATUS = "RESOURCE_EXHAUSTED"
_QUOTA_REASONS = frozenset(
    {
        "dailyLimitExceeded",
        "quotaExceeded",
        "rateLimitExceeded",
        "userRateLimitExceeded",
    }
)
_SERVICE_ERROR_DOMAIN = "googleapis.com"
_GRANT_REASONS = frozenset({"accessNotConfigured", "insufficientPermissions"})
DRIVE_FIELDS = (
    "nextPageToken,files(id,name,webViewLink,createdTime,modifiedTime,"
    "owners(emailAddress,displayName))"
)

GOOGLE_SHEETS_STREAMS: list[StreamSpec] = [
    StreamSpec(
        name="spreadsheets",
        source_object="spreadsheets",
        primary_key="spreadsheetId",
        cursor_field="updated_at",
        updated_at_field="updated_at",
    ),
    StreamSpec(
        name="sheets",
        source_object="sheets",
        primary_key="id",
        cursor_field="updated_at",
        canonical=False,
    ),
    StreamSpec(
        name="sheet_values",
        source_object="values",
        primary_key="id",
        cursor_field="updated_at",
        canonical=False,
    ),
]


class GoogleSheetsConnector(RestConnector):
    name = "googlesheets"
    base_url = "https://www.googleapis.com"
    streams_list = GOOGLE_SHEETS_STREAMS

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[StreamPage]:
        page: list[dict[str, Any]] = []
        reported = cursor
        try:
            async for spreadsheet in self._spreadsheet_records(client, cursor=cursor):
                if stream.name == "spreadsheets":
                    page.append(spreadsheet)
                elif stream.name == "sheets":
                    page.extend(_sheet_records(spreadsheet))
                elif stream.name == "sheet_values":
                    async for values in self._sheet_value_records(client, spreadsheet):
                        page.append(values)
                else:
                    raise NotImplementedError(
                        f"googlesheets: stream {stream.name!r} has no paginate dispatch"
                    )
                modified = spreadsheet.get("updated_at")
                if isinstance(modified, str):
                    reported = modified if reported is None else max(reported, modified)
                if len(page) >= PAGE_SIZE:
                    yield StreamPage(records=page, next_cursor=reported)
                    page = []
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status in _REFUSAL_STATUS and not _is_quota_refusal(_error_detail(error)):
                raise StreamSkipped(
                    f"googlesheets: {stream.name!r} refused ({status}); the grant cannot read "
                    "Drive or Sheets"
                ) from error
            raise
        if page or reported != cursor:
            yield StreamPage(records=page, next_cursor=reported)

    async def _iter_spreadsheet_files(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        token: str | None = None
        query = f"mimeType = '{SHEET_MIME}' and trashed = false"
        if cursor:
            query = f"{query} and modifiedTime >= '{cursor}'"
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

    async def _spreadsheet_records(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[dict[str, Any]]:
        async for files in self._iter_spreadsheet_files(client, cursor=cursor):
            for file in files:
                spreadsheet_id = file.get("id")
                if not isinstance(spreadsheet_id, str) or not spreadsheet_id:
                    continue
                try:
                    meta = await self._get(
                        client,
                        f"{SHEETS_API_URL}/spreadsheets/{spreadsheet_id}",
                        params={"includeGridData": "false"},
                    )
                except httpx.HTTPStatusError as error:
                    if not _is_per_file_refusal(error.response.status_code, _error_detail(error)):
                        raise
                    meta = {
                        "spreadsheetId": spreadsheet_id,
                        "properties": {"title": file.get("name")},
                    }
                yield {
                    **meta,
                    "id": spreadsheet_id,
                    "spreadsheetId": meta.get("spreadsheetId") or spreadsheet_id,
                    "title": (meta.get("properties") or {}).get("title") or file.get("name"),
                    "url": meta.get("spreadsheetUrl") or file.get("webViewLink"),
                    "created_at": file.get("createdTime"),
                    "updated_at": file.get("modifiedTime"),
                }

    async def _sheet_value_records(
        self, client: httpx.AsyncClient, spreadsheet: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        spreadsheet_id = spreadsheet["spreadsheetId"]
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
                if not _is_per_file_refusal(error.response.status_code, _error_detail(error)):
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
                            tab_error.response.status_code, _error_detail(tab_error)
                        ):
                            raise
                        continue
                    yield _sheet_value_record(spreadsheet, title, sheet_id, value_range)
                continue
            raw_ranges = data.get("valueRanges")
            if not isinstance(raw_ranges, list):
                raise ValueError("googlesheets: values:batchGet returned no valueRanges list")
            value_ranges: dict[str, dict[str, Any]] = {}
            for value_range in raw_ranges:
                if not isinstance(value_range, dict):
                    raise ValueError("googlesheets: values:batchGet returned a non-object range")
                parsed_title = _range_title(value_range.get("range"))
                if parsed_title in value_ranges:
                    raise ValueError(
                        f"googlesheets: values:batchGet returned duplicate {parsed_title!r}"
                    )
                value_ranges[parsed_title] = value_range
            expected = {title for title, _ in chunk}
            if value_ranges.keys() != expected:
                raise ValueError(
                    "googlesheets: values:batchGet ranges differ from requested tabs: "
                    f"expected {sorted(expected)!r}, got {sorted(value_ranges)!r}"
                )
            for title, sheet_id in chunk:
                yield _sheet_value_record(spreadsheet, title, sheet_id, value_ranges[title])

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


def _error_detail(error: httpx.HTTPStatusError) -> dict[str, Any]:
    try:
        body = error.response.json()
    except ValueError:
        return {}
    return dict_or_empty(dict_or_empty(body).get("error"))


def _is_per_file_refusal(status: int, detail: dict[str, Any]) -> bool:
    return (
        status in _METADATA_FALLBACK_STATUS
        and bool(detail)
        and not _is_quota_refusal(detail)
        and not _is_grant_refusal(detail)
    )


def _is_quota_refusal(detail: dict[str, Any]) -> bool:
    if detail.get("status") == _QUOTA_STATUS:
        return True
    return any(item.get("reason") in _QUOTA_REASONS for item in list_or_empty(detail.get("errors")))


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


def _range_title(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"googlesheets: invalid value range {value!r}")
    if not value.startswith("'"):
        return value.split("!", 1)[0]
    title: list[str] = []
    index = 1
    while index < len(value):
        character = value[index]
        if character != "'":
            title.append(character)
            index += 1
            continue
        if index + 1 < len(value) and value[index + 1] == "'":
            title.append("'")
            index += 2
            continue
        if index + 1 == len(value) or value[index + 1] == "!":
            return "".join(title)
        break
    raise ValueError(f"googlesheets: invalid value range {value!r}")


def _grid_text(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    return "\n".join(
        " | ".join(str(cell) for cell in row) for row in values if isinstance(row, list)
    )


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""
