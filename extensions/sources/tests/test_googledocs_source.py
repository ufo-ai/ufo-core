"""The Google Docs connector over a mock transport: the Drive-list→Docs-get fan-out, the incremental
`modifiedTime` watermark threaded into the Drive query, the per-doc `403` stub that keeps the run
going, a Drive-list refusal surfacing as `StreamSkipped`, and the `render` override that walks a
document's `body.content` paragraphs into readable prose. No conftest: the shared
`ufo_testsupport` plugin covers fixtures; these tests are offline (a canned transport, no DB,
no token, no broker)."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.googledocs import GoogleDocsConnector

from ufo.access.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped

ACCOUNT = "acct-1"


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None):
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler))
    return await ConnectorBackend(connector=GoogleDocsConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream="documents"), cursor, auth
    )


def _paragraph(text: str) -> dict:
    return {"paragraph": {"elements": [{"textRun": {"content": f"{text}\n"}}]}}


DOC_BODY = {
    "content": [
        _paragraph("Q3 Plan"),
        _paragraph("Ship the launch by Friday."),
        {"table": {"tableRows": [{"tableCells": [{"content": [_paragraph("Owner")]}]}]}},
    ]
}
FILE_1 = {
    "id": "doc1",
    "name": "Q3 Plan",
    "webViewLink": "https://docs.google.com/document/d/doc1",
    "createdTime": "2026-01-01T00:00:00.000Z",
    "modifiedTime": "2026-02-01T00:00:00.000Z",
}
FILE_2 = {
    "id": "doc2",
    "name": "Backlog",
    "webViewLink": "https://docs.google.com/document/d/doc2",
    "createdTime": "2026-01-02T00:00:00.000Z",
    "modifiedTime": "2026-02-05T00:00:00.000Z",
}


def _handler(seen_queries: list[str] | None = None) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.googleapis.com" and request.url.path == "/drive/v3/files":
            if seen_queries is not None:
                seen_queries.append(request.url.params.get("q", ""))
            return httpx.Response(200, json={"files": [FILE_1, FILE_2], "nextPageToken": None})
        if request.url.host == "docs.googleapis.com" and request.url.path.startswith(
            "/v1/documents/"
        ):
            doc_id = request.url.path.rsplit("/", 1)[-1]
            title = "Q3 Plan" if doc_id == "doc1" else "Backlog"
            return httpx.Response(
                200, json={"documentId": doc_id, "title": title, "body": DOC_BODY}
            )
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_lists_documents_and_render_walks_body_paragraphs_into_prose() -> None:
    result = await _fetch(_handler())

    assert {page.source_ref for page in result.pages} == {"documents/doc1", "documents/doc2"}
    assert result.snapshot is False
    assert result.deletes == ()
    assert result.next_cursor == "2026-02-05T00:00:00.000Z"

    page = next(page for page in result.pages if page.source_ref == "documents/doc1")
    assert page.created_at == "2026-01-01T00:00:00.000000+00:00"
    assert page.updated_at == "2026-02-01T00:00:00.000000+00:00"
    assert "Q3 Plan" in page.body
    assert "Ship the launch by Friday." in page.body
    # non-paragraph structural elements (tables) carry no paragraph runs → they fall through
    assert "Owner" not in page.body
    # the raw Docs structure never leaks into the recallable body
    assert "textRun" not in page.body
    assert "tableRows" not in page.body


async def test_incremental_threads_the_watermark_into_the_drive_query() -> None:
    seen: list[str] = []
    await _fetch(_handler(seen), cursor="2026-01-15T00:00:00.000Z")
    assert seen and all("modifiedTime > '2026-01-15T00:00:00.000Z'" in query for query in seen)


async def test_unreadable_doc_lands_as_a_title_stub() -> None:
    """A doc Drive lists but the Docs API refuses (`403`) does not fail the run — it lands as a
    title-only stub (title from the Drive file name, empty body)."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.googleapis.com":
            return httpx.Response(200, json={"files": [FILE_1], "nextPageToken": None})
        return httpx.Response(403, json={"error": {"code": 403, "message": "filePermission"}})

    result = await _fetch(handle)
    assert {page.source_ref for page in result.pages} == {"documents/doc1"}
    assert "Q3 Plan" in result.pages[0].body


async def test_drive_scope_refusal_yields_stream_skipped() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"error": {"code": 403, "message": "insufficientPermissions"}}
        )

    with pytest.raises(StreamSkipped):
        await _fetch(refuse)
