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

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.jira import JiraConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

ACCOUNT = "acct-1"
CLOUD_ID = "cloud-1"
SITE = {"id": CLOUD_ID, "url": "https://acme.atlassian.net", "name": "Acme"}


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    cursor: str | None = None,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=JiraConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
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


async def test_issue_comments_preserve_comment_creation_time() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth/token/accessible-resources":
            return httpx.Response(200, json=[SITE])
        if path == f"/ex/jira/{CLOUD_ID}/rest/api/3/search":
            return httpx.Response(
                200, json={"issues": [ISSUE_1], "startAt": 0, "maxResults": 100, "total": 1}
            )
        if path == f"/ex/jira/{CLOUD_ID}/rest/api/3/issue/10001/comment":
            return httpx.Response(
                200,
                json={
                    "comments": [
                        {
                            "id": "c1",
                            "author": {"displayName": "Ada"},
                            "body": {
                                "type": "doc",
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [{"type": "text", "text": "Investigating"}],
                                    }
                                ],
                            },
                            "created": "2026-01-02T00:00:00.000+0000",
                            "updated": "2026-01-03T00:00:00.000+0000",
                        }
                    ],
                    "startAt": 0,
                    "maxResults": 100,
                    "total": 1,
                },
            )
        return httpx.Response(404, json={"path": path})

    result = await _fetch("issue_comments", handle)
    assert result.pages[0].created_at == "2026-01-02T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-01-03T00:00:00.000000+00:00"


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
