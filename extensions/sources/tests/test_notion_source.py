"""Notion connector over a mock transport: the `/search` envelope, the recursive block walk, the
per-page comment fan-out, the `Notion-Version` header, and — the point of this provider — the
`render` override that lifts page/block/comment/user content into readable prose rather than the
default JSON dump. No conftest: the shared `ufo_testsupport` plugin covers fixtures, and these
tests are offline (a canned transport, no DB, no token, no broker)."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.notion import NOTION_VERSION, NotionConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped

ACCOUNT = "acct-1"


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(handler: Callable[[httpx.Request], httpx.Response]) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
):
    return await ConnectorBackend(connector=NotionConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, _auth(handler)
    )


def _page(page_id: str, title: str, summary: str, status: str, edited: str) -> dict:
    return {
        "object": "page",
        "id": page_id,
        "url": f"https://notion.so/{page_id}",
        "created_time": "2026-01-01T00:00:00.000Z",
        "last_edited_time": edited,
        "properties": {
            "Name": {"id": "title", "type": "title", "title": [{"plain_text": title}]},
            "Summary": {"id": "s", "type": "rich_text", "rich_text": [{"plain_text": summary}]},
            "Status": {"id": "st", "type": "status", "status": {"name": status}},
        },
    }


PAGE_1 = _page("p1", "Roadmap", "Q3 planning notes", "In progress", "2026-02-01T00:00:00.000Z")
PAGE_2 = _page("p2", "Backlog", "Icebox items", "Todo", "2026-02-05T00:00:00.000Z")


def _search_pages_handler(
    seen: list[str | None],
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.notion.com"
        seen.append(request.headers.get("Notion-Version"))
        if request.method == "POST" and request.url.path == "/v1/search":
            body = json.loads(request.content)
            assert body["filter"]["value"] == "page"
            if body.get("start_cursor") == "c2":
                return httpx.Response(
                    200, json={"results": [PAGE_2], "next_cursor": None, "has_more": False}
                )
            return httpx.Response(
                200, json={"results": [PAGE_1], "next_cursor": "c2", "has_more": True}
            )
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_pages_search_paginates_and_render_lifts_readable_content() -> None:
    seen: list[str | None] = []
    result = await _fetch("pages", _search_pages_handler(seen))

    assert {page.source_ref for page in result.pages} == {"pages/p1", "pages/p2"}
    assert result.snapshot is False
    assert result.deletes == ()
    assert result.next_cursor == "2026-02-05T00:00:00.000Z"
    assert seen and all(version == NOTION_VERSION for version in seen)

    page = next(page for page in result.pages if page.source_ref == "pages/p1")
    assert page.created_at == "2026-01-01T00:00:00.000000+00:00"
    assert page.updated_at == "2026-02-01T00:00:00.000000+00:00"
    body = page.body
    assert "Roadmap" in body
    assert "Q3 planning notes" in body
    assert "In progress" in body
    assert "plain_text" not in body
    assert "properties" not in body


async def test_pages_incremental_filters_past_the_watermark() -> None:
    result = await _fetch("pages", _search_pages_handler([]), cursor="2026-02-03T00:00:00.000Z")
    assert {page.source_ref for page in result.pages} == {"pages/p2"}


def _blocks_handler() -> Callable[[httpx.Request], httpx.Response]:
    para = {
        "object": "block",
        "id": "b1",
        "type": "paragraph",
        "has_children": False,
        "last_edited_time": "2026-02-01T00:00:00.000Z",
        "paragraph": {"rich_text": [{"plain_text": "Ship the launch by Friday"}]},
    }
    toggle = {
        "object": "block",
        "id": "tg1",
        "type": "toggle",
        "has_children": True,
        "last_edited_time": "2026-02-01T00:00:00.000Z",
        "toggle": {"rich_text": [{"plain_text": "Details"}]},
    }
    nested_todo = {
        "object": "block",
        "id": "b2",
        "type": "to_do",
        "has_children": False,
        "last_edited_time": "2026-02-01T00:00:00.000Z",
        "to_do": {"rich_text": [{"plain_text": "Draft the email"}], "checked": True},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/search":
            return httpx.Response(
                200, json={"results": [PAGE_1], "next_cursor": None, "has_more": False}
            )
        if request.method == "GET" and request.url.path == "/v1/blocks/p1/children":
            return httpx.Response(
                200, json={"results": [para, toggle], "next_cursor": None, "has_more": False}
            )
        if request.method == "GET" and request.url.path == "/v1/blocks/tg1/children":
            return httpx.Response(
                200, json={"results": [nested_todo], "next_cursor": None, "has_more": False}
            )
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_blocks_walk_recurses_and_render_extracts_block_text() -> None:
    result = await _fetch("blocks", _blocks_handler())

    assert {page.source_ref for page in result.pages} == {"blocks/b1", "blocks/tg1", "blocks/b2"}

    paragraph_body = next(p.body for p in result.pages if p.source_ref == "blocks/b1")
    assert "Ship the launch by Friday" in paragraph_body
    assert "rich_text" not in paragraph_body
    assert next(p.title for p in result.pages if p.source_ref == "blocks/b1") == (
        "Ship the launch by Friday"
    )

    nested_body = next(p.body for p in result.pages if p.source_ref == "blocks/b2")
    assert "[x] Draft the email" in nested_body


def _comments_handler() -> Callable[[httpx.Request], httpx.Response]:
    comment = {
        "object": "comment",
        "id": "cm1",
        "created_time": "2026-02-02T00:00:00.000Z",
        "rich_text": [{"plain_text": "Looks good to me"}],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/v1/search":
            return httpx.Response(
                200, json={"results": [PAGE_1], "next_cursor": None, "has_more": False}
            )
        if request.method == "GET" and request.url.path == "/v1/comments":
            assert request.url.params.get("block_id") == "p1"
            return httpx.Response(
                200, json={"results": [comment], "next_cursor": None, "has_more": False}
            )
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_comments_fan_out_per_page_and_render_the_body() -> None:
    result = await _fetch("comments", _comments_handler())
    assert {page.source_ref for page in result.pages} == {"comments/cm1"}
    assert result.pages[0].created_at == "2026-02-02T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None
    assert "Looks good to me" in result.pages[0].body
    assert result.pages[0].title == "Looks good to me"


async def test_users_collection_renders_name_and_email() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.path == "/v1/users"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "object": "user",
                        "id": "u1",
                        "name": "Ada Lovelace",
                        "person": {"email": "ada@example.com"},
                    }
                ],
                "next_cursor": None,
                "has_more": False,
            },
        )

    result = await _fetch("users", handle)
    assert {page.source_ref for page in result.pages} == {"users/u1"}
    body = result.pages[0].body
    assert "Ada Lovelace" in body
    assert "ada@example.com" in body


async def test_stream_skipped_when_the_integration_lacks_capability() -> None:
    """A 403 `restricted_resource` (the integration was never granted the user-read capability)
    surfaces as `StreamSkipped` so the run records a skip, never a failure."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": "restricted_resource"})

    with pytest.raises(StreamSkipped):
        await _fetch("users", handle)
