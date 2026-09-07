"""The PandaDoc connector over a mock transport: page-number paging over `results`, the
`modified_from` filter and `date_modified` watermark, the details hydration (and its tolerance of an
unreadable document), the `API-Key` scheme for a member-added key, and a refusal as `StreamSkipped`.
Offline — a canned transport, no DB, no token, no broker."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.pandadoc import PandaDocConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

KEY = "pandadoc-key"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    cursor: str | None = None,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=PandaDocConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


async def test_documents_filter_on_modified_from_and_land_their_details() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/public/v1/documents/doc-1/details":
            return httpx.Response(
                200,
                json={
                    "id": "doc-1",
                    "status": "document.completed",
                    "fields": [{"name": "amount", "value": "1200"}],
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "doc-1",
                        "name": "Acme order form",
                        "date_created": "2026-02-01T09:00:00.000000Z",
                        "date_modified": "2026-02-04T09:00:00.000000Z",
                    }
                ]
            },
        )

    result = await _fetch("documents", handle, cursor="2026-02-01T00:00:00Z")

    assert {page.source_ref for page in result.pages} == {"documents/doc-1"}
    assert [page.title for page in result.pages] == ["Acme order form"]
    assert result.next_cursor == "2026-02-04T09:00:00.000000Z"
    assert "modified_from=2026-02-01T00%3A00%3A00Z" in seen[0]
    assert "order_by=date_modified" in seen[0]
    assert "amount" in result.pages[0].body


async def test_an_unreadable_document_lands_as_its_list_row() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/details"):
            return httpx.Response(403, json={"detail": "no access"})
        return httpx.Response(
            200,
            json={"results": [{"id": "doc-1", "name": "Locked", "date_modified": "2026-02-04Z"}]},
        )

    result = await _fetch("documents", handle)
    assert [page.title for page in result.pages] == ["Locked"]


async def test_a_member_added_key_rides_the_api_key_scheme() -> None:
    client = PandaDocConnector()._make_client("https://api.pandadoc.com", Credential(bearer=KEY))
    try:
        assert client.headers["Authorization"] == f"API-Key {KEY}"
    finally:
        await client.aclose()


async def test_templates_page_over_results() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/public/v1/templates"
        assert "modified_from" not in request.url.params
        return httpx.Response(200, json={"results": [{"id": "t-1", "name": "NDA"}]})

    result = await _fetch("templates", handle)
    assert {page.source_ref for page in result.pages} == {"templates/t-1"}


async def test_a_refused_stream_is_skipped_rather_than_failed() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid key"})

    with pytest.raises(StreamSkipped):
        await _fetch("contacts", handle)
