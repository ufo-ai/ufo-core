"""Google Sheets connector over a mock transport: the Drive-list→Sheets-get fan-out, the inclusive
`modifiedTime` bound threaded into the Drive query of all three streams, the cursor each page
reports and how a run whose record cap ends inside a group of tied files resumes, the request volume
a corpus below the stored cursor and a one-file-modified corpus cost, the parent times each derived
record carries, the reported cursor covering a file that lands no derived record, the per-tab values
read whose per-file guard drops only the refused tab and leaves its rows to the run that lists the
file again, the `render` override that lifts a spreadsheet's tab titles and a tab's grid rows, and
the refusal taxonomy — `StreamSkipped` for a refusal naming the grant or the API, a Drive-metadata
fallback for one naming a single file, and a raise for a quota refusal or a `404` carrying no Google
error object. Offline — a canned transport, no DB, no token, no broker."""

import json
import logging
import re
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources import googlesheets
from ufo_ext_sources.googlesheets import GoogleSheetsConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig, StreamPage
from ufo.sources import backend as connector_backend
from ufo.sources.backend import BACKFILL_KEY
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
    assert seen and all("modifiedTime >= '2026-01-15T00:00:00.000Z'" in query for query in seen)


def _files_handler(files: list[dict[str, Any]]) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.googleapis.com":
            assert request.url.params.get("orderBy") == "modifiedTime"
            return httpx.Response(200, json={"files": files, "nextPageToken": None})
        file_id = request.url.path.removeprefix("/v4/spreadsheets/")
        return httpx.Response(
            200, json={"spreadsheetId": file_id, "properties": {"title": file_id}, "sheets": []}
        )

    return handle


UNDATED_FILE = {"id": "u1", "name": "Undated", "createdTime": "2026-01-01T00:00:00.000Z"}
LATER_FILE = {
    "id": "s2",
    "name": "Q4 Metrics",
    "createdTime": "2026-01-01T00:00:00.000Z",
    "modifiedTime": "2026-04-01T00:00:00.000Z",
}


async def test_the_reported_cursor_is_the_highest_modified_time_not_the_last_listed() -> None:
    result = await _fetch("spreadsheets", _files_handler([LATER_FILE, SPREADSHEET_FILE]))

    assert {page.source_ref for page in result.pages} == {"spreadsheets/s2", "spreadsheets/s1"}
    assert result.next_cursor == LATER_FILE["modifiedTime"]


TIED_FILES = {
    f"g{group}{suffix}": f"2026-03-0{group}T00:00:00.000Z"
    for group, suffixes in ((1, "abc"), (2, "abc"), (3, "abc"), (4, "a"))
    for suffix in suffixes
}
EDITED_TIME = "2026-03-05T00:00:00.000Z"


def _listing_handler(
    modified: dict[str, str],
    queries: list[str] | None = None,
    *,
    tabs: int = 0,
    barren: tuple[str, ...] = (),
    refused: dict[str, dict[str, Any]] | None = None,
    values_refused: dict[tuple[str, str], dict[str, Any]] | None = None,
    paths: list[str] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if paths is not None:
            paths.append(request.url.path)
        if request.url.host == "www.googleapis.com":
            query = request.url.params.get("q", "")
            if queries is not None:
                queries.append(query)
            assert request.url.params.get("orderBy") == "modifiedTime"
            listing = sorted(modified.items(), key=lambda item: (item[1], item[0]))
            bound = re.search(r"modifiedTime\s*(\S+)\s*'([^']*)'", query)
            if bound is not None:
                if bound.group(1) != ">=":
                    raise AssertionError(f"unmodelled drive bound, corpus not filtered: {query}")
                listing = [item for item in listing if item[1] >= bound.group(2)]
            return httpx.Response(
                200,
                json={
                    "files": [
                        {
                            "id": file_id,
                            "name": file_id,
                            "createdTime": "2026-01-01T00:00:00.000Z",
                            "modifiedTime": stamp,
                        }
                        for file_id, stamp in listing
                    ],
                    "nextPageToken": None,
                },
            )
        file_id, _, tab = request.url.path.removeprefix("/v4/spreadsheets/").partition("/values/")
        if refused is not None and file_id in refused:
            return httpx.Response(403, json=refused[file_id])
        if tab:
            if values_refused is not None and (file_id, tab) in values_refused:
                return httpx.Response(403, json=values_refused[(file_id, tab)])
            return httpx.Response(200, json={"range": tab, "values": [[tab, file_id]]})
        count = 0 if file_id in barren else tabs
        return httpx.Response(
            200,
            json={
                "spreadsheetId": file_id,
                "properties": {"title": file_id},
                "sheets": [
                    {"properties": {"sheetId": index, "title": f"t{index}"}}
                    for index in range(count)
                ],
            },
        )

    return handle


def _derived_refs(stream: str, file_id: str, tabs: int = 2) -> set[str]:
    suffix = ":values" if stream == "sheet_values" else ""
    return {f"{stream}/{file_id}:{index}{suffix}" for index in range(tabs)}


async def test_a_capped_run_inside_a_group_of_tied_files_re_lists_the_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connector_backend, "MAX_RECORDS_PER_RUN", 3)
    monkeypatch.setattr(googlesheets, "PAGE_SIZE", 2)
    modified = dict(TIED_FILES)
    landed: set[str] = set()
    split_at: list[str] = []
    cursor: str | None = None
    for index in range(8):
        result = await _fetch("spreadsheets", _listing_handler(modified), cursor=cursor)
        landed |= {page.source_ref for page in result.pages}
        assert result.next_cursor is not None
        assert BACKFILL_KEY not in result.next_cursor
        if any(
            f"spreadsheets/{file_id}" not in landed
            for file_id, stamp in modified.items()
            if stamp == result.next_cursor
        ):
            split_at.append(result.next_cursor)
        if index == 0:
            modified["g1a"] = EDITED_TIME
        if result.next_cursor == cursor:
            break
        cursor = result.next_cursor

    assert split_at
    assert landed == {f"spreadsheets/{file_id}" for file_id in modified}
    assert cursor == EDITED_TIME


async def test_the_files_tied_at_the_reported_cursor_re_list_onto_the_refs_they_hold() -> None:
    modified = {"g1a": TIED_FILES["g1a"], "g3a": TIED_FILES["g3a"], "g3b": TIED_FILES["g3b"]}
    queries: list[str] = []
    first = await _fetch("spreadsheets", _listing_handler(modified, queries))
    second = await _fetch(
        "spreadsheets", _listing_handler(modified, queries), cursor=first.next_cursor
    )

    assert first.next_cursor == TIED_FILES["g3a"]
    assert f"modifiedTime >= '{first.next_cursor}'" in queries[-1]
    assert {page.source_ref for page in second.pages} == {"spreadsheets/g3a", "spreadsheets/g3b"}
    assert {page.source_ref for page in second.pages} <= {page.source_ref for page in first.pages}
    assert second.next_cursor == first.next_cursor


async def test_sheet_values_reads_each_tab_grid_into_render() -> None:
    result = await _fetch("sheet_values", _handler())

    assert {page.source_ref for page in result.pages} == {
        "sheet_values/s1:0:values",
        "sheet_values/s1:1:values",
    }
    body = next(page.body for page in result.pages if page.source_ref == "sheet_values/s1:0:values")
    assert "Metric | Value" in body
    assert "Revenue | 100" in body


async def _pages(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None
) -> list[StreamPage]:
    connector = GoogleSheetsConnector()
    spec = next(item for item in connector.streams() if item.name == stream)
    async with httpx.AsyncClient(
        base_url=connector.base_url, transport=httpx.MockTransport(handler)
    ) as client:
        return [page async for page in connector.paginate(client, spec, cursor=cursor)]


async def test_a_page_landing_a_file_with_no_modified_time_reports_the_stored_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(googlesheets, "PAGE_SIZE", 1)
    pages = await _pages("spreadsheets", _files_handler([UNDATED_FILE]), "2026-01-15T00:00:00.000Z")

    assert [len(page.records) for page in pages] == [1]
    assert pages[0].next_cursor == "2026-01-15T00:00:00.000Z"


async def test_the_terminal_partial_page_reports_the_cursor_the_run_landed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(googlesheets, "PAGE_SIZE", 2)
    modified = {file_id: TIED_FILES[file_id] for file_id in ("g1a", "g3a", "g4a")}
    pages = await _pages("spreadsheets", _listing_handler(modified), cursor=None)

    assert [len(page.records) for page in pages] == [2, 1]
    assert pages[0].next_cursor == TIED_FILES["g3a"]
    assert pages[-1].next_cursor == TIED_FILES["g4a"]


@pytest.mark.parametrize("stream", ["sheets", "sheet_values"])
async def test_a_derived_stream_threads_the_bound_and_reports_the_parent_watermark(
    stream: str,
) -> None:
    seen: list[str] = []
    pages = await _pages(stream, _handler(seen), cursor="2026-01-15T00:00:00.000Z")

    assert pages
    assert all(page.next_cursor == SPREADSHEET_FILE["modifiedTime"] for page in pages)
    assert seen and all("modifiedTime >= '2026-01-15T00:00:00.000Z'" in query for query in seen)


@pytest.mark.parametrize("stream", ["sheets", "sheet_values"])
async def test_a_derived_record_carries_its_spreadsheets_times(stream: str) -> None:
    result = await _fetch(stream, _handler())

    assert result.pages
    assert {page.created_at for page in result.pages} == {"2026-01-01T00:00:00.000000+00:00"}
    assert {page.updated_at for page in result.pages} == {"2026-02-05T00:00:00.000000+00:00"}
    assert result.next_cursor == SPREADSHEET_FILE["modifiedTime"]


STEADY_CURSOR = "2026-06-01T00:00:00.000Z"


@pytest.mark.parametrize("stream", ["sheets", "sheet_values"])
async def test_a_corpus_entirely_below_the_stored_cursor_costs_one_drive_list(stream: str) -> None:
    paths: list[str] = []
    result = await _fetch(
        stream, _listing_handler(dict(TIED_FILES), tabs=2, paths=paths), cursor=STEADY_CURSOR
    )

    assert paths == ["/drive/v3/files"]
    assert result.pages == ()
    assert result.next_cursor == STEADY_CURSOR


@pytest.mark.parametrize("stream", ["sheets", "sheet_values"])
async def test_a_modified_spreadsheet_costs_its_own_reads_not_the_corpus(stream: str) -> None:
    modified = dict(TIED_FILES)
    first = await _fetch(stream, _listing_handler(modified, tabs=2), cursor=None)
    modified["g4a"] = EDITED_TIME
    paths: list[str] = []
    second = await _fetch(
        stream, _listing_handler(modified, tabs=2, paths=paths), cursor=first.next_cursor
    )

    assert first.next_cursor == TIED_FILES["g4a"]
    assert len(first.pages) == 2 * len(TIED_FILES)
    assert paths.count("/drive/v3/files") == 1
    assert paths.count("/v4/spreadsheets/g4a") == 1
    assert all(
        path == "/drive/v3/files" or path.startswith("/v4/spreadsheets/g4a") for path in paths
    )
    assert {page.source_ref for page in second.pages} == _derived_refs(stream, "g4a")
    assert second.next_cursor == EDITED_TIME


@pytest.mark.parametrize("stream", ["sheets", "sheet_values"])
async def test_tabs_added_below_the_watermark_without_a_drive_edit_do_not_land(
    stream: str,
) -> None:
    modified = {"g1a": TIED_FILES["g1a"], "g4a": TIED_FILES["g4a"]}
    first = await _fetch(stream, _listing_handler(modified, tabs=1), cursor=None)
    second = await _fetch(stream, _listing_handler(modified, tabs=2), cursor=first.next_cursor)

    assert {page.source_ref for page in first.pages} == _derived_refs(
        stream, "g1a", 1
    ) | _derived_refs(stream, "g4a", 1)
    assert {page.source_ref for page in second.pages} == _derived_refs(stream, "g4a")


@pytest.mark.parametrize("stream", ["sheets", "sheet_values"])
async def test_a_capped_derived_run_inside_a_group_of_tied_files_re_lists_the_group(
    stream: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(connector_backend, "MAX_RECORDS_PER_RUN", 3)
    monkeypatch.setattr(googlesheets, "PAGE_SIZE", 2)
    modified = dict(TIED_FILES)
    landed: set[str] = set()
    split_at: list[str] = []
    cursor: str | None = None
    with caplog.at_level(logging.WARNING, logger="ufo"):
        for index in range(16):
            result = await _fetch(stream, _listing_handler(modified, tabs=2), cursor=cursor)
            landed |= {page.source_ref for page in result.pages}
            assert result.next_cursor is not None
            assert BACKFILL_KEY not in result.next_cursor
            assert cursor is None or result.next_cursor >= cursor
            if any(
                not _derived_refs(stream, file_id) <= landed
                for file_id, stamp in modified.items()
                if stamp == result.next_cursor
            ):
                split_at.append(result.next_cursor)
            if index == 0:
                modified["g1a"] = EDITED_TIME
            if result.next_cursor == cursor:
                break
            cursor = result.next_cursor

    assert split_at
    assert landed == {ref for file_id in modified for ref in _derived_refs(stream, file_id)}
    assert cursor == EDITED_TIME
    assert "source_sync.cap_overrun" not in {record.getMessage() for record in caplog.records}


@pytest.mark.parametrize("stream", ["sheets", "sheet_values"])
async def test_a_capped_derived_run_beyond_the_overrun_ceiling_carries_its_watermark(
    stream: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(connector_backend, "MAX_RECORDS_PER_RUN", 1)
    monkeypatch.setattr(googlesheets, "PAGE_SIZE", 1)
    tied = {file_id: TIED_FILES["g1a"] for file_id in ("g1a", "g1b", "g1c", "g2a", "g2b")}
    result = await _fetch(stream, _listing_handler(tied, tabs=1), cursor=None)

    assert result.next_cursor is not None
    assert json.loads(result.next_cursor)[BACKFILL_KEY] == {
        "origin": None,
        "skip": len(result.pages),
        "watermark": TIED_FILES["g1a"],
    }


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


REFUSED_FILES = {"v1": "2026-05-01T00:00:00.000Z", "v2": "2026-06-01T00:00:00.000Z"}
REFUSED_TAB = ("v1", "t0")
REFUSED_REF = "sheet_values/v1:0:values"
RESTAMPED_TIME = "2026-07-01T00:00:00.000Z"


async def test_a_refused_tab_lands_its_rows_the_run_drive_restamps_the_file() -> None:
    every_ref = _derived_refs("sheet_values", "v1") | _derived_refs("sheet_values", "v2")
    modified = dict(REFUSED_FILES)
    queries: list[str] = []
    first = await _fetch(
        "sheet_values",
        _listing_handler(modified, tabs=2, values_refused={REFUSED_TAB: PERMISSION_ERROR}),
    )
    widened = await _fetch(
        "sheet_values", _listing_handler(modified, queries, tabs=2), cursor=first.next_cursor
    )
    modified["v1"] = RESTAMPED_TIME
    restamped = await _fetch(
        "sheet_values", _listing_handler(modified, tabs=2), cursor=widened.next_cursor
    )

    assert {page.source_ref for page in first.pages} == every_ref - {REFUSED_REF}
    assert first.next_cursor == REFUSED_FILES["v2"]
    assert f"modifiedTime >= '{REFUSED_FILES['v2']}'" in queries[-1]
    assert REFUSED_REF not in {page.source_ref for page in widened.pages}
    assert {page.source_ref for page in restamped.pages} == every_ref
    assert restamped.next_cursor == RESTAMPED_TIME


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


BARREN_FILES = {"s0": OLDER_FILE["modifiedTime"], "s1": SPREADSHEET_FILE["modifiedTime"]}
ARRIVED_TIME = "2026-03-01T00:00:00.000Z"


@pytest.mark.parametrize("stream", ["sheets", "sheet_values"])
@pytest.mark.parametrize(
    "blocked",
    [
        {"barren": ("s1", "s2")},
        {"refused": {"s1": PERMISSION_ERROR, "s2": PERMISSION_ERROR}},
    ],
    ids=["no-tabs", "tabs-unreadable"],
)
async def test_a_file_landing_no_derived_record_is_covered_by_the_reported_cursor(
    stream: str, blocked: dict[str, Any]
) -> None:
    modified = dict(BARREN_FILES)
    first = await _fetch(stream, _listing_handler(modified, tabs=2, **blocked), cursor=None)
    modified["s2"] = ARRIVED_TIME
    queries: list[str] = []
    paths: list[str] = []
    second = await _fetch(
        stream,
        _listing_handler(modified, queries, tabs=2, paths=paths, **blocked),
        cursor=first.next_cursor,
    )
    third = await _fetch(
        stream, _listing_handler(modified, tabs=2, **blocked), cursor=second.next_cursor
    )
    healed = await _fetch(stream, _listing_handler(modified, tabs=2), cursor=third.next_cursor)

    assert {page.source_ref for page in first.pages} == _derived_refs(stream, "s0")
    assert first.next_cursor == BARREN_FILES["s1"]
    assert second.pages == ()
    assert second.next_cursor == ARRIVED_TIME
    assert f"modifiedTime >= '{first.next_cursor}'" in queries[-1]
    assert paths.count("/drive/v3/files") == 1
    assert not [path for path in paths if path.startswith("/v4/spreadsheets/s0")]
    assert third.pages == ()
    assert third.next_cursor == second.next_cursor
    assert {page.source_ref for page in healed.pages} == _derived_refs(stream, "s2")


async def test_a_refused_file_with_no_modified_time_reports_no_cursor() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.googleapis.com":
            return httpx.Response(200, json={"files": [UNDATED_FILE], "nextPageToken": None})
        return httpx.Response(403, json=PERMISSION_ERROR)

    result = await _fetch("spreadsheets", handle)

    assert {page.source_ref for page in result.pages} == {"spreadsheets/u1"}
    assert result.next_cursor is None


@pytest.mark.parametrize("stream", ["sheets", "sheet_values"])
@pytest.mark.parametrize(
    ("error", "raised"),
    [(API_DISABLED_ERROR, StreamSkipped), (QUOTA_ERROR, httpx.HTTPStatusError)],
    ids=["grant-wide", "quota"],
)
async def test_a_derived_refusal_commits_no_cursor_so_the_next_run_lands_the_edit(
    stream: str, error: dict[str, Any], raised: type[Exception]
) -> None:
    modified = dict(BARREN_FILES)
    first = await _fetch(stream, _listing_handler(modified, tabs=2), cursor=None)
    modified["s0"] = EDITED_TIME
    with pytest.raises(raised):
        await _fetch(
            stream, _sheets_refusing_handler(403, {"json": error}), cursor=first.next_cursor
        )
    resumed = await _fetch(stream, _listing_handler(modified, tabs=2), cursor=first.next_cursor)

    assert first.next_cursor == BARREN_FILES["s1"]
    assert {page.source_ref for page in resumed.pages} == _derived_refs(
        stream, "s0"
    ) | _derived_refs(stream, "s1")
    assert resumed.next_cursor == EDITED_TIME
