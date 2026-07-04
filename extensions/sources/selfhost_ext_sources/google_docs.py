"""The Google Docs connector — every Google Doc a grant can reach, synced as recallable prose.

A Google Doc's text lives in a nested `body.content` tree of structural elements — paragraphs whose
`elements[].textRun.content` carry the characters, heading styles, bullet lists, and tables — not in
flat columns, so this is a connector that overrides `render`: the default JSON dump of that tree is
unrecallable. `render` walks the tree into readable plain text — a heading keeps its level as a
markdown prefix, a bulleted item as a dash, a table row as pipe-separated cells — so a synced
document recalls as what a member would read.

Reads are a Drive-list→Docs-get fan-out: Drive enumerates the grant's Google Docs
(`GET /drive/v3/files` filtered to the `application/vnd.google-apps.document` mime type, untrashed,
ordered by `modifiedTime` and paged by `nextPageToken`), and each file id is fetched in full through
the Docs API (`GET https://docs.googleapis.com/v1/documents/{id}`). The stream is incremental: the
Drive query filters server-side past the stored `modifiedTime` watermark, and each record lifts that
time to a flat `updated_at` the sync advances a cursor over. A grant that lacks the Drive or Docs
scope, or was not shared the document (`401`/`403`), yields `StreamSkipped` so the run records a
skip, not a failure. The credential is resolved through the auth proxy the runner threads — this
connector holds no token. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from selfhost.sdk.sources import StreamSkipped
from selfhost_ext_sources.connector import StreamSpec
from selfhost_ext_sources.rest import RestConnector, get_path, list_or_empty

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
_HEADING_PREFIX: dict[str, str] = {
    "TITLE": "#",
    "SUBTITLE": "##",
    "HEADING_1": "#",
    "HEADING_2": "##",
    "HEADING_3": "###",
    "HEADING_4": "####",
    "HEADING_5": "#####",
    "HEADING_6": "######",
}

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
        if stream.name != "documents":
            raise NotImplementedError(
                f"google_docs: stream {stream.name!r} has no paginate dispatch"
            )
        page: list[dict[str, Any]] = []
        try:
            async for file in self._iter_doc_files(client, cursor=cursor):
                file_id = file.get("id")
                if not isinstance(file_id, str) or not file_id:
                    continue
                doc = await self._get(client, f"{DOCS_API_URL}/documents/{file_id}")
                page.append(
                    {
                        **doc,
                        "documentId": doc.get("documentId") or file_id,
                        "title": doc.get("title") or file.get("name"),
                        "url": file.get("webViewLink"),
                        "created_at": file.get("createdTime"),
                        "updated_at": file.get("modifiedTime"),
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

    async def _iter_doc_files(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[dict[str, Any]]:
        """Enumerate the grant's Google Docs from Drive, filtered server-side past the stored
        `modifiedTime` watermark, ascending, one file at a time across `nextPageToken` pages."""
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
            for file in list_or_empty(data.get("files")):
                yield file
            token = data.get("nextPageToken")
            if not isinstance(token, str) or not token:
                return

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        if stream.name != "documents":
            return super().render(record, stream)
        title = _str(record.get("title"))
        body = "\n".join(_structural_elements_text(get_path(record, "body.content"))).strip()
        heading = f"# google_docs {stream.name}: {title}".rstrip()
        return title, f"{heading}\n\n{body}".rstrip()


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _structural_elements_text(content: Any) -> list[str]:
    lines: list[str] = []
    for item in content if isinstance(content, list) else []:
        if not isinstance(item, dict):
            continue
        paragraph = item.get("paragraph")
        table = item.get("table")
        toc = item.get("tableOfContents")
        if isinstance(paragraph, dict):
            line = _paragraph_text(paragraph)
            if line:
                lines.append(line)
        elif isinstance(table, dict):
            lines.extend(_table_text(table))
        elif isinstance(toc, dict):
            lines.extend(_structural_elements_text(toc.get("content")))
    return lines


def _paragraph_text(paragraph: dict[str, Any]) -> str:
    """A paragraph's runs joined, prefixed by a markdown heading level or a bullet dash."""
    text = "".join(
        _text_run_content(element)
        for element in paragraph.get("elements") or []
        if isinstance(element, dict)
    ).strip()
    if not text:
        return ""
    style = paragraph.get("paragraphStyle")
    named = style.get("namedStyleType") if isinstance(style, dict) else None
    prefix = _HEADING_PREFIX.get(named) if isinstance(named, str) else None
    if prefix:
        return f"{prefix} {text}"
    if isinstance(paragraph.get("bullet"), dict):
        return f"- {text}"
    return text


def _text_run_content(element: dict[str, Any]) -> str:
    run = element.get("textRun")
    content = run.get("content") if isinstance(run, dict) else None
    return content if isinstance(content, str) else ""


def _table_text(table: dict[str, Any]) -> list[str]:
    """Each table row as its cells' text joined by a pipe, cell text lifted from the cell's own
    nested structural elements."""
    rows: list[str] = []
    for row in table.get("tableRows") or []:
        if not isinstance(row, dict):
            continue
        cells = [
            " ".join(_structural_elements_text(cell.get("content")))
            for cell in row.get("tableCells") or []
            if isinstance(cell, dict)
        ]
        line = " | ".join(cell for cell in cells if cell)
        if line:
            rows.append(line)
    return rows
