"""The Google Sheets connector — spreadsheets, their sheet tabs, and each tab's cell values synced
as recallable content.

Reads are a Drive-list→Sheets-get fan-out: `GET /drive/v3/files` enumerates the grant's
spreadsheets (filtered to the spreadsheet mime type, untrashed, ordered by `modifiedTime`), and each
file id is read through the Sheets API (`GET /v4/spreadsheets/{id}`) for its title and tab list. The
`spreadsheets` stream is incremental — the Drive query filters server-side past the stored
`modifiedTime` watermark and each record carries that time as a flat `updated_at` the sync advances
a cursor over. `sheets` explodes each spreadsheet into one record per tab; `sheet_values` reads each
tab's grid (`/values/{tab}`) so a synced sheet recalls as its rows. A grant that lacks the scope
(`401`/`403`) yields `StreamSkipped` so the run records a skip, not a failure; a spreadsheet the
grant can list but not open (`403`/`404` on the Sheets get) falls back to its Drive metadata.
`render` lifts a spreadsheet's tab titles, a tab's name, and a grid's rows into a readable body. The
credential is resolved through the auth proxy the runner threads — this connector holds no token.
The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

import httpx

from selfhost.sdk.sources import RestConnector, StreamSkipped, StreamSpec, list_or_empty

SHEET_MIME = "application/vnd.google-apps.spreadsheet"
SHEETS_API_URL = "https://sheets.googleapis.com/v4"
DRIVE_PAGE_SIZE = 1000
PAGE_SIZE = 100
_REFUSAL_STATUS = frozenset({401, 403})
_METADATA_FALLBACK_STATUS = frozenset({403, 404})
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
    ),
    StreamSpec(name="sheets", source_object="sheets", primary_key="id", canonical=False),
    StreamSpec(name="sheet_values", source_object="values", primary_key="id", canonical=False),
]


class GoogleSheetsConnector(RestConnector):
    name = "google_sheets"
    base_url = "https://www.googleapis.com"
    streams_list = GOOGLE_SHEETS_STREAMS

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        page: list[dict[str, Any]] = []
        try:
            async for spreadsheet in self._spreadsheet_records(
                client, cursor=cursor if stream.name == "spreadsheets" else None
            ):
                if stream.name == "spreadsheets":
                    page.append(spreadsheet)
                elif stream.name == "sheets":
                    page.extend(_sheet_records(spreadsheet))
                elif stream.name == "sheet_values":
                    async for values in self._sheet_value_records(client, spreadsheet):
                        page.append(values)
                else:
                    raise NotImplementedError(
                        f"google_sheets: stream {stream.name!r} has no paginate dispatch"
                    )
                if len(page) >= PAGE_SIZE:
                    yield page
                    page = []
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"google_sheets: {stream.name!r} refused ({error.response.status_code}); the "
                    "grant lacks the Drive or Sheets scope"
                ) from error
            raise
        if page:
            yield page

    async def _iter_spreadsheet_files(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        token: str | None = None
        query = f"mimeType = '{SHEET_MIME}' and trashed = false"
        if cursor:
            query = f"{query} and modifiedTime > '{cursor}'"
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
                    if error.response.status_code in _METADATA_FALLBACK_STATUS:
                        meta = {
                            "spreadsheetId": spreadsheet_id,
                            "properties": {"title": file.get("name")},
                        }
                    else:
                        raise
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
        for sheet in spreadsheet.get("sheets") or []:
            properties = sheet.get("properties") or {} if isinstance(sheet, dict) else {}
            title = properties.get("title")
            sheet_id = properties.get("sheetId")
            if not isinstance(title, str) or sheet_id is None:
                continue
            data = await self._get(
                client,
                f"{SHEETS_API_URL}/spreadsheets/{spreadsheet_id}/values/{quote(title, safe='')}",
                params={"majorDimension": "ROWS"},
            )
            yield {
                **data,
                "id": f"{spreadsheet_id}:{sheet_id}:values",
                "spreadsheet_id": spreadsheet_id,
                "spreadsheet_title": spreadsheet.get("title"),
                "sheet_id": sheet_id,
                "sheet_title": title,
            }

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
        heading = f"# google_sheets {stream.name}: {title}".rstrip()
        return title, f"{heading}\n\n{body}".rstrip()


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
            }
        )
    return records


def _grid_text(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    return "\n".join(
        " | ".join(str(cell) for cell in row) for row in values if isinstance(row, list)
    )


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""
