"""Confluence connector over a mock transport: the accessible-resources site fan-out, the
`start`+`limit` walk continued while a `_links.next` link is present, the `version.createdAt`
watermark, per-site id scoping so two sites never collide on one ref, the groups/audit streams, and
— the point of this provider — the `render` override that lifts storage-format XHTML into readable
prose (tags dropped, entities unescaped, macro tags transparent) rather than the default JSON dump.
Offline: a canned transport, no DB, no token, no broker."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.confluence import CONFLUENCE_STREAMS, ConfluenceConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig, StreamSpec
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"
RESOURCES = "/oauth/token/accessible-resources"


def _stream(name: str) -> StreamSpec:
    return next(spec for spec in CONFLUENCE_STREAMS if spec.name == name)


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


def _paginated_pages_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.atlassian.com"
        if request.url.path == RESOURCES:
            return httpx.Response(200, json=[{"id": "cloud-1", "url": "https://x.atlassian.net"}])
        assert request.url.path == "/ex/confluence/cloud-1/wiki/api/v2/pages"
        assert request.url.params.get("body-format") == "storage"
        if request.url.params.get("start") == "1":
            return httpx.Response(200, json={"results": [PAGE_2], "_links": {}})
        return httpx.Response(
            200,
            json={"results": [PAGE_1], "_links": {"next": "/wiki/api/v2/pages?cursor=CUR"}},
        )

    return handle


async def test_pages_follow_offset_pagination_and_render_lifts_readable_prose() -> None:
    result = await _fetch("pages", _paginated_pages_handler())

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
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == RESOURCES:
            return httpx.Response(200, json=[{"id": "cloud-1"}])
        return httpx.Response(200, json={"results": [PAGE_1, PAGE_2], "_links": {}})

    result = await _fetch("pages", handle, cursor="2026-02-03T00:00:00.000Z")
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
        assert "description" in (request.url.params.get("expand") or "")
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


async def test_groups_and_audit_streams_are_runnable() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == RESOURCES:
            return httpx.Response(200, json=[{"id": "cloud-1"}])
        if path == "/ex/confluence/cloud-1/wiki/rest/api/group":
            return httpx.Response(
                200, json={"results": [{"id": "g1", "name": "admins"}], "_links": {}}
            )
        if path == "/ex/confluence/cloud-1/wiki/rest/api/audit":
            return httpx.Response(
                200,
                json={"results": [{"creationDate": "20260201", "summary": "login"}], "_links": {}},
            )
        return httpx.Response(404, json={"path": path})

    groups = await _fetch("groups", handle)
    assert groups.snapshot is False
    assert _refs(groups) == {"groups/cloud-1:g1"}

    audit = await _fetch("audit", handle)
    assert audit.snapshot is False
    assert _refs(audit) == {"audit/20260201"}


def test_pages_flatten_carries_canonical_fields_and_preserves_scoping_and_cursor_lift() -> None:
    record = {
        "id": "p1",
        "cloud_id": "cloud-1",
        "site_url": "https://x.atlassian.net",
        "title": "Release Plan",
        "version": {"createdAt": "2026-02-01T00:00:00.000Z", "authorId": "u1"},
        "body": {"storage": {"value": "<p>hi</p>"}},
        "_links": {"webui": "/pages/p1"},
    }
    flat = ConfluenceConnector().flatten(record, _stream("pages"))
    assert flat["title"] == "Release Plan"
    assert flat["kind"] == "page"
    assert flat["url"] == "https://x.atlassian.net/pages/p1"
    assert flat["body"] == "<p>hi</p>"
    assert flat["created_at"] == "2026-02-01T00:00:00.000Z"
    assert flat["updated_at"] == "2026-02-01T00:00:00.000Z"
    assert flat["id"] == "cloud-1:p1"
    assert flat["version.createdAt"] == "2026-02-01T00:00:00.000Z"


def test_spaces_flatten_derive_name_and_api_url() -> None:
    record = {
        "id": "sp1",
        "cloud_id": "cloud-1",
        "name": "Engineering",
        "createdAt": "2026-01-01T00:00:00.000Z",
        "_links": {"self": "https://x.atlassian.net/wiki/api/v2/spaces/sp1"},
    }
    flat = ConfluenceConnector().flatten(record, _stream("spaces"))
    assert flat["name"] == "Engineering"
    assert flat["api_url"] == "https://x.atlassian.net/wiki/api/v2/spaces/sp1"
    assert flat["created_at"] == "2026-01-01T00:00:00.000Z"
    assert flat["id"] == "cloud-1:sp1"


def test_comments_flatten_derive_body_author_and_parent() -> None:
    record = {
        "id": "cm1",
        "cloud_id": "cloud-1",
        "site_url": "https://x.atlassian.net",
        "pageId": "p1",
        "version": {"createdAt": "2026-02-02T00:00:00.000Z", "authorId": "u9"},
        "body": {"storage": {"value": "<p>ok</p>"}},
        "_links": {"webui": "/pages/p1?focusedCommentId=cm1"},
    }
    flat = ConfluenceConnector().flatten(record, _stream("comments"))
    assert flat["body"] == "<p>ok</p>"
    assert flat["author"] == "u9"
    assert flat["parent_external_id"] == "p1"
    assert flat["url"] == "https://x.atlassian.net/pages/p1?focusedCommentId=cm1"
    assert flat["id"] == "cloud-1:cm1"
    assert flat["version.createdAt"] == "2026-02-02T00:00:00.000Z"


async def test_stream_skipped_on_permission_refusal() -> None:
    """A 403 (the grant lacks the scope, or was never shared the space) surfaces as `StreamSkipped`
    so the run records a skip, never a failure."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == RESOURCES:
            return httpx.Response(200, json=[{"id": "cloud-1"}])
        return httpx.Response(403, json={"errors": [{"status": 403, "title": "not permitted"}]})

    with pytest.raises(StreamSkipped):
        await _fetch("pages", handle)
