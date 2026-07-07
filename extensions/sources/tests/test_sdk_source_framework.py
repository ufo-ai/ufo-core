"""Any extension can build a source from the framework alone, reached only through `ufo.sdk`.

This proof imports the whole source-building surface — `RestConnector`, `StreamSpec`, `Pagination`,
`PaginationStrategy`, `ConnectorBackend`, `ConnectorSourceConfig`, `SourceAuth` — from
`ufo.sdk.sources`, and `Credential` from `ufo.sdk.authproxy`, and nothing else from
ufo. It defines a throwaway REST connector no shipped provider knows about, drives it through
`ConnectorBackend` over an `httpx.MockTransport` (no live API, no token), and asserts the collapsed
`SyncResult`: a declared `next_cursor` strategy paginates two mock pages, and a `delete_missing`
stream returns as an authoritative snapshot. If the framework were still private to the sources
extension, this file would not import."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar
from uuid import UUID, uuid4

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    Pagination,
    PaginationStrategy,
    RestConnector,
    SourceAuth,
    StreamSpec,
)

BASE_URL = "https://api.widget.test"


class WidgetConnector(RestConnector):
    """A minimal REST connector defined entirely against the sdk framework: one full-collection
    stream whose declared `next_cursor` pagination walks the mock provider's two pages."""

    name = "widget"
    base_url = BASE_URL
    streams_list: ClassVar[list[StreamSpec]] = [
        StreamSpec(
            name="widgets",
            source_object="widgets",
            primary_key="id",
            delete_missing=True,
            pagination=Pagination(
                strategy=PaginationStrategy.next_cursor,
                path="/widgets",
                record_path="data",
                cursor_path="next",
                cursor_param="cursor",
            ),
        )
    ]


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _handler(request: httpx.Request) -> httpx.Response:
    if request.url.params.get("cursor") == "page-2":
        return httpx.Response(200, json={"data": [{"id": "2", "title": "Beta"}], "next": None})
    return httpx.Response(200, json={"data": [{"id": "1", "title": "Alpha"}], "next": "page-2"})


async def test_sdk_framework_builds_a_source_and_paginates_to_a_snapshot() -> None:
    backend = ConnectorBackend(connector=WidgetConnector())
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=_handler))

    config = ConnectorSourceConfig(account="acct", stream="widgets")
    result = await backend.fetch(config, None, auth)

    assert result.snapshot is True
    assert result.next_cursor is None
    assert {page.source_ref for page in result.pages} == {"widgets/1", "widgets/2"}
    alpha = next(page for page in result.pages if page.source_ref == "widgets/1")
    assert "# widget widgets: Alpha" in alpha.body
    assert alpha.digest.startswith("sha256:")
