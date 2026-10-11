"""Google Drive connector over a mock transport: the first-run file list that seeds the changes
`startPageToken` cursor, the delta run that upserts changed files and tombstones removed ones while
advancing the token, the shared-drive list, the three per-file children declared as edges under
`files`, the `render` override that lifts a file's name/mimeType/owners, and `StreamSkipped` on a
scope refusal. Offline — a canned transport, no DB, no token, no broker."""

import json
from collections.abc import Callable, Mapping
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.googledrive import GoogleDriveConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources import backend as backend_module
from ufo.runtime.sources.backend import BACKFILL_KEY
from ufo.runtime.sources.sync import CursorExpired, SourceAuth, StreamSkipped
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig, ParentPages, ParentRecord

ParentsReader = Callable[[Mapping[str, tuple[ParentRecord, ...]]], ParentPages]
LANDED: Mapping[str, tuple[ParentRecord, ...]] = {
    "files": (
        ParentRecord(ref="files/f1", fields={"id": "f1"}),
        ParentRecord(ref="files/f2", fields={"id": "f2"}),
    )
}


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    parents: ParentPages,
    cursor: str | None = None,
):
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler), parents=parents)
    return await ConnectorBackend(connector=GoogleDriveConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


FILE_1 = {
    "id": "f1",
    "name": "Roadmap",
    "mimeType": "application/vnd.google-apps.document",
    "webViewLink": "https://drive.google.com/file/f1",
    "createdTime": "2026-01-01T00:00:00.000Z",
    "modifiedTime": "2026-02-01T00:00:00.000Z",
    "owners": [{"displayName": "Alex", "emailAddress": "alex@example.com"}],
}
FILE_2 = {
    "id": "f2",
    "name": "Budget",
    "mimeType": "application/vnd.google-apps.spreadsheet",
    "webViewLink": "https://drive.google.com/file/f2",
    "createdTime": "2026-01-02T00:00:00.000Z",
    "modifiedTime": "2026-02-05T00:00:00.000Z",
    "owners": [{"emailAddress": "sam@example.com"}],
}
FILE_3 = {
    "id": "f3",
    "name": "New Deck",
    "mimeType": "application/vnd.google-apps.presentation",
    "createdTime": "2026-01-03T00:00:00.000Z",
    "modifiedTime": "2026-02-10T00:00:00.000Z",
    "owners": [],
}


async def test_first_run_lists_files_and_seeds_the_changes_token_cursor(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.googleapis.com"
        if request.url.path == "/drive/v3/files":
            return httpx.Response(200, json={"files": [FILE_1, FILE_2], "nextPageToken": None})
        if request.url.path == "/drive/v3/changes/startPageToken":
            return httpx.Response(200, json={"startPageToken": "tok-100"})
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("files", handle, parents=parents_reader(LANDED))
    assert {page.source_ref for page in result.pages} == {"files/f1", "files/f2"}
    assert result.snapshot is False
    assert result.next_cursor == "tok-100"

    page = next(page for page in result.pages if page.source_ref == "files/f1")
    assert page.created_at == "2026-01-01T00:00:00.000000+00:00"
    assert page.updated_at == "2026-02-01T00:00:00.000000+00:00"
    body = page.body
    assert "Roadmap" in body
    assert "mimeType: application/vnd.google-apps.document" in body
    assert "owners: Alex" in body


async def test_delta_run_upserts_changes_tombstones_removals_and_advances_the_token(
    parents_reader: ParentsReader,
) -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/drive/v3/changes":
            seen.append(request.url.params.get("pageToken", ""))
            return httpx.Response(
                200,
                json={
                    "changes": [
                        {"fileId": "f3", "file": FILE_3},
                        {"fileId": "f1", "removed": True},
                    ],
                    "newStartPageToken": "tok-200",
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("files", handle, parents=parents_reader(LANDED), cursor="tok-100")
    assert seen == ["tok-100"]
    assert {page.source_ref for page in result.pages} == {"files/f3"}
    assert result.deletes == ("files/f1",)
    assert result.next_cursor == "tok-200"


async def test_comments_fan_out_per_file(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/drive/v3/files/f1/comments":
            return httpx.Response(
                200,
                json={
                    "comments": [
                        {
                            "id": "c1",
                            "content": "Looks good",
                            "createdTime": "2026-01-01T00:00:00.000Z",
                            "modifiedTime": "2026-02-01T00:00:00.000Z",
                        }
                    ],
                    "nextPageToken": None,
                },
            )
        if request.url.path == "/drive/v3/files/f2/comments":
            return httpx.Response(200, json={"comments": [], "nextPageToken": None})
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("comments", handle, parents=parents_reader(LANDED))
    assert {page.source_ref for page in result.pages} == {"comments/f1/c1"}
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"
    assert "Looks good" in result.pages[0].body


@pytest.mark.parametrize(
    ("stream", "key"),
    [("permissions", "perm-a"), ("comments", "c1"), ("revisions", "1")],
)
async def test_one_key_under_two_files_is_two_pages(
    stream: str, key: str, parents_reader: ParentsReader
) -> None:
    """A Drive child's id is unique inside one file and nowhere else: a user's permission id is
    the same value on every file shared with them, and a revision numbers from `1` per file."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path in {f"/drive/v3/files/{file}/{stream}" for file in ("f1", "f2")}:
            return httpx.Response(200, json={stream: [{"id": key}]})
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(stream, handle, parents=parents_reader(LANDED))
    assert {page.source_ref for page in result.pages} == {
        f"{stream}/f1/{key}",
        f"{stream}/f2/{key}",
    }


async def test_a_child_spends_no_request_on_the_file_listing(parents_reader: ParentsReader) -> None:
    """The files a child fans over are the `files` stream's landed pages, so the child's own run
    never walks `/drive/v3/files` again — the redundancy the flat declaration paid every tick."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path.endswith("/permissions"):
            return httpx.Response(200, json={"permissions": [{"id": "perm-a"}]})
        return httpx.Response(404, json={"path": request.url.path})

    await _fetch("permissions", handle, parents=parents_reader(LANDED))
    assert asked == ["/drive/v3/files/f1/permissions", "/drive/v3/files/f2/permissions"]


async def test_a_refused_file_drops_out_and_the_rest_land(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/drive/v3/files/f1/permissions":
            return httpx.Response(404, json={"error": {"code": 404, "message": "File not found"}})
        if request.url.path == "/drive/v3/files/f2/permissions":
            return httpx.Response(200, json={"permissions": [{"id": "perm-a"}]})
        return httpx.Response(500, json={"path": request.url.path})

    result = await _fetch("permissions", handle, parents=parents_reader(LANDED))
    assert {page.source_ref for page in result.pages} == {"permissions/f2/perm-a"}


async def test_a_landed_file_carries_the_id_its_children_read(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/drive/v3/files":
            return httpx.Response(200, json={"files": [FILE_1]})
        if request.url.path == "/drive/v3/changes/startPageToken":
            return httpx.Response(200, json={"startPageToken": "tok-1"})
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("files", handle, parents=parents_reader(LANDED))
    assert result.pages[0].parent_fields == {"id": "f1"}


async def test_scope_refusal_yields_stream_skipped(parents_reader: ParentsReader) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": 403, "message": "insufficientScopes"}})

    try:
        await _fetch("files", refuse, parents=parents_reader(LANDED))
    except StreamSkipped:
        return
    raise AssertionError("a 403 from Drive must raise StreamSkipped")


QUOTA_ERRORS = pytest.mark.parametrize(
    "error",
    [
        {
            "code": 403,
            "message": "Rate Limit Exceeded",
            "errors": [{"reason": "rateLimitExceeded"}],
        },
        {"code": 403, "message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"},
    ],
    ids=["usage-limits-reason", "resource-exhausted-status"],
)


FILE_4 = {**FILE_3, "id": "f4", "name": "Notes"}
LISTED = {
    None: ([FILE_1], "p2"),
    "p2": ([FILE_2], "p3"),
    "p3": ([FILE_3], "p4"),
    "p4": ([FILE_4], None),
}


def _listing(
    asked: list[str | None], refused: Mapping[str, httpx.Response] | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/drive/v3/changes/startPageToken":
            asked.append("start")
            return httpx.Response(200, json={"startPageToken": "tok-7"})
        token = request.url.params.get("pageToken")
        asked.append(token)
        if token in (refused or {}):
            return (refused or {})[token]
        files, next_token = LISTED[token]
        return httpx.Response(200, json={"files": files, "nextPageToken": next_token})

    return handle


def _cursor_at(page: str) -> str:
    return json.dumps({"start": "tok-7", "page": page})


@QUOTA_ERRORS
async def test_a_quota_refusal_mid_listing_yields_a_cursor_at_the_refused_page(
    error: dict[str, object], parents_reader: ParentsReader
) -> None:
    asked: list[str | None] = []
    refused = {"p2": httpx.Response(403, json={"error": error})}

    result = await _fetch("files", _listing(asked, refused), parents=parents_reader(LANDED))

    assert asked == ["start", None, "p2"]
    assert {page.source_ref for page in result.pages} == {"files/f1"}
    assert result.retry_after_seconds == 60
    assert json.loads(result.next_cursor or "") == json.loads(_cursor_at("p2"))


async def test_a_resumed_listing_reads_from_its_page_and_ends_on_the_start_token(
    parents_reader: ParentsReader,
) -> None:
    asked: list[str | None] = []

    result = await _fetch(
        "files", _listing(asked), parents=parents_reader(LANDED), cursor=_cursor_at("p2")
    )

    assert asked == ["p2", "p3", "p4"]
    assert {page.source_ref for page in result.pages} == {"files/f2", "files/f3", "files/f4"}
    assert result.next_cursor == "tok-7"


async def test_a_listing_page_token_drive_rejects_expires_the_cursor(
    parents_reader: ParentsReader,
) -> None:
    rejected = {
        "code": 400,
        "message": "Invalid Value",
        "errors": [{"reason": "invalid", "location": "pageToken", "locationType": "parameter"}],
    }
    refused = {"p2": httpx.Response(400, json={"error": rejected})}

    with pytest.raises(CursorExpired):
        await _fetch(
            "files",
            _listing([], refused),
            parents=parents_reader(LANDED),
            cursor=_cursor_at("p2"),
        )


async def test_a_listing_400_naming_no_page_token_fails_the_run(
    parents_reader: ParentsReader,
) -> None:
    rejected = {"code": 400, "message": "Invalid Value", "errors": [{"reason": "invalid"}]}
    refused = {"p2": httpx.Response(400, json={"error": rejected})}

    with pytest.raises(httpx.HTTPStatusError):
        await _fetch(
            "files",
            _listing([], refused),
            parents=parents_reader(LANDED),
            cursor=_cursor_at("p2"),
        )


async def test_a_stored_skip_envelope_resumes_onto_a_listing_cursor(
    parents_reader: ParentsReader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 1)
    envelope = json.dumps(
        {BACKFILL_KEY: {"origin": None, "skip": 1, "watermark": None}}, sort_keys=True
    )

    result = await _fetch("files", _listing([]), parents=parents_reader(LANDED), cursor=envelope)

    assert {page.source_ref for page in result.pages} == {"files/f2", "files/f3"}
    assert json.loads(result.next_cursor or "") == json.loads(_cursor_at("p4"))
