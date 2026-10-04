"""Google Sheets connector over a mock transport: the Drive-list→Sheets-get fan-out, the inclusive
`modifiedTime` bound threaded into the Drive query of all three streams, the cursor each page
reports and how a run whose record cap ends inside a group of tied files resumes, the id every
refused file is carried by until a grant lands its tabs, the carried retry sitting outside the
adapter's positional resume so four consecutive capped runs neither oscillate nor re-land, the
resume whose listing shrank below its stored count waiting for a Drive restamp, the watermark a
carried `files.get` cannot lift, the carried id a member trashes dropping out of the set, the
request volume a corpus below the stored cursor, a one-file-modified corpus and a corpus carrying a
refusal cost, the parent times each derived record carries, the reported cursor covering a file
that lands no derived record, the quoted A1 sheet range every values request names its tab by, the
position each batched `valueRange` lands on its tab by and the count that faults the read, the
per-tab values read whose per-file guard drops only the refused tab, the cursor forms the checkpoint
reads and refuses, the `render` override that lifts a spreadsheet's tab titles and a tab's grid
rows, and the refusal taxonomy —
`StreamSkipped` for a refusal naming the grant or the API, a Drive-metadata fallback for one naming
a single file, and a raise for a quota refusal or a `404` carrying no Google error object. Offline —
a canned transport, no DB, no token, no broker."""

import json
import logging
import re
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers import googlesheets
from ufo_ext_sources.providers.googlesheets import (
    GoogleSheetsConnector,
)

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources import backend as connector_backend
from ufo.runtime.sources.backend import BACKFILL_KEY
from ufo.runtime.sources.sync import SourceAuth, StreamFault, StreamSkipped
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    Run,
    StreamPage,
    no_parents,
)


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(handler: Callable[[httpx.Request], httpx.Response]) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
):
    return await ConnectorBackend(connector=GoogleSheetsConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, _auth(handler)
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


def _tab_of(value: str) -> str:
    assert value.startswith("'") and value.endswith("'"), f"unquoted A1 sheet range {value!r}"
    return value[1:-1].replace("''", "'")


def _handler(
    seen_queries: list[str] | None = None,
    value_requests: list[httpx.Request] | None = None,
    spreadsheet_meta: dict[str, Any] = SPREADSHEET_META,
    values_response: Callable[[httpx.Request], httpx.Response] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.googleapis.com" and request.url.path == "/drive/v3/files":
            if seen_queries is not None:
                seen_queries.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"files": [SPREADSHEET_FILE], "nextPageToken": None})
        if request.url.host == "sheets.googleapis.com":
            if request.url.path == "/v4/spreadsheets/s1":
                return httpx.Response(200, json=spreadsheet_meta)
            if request.url.path == "/v4/spreadsheets/s1/values:batchGet":
                if value_requests is not None:
                    value_requests.append(request)
                if values_response is not None:
                    return values_response(request)
                return httpx.Response(
                    200,
                    json={
                        "valueRanges": [
                            {
                                "range": "Summary!A1:B2",
                                "majorDimension": "ROWS",
                                "values": [["Metric", "Value"], ["Revenue", "100"]],
                            },
                            {"range": "Detail", "values": []},
                        ]
                    },
                )
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
    drive_refused: dict[str, tuple[int, dict[str, Any]]] | None = None,
    trashed: tuple[str, ...] = (),
    restamped: dict[str, str] | None = None,
    paths: list[str] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if paths is not None:
            paths.append(request.url.path)
        if request.url.host == "www.googleapis.com":
            single = request.url.path.removeprefix("/drive/v3/files/")
            if single != request.url.path:
                if request.url.params.get("supportsAllDrives") != "true":
                    return httpx.Response(404, json=NOT_FOUND_ERROR)
                if drive_refused is not None and single in drive_refused:
                    status, body = drive_refused[single]
                    return httpx.Response(status, **body)
                if single not in modified:
                    return httpx.Response(404, json=NOT_FOUND_ERROR)
                row = _file_row(single, (restamped or {}).get(single) or modified[single])
                if single in trashed:
                    row["trashed"] = True
                return httpx.Response(200, json=_projected(row, request.url.params.get("fields")))
            query = request.url.params.get("q", "")
            if queries is not None:
                queries.append(query)
            assert request.url.params.get("orderBy") == "modifiedTime"
            listing = sorted(
                (item for item in modified.items() if item[0] not in trashed),
                key=lambda item: (item[1], item[0]),
            )
            bound = re.search(r"modifiedTime\s*(\S+)\s*'([^']*)'", query)
            if bound is not None:
                if bound.group(1) != ">=":
                    raise AssertionError(f"unmodelled drive bound, corpus not filtered: {query}")
                listing = [item for item in listing if item[1] >= bound.group(2)]
            return httpx.Response(
                200,
                json={
                    "files": [_file_row(file_id, stamp) for file_id, stamp in listing],
                    "nextPageToken": None,
                },
            )
        resource = request.url.path.removeprefix("/v4/spreadsheets/")
        if resource.endswith("/values:batchGet"):
            file_id = resource.removesuffix("/values:batchGet")
            requested = [_tab_of(value) for value in request.url.params.get_list("ranges")]
            if values_refused is not None:
                refused_tab = next(
                    (title for title in requested if (file_id, title) in values_refused), None
                )
                if refused_tab is not None:
                    return httpx.Response(403, json=values_refused[(file_id, refused_tab)])
            return httpx.Response(
                200,
                json={
                    "valueRanges": [
                        {"range": title, "values": [[title, file_id]]} for title in requested
                    ]
                },
            )
        file_id, _, quoted = resource.partition("/values/")
        if refused is not None and file_id in refused:
            return httpx.Response(403, json=refused[file_id])
        if quoted:
            tab = _tab_of(quoted)
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


def _file_row(file_id: str, stamp: str) -> dict[str, Any]:
    return {
        "id": file_id,
        "name": file_id,
        "createdTime": "2026-01-01T00:00:00.000Z",
        "modifiedTime": stamp,
    }


def _projected(row: dict[str, Any], fields: str | None) -> dict[str, Any]:
    named = (fields or "").split(",")
    return {key: value for key, value in row.items() if key in named}


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


async def test_sheet_values_skips_the_batch_endpoint_for_a_spreadsheet_without_tabs() -> None:
    value_requests: list[httpx.Request] = []
    metadata = {**SPREADSHEET_META, "sheets": []}

    result = await _fetch(
        "sheet_values", _handler(value_requests=value_requests, spreadsheet_meta=metadata)
    )

    assert result.pages == ()
    assert value_requests == []


async def test_sheet_values_skips_malformed_tabs_and_reads_the_valid_one() -> None:
    value_requests: list[httpx.Request] = []
    metadata = {
        **SPREADSHEET_META,
        "sheets": [
            None,
            {"properties": {"sheetId": 1}},
            {"properties": {"title": "No id"}},
            {"properties": {"sheetId": 2, "title": "Valid"}},
        ],
    }

    def valid_range(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"valueRanges": [{"range": "Valid", "values": []}]})

    result = await _fetch(
        "sheet_values",
        _handler(
            value_requests=value_requests,
            spreadsheet_meta=metadata,
            values_response=valid_range,
        ),
    )

    assert value_requests[0].url.params.get_list("ranges") == ["'Valid'"]
    assert [page.source_ref for page in result.pages] == ["sheet_values/s1:2:values"]


NON_GRID_TAB_RANGE = {
    "error": {
        "code": 400,
        "message": "Unable to parse range: 'Revenue chart'",
        "errors": [
            {
                "message": "Unable to parse range: 'Revenue chart'",
                "domain": "global",
                "reason": "badRequest",
            }
        ],
        "status": "INVALID_ARGUMENT",
    }
}


async def test_sheet_values_requests_only_grid_tabs() -> None:
    value_requests: list[httpx.Request] = []
    metadata = {
        **SPREADSHEET_META,
        "sheets": [
            {"properties": {"sheetId": 0, "title": "Summary", "sheetType": "GRID"}},
            {"properties": {"sheetId": 1, "title": "Revenue chart", "sheetType": "OBJECT"}},
            {"properties": {"sheetId": 2, "title": "Warehouse", "sheetType": "DATA_SOURCE"}},
            {"properties": {"sheetId": 3, "title": "Detail"}},
        ],
    }

    def grid_only(request: httpx.Request) -> httpx.Response:
        ranges = request.url.params.get_list("ranges")
        if {"'Revenue chart'", "'Warehouse'"} & set(ranges):
            return httpx.Response(400, json=NON_GRID_TAB_RANGE)
        return httpx.Response(
            200, json={"valueRanges": [{"range": value, "values": []} for value in ranges]}
        )

    result = await _fetch(
        "sheet_values",
        _handler(
            value_requests=value_requests, spreadsheet_meta=metadata, values_response=grid_only
        ),
    )

    assert value_requests[0].url.params.get_list("ranges") == ["'Summary'", "'Detail'"]
    assert {page.source_ref for page in result.pages} == {
        "sheet_values/s1:0:values",
        "sheet_values/s1:3:values",
    }


async def test_sheet_values_fault_carries_googles_enumerated_reason_without_its_message() -> None:
    def refused(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json=NON_GRID_TAB_RANGE)

    with pytest.raises(StreamFault) as raised:
        await _fetch("sheet_values", _handler(values_response=refused))

    assert raised.value.reason == (
        "googlesheets: values:batchGet on spreadsheet s1 refused (400 INVALID_ARGUMENT): badRequest"
    )
    assert "Revenue chart" not in raised.value.reason


AMBIGUOUS_TABS = ("Summary", "Q1", "ROI Annual Billing - Premium", "Owner's View")
REFERENCE_SHAPED_TABS = ("Summary", "Q1")
UNPARSEABLE_RANGE = {
    "error": {
        "code": 400,
        "message": "Unable to parse range",
        "errors": [{"domain": "global", "reason": "badRequest"}],
    }
}
_CELL_RANGE_RE = re.compile(r"[A-Za-z]{1,3}[1-9][0-9]*")
_BARE_TITLE_RE = re.compile(r"\w+")


def _a1_parsing_handler(titles: tuple[str, ...]) -> Callable[[httpx.Request], httpx.Response]:
    metadata = {
        **SPREADSHEET_META,
        "sheets": [
            {"properties": {"sheetId": index, "title": title}} for index, title in enumerate(titles)
        ],
    }

    def resolve(requested: str) -> str | None:
        if requested.startswith("'") and requested.endswith("'"):
            return requested[1:-1].replace("''", "'")
        if _CELL_RANGE_RE.fullmatch(requested):
            return titles[0]
        return requested if _BARE_TITLE_RE.fullmatch(requested) else None

    def echoed(tab: str) -> str:
        escaped = tab.replace("'", "''")
        return f"'{escaped}'!A1:A1"

    def values(request: httpx.Request) -> httpx.Response:
        resolved = [resolve(value) for value in request.url.params.get_list("ranges")]
        if None in resolved:
            return httpx.Response(400, json=UNPARSEABLE_RANGE)
        return httpx.Response(
            200,
            json={
                "valueRanges": [
                    {"range": echoed(tab), "values": [[f"row of {tab}"]]} for tab in resolved
                ]
            },
        )

    return _handler(spreadsheet_meta=metadata, values_response=values)


@pytest.mark.parametrize(
    ("payload", "returned"),
    (
        ({}, 0),
        ({"valueRanges": [{"range": "Summary", "values": []}]}, 1),
        ({"valueRanges": [{"range": "Summary", "values": []}, None]}, 1),
        (
            {
                "valueRanges": [
                    {"range": "Summary", "values": []},
                    {"range": "Detail", "values": []},
                    {"range": "Extra", "values": []},
                ]
            },
            3,
        ),
    ),
)
async def test_sheet_values_faults_on_a_range_count_it_did_not_ask_for(
    payload: dict[str, Any], returned: int
) -> None:

    def malformed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(StreamFault) as raised:
        await _fetch("sheet_values", _handler(values_response=malformed))

    assert raised.value.reason == (
        "googlesheets: values:batchGet on spreadsheet s1 asked for 2 ranges and "
        f"returned {returned}"
    )


async def _pages(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None
) -> list[StreamPage]:
    connector = GoogleSheetsConnector()
    spec = next(item for item in connector.streams() if item.name == stream)
    async with httpx.AsyncClient(
        base_url=connector.base_url, transport=httpx.MockTransport(handler)
    ) as client:
        return [
            page
            async for page in connector.paginate(
                client, spec, Run(cursor=cursor, parents=no_parents)
            )
        ]


@pytest.mark.parametrize("stream", ["sheets", "sheet_values"])
async def test_a_derived_stream_threads_the_bound_and_reports_the_parent_watermark(
    stream: str,
) -> None:
    seen: list[str] = []
    pages = await _pages(stream, _handler(seen), cursor="2026-01-15T00:00:00.000Z")

    assert pages
    assert all(page.next_cursor == SPREADSHEET_FILE["modifiedTime"] for page in pages)
    assert seen and all("modifiedTime >= '2026-01-15T00:00:00.000Z'" in query for query in seen)


STEADY_CURSOR = "2026-06-01T00:00:00.000Z"


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
    if request.url.path == "/v4/spreadsheets/s0/values:batchGet":
        return httpx.Response(
            200,
            json={"valueRanges": [{"range": "Totals", "values": [["Total", "3"]]}]},
        )
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
        if request.url.path.startswith("/v4/spreadsheets/s1/values"):
            return httpx.Response(status, json=error)
        return httpx.Response(404, json={"path": request.url.path})

    return handle


def _first_tab_refusing_handler(
    status: int, error: dict[str, Any], tab_error: dict[str, Any] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        for canned in (_drive_list(request), _older_spreadsheet(request)):
            if canned is not None:
                return canned
        if request.url.path == "/v4/spreadsheets/s1":
            return httpx.Response(200, json=SPREADSHEET_META)
        if request.url.path == "/v4/spreadsheets/s1/values:batchGet":
            return httpx.Response(status, json=error)
        if request.url.path == "/v4/spreadsheets/s1/values/'Summary'":
            return httpx.Response(status, json=tab_error if tab_error is not None else error)
        if request.url.path == "/v4/spreadsheets/s1/values/'Detail'":
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


async def test_a_grant_wide_sheets_refusal_skips_the_stream() -> None:
    streams = ("spreadsheets", "sheets", "sheet_values")
    errors = (SCOPE_INSUFFICIENT_ERROR, API_DISABLED_ERROR, INSUFFICIENT_SCOPES_ERROR)
    for stream in streams:
        for error in errors:
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


async def test_a_quota_refusal_during_tab_fallback_fails_the_run() -> None:
    with pytest.raises(httpx.HTTPStatusError) as raised:
        await _fetch(
            "sheet_values", _first_tab_refusing_handler(403, PERMISSION_ERROR, QUOTA_ERROR)
        )

    assert raised.value.response.json() == QUOTA_ERROR


REFUSED_FILES = {"v1": "2026-05-01T00:00:00.000Z", "v2": "2026-06-01T00:00:00.000Z"}
REFUSED_TAB = ("v1", "t0")
REFUSED_REF = "sheet_values/v1:0:values"


async def test_a_refused_tab_carries_its_file_until_the_grid_read_succeeds() -> None:
    every_ref = _derived_refs("sheet_values", "v1") | _derived_refs("sheet_values", "v2")
    modified = dict(REFUSED_FILES)
    queries: list[str] = []
    paths: list[str] = []
    landed: set[str] = set()
    reported: list[str] = []
    cursor: str | None = None
    for _ in range(3):
        refusing = await _fetch(
            "sheet_values",
            _listing_handler(
                modified,
                queries,
                tabs=2,
                values_refused={REFUSED_TAB: PERMISSION_ERROR},
                paths=paths,
            ),
            cursor=cursor,
        )
        landed |= {page.source_ref for page in refusing.pages}
        assert refusing.next_cursor is not None
        reported.append(refusing.next_cursor)
        cursor = refusing.next_cursor
    healed_paths: list[str] = []
    healed = await _fetch(
        "sheet_values", _listing_handler(modified, tabs=2, paths=healed_paths), cursor=cursor
    )

    assert landed == every_ref - {REFUSED_REF}
    assert [json.loads(value) for value in reported] == [
        {"watermark": REFUSED_FILES["v2"], "refused": ["v1"]}
    ] * 3
    assert all(f"modifiedTime >= '{REFUSED_FILES['v2']}'" in query for query in queries[1:])
    assert not [query for query in queries if REFUSED_FILES["v1"] in query]
    assert paths.count("/drive/v3/files/v1") == 2
    assert {page.source_ref for page in healed.pages} == every_ref
    assert healed_paths.count("/drive/v3/files/v1") == 1
    assert healed.next_cursor == REFUSED_FILES["v2"]


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


@pytest.mark.parametrize(
    ("stream", "blocked"),
    [
        ("sheets", {"refused": {"s1": PERMISSION_ERROR}}),
        ("sheet_values", {"refused": {"s1": PERMISSION_ERROR}}),
        (
            "sheet_values",
            {
                "values_refused": {
                    ("s1", "t0"): PERMISSION_ERROR,
                    ("s1", "t1"): PERMISSION_ERROR,
                }
            },
        ),
    ],
    ids=["sheets-metadata", "sheet_values-metadata", "sheet_values-tab"],
)
async def test_a_refused_file_is_carried_by_id_until_a_grant_lands_its_tabs(
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
    healed_paths: list[str] = []
    healed = await _fetch(
        stream,
        _listing_handler(modified, tabs=2, paths=healed_paths),
        cursor=second.next_cursor,
    )

    assert {page.source_ref for page in first.pages} == _derived_refs(stream, "s0")
    assert first.next_cursor is not None
    assert json.loads(first.next_cursor) == {
        "watermark": BARREN_FILES["s1"],
        "refused": ["s1"],
    }
    assert {page.source_ref for page in second.pages} == _derived_refs(stream, "s2")
    assert second.next_cursor is not None
    assert json.loads(second.next_cursor) == {"watermark": ARRIVED_TIME, "refused": ["s1"]}
    assert f"modifiedTime >= '{BARREN_FILES['s1']}'" in queries[-1]
    assert paths.count("/drive/v3/files") == 1
    assert paths.count("/drive/v3/files/s1") == 0
    assert paths.count("/v4/spreadsheets/s1") == 1
    assert not [path for path in paths if path.startswith("/v4/spreadsheets/s0")]
    assert healed_paths.count("/drive/v3/files/s1") == 1
    assert not [path for path in healed_paths if path.startswith("/v4/spreadsheets/s0")]
    assert {page.source_ref for page in healed.pages} == _derived_refs(
        stream, "s1"
    ) | _derived_refs(stream, "s2")
    assert healed.next_cursor == ARRIVED_TIME


async def test_a_refused_spreadsheet_is_carried_by_id_until_a_grant_restores_its_tabs() -> None:
    modified = dict(BARREN_FILES)
    refused = {"s1": PERMISSION_ERROR}
    first = await _fetch(
        "spreadsheets", _listing_handler(modified, tabs=2, refused=refused), cursor=None
    )
    modified["s2"] = ARRIVED_TIME
    second = await _fetch(
        "spreadsheets",
        _listing_handler(modified, tabs=2, refused=refused),
        cursor=first.next_cursor,
    )
    healed = await _fetch(
        "spreadsheets", _listing_handler(modified, tabs=2), cursor=second.next_cursor
    )

    assert first.next_cursor is not None
    assert json.loads(first.next_cursor) == {
        "watermark": BARREN_FILES["s1"],
        "refused": ["s1"],
    }
    assert "sheets: t0, t1" not in next(
        page.body for page in first.pages if page.source_ref == "spreadsheets/s1"
    )
    assert {page.source_ref for page in second.pages} == {"spreadsheets/s1", "spreadsheets/s2"}
    assert second.next_cursor is not None
    assert json.loads(second.next_cursor) == {"watermark": ARRIVED_TIME, "refused": ["s1"]}
    assert "sheets: t0, t1" in next(
        page.body for page in healed.pages if page.source_ref == "spreadsheets/s1"
    )
    assert healed.next_cursor == ARRIVED_TIME


async def test_a_carried_refusal_that_stays_refused_keeps_its_drive_name() -> None:
    modified = dict(BARREN_FILES)
    refused = {"s1": PERMISSION_ERROR}
    first = await _fetch(
        "spreadsheets", _listing_handler(modified, tabs=2, refused=refused), cursor=None
    )
    modified["s2"] = ARRIVED_TIME
    listed = await _fetch(
        "spreadsheets",
        _listing_handler(modified, tabs=2, refused=refused),
        cursor=first.next_cursor,
    )
    carried = await _fetch(
        "spreadsheets",
        _listing_handler(modified, tabs=2, refused=refused),
        cursor=listed.next_cursor,
    )

    assert next(page.title for page in first.pages if page.source_ref == "spreadsheets/s1") == "s1"
    assert (
        next(page.title for page in carried.pages if page.source_ref == "spreadsheets/s1") == "s1"
    )
    assert carried.next_cursor is not None
    assert json.loads(carried.next_cursor) == {"watermark": ARRIVED_TIME, "refused": ["s1"]}


RESTAMPED_TIME = "2026-09-09T00:00:00.000Z"


DISTINCT_FILES = {f"d{index}": f"2026-0{index + 1}-01T00:00:00.000Z" for index in range(6)}


@pytest.mark.parametrize(
    ("stream", "sheets_reads"), [("spreadsheets", 2), ("sheets", 2), ("sheet_values", 3)]
)
async def test_a_carried_refusal_costs_its_own_two_reads_not_the_corpus(
    stream: str, sheets_reads: int
) -> None:
    refused = {"d0": PERMISSION_ERROR}
    first = await _fetch(
        stream, _listing_handler(DISTINCT_FILES, tabs=2, refused=refused), cursor=None
    )
    paths: list[str] = []
    second = await _fetch(
        stream,
        _listing_handler(DISTINCT_FILES, tabs=2, refused=refused, paths=paths),
        cursor=first.next_cursor,
    )

    assert paths.count("/drive/v3/files") == 1
    assert paths.count("/drive/v3/files/d0") == 1
    assert len([path for path in paths if path.startswith("/v4/spreadsheets/")]) == sheets_reads
    assert second.next_cursor is not None
    assert json.loads(second.next_cursor) == {
        "watermark": DISTINCT_FILES["d5"],
        "refused": ["d0"],
    }


@pytest.mark.parametrize("stream", ["sheets", "sheet_values"])
async def test_a_capped_run_carrying_a_refusal_advances_its_checkpoint(
    stream: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(connector_backend, "MAX_RECORDS_PER_RUN", 2)
    monkeypatch.setattr(googlesheets, "PAGE_SIZE", 1)
    landed: set[str] = set()
    cursor: str | None = None
    with caplog.at_level(logging.WARNING, logger="ufo"):
        for _ in range(8):
            result = await _fetch(
                stream,
                _listing_handler(DISTINCT_FILES, tabs=2, refused={"d0": PERMISSION_ERROR}),
                cursor=cursor,
            )
            landed |= {page.source_ref for page in result.pages}
            assert result.next_cursor is not None
            assert BACKFILL_KEY not in result.next_cursor
            if result.next_cursor == cursor:
                break
            cursor = result.next_cursor

    assert landed == {
        ref
        for file_id in DISTINCT_FILES
        if file_id != "d0"
        for ref in _derived_refs(stream, file_id)
    }
    assert cursor is not None
    assert json.loads(cursor) == {"watermark": DISTINCT_FILES["d5"], "refused": ["d0"]}
    assert "source_sync.cap_overrun" not in [record.getMessage() for record in caplog.records]


REFUSED_EDIT_TIME = "2026-07-01T00:00:00.000Z"


@pytest.mark.parametrize("stream", ["sheets", "sheet_values"])
async def test_a_capped_run_carrying_an_edited_refusal_advances_on_what_it_drained(
    stream: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(connector_backend, "MAX_RECORDS_PER_RUN", 2)
    monkeypatch.setattr(googlesheets, "PAGE_SIZE", 1)
    modified = dict(DISTINCT_FILES)
    refused = {"d0": PERMISSION_ERROR}
    landed: set[str] = set()
    cursor: str | None = None
    with caplog.at_level(logging.WARNING, logger="ufo"):
        for index in range(10):
            result = await _fetch(
                stream, _listing_handler(modified, tabs=2, refused=refused), cursor=cursor
            )
            landed |= {page.source_ref for page in result.pages}
            assert result.next_cursor is not None
            assert BACKFILL_KEY not in result.next_cursor
            if index == 0:
                modified["d0"] = REFUSED_EDIT_TIME
            if result.next_cursor == cursor:
                break
            cursor = result.next_cursor

    assert landed == {
        ref
        for file_id in DISTINCT_FILES
        if file_id != "d0"
        for ref in _derived_refs(stream, file_id)
    }
    assert cursor is not None
    assert json.loads(cursor) == {"watermark": REFUSED_EDIT_TIME, "refused": ["d0"]}
    assert "source_sync.cap_overrun" not in [record.getMessage() for record in caplog.records]


REFUSED_UNDER_A_TIE = {"c": "2026-01-01T00:00:00.000Z"} | {
    f"a{index}": "2026-03-03T00:00:00.000Z" for index in range(1, 4)
}


async def test_a_carried_retry_never_falls_inside_a_stored_skip_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connector_backend, "MAX_RECORDS_PER_RUN", 1)
    refused = {"c": PERMISSION_ERROR}
    bodies: list[str] = []
    cursor: str | None = None
    for index in range(5):
        result = await _fetch(
            "spreadsheets",
            _listing_handler(REFUSED_UNDER_A_TIE, tabs=2, refused=refused),
            cursor=cursor,
        )
        bodies += [page.body for page in result.pages if page.source_ref == "spreadsheets/c"]
        cursor = result.next_cursor
        if index == 2:
            refused.clear()

    assert [body for body in bodies if "sheets: t0, t1" in body]
    assert cursor == REFUSED_UNDER_A_TIE["a1"]


TWO_REFUSALS_BELOW = {"d0": DISTINCT_FILES["d0"], "d1": DISTINCT_FILES["d1"]} | {
    f"z{index}": DISTINCT_FILES["d5"] for index in range(4)
}


@pytest.mark.parametrize("stream", ["sheets", "sheet_values"])
async def test_a_capped_run_settles_one_carried_id_and_stores_where_it_stopped(
    stream: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(connector_backend, "MAX_RECORDS_PER_RUN", 4)
    monkeypatch.setattr(googlesheets, "PAGE_SIZE", 1)
    refused = {"d0": PERMISSION_ERROR, "d1": PERMISSION_ERROR}
    seeded = await _fetch(
        stream, _listing_handler(TWO_REFUSALS_BELOW, tabs=2, refused=refused), cursor=None
    )
    first_paths: list[str] = []
    first = await _fetch(
        stream,
        _listing_handler(TWO_REFUSALS_BELOW, tabs=2, refused=refused, paths=first_paths),
        cursor=seeded.next_cursor,
    )
    second_paths: list[str] = []
    second = await _fetch(
        stream,
        _listing_handler(TWO_REFUSALS_BELOW, tabs=2, refused=refused, paths=second_paths),
        cursor=first.next_cursor,
    )
    settled: list[str] = []
    third = await _fetch(
        stream,
        _listing_handler(TWO_REFUSALS_BELOW, tabs=2, refused=refused, paths=settled),
        cursor=second.next_cursor,
    )

    assert seeded.next_cursor is not None
    assert json.loads(seeded.next_cursor) == {
        "watermark": DISTINCT_FILES["d5"],
        "refused": ["d0", "d1"],
    }
    assert first.next_cursor is not None
    assert json.loads(first.next_cursor) == {
        "watermark": DISTINCT_FILES["d5"],
        "refused": ["d0", "d1"],
        "retried": "d0",
    }
    assert first_paths.count("/drive/v3/files/d0") == 1
    assert "/drive/v3/files/d1" not in first_paths
    assert second.next_cursor is not None
    assert json.loads(second.next_cursor) == {
        "watermark": DISTINCT_FILES["d5"],
        "refused": ["d0", "d1"],
        "retried": "d1",
    }
    assert second_paths.count("/drive/v3/files/d1") == 1
    assert "/drive/v3/files/d0" not in second_paths
    assert third.next_cursor is not None
    assert json.loads(third.next_cursor) == {
        "watermark": DISTINCT_FILES["d5"],
        "refused": ["d0", "d1"],
    }
    assert not [path for path in settled if path.startswith("/drive/v3/files/")]


TWELVE_FILES = {"r0": "2026-01-01T00:00:00.000Z"} | {
    f"m{index}": f"2026-{index + 2:02d}-01T00:00:00.000Z" for index in range(11)
}


EIGHT_REFUSALS_UNDER_A_BARREN_TOP = {
    f"c{index}": f"2026-{index + 1:02d}-01T00:00:00.000Z" for index in range(8)
} | {"top": "2026-12-01T00:00:00.000Z"}


@pytest.mark.parametrize("stream", ["sheets", "sheet_values"])
async def test_four_capped_runs_carrying_a_refusal_neither_oscillate_nor_re_land(
    stream: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    refused = {
        file_id: PERMISSION_ERROR
        for file_id in EIGHT_REFUSALS_UNDER_A_BARREN_TOP
        if file_id != "top"
    }
    seeded = await _fetch(
        stream,
        _listing_handler(
            EIGHT_REFUSALS_UNDER_A_BARREN_TOP, tabs=2, barren=("top",), refused=refused
        ),
        cursor=None,
    )
    monkeypatch.setattr(connector_backend, "MAX_RECORDS_PER_RUN", 2)
    monkeypatch.setattr(googlesheets, "PAGE_SIZE", 1)
    cursor = seeded.next_cursor
    cursors: list[str] = []
    landed: list[set[str]] = []
    carried: list[list[str]] = []
    with caplog.at_level(logging.WARNING, logger="ufo"):
        for _ in range(4):
            paths: list[str] = []
            result = await _fetch(
                stream,
                _listing_handler(
                    EIGHT_REFUSALS_UNDER_A_BARREN_TOP, tabs=2, barren=("top",), paths=paths
                ),
                cursor=cursor,
            )
            landed.append({page.source_ref for page in result.pages})
            carried.append([path for path in paths if path.startswith("/drive/v3/files/")])
            assert result.next_cursor is not None
            cursors.append(result.next_cursor)
            cursor = result.next_cursor

    assert seeded.pages == ()
    assert not [stored for stored in cursors if BACKFILL_KEY in stored]
    assert len(set(cursors)) == 4
    assert carried == [
        ["/drive/v3/files/c0", "/drive/v3/files/c1"],
        ["/drive/v3/files/c2", "/drive/v3/files/c3"],
        ["/drive/v3/files/c4", "/drive/v3/files/c5"],
        ["/drive/v3/files/c6", "/drive/v3/files/c7"],
    ]
    assert [json.loads(stored)["retried"] for stored in cursors[:-1]] == ["c1", "c3", "c5"]
    assert cursors[-1] == EIGHT_REFUSALS_UNDER_A_BARREN_TOP["top"]
    assert [len(refs) for refs in landed] == [4, 4, 4, 4]
    assert all(refs - set().union(*landed[:index]) for index, refs in enumerate(landed) if index)
    assert len(set().union(*landed)) == 16
    assert "source_sync.cap_overrun" not in [record.getMessage() for record in caplog.records]


@pytest.mark.parametrize("stream", ["sheets", "sheet_values"])
async def test_capped_runs_carrying_a_refusal_land_the_healed_tabs(
    stream: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(connector_backend, "MAX_RECORDS_PER_RUN", 2)
    monkeypatch.setattr(googlesheets, "PAGE_SIZE", 1)
    refused = {"r0": PERMISSION_ERROR}
    landed: set[str] = set()
    carried_reads = 0
    cursor: str | None = None
    for index in range(12):
        paths: list[str] = []
        result = await _fetch(
            stream,
            _listing_handler(TWELVE_FILES, tabs=2, refused=refused, paths=paths),
            cursor=cursor,
        )
        landed |= {page.source_ref for page in result.pages}
        carried_reads += paths.count("/drive/v3/files/r0")
        assert result.next_cursor is not None
        assert BACKFILL_KEY not in result.next_cursor
        if index == 5:
            refused.clear()
        cursor = result.next_cursor

    assert carried_reads
    assert _derived_refs(stream, "r0") <= landed
    assert landed == {ref for file_id in TWELVE_FILES for ref in _derived_refs(stream, file_id)}
    assert cursor == TWELVE_FILES["m10"]


SECOND_REFUSAL_FILES = {
    f"v{index + 1}": f"2026-{index + 5:02d}-01T00:00:00.000Z" for index in range(4)
}
SECOND_REFUSAL_TABS = {("v1", "t0"): PERMISSION_ERROR, ("v2", "t0"): PERMISSION_ERROR}
SECOND_REFUSAL_REFS = {"sheet_values/v1:0:values", "sheet_values/v2:0:values"}


async def test_a_second_refused_file_lands_on_the_run_that_heals_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(connector_backend, "MAX_RECORDS_PER_RUN", 3)
    monkeypatch.setattr(googlesheets, "PAGE_SIZE", 2)
    every_ref = {
        ref for file_id in SECOND_REFUSAL_FILES for ref in _derived_refs("sheet_values", file_id)
    }
    refusing = await _fetch(
        "sheet_values",
        _listing_handler(SECOND_REFUSAL_FILES, tabs=2, values_refused=SECOND_REFUSAL_TABS),
        cursor=None,
    )
    queries: list[str] = []
    paths: list[str] = []
    widened = await _fetch(
        "sheet_values",
        _listing_handler(SECOND_REFUSAL_FILES, queries, tabs=2, paths=paths),
        cursor=refusing.next_cursor,
    )
    steady = await _fetch(
        "sheet_values",
        _listing_handler(SECOND_REFUSAL_FILES, tabs=2),
        cursor=widened.next_cursor,
    )

    assert {page.source_ref for page in refusing.pages} == every_ref - SECOND_REFUSAL_REFS
    assert refusing.next_cursor is not None
    assert json.loads(refusing.next_cursor) == {
        "watermark": SECOND_REFUSAL_FILES["v4"],
        "refused": ["v1", "v2"],
    }
    assert f"modifiedTime >= '{SECOND_REFUSAL_FILES['v4']}'" in queries[-1]
    assert [path for path in paths if path.startswith("/drive/v3/files/")] == [
        "/drive/v3/files/v1",
        "/drive/v3/files/v2",
    ]
    assert SECOND_REFUSAL_REFS <= {page.source_ref for page in widened.pages}
    assert widened.next_cursor == SECOND_REFUSAL_FILES["v4"]
    assert {page.source_ref for page in steady.pages} == _derived_refs("sheet_values", "v4")
    assert steady.next_cursor == SECOND_REFUSAL_FILES["v4"]


TIED_ABOVE_A_REFUSAL = {"r0": DISTINCT_FILES["d0"]} | {
    f"z{index}": TIED_FILES["g3a"] for index in range(4)
}


@pytest.mark.parametrize("stream", ["sheets", "sheet_values"])
async def test_a_grant_landing_under_a_stored_skip_count_lands_the_carried_file(
    stream: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    refused = {"r0": PERMISSION_ERROR}
    first = await _fetch(
        stream, _listing_handler(TIED_ABOVE_A_REFUSAL, tabs=2, refused=refused), cursor=None
    )
    monkeypatch.setattr(connector_backend, "MAX_RECORDS_PER_RUN", 2)
    monkeypatch.setattr(googlesheets, "PAGE_SIZE", 8)
    capped = await _fetch(
        stream,
        _listing_handler(TIED_ABOVE_A_REFUSAL, tabs=2, refused=refused),
        cursor=first.next_cursor,
    )
    stored = capped.next_cursor
    assert stored is not None
    assert json.loads(stored)[BACKFILL_KEY]["skip"] == 8
    paths: list[str] = []
    resumed = await _fetch(
        stream, _listing_handler(TIED_ABOVE_A_REFUSAL, tabs=2, paths=paths), cursor=stored
    )

    assert {page.source_ref for page in resumed.pages} == _derived_refs(stream, "r0")
    assert paths.count("/drive/v3/files/r0") == 1
    assert resumed.next_cursor == TIED_FILES["g3a"]


async def test_a_resume_whose_listing_shrank_waits_for_drive_to_restamp_the_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refused = {"r0": PERMISSION_ERROR}
    modified = dict(TIED_ABOVE_A_REFUSAL)
    uncapped = connector_backend.MAX_RECORDS_PER_RUN
    first = await _fetch("sheets", _listing_handler(modified, tabs=2, refused=refused), cursor=None)
    monkeypatch.setattr(connector_backend, "MAX_RECORDS_PER_RUN", 2)
    monkeypatch.setattr(googlesheets, "PAGE_SIZE", 8)
    capped = await _fetch(
        "sheets", _listing_handler(modified, tabs=2, refused=refused), cursor=first.next_cursor
    )
    stored = capped.next_cursor
    assert stored is not None
    assert json.loads(stored)[BACKFILL_KEY]["skip"] == 8
    del modified["z3"]
    refused.clear()
    paths: list[str] = []
    shrunk = await _fetch("sheets", _listing_handler(modified, tabs=2, paths=paths), cursor=stored)
    monkeypatch.setattr(connector_backend, "MAX_RECORDS_PER_RUN", uncapped)
    later = await _fetch("sheets", _listing_handler(modified, tabs=2), cursor=shrunk.next_cursor)
    modified["r0"] = RESTAMPED_TIME
    restamped = await _fetch("sheets", _listing_handler(modified, tabs=2), cursor=later.next_cursor)

    assert paths.count("/drive/v3/files/r0") == 1
    assert shrunk.pages == ()
    assert shrunk.next_cursor == TIED_FILES["g3a"]
    assert _derived_refs("sheets", "r0") & {page.source_ref for page in later.pages} == set()
    assert _derived_refs("sheets", "r0") <= {page.source_ref for page in restamped.pages}
    assert restamped.next_cursor == RESTAMPED_TIME


async def test_the_carried_refusal_set_stays_bounded_when_every_file_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(googlesheets, "REFUSED_LIMIT", 2)
    refused = {file_id: PERMISSION_ERROR for file_id in DISTINCT_FILES}
    first = await _fetch(
        "sheet_values", _listing_handler(DISTINCT_FILES, tabs=2, refused=refused), cursor=None
    )
    second = await _fetch(
        "sheet_values",
        _listing_handler(DISTINCT_FILES, tabs=2, refused=refused),
        cursor=first.next_cursor,
    )

    assert first.next_cursor is not None
    assert json.loads(first.next_cursor) == {
        "watermark": DISTINCT_FILES["d5"],
        "refused": ["d0", "d1"],
    }
    assert second.next_cursor == first.next_cursor


async def test_a_carried_refusal_whose_file_is_gone_leaves_the_cursor() -> None:
    modified = dict(BARREN_FILES)
    refused = {"s1": PERMISSION_ERROR}
    first = await _fetch(
        "spreadsheets", _listing_handler(modified, tabs=2, refused=refused), cursor=None
    )
    del modified["s1"]
    paths: list[str] = []
    second = await _fetch(
        "spreadsheets",
        _listing_handler(modified, tabs=2, refused=refused, paths=paths),
        cursor=first.next_cursor,
    )
    settled: list[str] = []
    third = await _fetch(
        "spreadsheets",
        _listing_handler(modified, tabs=2, refused=refused, paths=settled),
        cursor=second.next_cursor,
    )

    assert paths.count("/drive/v3/files/s1") == 1
    assert second.pages == ()
    assert second.next_cursor == BARREN_FILES["s1"]
    assert "/drive/v3/files/s1" not in settled
    assert third.next_cursor == second.next_cursor


async def test_a_carried_refusal_the_member_trashes_leaves_the_cursor() -> None:
    modified = dict(BARREN_FILES)
    refused = {"s1": PERMISSION_ERROR}
    first = await _fetch(
        "spreadsheets", _listing_handler(modified, tabs=2, refused=refused), cursor=None
    )
    paths: list[str] = []
    second = await _fetch(
        "spreadsheets",
        _listing_handler(modified, tabs=2, refused=refused, trashed=("s1",), paths=paths),
        cursor=first.next_cursor,
    )
    settled: list[str] = []
    third = await _fetch(
        "spreadsheets",
        _listing_handler(modified, tabs=2, refused=refused, trashed=("s1",), paths=settled),
        cursor=second.next_cursor,
    )

    assert paths.count("/drive/v3/files/s1") == 1
    assert not [path for path in paths if path.startswith("/v4/spreadsheets/s1")]
    assert second.pages == ()
    assert second.next_cursor == BARREN_FILES["s1"]
    assert "/drive/v3/files/s1" not in settled
    assert third.next_cursor == second.next_cursor


async def test_a_carried_refusal_whose_drive_row_is_refused_stays_carried() -> None:
    modified = dict(BARREN_FILES) | {"s2": ARRIVED_TIME}
    refused = {"s1": PERMISSION_ERROR}
    first = await _fetch(
        "spreadsheets", _listing_handler(modified, tabs=2, refused=refused), cursor=None
    )
    second = await _fetch(
        "spreadsheets",
        _listing_handler(
            modified,
            tabs=2,
            refused=refused,
            drive_refused={"s1": (403, {"json": PERMISSION_ERROR})},
        ),
        cursor=first.next_cursor,
    )

    assert {page.source_ref for page in second.pages} == {"spreadsheets/s2"}
    assert second.next_cursor is not None
    assert json.loads(second.next_cursor) == {"watermark": ARRIVED_TIME, "refused": ["s1"]}


@pytest.mark.parametrize(
    ("status", "body"),
    [(403, {"json": QUOTA_ERROR}), (404, {"json": PROXY_ERROR})],
    ids=["quota", "no-google-error-body"],
)
async def test_a_carried_files_get_neither_absorbs_a_quota_nor_drops_a_bodyless_404(
    status: int, body: dict[str, Any]
) -> None:
    modified = dict(BARREN_FILES) | {"s2": ARRIVED_TIME}
    refused = {"s1": PERMISSION_ERROR}
    first = await _fetch(
        "spreadsheets", _listing_handler(modified, tabs=2, refused=refused), cursor=None
    )
    with pytest.raises(httpx.HTTPStatusError) as raised:
        await _fetch(
            "spreadsheets",
            _listing_handler(modified, tabs=2, drive_refused={"s1": (status, body)}),
            cursor=first.next_cursor,
        )
    healed = await _fetch(
        "spreadsheets", _listing_handler(modified, tabs=2), cursor=first.next_cursor
    )

    assert raised.value.response.status_code == status
    assert {page.source_ref for page in healed.pages} == {"spreadsheets/s1", "spreadsheets/s2"}
    assert healed.next_cursor == ARRIVED_TIME


async def test_a_cursor_that_parses_as_json_but_not_an_object_is_an_opaque_bound() -> None:
    seen: list[str] = []
    result = await _fetch("spreadsheets", _handler(seen), cursor="2026")

    assert seen and all("modifiedTime >= '2026'" in query for query in seen)
    assert result.next_cursor == SPREADSHEET_FILE["modifiedTime"]


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
