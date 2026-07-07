"""The Linear connector over a mock transport: the GraphQL `POST /graphql` shape — one query per
page, `pageInfo.endCursor` threaded into the next request's `after`, `hasNextPage` ending the walk —
the incremental `filter: { updatedAt: { gte } }` gate, a full-refresh stream (no `updatedAt` filter)
threading no variables, the `render` override that lifts an issue/project into readable prose, a 403
surfacing as `StreamSkipped`, and a GraphQL `errors` array failing loud. No conftest: the shared
`ufo_testsupport` plugin covers fixtures, and these tests are offline (a canned transport, no
DB, no token, no broker)."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.linear import LinearConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    cursor: str | None = None,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler))
    return await ConnectorBackend(connector=LinearConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


def _issue(issue_id: str, title: str, updated: str) -> dict[str, object]:
    return {
        "id": issue_id,
        "identifier": f"ENG-{issue_id}",
        "title": title,
        "description": f"Do the {title} work",
        "state": {"id": "s1", "type": "started"},
        "priorityLabel": "High",
        "assignee": {"id": "u1"},
        "updatedAt": updated,
    }


def _issues_handler(
    bodies: list[dict[str, object]],
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.linear.app"
        assert request.method == "POST" and request.url.path == "/graphql"
        body = json.loads(request.content)
        bodies.append(body)
        variables = body.get("variables") or {}
        if variables.get("after") == "c2":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issues": {
                            "nodes": [_issue("i2", "Second", "2026-02-05T00:00:00.000Z")],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        }
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [_issue("i1", "First", "2026-02-01T00:00:00.000Z")],
                        "pageInfo": {"hasNextPage": True, "endCursor": "c2"},
                    }
                }
            },
        )

    return handle


async def test_issues_paginate_over_pageinfo_and_render_lifts_readable_body() -> None:
    bodies: list[dict[str, object]] = []
    result = await _fetch("issues", _issues_handler(bodies))

    assert _refs(result) == {"issues/i1", "issues/i2"}
    assert result.snapshot is False
    assert result.deletes == ()
    assert result.next_cursor == "2026-02-05T00:00:00.000Z"
    assert any((body.get("variables") or {}).get("after") == "c2" for body in bodies)

    body = next(page.body for page in result.pages if page.source_ref == "issues/i1")
    assert "First" in body
    assert "Do the First work" in body
    assert "started" in body
    assert "High" in body
    assert "pageInfo" not in body
    assert "nodes" not in body


async def test_issues_incremental_sends_the_updatedat_filter_and_orderby() -> None:
    bodies: list[dict[str, object]] = []
    await _fetch("issues", _issues_handler(bodies), cursor="2026-01-01T00:00:00.000Z")

    variables = bodies[0]["variables"]
    assert variables["orderBy"] == "updatedAt"
    assert variables["filter"] == {"updatedAt": {"gte": "2026-01-01T00:00:00.000Z"}}


async def test_full_refresh_stream_upserts_without_a_snapshot() -> None:
    """A collection with no `updatedAt` filter (issue_relations) full-refreshes each run:
    snapshot=False, no cursor advanced, and no `filter`/`orderBy` variables threaded."""
    bodies: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "data": {
                    "issueRelations": {
                        "nodes": [
                            {
                                "id": "r1",
                                "type": "blocks",
                                "issue": {"id": "i1"},
                                "relatedIssue": {"id": "i2"},
                            }
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            },
        )

    result = await _fetch("issue_relations", handle)
    assert result.snapshot is False
    assert result.next_cursor is None
    assert result.deletes == ()
    assert _refs(result) == {"issue_relations/r1"}
    assert "variables" not in bodies[0]


async def test_project_render_is_readable() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": {
                    "projects": {
                        "nodes": [
                            {
                                "id": "p1",
                                "name": "Launch",
                                "description": "Ship v1 to customers",
                                "state": "started",
                                "lead": {"id": "u1"},
                                "targetDate": "2026-03-01",
                                "updatedAt": "2026-02-01T00:00:00.000Z",
                            }
                        ],
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                    }
                }
            },
        )

    result = await _fetch("projects", handle)
    assert _refs(result) == {"projects/p1"}
    body = result.pages[0].body
    assert "Launch" in body
    assert "Ship v1 to customers" in body
    assert "started" in body
    assert "nodes" not in body


async def test_forbidden_status_raises_stream_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errors": [{"message": "forbidden"}]})

    with pytest.raises(StreamSkipped, match="refused"):
        await _fetch("issues", handle)


async def test_graphql_errors_fail_loud() -> None:
    """A GraphQL `errors` array (a 200 the query engine rejected) fails loud rather than commit a
    partial page — the run surfaces the fault instead of silently syncing nothing."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"errors": [{"message": "Field 'bogus' doesn't exist on type 'Issue'"}]}
        )

    with pytest.raises(RuntimeError, match="graphql error"):
        await _fetch("issues", handle)
