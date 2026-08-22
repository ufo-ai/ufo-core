"""Google Drive connector over a mock transport: the first-run file list that seeds the changes
`startPageToken` cursor, the delta run that upserts changed files and tombstones removed ones while
advancing the token, the shared-drive list, the per-file children fan-out (comments), the `render`
override that lifts a file's name/mimeType/owners, and `StreamSkipped` on a scope refusal. Offline —
a canned transport, no DB, no token, no broker."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
from ufo_ext_sources.providers.googledrive import GoogleDriveConnector

from ufo.access.connectors import Credential
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
    return await ConnectorBackend(connector=GoogleDriveConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, _auth(handler)
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


async def test_first_run_lists_files_and_seeds_the_changes_token_cursor() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.googleapis.com"
        if request.url.path == "/drive/v3/files":
            return httpx.Response(200, json={"files": [FILE_1, FILE_2], "nextPageToken": None})
        if request.url.path == "/drive/v3/changes/startPageToken":
            return httpx.Response(200, json={"startPageToken": "tok-100"})
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("files", handle)
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


async def test_delta_run_upserts_changes_tombstones_removals_and_advances_the_token() -> None:
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

    result = await _fetch("files", handle, cursor="tok-100")
    assert seen == ["tok-100"]
    assert {page.source_ref for page in result.pages} == {"files/f3"}
    assert result.deletes == ("files/f1",)
    assert result.next_cursor == "tok-200"


async def test_shared_drives_lists_the_collection() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/drive/v3/drives":
            return httpx.Response(
                200,
                json={
                    "drives": [
                        {
                            "id": "d1",
                            "name": "Engineering",
                            "createdTime": "2026-01-01T00:00:00.000Z",
                        }
                    ],
                    "nextPageToken": None,
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("shared_drives", handle)
    assert {page.source_ref for page in result.pages} == {"shared_drives/d1"}
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert "Engineering" in result.pages[0].body


async def test_comments_fan_out_per_file() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/drive/v3/files":
            return httpx.Response(200, json={"files": [FILE_1], "nextPageToken": None})
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
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("comments", handle)
    assert {page.source_ref for page in result.pages} == {"comments/c1"}
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"
    assert "Looks good" in result.pages[0].body


async def test_scope_refusal_yields_stream_skipped() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": 403, "message": "insufficientScopes"}})

    try:
        await _fetch("files", refuse)
    except StreamSkipped:
        return
    raise AssertionError("a 403 from Drive must raise StreamSkipped")
