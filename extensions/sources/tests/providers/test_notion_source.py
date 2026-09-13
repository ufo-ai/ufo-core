"""Notion connector over a mock transport: the `/search` envelope, the block tree descending one
level per pass over the `pages` and `blocks` edges, the per-page comment fan-out, the
`Notion-Version` header, and — the point of this provider — the `render` override that lifts
page/block/comment/user content into readable prose rather than the default JSON dump. Offline —
a canned transport, no DB, no token, no broker."""

import json
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.notion import NOTION_VERSION, NotionConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig, ParentPages, ParentRecord


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


Landed = Mapping[str, tuple[ParentRecord, ...]]
ParentsReader = Callable[[Landed], ParentPages]


async def _no_parents(stream: str) -> AsyncIterator[ParentRecord]:
    return
    yield


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    cursor: str | None = None,
    parents: ParentPages = _no_parents,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler), parents=parents)
    return await ConnectorBackend(connector=NotionConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
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


PAGE_PARENT = (ParentRecord(ref="pages/p1", fields={"id": "p1"}),)


def _block(block_id: str, block_type: str, text: str, *, has_children: bool) -> dict:
    return {
        "object": "block",
        "id": block_id,
        "type": block_type,
        "has_children": has_children,
        "last_edited_time": "2026-02-01T00:00:00.000Z",
        block_type: {"rich_text": [{"plain_text": text}]},
    }


LEVEL_1 = _block("b1", "toggle", "Ship the launch by Friday", has_children=True)
LEVEL_2 = _block("b2", "toggle", "Details", has_children=True)
LEVEL_3 = _block("b3", "to_do", "Draft the email", has_children=False)
CHILD_PAGE = {
    "object": "block",
    "id": "cp1",
    "type": "child_page",
    "has_children": True,
    "last_edited_time": "2026-02-01T00:00:00.000Z",
    "child_page": {"title": "Sub-page"},
}
BLOCK_TREE = {"p1": [LEVEL_1], "b1": [LEVEL_2], "b2": [LEVEL_3, CHILD_PAGE]}


def _blocks_handler(asked: list[str]) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        asked.append(path)
        if path.startswith("/v1/blocks/") and path.endswith("/children"):
            results = BLOCK_TREE.get(path.split("/")[3])
            if results is None:
                return httpx.Response(404, json={"path": path})
            return httpx.Response(
                200, json={"results": results, "next_cursor": None, "has_more": False}
            )
        return httpx.Response(404, json={"path": path})

    return handle


def _landed(result: SyncResult, *, under: Mapping[str, tuple[ParentRecord, ...]]):
    grown = dict(under)
    grown["blocks"] = tuple(under.get("blocks", ())) + tuple(
        ParentRecord(ref=page.source_identity, fields=page.parent_fields or {})
        for page in result.pages
    )
    return grown


async def test_blocks_descend_one_level_per_pass_and_stop_at_a_block_holding_no_body(
    parents_reader: ParentsReader,
) -> None:
    """Three passes reach a three-deep tree, one level each. A leaf and a `child_page` block land
    like any other block and are then no partition of the self-edge, so the fourth pass — the first
    after both landed — asks only the two branches that hold further blocks."""
    asked: list[str] = []
    landed: Mapping[str, tuple[ParentRecord, ...]] = {"pages": PAGE_PARENT}
    cursor: str | None = None
    reached: list[set[str]] = []
    per_pass: list[list[str]] = []

    for _ in range(4):
        asked.clear()
        result = await _fetch("blocks", _blocks_handler(asked), cursor, parents_reader(landed))
        cursor = result.next_cursor
        reached.append({page.source_identity for page in result.pages})
        per_pass.append(list(asked))
        landed = _landed(result, under=landed)

    assert reached == [
        {"blocks/p1/b1"},
        {"blocks/b1/b2"},
        {"blocks/b2/b3", "blocks/b2/cp1"},
        set(),
    ]
    assert per_pass == [
        ["/v1/blocks/p1/children"],
        ["/v1/blocks/b1/children", "/v1/blocks/p1/children"],
        ["/v1/blocks/b2/children", "/v1/blocks/b1/children", "/v1/blocks/p1/children"],
        ["/v1/blocks/b2/children", "/v1/blocks/b1/children", "/v1/blocks/p1/children"],
    ]


async def test_blocks_render_extracts_block_text(parents_reader: ParentsReader) -> None:
    asked: list[str] = []
    result = await _fetch(
        "blocks", _blocks_handler(asked), None, parents_reader({"pages": PAGE_PARENT})
    )

    page = result.pages[0]
    assert page.source_ref == "blocks/p1/b1"
    assert "Ship the launch by Friday" in page.body
    assert "rich_text" not in page.body
    assert page.title == "Ship the launch by Friday"
    assert page.parent_fields == {"has_children": True, "id": "b1", "type": "toggle"}


async def test_the_self_edge_declares_which_blocks_hold_further_blocks() -> None:
    """The page edge reads only `{id}`, so a predicate on it would admit no page at all — a Notion
    page carries no `has_children`, and a record answering none of a predicate's fields is not a
    parent of that edge."""
    by_name = {stream.name: stream for stream in NotionConnector().streams()}
    under_pages, under_blocks = by_name["blocks"].parents

    assert under_pages.stream == "pages"
    assert (under_pages.where, under_pages.unless) == ({}, {})
    assert under_blocks.stream == "blocks"
    assert under_blocks.where == {"has_children": (True,)}
    assert under_blocks.unless == {"type": ("child_page", "child_database", "ai_block")}
    assert under_pages.admits(PAGE_PARENT[0])


async def test_blocks_resume_filters_each_partition_past_its_own_watermark(
    parents_reader: ParentsReader,
) -> None:
    edited = _block("b9", "paragraph", "edited later", has_children=False)
    edited["last_edited_time"] = "2026-03-01T00:00:00.000Z"

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [LEVEL_1, edited], "next_cursor": None, "has_more": False},
        )

    first = await _fetch("blocks", handle, None, parents_reader({"pages": PAGE_PARENT}))
    assert {page.source_identity for page in first.pages} == {"blocks/p1/b1", "blocks/p1/b9"}

    second = await _fetch(
        "blocks", handle, first.next_cursor, parents_reader({"pages": PAGE_PARENT})
    )
    assert second.pages == ()


def _comments_handler(asked: list[str]) -> Callable[[httpx.Request], httpx.Response]:
    comment = {
        "object": "comment",
        "id": "cm1",
        "created_time": "2026-02-02T00:00:00.000Z",
        "rich_text": [{"plain_text": "Looks good to me"}],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(f"{request.url.path}?{request.url.params.get('block_id')}")
        if request.method == "GET" and request.url.path == "/v1/comments":
            return httpx.Response(
                200, json={"results": [comment], "next_cursor": None, "has_more": False}
            )
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_comments_fan_out_per_page_and_render_the_body(
    parents_reader: ParentsReader,
) -> None:
    asked: list[str] = []
    result = await _fetch(
        "comments", _comments_handler(asked), None, parents_reader({"pages": PAGE_PARENT})
    )
    assert asked == ["/v1/comments?p1"]
    assert {page.source_identity for page in result.pages} == {"comments/p1/cm1"}
    assert result.pages[0].created_at == "2026-02-02T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None
    assert "Looks good to me" in result.pages[0].body
    assert result.pages[0].title == "Looks good to me"


async def test_the_scoped_child_identities_restamp_nothing() -> None:
    """`blocks/b1` and `comments/cm1` are what main addresses these records by. On main, `_create`
    registered canonical streams only, so neither of these has ever landed a page and scoping them
    under their parent restamps nothing."""
    by_name = {stream.name: stream for stream in NotionConnector().streams()}
    assert by_name["blocks"].canonical is False
    assert by_name["comments"].canonical is False
    assert by_name["pages"].canonical is True


async def test_stream_skipped_when_the_integration_lacks_capability() -> None:
    """A 403 `restricted_resource` (the integration was never granted the user-read capability)
    surfaces as `StreamSkipped` so the run records a skip, never a failure."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": "restricted_resource"})

    with pytest.raises(StreamSkipped):
        await _fetch("users", handle)
