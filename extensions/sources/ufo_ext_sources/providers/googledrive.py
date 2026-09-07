"""The Google Drive connector — files, shared drives, and per-file permissions, comments, and
revisions synced as recallable metadata.

`files` is a delta stream: the first run enumerates the grant's live files
(`GET /drive/v3/files`, untrashed, ordered by `modifiedTime`) and then reads the changes
`startPageToken`, which becomes the cursor; a subsequent run walks `GET /drive/v3/changes` from that
token, upserting changed files, tombstoning removed or trashed ones, and advancing the cursor to the
next page (or the fresh `newStartPageToken` at the end). A `410` on the changes token means it
expired, so the connector raises `CursorExpired` and core refetches fresh. `shared_drives` re-reads
the whole set each run; `permissions`, `comments`, and `revisions` fan out over every file to its
sub-collection. A grant that lacks the Drive scope (`401`/`403`) yields `StreamSkipped` so the run
records a skip, not a failure; a refusal naming a usage limit instead of the grant raises
(`ufo_ext_sources.providers.google`). `render` lifts a file's name, mime type, owners, and link
into a readable body. The credential is resolved through the auth proxy the runner threads — this
connector holds no token. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.sources import (
    CursorExpired,
    RestConnector,
    StreamPage,
    StreamSkipped,
    StreamSpec,
    list_or_empty,
)
from ufo_ext_sources.providers import google
from ufo_ext_sources.watermark import text_checkpoint

PAGE_SIZE = 1000
CHILD_PAGE_SIZE = 100
DRIVE_PAGE_SIZE = 100
_CHILD_REFUSAL_STATUS = frozenset({403, 404})
FILE_FIELDS = (
    "nextPageToken,files(id,name,mimeType,webViewLink,createdTime,modifiedTime,"
    "owners(emailAddress,displayName),parents,driveId,trashed,size)"
)
CHANGE_FIELDS = (
    "nextPageToken,newStartPageToken,"
    "changes(fileId,removed,file(id,name,mimeType,webViewLink,createdTime,"
    "modifiedTime,owners(emailAddress,displayName),parents,driveId,trashed,size))"
)

GOOGLE_DRIVE_STREAMS: list[StreamSpec] = [
    StreamSpec(
        name="files",
        source_object="files",
        primary_key="id",
        cursor_field="modifiedTime",
        created_at_field="createdTime",
        updated_at_field="modifiedTime",
        canonical=True,
    ),
    StreamSpec(
        name="shared_drives",
        source_object="drives",
        primary_key="id",
        created_at_field="createdTime",
    ),
    StreamSpec(name="permissions", source_object="permissions", primary_key="id"),
    StreamSpec(
        name="comments",
        source_object="comments",
        primary_key="id",
        cursor_field="modifiedTime",
        created_at_field="createdTime",
        updated_at_field="modifiedTime",
    ),
    StreamSpec(
        name="revisions",
        source_object="revisions",
        primary_key="id",
        cursor_field="modifiedTime",
        updated_at_field="modifiedTime",
    ),
]


class GoogleDriveConnector(RestConnector):
    name = "googledrive"
    base_url = "https://www.googleapis.com"
    streams_list = GOOGLE_DRIVE_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            if stream.name == "files":
                if cursor:
                    async for change_page in self._paginate_file_changes(client, cursor=cursor):
                        yield change_page
                    return
                async for file_page in self._paginate_files(client, cursor=None):
                    yield file_page
                start_token = await self._start_page_token(client)
                if start_token:
                    yield StreamPage(next_cursor=start_token)
                return
            if stream.name == "shared_drives":
                async for drive_page in self._paginate_shared_drives(client):
                    yield drive_page
                return
            if stream.name in {"permissions", "comments", "revisions"}:
                async for child_page in self._paginate_file_children(client, stream, cursor=cursor):
                    yield child_page
                return
            raise NotImplementedError(
                f"googledrive: stream {stream.name!r} has no paginate dispatch"
            )
        except httpx.HTTPStatusError as error:
            if google.refused_for_scope(error):
                raise StreamSkipped(
                    f"googledrive: {stream.name!r} refused ({error.response.status_code}); the "
                    "grant lacks the Drive scope"
                ) from error
            raise

    async def _paginate_files(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        token: str | None = None
        query = "trashed = false"
        if cursor:
            query = f"{query} and modifiedTime > '{cursor}'"
        while True:
            params: dict[str, Any] = {
                "pageSize": PAGE_SIZE,
                "fields": FILE_FIELDS,
                "q": query,
                "orderBy": "modifiedTime",
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

    async def _start_page_token(self, client: httpx.AsyncClient) -> str | None:
        data = await self._get(
            client, "/drive/v3/changes/startPageToken", params={"supportsAllDrives": "true"}
        )
        token = data.get("startPageToken")
        return token if isinstance(token, str) and token else None

    async def _paginate_file_changes(
        self, client: httpx.AsyncClient, *, cursor: str
    ) -> AsyncIterator[StreamPage]:
        token: str | None = cursor
        while token:
            params: dict[str, Any] = {
                "pageToken": token,
                "pageSize": PAGE_SIZE,
                "fields": CHANGE_FIELDS,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "includeRemoved": "true",
            }
            try:
                data = await self._get(client, "/drive/v3/changes", params=params)
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 410:
                    raise CursorExpired("googledrive changes token expired") from error
                raise
            records: list[dict[str, Any]] = []
            deletes: list[str] = []
            for change in list_or_empty(data.get("changes")):
                file_id = change.get("fileId")
                if not isinstance(file_id, str) or not file_id:
                    continue
                file = change.get("file")
                if change.get("removed") is True or (
                    isinstance(file, dict) and file.get("trashed") is True
                ):
                    deletes.append(file_id)
                    continue
                if isinstance(file, dict):
                    file.setdefault("id", file_id)
                    records.append(file)
            next_token = data.get("nextPageToken")
            if isinstance(next_token, str) and next_token:
                token = next_token
                yield StreamPage(records=records, deletes=tuple(deletes), next_cursor=next_token)
                continue
            start_token = data.get("newStartPageToken")
            yield StreamPage(
                records=records,
                deletes=tuple(deletes),
                next_cursor=start_token if isinstance(start_token, str) else None,
            )
            return

    async def _paginate_shared_drives(
        self, client: httpx.AsyncClient
    ) -> AsyncIterator[list[dict[str, Any]]]:
        token: str | None = None
        while True:
            params: dict[str, Any] = {
                "pageSize": DRIVE_PAGE_SIZE,
                "fields": "nextPageToken,drives(id,name,createdTime,hidden,capabilities)",
            }
            if token:
                params["pageToken"] = token
            data = await self._get(client, "/drive/v3/drives", params=params)
            records = list_or_empty(data.get("drives"))
            if records:
                yield records
            token = data.get("nextPageToken")
            if not isinstance(token, str) or not token:
                return

    async def _paginate_file_children(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for files in self._paginate_files(client, cursor=None):
            for file in files:
                file_id = file.get("id")
                if not isinstance(file_id, str) or not file_id:
                    continue
                path = f"/drive/v3/files/{file_id}/{stream.source_object}"
                token: str | None = None
                while True:
                    params: dict[str, Any] = {
                        "pageSize": CHILD_PAGE_SIZE,
                        "fields": "nextPageToken,*",
                        "supportsAllDrives": "true",
                    }
                    if token:
                        params["pageToken"] = token
                    try:
                        data = await self._get(client, path, params=params)
                    except httpx.HTTPStatusError as error:
                        if error.response.status_code in _CHILD_REFUSAL_STATUS:
                            break
                        raise
                    records = list_or_empty(data.get(stream.source_object))
                    if cursor and stream.cursor_field:
                        records = [
                            record
                            for record in records
                            if str(record.get(stream.cursor_field) or "") > cursor
                        ]
                    if records:
                        yield [
                            {**record, "file_id": file_id, "file_name": file.get("name")}
                            for record in records
                        ]
                    token = data.get("nextPageToken")
                    if not isinstance(token, str) or not token:
                        break

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        if stream.name != "files":
            return super().render(record, stream)
        title = _str(record.get("name"))
        lines = [f"# googledrive files: {title}".rstrip(), f"mimeType: {record.get('mimeType')}"]
        owners = ", ".join(
            owner.get("displayName") or owner.get("emailAddress") or ""
            for owner in record.get("owners") or []
            if isinstance(owner, dict)
        ).strip(", ")
        if owners:
            lines.append(f"owners: {owners}")
        link = record.get("webViewLink")
        if isinstance(link, str) and link:
            lines.append(f"link: {link}")
        return title, "\n".join(lines).strip()


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""
