"""Google Docs connector over a mock transport: the Drive-list→Docs-get fan-out, the incremental
`modifiedTime` watermark threaded into the Drive query, and — the point of this provider — the
`render` override that walks a document's `body.content` tree (headings, bullets, tables) into
readable prose rather than the default JSON dump. No conftest: the shared `selfhost_testsupport`
plugin covers fixtures; these tests are offline (a canned transport, no DB, no token, no broker)."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
from selfhost_ext_sources.backend import ConnectorBackend, ConnectorSourceConfig
from selfhost_ext_sources.google_docs import GoogleDocsConnector

from selfhost.connectors import Credential
from selfhost.memory.sources import SourceAuth, StreamSkipped

ACCOUNT = "acct-1"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(handler: Callable[[httpx.Request], httpx.Response]) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))


async def _fetch(handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None):
    return await ConnectorBackend(connector=GoogleDocsConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream="documents"), cursor, _auth(handler)
    )


def _paragraph(text: str, *, style: str | None = None, bullet: bool = False) -> dict:
    para: dict = {"elements": [{"textRun": {"content": f"{text}\n"}}]}
    if style:
        para["paragraphStyle"] = {"namedStyleType": style}
    if bullet:
        para["bullet"] = {"listId": "l1"}
    return {"paragraph": para}


DOC_BODY = {
    "content": [
        _paragraph("Q3 Plan", style="TITLE"),
        _paragraph("Goals", style="HEADING_1"),
        _paragraph("Ship the launch by Friday."),
        _paragraph("Draft the email", bullet=True),
        {
            "table": {
                "tableRows": [
                    {
                        "tableCells": [
                            {"content": [_paragraph("Owner")]},
                            {"content": [_paragraph("Alex")]},
                        ]
                    }
                ]
            }
        },
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


async def test_lists_documents_and_render_walks_the_body_into_prose() -> None:
    result = await _fetch(_handler())

    assert {page.source_ref for page in result.pages} == {"documents/doc1", "documents/doc2"}
    assert result.snapshot is False
    assert result.deletes == ()
    assert result.next_cursor == "2026-02-05T00:00:00.000Z"

    body = next(page.body for page in result.pages if page.source_ref == "documents/doc1")
    assert "# Q3 Plan" in body
    assert "# Goals" in body
    assert "Ship the launch by Friday." in body
    assert "- Draft the email" in body
    assert "Owner | Alex" in body
    # the raw Docs structure never leaks into the recallable body
    assert "textRun" not in body
    assert "structuralElement" not in body
    assert "namedStyleType" not in body
    assert "tableRows" not in body


async def test_incremental_threads_the_watermark_into_the_drive_query() -> None:
    seen: list[str] = []
    await _fetch(_handler(seen), cursor="2026-01-15T00:00:00.000Z")
    assert seen and all("modifiedTime > '2026-01-15T00:00:00.000Z'" in query for query in seen)


async def test_scope_refusal_yields_stream_skipped() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403, json={"error": {"code": 403, "message": "insufficientPermissions"}}
        )

    try:
        await _fetch(refuse)
    except StreamSkipped:
        return
    raise AssertionError("a 403 from Drive must raise StreamSkipped")
