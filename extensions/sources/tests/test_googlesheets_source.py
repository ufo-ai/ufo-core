"""Google Sheets connector over a mock transport: the Drive-list→Sheets-get fan-out, the incremental
`modifiedTime` watermark threaded into the Drive query, the per-tab values read, the `render`
override that lifts a spreadsheet's tab titles and a tab's grid rows, and `StreamSkipped` on a scope
refusal. Offline — a canned transport, no DB, no token, no broker."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
from ufo_ext_sources.googlesheets import GoogleSheetsConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped

ACCOUNT = "acct-1"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(handler: Callable[[httpx.Request], httpx.Response]) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
):
    return await ConnectorBackend(connector=GoogleSheetsConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, _auth(handler)
    )


SPREADSHEET_FILE = {
    "id": "s1",
    "name": "Q3 Metrics",
    "webViewLink": "https://docs.google.com/spreadsheets/d/s1",
    "createdTime": "2026-01-01T00:00:00.000Z",
    "modifiedTime": "2026-02-05T00:00:00.000Z",
}
SPREADSHEET_META = {
    "spreadsheetId": "s1",
    "spreadsheetUrl": "https://docs.google.com/spreadsheets/d/s1",
    "properties": {"title": "Q3 Metrics"},
    "sheets": [
        {"properties": {"sheetId": 0, "title": "Summary"}},
        {"properties": {"sheetId": 1, "title": "Detail"}},
    ],
}


def _handler(seen_queries: list[str] | None = None) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.googleapis.com" and request.url.path == "/drive/v3/files":
            if seen_queries is not None:
                seen_queries.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"files": [SPREADSHEET_FILE], "nextPageToken": None})
        if request.url.host == "sheets.googleapis.com":
            if request.url.path == "/v4/spreadsheets/s1":
                return httpx.Response(200, json=SPREADSHEET_META)
            if request.url.path == "/v4/spreadsheets/s1/values/Summary":
                return httpx.Response(
                    200,
                    json={
                        "range": "Summary!A1:B2",
                        "majorDimension": "ROWS",
                        "values": [["Metric", "Value"], ["Revenue", "100"]],
                    },
                )
            if request.url.path == "/v4/spreadsheets/s1/values/Detail":
                return httpx.Response(200, json={"range": "Detail", "values": []})
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_lists_spreadsheets_and_render_lists_the_tab_titles() -> None:
    result = await _fetch("spreadsheets", _handler())

    assert {page.source_ref for page in result.pages} == {"spreadsheets/s1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-05T00:00:00.000Z"
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-05T00:00:00.000000+00:00"

    body = result.pages[0].body
    assert "Q3 Metrics" in body
    assert "sheets: Summary, Detail" in body


async def test_incremental_threads_the_watermark_into_the_drive_query() -> None:
    seen: list[str] = []
    await _fetch("spreadsheets", _handler(seen), cursor="2026-01-15T00:00:00.000Z")
    assert seen and all("modifiedTime > '2026-01-15T00:00:00.000Z'" in query for query in seen)


async def test_sheet_values_reads_each_tab_grid_into_render() -> None:
    result = await _fetch("sheet_values", _handler())

    assert {page.source_ref for page in result.pages} == {
        "sheet_values/s1:0:values",
        "sheet_values/s1:1:values",
    }
    body = next(page.body for page in result.pages if page.source_ref == "sheet_values/s1:0:values")
    assert "Metric | Value" in body
    assert "Revenue | 100" in body


async def test_scope_refusal_yields_stream_skipped() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": 403, "message": "insufficientScopes"}})

    try:
        await _fetch("spreadsheets", refuse)
    except StreamSkipped:
        return
    raise AssertionError("a 403 from Drive must raise StreamSkipped")
