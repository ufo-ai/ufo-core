"""Google Sheets connector over a mock transport: the Drive-list→Sheets-get fan-out, the incremental
`modifiedTime` watermark threaded into the Drive query, the per-tab values read whose per-file guard
drops only the refused tab, the `render` override that lifts a spreadsheet's tab titles and a tab's
grid rows, and the refusal taxonomy — `StreamSkipped` for a refusal naming the grant or the API, a
Drive-metadata fallback for one naming a single file, and a raise for a quota refusal or a `404`
carrying no Google error object. Offline — a canned transport, no DB, no token, no broker."""

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
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


OLDER_FILE = {
    "id": "s0",
    "name": "Q2 Metrics",
    "createdTime": "2026-01-01T00:00:00.000Z",
    "modifiedTime": "2026-02-01T00:00:00.000Z",
}
OLDER_META = {
    "spreadsheetId": "s0",
    "properties": {"title": "Q2 Metrics"},
    "sheets": [{"properties": {"sheetId": 0, "title": "Totals"}}],
}
QUOTA_ERROR = {
    "error": {
        "code": 403,
        "status": "RESOURCE_EXHAUSTED",
        "message": "Quota exceeded for quota metric 'Read requests'",
        "errors": [{"reason": "rateLimitExceeded"}],
    }
}
RESOURCE_EXHAUSTED_ERROR = {
    "error": {
        "code": 403,
        "status": "RESOURCE_EXHAUSTED",
        "message": "Quota exceeded for quota metric 'Read requests'",
    }
}
PERMISSION_ERROR = {
    "error": {
        "code": 403,
        "status": "PERMISSION_DENIED",
        "message": "The caller does not have permission",
        "errors": [{"reason": "forbidden"}],
    }
}
CALLER_PERMISSION_ERROR = {
    "error": {
        "code": 403,
        "status": "PERMISSION_DENIED",
        "message": "The caller does not have permission",
    }
}
NOT_FOUND_ERROR = {
    "error": {
        "code": 404,
        "status": "NOT_FOUND",
        "message": "Requested entity was not found.",
        "errors": [{"reason": "notFound"}],
    }
}
SCOPE_INSUFFICIENT_ERROR = {
    "error": {
        "code": 403,
        "status": "PERMISSION_DENIED",
        "message": "Request had insufficient authentication scopes.",
        "details": [
            {
                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                "reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
                "domain": "googleapis.com",
                "metadata": {"service": "sheets.googleapis.com"},
            }
        ],
    }
}
API_DISABLED_ERROR = {
    "error": {
        "code": 403,
        "status": "PERMISSION_DENIED",
        "message": "Google Sheets API has not been used in this project before or it is disabled.",
        "errors": [{"domain": "usageLimits", "reason": "accessNotConfigured"}],
    }
}
INSUFFICIENT_SCOPES_ERROR = {
    "error": {
        "code": 403,
        "status": "PERMISSION_DENIED",
        "message": "Request had insufficient authentication scopes.",
        "errors": [{"domain": "global", "reason": "insufficientPermissions"}],
    }
}
PROXY_ERROR = {"detail": "connected account is no longer authorized"}
NOT_A_GOOGLE_ERROR: list[dict[str, Any]] = [
    {"json": PROXY_ERROR},
    {"json": [{"error": {"code": 403, "message": "denied by policy"}}]},
    {"html": "<html><head><title>Forbidden</title></head></html>"},
    {"content": b""},
]
NOT_A_GOOGLE_ERROR_IDS = ["proxy-json", "json-array", "html", "empty"]


def _drive_list(request: httpx.Request) -> httpx.Response | None:
    if request.url.host == "www.googleapis.com" and request.url.path == "/drive/v3/files":
        return httpx.Response(
            200, json={"files": [OLDER_FILE, SPREADSHEET_FILE], "nextPageToken": None}
        )
    return None


def _sheets_refusing_handler(
    status: int, body: dict[str, Any]
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        listed = _drive_list(request)
        if listed is not None:
            return listed
        return httpx.Response(status, **body)

    return handle


def _older_spreadsheet(request: httpx.Request) -> httpx.Response | None:
    if request.url.path == "/v4/spreadsheets/s0":
        return httpx.Response(200, json=OLDER_META)
    if request.url.path == "/v4/spreadsheets/s0/values/Totals":
        return httpx.Response(200, json={"range": "Totals", "values": [["Total", "3"]]})
    return None


def _newest_refusing_handler(
    status: int, error: dict[str, Any]
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        for canned in (_drive_list(request), _older_spreadsheet(request)):
            if canned is not None:
                return canned
        if request.url.path == "/v4/spreadsheets/s1":
            return httpx.Response(status, json=error)
        return httpx.Response(404, json={"path": request.url.path})

    return handle


def _values_refusing_handler(
    status: int, error: dict[str, Any]
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        for canned in (_drive_list(request), _older_spreadsheet(request)):
            if canned is not None:
                return canned
        if request.url.path == "/v4/spreadsheets/s1":
            return httpx.Response(200, json=SPREADSHEET_META)
        if request.url.path.startswith("/v4/spreadsheets/s1/values/"):
            return httpx.Response(status, json=error)
        return httpx.Response(404, json={"path": request.url.path})

    return handle


def _first_tab_refusing_handler(
    status: int, error: dict[str, Any]
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        for canned in (_drive_list(request), _older_spreadsheet(request)):
            if canned is not None:
                return canned
        if request.url.path == "/v4/spreadsheets/s1":
            return httpx.Response(200, json=SPREADSHEET_META)
        if request.url.path == "/v4/spreadsheets/s1/values/Summary":
            return httpx.Response(status, json=error)
        if request.url.path == "/v4/spreadsheets/s1/values/Detail":
            return httpx.Response(200, json={"range": "Detail", "values": [["Region", "West"]]})
        return httpx.Response(404, json={"path": request.url.path})

    return handle


@pytest.mark.parametrize(
    "reason",
    ["dailyLimitExceeded", "quotaExceeded", "rateLimitExceeded", "userRateLimitExceeded"],
)
async def test_a_quota_refusal_from_the_drive_list_is_not_a_scope_skip(reason: str) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": 403,
                    "message": "Rate Limit Exceeded",
                    "errors": [{"domain": "usageLimits", "reason": reason}],
                }
            },
        )

    with pytest.raises(httpx.HTTPStatusError):
        await _fetch("spreadsheets", refuse)


@pytest.mark.parametrize("stream", ["spreadsheets", "sheets", "sheet_values"])
@pytest.mark.parametrize(
    "error",
    [QUOTA_ERROR, RESOURCE_EXHAUSTED_ERROR],
    ids=["usage-limits-reason", "resource-exhausted-status-only"],
)
async def test_a_quota_refusal_on_the_sheets_get_is_not_a_metadata_fallback(
    stream: str, error: dict[str, Any]
) -> None:
    with pytest.raises(httpx.HTTPStatusError) as raised:
        await _fetch(stream, _newest_refusing_handler(403, error))

    assert raised.value.response.status_code == 403


@pytest.mark.parametrize(
    ("status", "error"),
    [(403, PERMISSION_ERROR), (403, CALLER_PERMISSION_ERROR), (404, NOT_FOUND_ERROR)],
    ids=["reason-forbidden", "no-reason", "deleted-file"],
)
async def test_a_refusal_naming_one_file_falls_back_to_drive_metadata(
    status: int, error: dict[str, Any]
) -> None:
    result = await _fetch("spreadsheets", _newest_refusing_handler(status, error))

    assert {page.source_ref for page in result.pages} == {"spreadsheets/s0", "spreadsheets/s1"}
    assert "Q3 Metrics" in next(
        page.body for page in result.pages if page.source_ref == "spreadsheets/s1"
    )


@pytest.mark.parametrize("stream", ["spreadsheets", "sheets", "sheet_values"])
@pytest.mark.parametrize(
    "error",
    [SCOPE_INSUFFICIENT_ERROR, API_DISABLED_ERROR, INSUFFICIENT_SCOPES_ERROR],
    ids=["scope-insufficient", "api-disabled", "insufficient-permissions"],
)
async def test_a_grant_wide_sheets_refusal_skips_the_stream(
    stream: str, error: dict[str, Any]
) -> None:
    with pytest.raises(StreamSkipped):
        await _fetch(stream, _newest_refusing_handler(403, error))


@pytest.mark.parametrize("body", NOT_A_GOOGLE_ERROR, ids=NOT_A_GOOGLE_ERROR_IDS)
async def test_a_403_with_no_google_error_body_skips_the_stream(body: dict[str, Any]) -> None:
    with pytest.raises(StreamSkipped):
        await _fetch("spreadsheets", _sheets_refusing_handler(403, body))


@pytest.mark.parametrize("body", NOT_A_GOOGLE_ERROR, ids=NOT_A_GOOGLE_ERROR_IDS)
async def test_a_404_with_no_google_error_body_fails_the_run(body: dict[str, Any]) -> None:
    with pytest.raises(httpx.HTTPStatusError) as raised:
        await _fetch("spreadsheets", _sheets_refusing_handler(404, body))

    assert raised.value.response.status_code == 404


@pytest.mark.parametrize(
    ("status", "error"),
    [(403, PERMISSION_ERROR), (404, NOT_FOUND_ERROR)],
    ids=["reason-forbidden", "deleted-tab"],
)
async def test_a_values_refusal_naming_one_file_keeps_the_other_spreadsheets_rows(
    status: int, error: dict[str, Any]
) -> None:
    result = await _fetch("sheet_values", _values_refusing_handler(status, error))

    assert {page.source_ref for page in result.pages} == {"sheet_values/s0:0:values"}
    assert "Total | 3" in result.pages[0].body


@pytest.mark.parametrize(
    ("status", "error"),
    [(403, PERMISSION_ERROR), (404, NOT_FOUND_ERROR)],
    ids=["reason-forbidden", "deleted-tab"],
)
async def test_a_refused_tab_keeps_a_later_tab_of_the_same_spreadsheet(
    status: int, error: dict[str, Any]
) -> None:
    result = await _fetch("sheet_values", _first_tab_refusing_handler(status, error))

    assert {page.source_ref for page in result.pages} == {
        "sheet_values/s0:0:values",
        "sheet_values/s1:1:values",
    }
    assert "Region | West" in next(
        page.body for page in result.pages if page.source_ref == "sheet_values/s1:1:values"
    )


@pytest.mark.parametrize(
    "error",
    [SCOPE_INSUFFICIENT_ERROR, API_DISABLED_ERROR, INSUFFICIENT_SCOPES_ERROR, PROXY_ERROR],
    ids=["scope-insufficient", "api-disabled", "insufficient-permissions", "no-google-error-body"],
)
async def test_a_values_refusal_naming_the_grant_or_nothing_skips_the_stream(
    error: dict[str, Any],
) -> None:
    with pytest.raises(StreamSkipped):
        await _fetch("sheet_values", _values_refusing_handler(403, error))


async def test_a_quota_refusal_on_the_values_get_fails_the_run() -> None:
    with pytest.raises(httpx.HTTPStatusError) as raised:
        await _fetch("sheet_values", _values_refusing_handler(403, QUOTA_ERROR))

    assert raised.value.response.status_code == 403


async def test_a_404_values_refusal_with_no_google_error_body_fails_the_run() -> None:
    with pytest.raises(httpx.HTTPStatusError) as raised:
        await _fetch("sheet_values", _values_refusing_handler(404, PROXY_ERROR))

    assert raised.value.response.status_code == 404
