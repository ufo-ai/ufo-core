"""The Google Docs connector — every Google Doc a grant can reach, synced as recallable prose.

Reads are a Drive-list→Docs-get fan-out: Drive enumerates the grant's Google Docs
(`GET /drive/v3/files` filtered to the `application/vnd.google-apps.document` mime type, untrashed,
ordered by `modifiedTime`, paged by `nextPageToken`) and each file id is fetched in full through the
Docs API (`GET https://docs.googleapis.com/v1/documents/{id}`). The stream is incremental: the Drive
query filters server-side past the stored `modifiedTime` watermark, and each record lifts that time
to a flat `updated_at` the sync advances a cursor over. A doc the grant can list but not open
(`403`/`404`) lands as a title-only stub so one unreadable file never fails the run; a Drive-list
refusal (`401`/`403`) yields `StreamSkipped`. A Google Doc's text lives in a nested `body.content`
tree of paragraphs, so `render` walks that tree into the readable prose a member would see. The
credential is resolved through the auth proxy the runner threads — this connector holds no token.
The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from selfhost.sdk.sources import StreamSkipped
from selfhost_ext_sources.connector import StreamSpec
from selfhost_ext_sources.rest import RestConnector, list_or_empty

DOC_MIME = "application/vnd.google-apps.document"
DOCS_API_URL = "https://docs.googleapis.com/v1"
DRIVE_FILES_PATH = "/drive/v3/files"
DRIVE_PAGE_SIZE = 1000
DOC_PAGE_SIZE = 100
DRIVE_FIELDS = (
    "nextPageToken,files(id,name,webViewLink,createdTime,modifiedTime,"
    "owners(emailAddress,displayName))"
)
_REFUSAL_STATUS = frozenset({401, 403})
_DOC_MISSING_STATUS = frozenset({403, 404})

GOOGLE_DOCS_STREAMS: list[StreamSpec] = [
    StreamSpec(
        name="documents",
        source_object="documents",
        primary_key="documentId",
        cursor_field="updated_at",
    ),
]


class GoogleDocsConnector(RestConnector):
    name = "google_docs"
    base_url = "https://www.googleapis.com"
    streams_list = GOOGLE_DOCS_STREAMS

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        page: list[dict[str, Any]] = []
        try:
            async for files in self._iter_doc_files(client, cursor=cursor):
                for file in files:
                    file_id = file.get("id")
                    if not isinstance(file_id, str) or not file_id:
                        continue
                    doc = await self._document(client, file_id)
                    page.append(
                        {
                            **doc,
                            "id": file_id,
                            "documentId": doc.get("documentId") or file_id,
                            "title": doc.get("title") or file.get("name"),
                            "kind": "document",
                            "mime_type": DOC_MIME,
                            "url": file.get("webViewLink"),
                            "created_at": file.get("createdTime"),
                            "updated_at": file.get("modifiedTime"),
                            "drive_file": file,
                        }
                    )
                    if len(page) >= DOC_PAGE_SIZE:
                        yield page
                        page = []
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"google_docs: {stream.name!r} refused ({error.response.status_code}); the "
                    "grant lacks the Drive or Docs scope, or was not shared the document"
                ) from error
            raise
        if page:
            yield page

    async def _document(self, client: httpx.AsyncClient, file_id: str) -> dict[str, Any]:
        """One document fetched in full from the Docs API. A doc the grant can list but not open
        (`403`) or that has vanished (`404`) lands as a title-less stub so one unreadable file never
        fails the whole run."""
        try:
            return await self._get(client, f"{DOCS_API_URL}/documents/{file_id}")
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _DOC_MISSING_STATUS:
                return {"documentId": file_id}
            raise

    async def _iter_doc_files(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Enumerate the grant's Google Docs from Drive, filtered server-side past the stored
        `modifiedTime` watermark, ascending, one page at a time across `nextPageToken`."""
        query = f"mimeType = '{DOC_MIME}' and trashed = false"
        if cursor:
            query = f"{query} and modifiedTime > '{cursor}'"
        token: str | None = None
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
            data = await self._get(client, DRIVE_FILES_PATH, params=params)
            files = list_or_empty(data.get("files"))
            if files:
                yield files
            token = data.get("nextPageToken")
            if not isinstance(token, str) or not token:
                return

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        title = record.get("title")
        title = title if isinstance(title, str) else ""
        body = _plain_text(record)
        heading = f"# google_docs {stream.name}: {title}".rstrip()
        return title, f"{heading}\n\n{body}".rstrip()


def _plain_text(record: dict[str, Any]) -> str:
    """A document's text: the character runs of every paragraph in its `body.content` tree, joined
    in order. Non-paragraph structural elements (tables, section breaks) carry no paragraph runs and
    fall through."""
    body = record.get("body")
    content = body.get("content") if isinstance(body, dict) else None
    chunks: list[str] = []
    for item in content if isinstance(content, list) else []:
        paragraph = item.get("paragraph") if isinstance(item, dict) else None
        if not isinstance(paragraph, dict):
            continue
        for element in paragraph.get("elements") or []:
            run = element.get("textRun") if isinstance(element, dict) else None
            text = run.get("content") if isinstance(run, dict) else None
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks).strip()
