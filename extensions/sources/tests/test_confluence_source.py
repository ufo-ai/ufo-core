"""Confluence connector over a mock transport: the accessible-resources site fan-out, the
`_links.next` cursor walk, the `version.createdAt` watermark lifted to `updated_at`, per-site id
scoping so two sites never collide on one ref, and — the point of this provider — the `render`
override that lifts storage-format XHTML into readable prose (tags dropped, entities unescaped,
macro tags transparent) rather than the default JSON dump. Offline: a canned transport, no DB, no
token, no broker."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from selfhost_ext_sources.backend import ConnectorBackend, ConnectorSourceConfig
from selfhost_ext_sources.confluence import ConfluenceConnector

from selfhost.connectors import Credential
from selfhost.memory.sources import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"
RESOURCES = "/oauth/token/accessible-resources"


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    cursor: str | None = None,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler))
    return await ConnectorBackend(connector=ConfluenceConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


PAGE_1_BODY = (
    "<h1>Release Plan</h1>"
    "<p>Ship the <strong>launch</strong> by &amp; Friday.</p>"
    "<ul><li>Draft the email</li></ul>"
    '<ac:structured-macro ac:name="info"><ac:rich-text-body><p>Heads up</p>'
    "</ac:rich-text-body></ac:structured-macro>"
)


def _page(page_id: str, title: str, body_html: str, created: str) -> dict:
    return {
        "id": page_id,
        "status": "current",
        "title": title,
        "spaceId": "s1",
        "version": {"number": 3, "createdAt": created},
        "body": {"storage": {"value": body_html, "representation": "storage"}},
        "_links": {"webui": f"/pages/{page_id}"},
    }


PAGE_1 = _page("p1", "Release Plan", PAGE_1_BODY, "2026-02-01T00:00:00.000Z")
PAGE_2 = _page("p2", "Backlog", "<p>Second page</p>", "2026-02-05T00:00:00.000Z")


def _pages_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.atlassian.com"
        if request.url.path == RESOURCES:
            return httpx.Response(200, json=[{"id": "cloud-1", "url": "https://x.atlassian.net"}])
        assert request.url.path == "/ex/confluence/cloud-1/wiki/api/v2/pages"
        assert request.url.params.get("body-format") == "storage"
        if request.url.params.get("cursor") == "CUR":
            return httpx.Response(200, json={"results": [PAGE_2], "_links": {}})
        return httpx.Response(
            200,
            json={
                "results": [PAGE_1],
                "_links": {"next": "/wiki/api/v2/pages?limit=100&cursor=CUR"},
            },
        )

    return handle


async def test_pages_follow_cursor_and_render_lifts_readable_prose() -> None:
    result = await _fetch("pages", _pages_handler())

    assert _refs(result) == {"pages/cloud-1:p1", "pages/cloud-1:p2"}
    assert result.snapshot is False
    assert result.deletes == ()
    assert result.next_cursor == "2026-02-05T00:00:00.000Z"

    body = next(p.body for p in result.pages if p.source_ref == "pages/cloud-1:p1")
    assert "Release Plan" in body
    assert "Ship the launch by & Friday." in body
    assert "Draft the email" in body
    assert "Heads up" in body
    assert "<" not in body
    assert "storage" not in body


async def test_pages_incremental_filters_past_the_watermark() -> None:
    result = await _fetch("pages", _pages_handler(), cursor="2026-02-03T00:00:00.000Z")
    assert _refs(result) == {"pages/cloud-1:p2"}
    assert result.next_cursor == "2026-02-05T00:00:00.000Z"


async def test_multi_site_fan_out_scopes_ids_per_cloud() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == RESOURCES:
            return httpx.Response(200, json=[{"id": "cloud-1"}, {"id": "cloud-2"}])
        assert request.url.path in (
            "/ex/confluence/cloud-1/wiki/api/v2/pages",
            "/ex/confluence/cloud-2/wiki/api/v2/pages",
        )
        return httpx.Response(200, json={"results": [PAGE_1], "_links": {}})

    result = await _fetch("pages", handle)
    assert _refs(result) == {"pages/cloud-1:p1", "pages/cloud-2:p1"}


async def test_spaces_render_lifts_name_and_description() -> None:
    space = {
        "id": "sp1",
        "key": "ENG",
        "name": "Engineering",
        "description": {
            "view": {
                "value": "<p>Team <strong>space</strong> &amp; home</p>",
                "representation": "view",
            }
        },
        "_links": {"webui": "/spaces/ENG"},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == RESOURCES:
            return httpx.Response(200, json=[{"id": "cloud-1"}])
        assert request.url.path == "/ex/confluence/cloud-1/wiki/api/v2/spaces"
        assert request.url.params.get("description-format") == "view"
        return httpx.Response(200, json={"results": [space], "_links": {}})

    result = await _fetch("spaces", handle)
    assert _refs(result) == {"spaces/cloud-1:sp1"}
    assert result.snapshot is False
    assert result.next_cursor is None

    body = result.pages[0].body
    assert "Engineering" in body
    assert "Team space & home" in body
    assert "<" not in body


async def test_comments_render_the_body() -> None:
    comment = {
        "id": "cm1",
        "pageId": "p1",
        "version": {"number": 1, "createdAt": "2026-02-02T00:00:00.000Z"},
        "body": {
            "storage": {"value": "<p>Looks good &amp; ready</p>", "representation": "storage"}
        },
        "_links": {"webui": "/pages/p1?focusedCommentId=cm1"},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == RESOURCES:
            return httpx.Response(200, json=[{"id": "cloud-1"}])
        assert request.url.path == "/ex/confluence/cloud-1/wiki/api/v2/footer-comments"
        return httpx.Response(200, json={"results": [comment], "_links": {}})

    result = await _fetch("comments", handle)
    assert _refs(result) == {"comments/cloud-1:cm1"}
    assert "Looks good & ready" in result.pages[0].body


async def test_stream_skipped_on_permission_refusal() -> None:
    """A 403 (the grant lacks the scope, or was never shared the space) surfaces as `StreamSkipped`
    so the run records a skip, never a failure."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == RESOURCES:
            return httpx.Response(200, json=[{"id": "cloud-1"}])
        return httpx.Response(403, json={"errors": [{"status": 403, "title": "not permitted"}]})

    with pytest.raises(StreamSkipped):
        await _fetch("pages", handle)
