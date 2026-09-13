"""The Jira source connector, offline over a mock transport.

Jira authenticates through the `AuthProxy` seam, so these drive the connector with a mock proxy
whose `Credential` carries an `httpx.MockTransport` bound to `api.atlassian.com` — no live API, no
token. Covered: the `/oauth/token/accessible-resources` site fan-out feeding `startAt` pagination,
the incremental `issues` stream advancing an `updated` watermark and — the point of this provider —
`render` lifting an issue's summary/status/assignee/description out of its Atlassian Document Format
body rather than dumping JSON, the JQL `updated > "<cursor>"` filter on an incremental run, the
`users` bare-array read, and a permission refusal (403) surfacing as `StreamSkipped` so the run
records a skip, not a failure. Every Jira stream is incremental (no `delete_missing`), so a run is
never an authoritative snapshot."""

from collections.abc import AsyncIterator, Callable, Mapping
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.jira import JiraConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig, ParentPages, ParentRecord

CLOUD_ID = "cloud-1"
SITE = {"id": CLOUD_ID, "url": "https://acme.atlassian.net", "name": "Acme"}
Landed = Mapping[str, tuple[ParentRecord, ...]]
ParentsReader = Callable[[Landed], ParentPages]


async def _no_parents(stream: str) -> AsyncIterator[ParentRecord]:
    return
    yield


LANDED_ISSUE = (ParentRecord(ref="issues/10001", fields={"cloud_id": CLOUD_ID, "id": "10001"}),)
LANDED_BOARD = (ParentRecord(ref="boards/7", fields={"cloud_id": CLOUD_ID, "id": "7"}),)


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
    parents: ParentPages = _no_parents,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler), parents=parents)
    return await ConnectorBackend(connector=JiraConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


def _issue(
    issue_id: str,
    key: str,
    summary: str,
    description: str,
    status: str,
    assignee: str,
    updated: str,
) -> dict[str, object]:
    return {
        "id": issue_id,
        "key": key,
        "self": f"https://acme.atlassian.net/rest/api/3/issue/{issue_id}",
        "fields": {
            "summary": summary,
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {"type": "paragraph", "content": [{"type": "text", "text": description}]}
                ],
            },
            "status": {"name": status},
            "priority": {"name": "High"},
            "created": "2026-01-01T00:00:00.000+0000",
            "updated": updated,
            "assignee": {"displayName": assignee, "emailAddress": f"{assignee}@acme.com"},
            "reporter": {"displayName": "Reporter One"},
        },
    }


ISSUE_1 = _issue(
    "10001",
    "ACME-1",
    "Fix login bug",
    "Users cannot log in on mobile",
    "In Progress",
    "Alice",
    "2026-02-01T00:00:00.000+0000",
)
ISSUE_2 = _issue(
    "10002",
    "ACME-2",
    "Add dark mode",
    "Support a system dark-mode toggle",
    "To Do",
    "Bob",
    "2026-02-05T00:00:00.000+0000",
)


def _search_handler(seen: list[str]) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.atlassian.com"
        path = request.url.path
        if path == "/oauth/token/accessible-resources":
            return httpx.Response(200, json=[SITE])
        if path == f"/ex/jira/{CLOUD_ID}/rest/api/3/search":
            jql = request.url.params.get("jql") or ""
            seen.append(jql)
            if jql.startswith("updated >"):
                return httpx.Response(
                    200, json={"issues": [ISSUE_2], "startAt": 0, "maxResults": 100, "total": 1}
                )
            start = int(request.url.params.get("startAt") or "0")
            if start == 0:
                return httpx.Response(
                    200,
                    json={"issues": [ISSUE_1], "startAt": 0, "maxResults": 1, "total": 2},
                )
            return httpx.Response(
                200, json={"issues": [ISSUE_2], "startAt": 1, "maxResults": 1, "total": 2}
            )
        return httpx.Response(404, json={"path": path})

    return handle


async def test_issues_paginate_advance_watermark_and_render_readable_body() -> None:
    result = await _fetch("issues", _search_handler([]))

    assert _refs(result) == {"issues/10001", "issues/10002"}
    assert result.snapshot is False
    assert result.deletes == ()
    assert result.next_cursor == "2026-02-05T00:00:00.000+0000"

    page = next(page for page in result.pages if page.source_ref == "issues/10001")
    assert page.created_at == "2026-01-01T00:00:00.000000+00:00"
    assert page.updated_at == "2026-02-01T00:00:00.000000+00:00"
    body = page.body
    assert "Fix login bug" in body
    assert "Status: In Progress" in body
    assert "Assignee: Alice" in body
    assert "Users cannot log in on mobile" in body
    assert "paragraph" not in body
    assert "emailAddress" not in body


async def test_issues_incremental_filters_with_jql_and_advances_the_watermark() -> None:
    seen: list[str] = []
    result = await _fetch("issues", _search_handler(seen), cursor="2026-02-03T00:00:00.000+0000")
    assert seen and seen[0] == 'updated > "2026-02-03T00:00:00.000+0000" ORDER BY updated ASC'
    assert _refs(result) == {"issues/10002"}
    assert result.next_cursor == "2026-02-05T00:00:00.000+0000"


def _comment(comment_id: str, author: str, text: str, created: str, updated: str) -> dict:
    return {
        "id": comment_id,
        "author": {"displayName": author},
        "body": {
            "type": "doc",
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}],
        },
        "created": created,
        "updated": updated,
    }


COMMENT_1 = _comment(
    "c1", "Ada", "Investigating", "2026-01-02T00:00:00.000+0000", "2026-01-03T00:00:00.000+0000"
)
COMMENT_2 = _comment(
    "c2", "Bo", "Shipped", "2026-01-06T00:00:00.000+0000", "2026-01-06T00:00:00.000+0000"
)


def _comments_handler(asked: list[str]) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        asked.append(path)
        if path == f"/ex/jira/{CLOUD_ID}/rest/api/3/issue/10001/comment":
            start = int(request.url.params.get("startAt") or "0")
            rows = [] if start else [COMMENT_1, COMMENT_2]
            return httpx.Response(
                200, json={"comments": rows, "startAt": start, "maxResults": 100, "total": 2}
            )
        return httpx.Response(404, json={"path": path})

    return handle


async def test_issue_comments_read_one_issue_and_nothing_above_it(
    parents_reader: ParentsReader,
) -> None:
    """The comment walk asks the issue's own comment collection and no more: the issues enumeration
    that re-derived those partitions — and the site lookup above it — belong to the `issues` row."""
    asked: list[str] = []
    result = await _fetch(
        "issue_comments", _comments_handler(asked), parents=parents_reader({"issues": LANDED_ISSUE})
    )

    assert asked == [f"/ex/jira/{CLOUD_ID}/rest/api/3/issue/10001/comment"]
    assert {page.source_identity for page in result.pages} == {
        f"issue_comments/{CLOUD_ID}/10001/c1",
        f"issue_comments/{CLOUD_ID}/10001/c2",
    }
    first = next(page for page in result.pages if page.source_ref.endswith("/c1"))
    assert first.created_at == "2026-01-02T00:00:00.000000+00:00"
    assert first.updated_at == "2026-01-03T00:00:00.000000+00:00"


async def test_issue_comments_resume_past_that_issues_own_watermark(
    parents_reader: ParentsReader,
) -> None:
    asked: list[str] = []
    first = await _fetch(
        "issue_comments", _comments_handler(asked), parents=parents_reader({"issues": LANDED_ISSUE})
    )
    assert first.next_cursor == (
        f'{{"issues/10001\\n/ex/jira/{CLOUD_ID}/rest/api/3/issue/10001/comment": '
        '"2026-01-06T00:00:00.000+0000"}'
    )

    second = await _fetch(
        "issue_comments",
        _comments_handler(asked),
        cursor=first.next_cursor,
        parents=parents_reader({"issues": LANDED_ISSUE}),
    )
    assert second.pages == ()

    edited = dict(COMMENT_1, updated="2026-02-01T00:00:00.000+0000")

    def handle(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params.get("startAt") or "0")
        rows = [] if start else [edited, COMMENT_2]
        return httpx.Response(
            200, json={"comments": rows, "startAt": start, "maxResults": 100, "total": 2}
        )

    third = await _fetch(
        "issue_comments",
        handle,
        cursor=first.next_cursor,
        parents=parents_reader({"issues": LANDED_ISSUE}),
    )
    assert {page.source_identity for page in third.pages} == {f"issue_comments/{CLOUD_ID}/10001/c1"}


async def test_a_second_issue_keeps_its_own_cursor_entry(parents_reader: ParentsReader) -> None:
    landed = {
        "issues": (
            *LANDED_ISSUE,
            ParentRecord(ref="issues/10002", fields={"cloud_id": CLOUD_ID, "id": "10002"}),
        )
    }
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        asked.append(path)
        start = int(request.url.params.get("startAt") or "0")
        rows = [] if start else [COMMENT_1]
        return httpx.Response(
            200, json={"comments": rows, "startAt": start, "maxResults": 100, "total": 1}
        )

    result = await _fetch("issue_comments", handle, parents=parents_reader(landed))
    assert asked == [
        f"/ex/jira/{CLOUD_ID}/rest/api/3/issue/10001/comment",
        f"/ex/jira/{CLOUD_ID}/rest/api/3/issue/10002/comment",
    ]
    assert result.next_cursor == (
        f'{{"issues/10001\\n/ex/jira/{CLOUD_ID}/rest/api/3/issue/10001/comment": '
        '"2026-01-03T00:00:00.000+0000", '
        f'"issues/10002\\n/ex/jira/{CLOUD_ID}/rest/api/3/issue/10002/comment": '
        '"2026-01-03T00:00:00.000+0000"}'
    )


async def test_sprints_hang_under_their_board(parents_reader: ParentsReader) -> None:
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        start = int(request.url.params.get("startAt") or "0")
        rows = (
            []
            if start
            else [{"id": 70, "name": "Sprint 1", "updatedDate": "2026-01-03T00:00:00.000Z"}]
        )
        return httpx.Response(200, json={"values": rows, "isLast": True})

    result = await _fetch("sprints", handle, parents=parents_reader({"boards": LANDED_BOARD}))
    assert asked == [f"/ex/jira/{CLOUD_ID}/rest/agile/1.0/board/7/sprint"]
    assert {page.source_identity for page in result.pages} == {f"sprints/{CLOUD_ID}/7/70"}


async def test_a_child_whose_parent_landed_nothing_spends_no_request() -> None:
    asked: list[str] = []
    result = await _fetch("issue_comments", _comments_handler(asked))
    assert asked == []
    assert result.pages == ()


async def test_the_scoped_child_identities_restamp_nothing() -> None:
    """`issue_comments/c1` and `sprints/70` are what main addresses these records by. On main,
    `_create` registered canonical streams only, so neither of these has ever landed a page and
    scoping them under their parent restamps nothing."""
    by_name = {stream.name: stream for stream in JiraConnector().streams()}
    assert by_name["issue_comments"].canonical is False
    assert by_name["sprints"].canonical is False
    assert by_name["issues"].canonical is True


async def test_projects_are_incremental_not_a_snapshot() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth/token/accessible-resources":
            return httpx.Response(200, json=[SITE])
        if path == f"/ex/jira/{CLOUD_ID}/rest/api/3/project/search":
            return httpx.Response(
                200,
                json={
                    "values": [{"id": "p1", "key": "ACME", "name": "Acme", "self": "https://x/p1"}],
                    "isLast": True,
                    "total": 1,
                },
            )
        return httpx.Response(404, json={"path": path})

    result = await _fetch("projects", handle)
    assert result.snapshot is False
    assert result.next_cursor is None
    assert _refs(result) == {"projects/p1"}


async def test_users_read_the_bare_array_page() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth/token/accessible-resources":
            return httpx.Response(200, json=[SITE])
        if path == f"/ex/jira/{CLOUD_ID}/rest/api/3/users/search":
            return httpx.Response(
                200,
                json=[
                    {"accountId": "a1", "displayName": "Alice", "emailAddress": "alice@acme.com"},
                    {"accountId": "a2", "displayName": "Bob", "emailAddress": "bob@acme.com"},
                ],
            )
        return httpx.Response(404, json={"path": path})

    result = await _fetch("users", handle)
    assert result.snapshot is False
    assert result.next_cursor is None
    assert _refs(result) == {"users/a1", "users/a2"}


async def test_permission_refusal_raises_stream_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token/accessible-resources":
            return httpx.Response(200, json=[SITE])
        return httpx.Response(403, json={"errorMessages": ["forbidden"]})

    with pytest.raises(StreamSkipped, match="refused"):
        await _fetch("issues", handle)
