"""The Google Drive connector — files, shared drives, and per-file permissions, comments, and
revisions synced as recallable metadata.

`files` is a delta stream: the first run reads the changes `startPageToken`, then enumerates the
grant's live files (`GET /drive/v3/files`, untrashed, ordered by `modifiedTime`), checkpointing each
page as a `_Listing` of that token and the next `pageToken`, so a capped or rate-limited listing
resumes at its page; the token is read first so a change made mid-listing replays rather than being
lost. When the listing ends the token becomes the cursor, and a subsequent run walks
`GET /drive/v3/changes` from it, upserting changed files, tombstoning removed or trashed ones, and
advancing the cursor to the next page (or the fresh `newStartPageToken` at the end). A `410` on the
changes token, or a `400` naming the listing's `pageToken`, means the cursor expired, so the
connector raises `CursorExpired` and core refetches fresh. `shared_drives` re-reads
the whole set each run; `permissions`, `comments`, and `revisions` each declare one edge under
`files`. Their ids are unique inside one file and nowhere else — a user's permission id is the same
value on every file shared with them, a revision numbers from `1` per file. A grant that lacks the
Drive scope (`401`/`403`) yields `StreamSkipped` so the run records a skip, not a failure; a refusal
naming a usage limit instead of the grant is a rate limit (`ufo_ext_sources.providers.google`).
`render` lifts a file's name, mime type, owners, and link into a readable body. The credential is
resolved through the auth proxy the runner threads — this connector holds no token. The write path
is intentionally absent — the source seam only reads."""

import json
from collections.abc import AsyncIterator
from functools import partial
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from ufo.sdk.sources import (
    CursorExpired,
    ParentEdge,
    Partition,
    PartitionBound,
    PartitionSkipped,
    RestConnector,
    Run,
    StreamPage,
    StreamSkipped,
    StreamSpec,
    WalkPage,
    fanned_out,
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


class _Listing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str
    page: str


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
    StreamSpec(
        name="permissions",
        source_object="permissions",
        primary_key="id",
        parents=(ParentEdge(stream="files", path="/drive/v3/files/{id}/permissions"),),
    ),
    StreamSpec(
        name="comments",
        source_object="comments",
        primary_key="id",
        created_at_field="createdTime",
        updated_at_field="modifiedTime",
        parents=(ParentEdge(stream="files", path="/drive/v3/files/{id}/comments"),),
    ),
    StreamSpec(
        name="revisions",
        source_object="revisions",
        primary_key="id",
        updated_at_field="modifiedTime",
        parents=(ParentEdge(stream="files", path="/drive/v3/files/{id}/revisions"),),
    ),
]


class GoogleDriveConnector(RestConnector):
    name = "googledrive"
    base_url = "https://www.googleapis.com"
    streams_list = GOOGLE_DRIVE_STREAMS
    rate_limited = staticmethod(google.rate_limited)
    checkpoint = staticmethod(text_checkpoint)

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            if stream.name == "files":
                listing = _decode_listing(run.cursor)
                if run.cursor and listing is None:
                    async for change_page in self._paginate_file_changes(client, cursor=run.cursor):
                        yield change_page
                    return
                if listing is None:
                    listing_start, listing_page = await self._start_page_token(client), None
                else:
                    listing_start, listing_page = listing.start, listing.page
                async for file_page in self._paginate_files(
                    client, start=listing_start, page=listing_page
                ):
                    yield file_page
                return
            if stream.name == "shared_drives":
                async for drive_page in self._paginate_shared_drives(client):
                    yield drive_page
                return
            if not stream.parents:
                raise NotImplementedError(
                    f"googledrive: stream {stream.name!r} has no paginate dispatch"
                )
            pages = partial(self._child_pages, client, stream)
            async for page in fanned_out(stream, run, pages):
                yield page
        except httpx.HTTPStatusError as error:
            if google.refused_for_scope(error):
                raise StreamSkipped(
                    f"googledrive: {stream.name!r} refused ({error.response.status_code}); the "
                    "grant lacks the Drive scope"
                ) from error
            raise

    async def _paginate_files(
        self, client: httpx.AsyncClient, *, start: str, page: str | None
    ) -> AsyncIterator[StreamPage]:
        token = page
        while True:
            params: dict[str, Any] = {
                "pageSize": PAGE_SIZE,
                "fields": FILE_FIELDS,
                "q": "trashed = false",
                "orderBy": "modifiedTime",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "corpora": "allDrives",
            }
            if token:
                params["pageToken"] = token
            try:
                data = await self._get(client, "/drive/v3/files", params=params)
            except httpx.HTTPStatusError as error:
                named = list_or_empty(google.error_detail(error).get("errors"))
                if error.response.status_code == 400 and any(
                    item.get("location") == "pageToken" for item in named
                ):
                    raise CursorExpired("googledrive listing pageToken expired") from error
                raise
            records = list_or_empty(data.get("files"))
            token = data.get("nextPageToken")
            if not isinstance(token, str) or not token:
                yield StreamPage(records=records, next_cursor=start)
                return
            yield StreamPage(
                records=records, next_cursor=_Listing(start=start, page=token).model_dump_json()
            )

    async def _start_page_token(self, client: httpx.AsyncClient) -> str:
        data = await self._get(
            client, "/drive/v3/changes/startPageToken", params={"supportsAllDrives": "true"}
        )
        token = data.get("startPageToken")
        if not isinstance(token, str) or not token:
            raise RuntimeError(f"googledrive: startPageToken answered no token: {data!r}")
        return token

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

    async def _child_pages(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        partition: Partition,
        bound: PartitionBound,
    ) -> AsyncIterator[WalkPage]:
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
                data = await self._get(client, partition.path, params=params)
            except httpx.HTTPStatusError as error:
                if error.response.status_code in _CHILD_REFUSAL_STATUS:
                    raise PartitionSkipped(f"googledrive: {partition.ref} refused") from error
                raise
            yield WalkPage(records=list_or_empty(data.get(stream.source_object)))
            token = data.get("nextPageToken")
            if not isinstance(token, str) or not token:
                return

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


def _decode_listing(cursor: str | None) -> _Listing | None:
    if not cursor:
        return None
    try:
        parsed = json.loads(cursor)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    try:
        return _Listing.model_validate(parsed)
    except ValidationError as error:
        raise RuntimeError(f"googledrive: malformed listing cursor {cursor!r}") from error


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""
