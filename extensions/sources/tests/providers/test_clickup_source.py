"""The ClickUp connector over a mock transport: the top-down hierarchy walk (`/team` →
`/team/{id}/space` → `/space/{id}/folder` + `/space/{id}/list`), the members collapsed into `users`,
the per-list task fan-out paged by an integer `?page`, and the `date_updated` watermark advancing.
Offline — a canned transport, no DB, no token, no broker."""

import json
from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.clickup import ClickUpConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

ACCOUNT = "acct-1"

TEAM = {
    "id": "t1",
    "name": "Acme",
    "members": [{"user": {"id": "u1", "username": "Ada", "email": "ada@example.com"}}],
}


def _flat(result: SyncResult, ref: str) -> dict:
    body = next(page.body for page in result.pages if page.source_ref == ref)
    return json.loads(body.split("\n\n", 1)[1])


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=ClickUpConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _hierarchy_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.clickup.com"
        path = request.url.path
        if path.endswith("/team"):
            return httpx.Response(200, json={"teams": [TEAM]})
        if path.endswith("/team/t1/space"):
            return httpx.Response(200, json={"spaces": [{"id": "s1", "name": "Space"}]})
        if path.endswith("/space/s1/folder"):
            return httpx.Response(200, json={"folders": []})
        if path.endswith("/space/s1/list"):
            return httpx.Response(200, json={"lists": [{"id": "l1", "name": "List"}]})
        if path.endswith("/list/l1/task"):
            if request.url.params.get("page") == "0":
                return httpx.Response(
                    200, json={"tasks": [{"id": "tk1", "name": "Do", "date_updated": "200"}]}
                )
            return httpx.Response(200, json={"tasks": []})
        return httpx.Response(404, json={"path": path})

    return handle


async def test_teams_are_the_root_of_the_hierarchy() -> None:
    result = await _fetch("teams", _hierarchy_handler())
    assert {page.source_ref for page in result.pages} == {"teams/t1"}
    assert result.snapshot is False
    assert "Acme" in result.pages[0].body


async def test_users_collapse_the_team_members() -> None:
    result = await _fetch("users", _hierarchy_handler())
    assert {page.source_ref for page in result.pages} == {"users/u1"}
    assert "ada@example.com" in result.pages[0].body


async def test_tasks_fan_out_per_list_and_advance_the_watermark() -> None:
    result = await _fetch("tasks", _hierarchy_handler())
    assert {page.source_ref for page in result.pages} == {"tasks/tk1"}
    assert result.next_cursor == "200"


def _shaped_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/team"):
            return httpx.Response(200, json={"teams": [TEAM]})
        if path.endswith("/team/t1/space"):
            return httpx.Response(200, json={"spaces": [{"id": "s1", "name": "Space"}]})
        if path.endswith("/space/s1/folder"):
            return httpx.Response(200, json={"folders": []})
        if path.endswith("/space/s1/list"):
            return httpx.Response(200, json={"lists": [{"id": "l1", "name": "List"}]})
        if path.endswith("/list/l1/task"):
            if request.url.params.get("page") == "0":
                return httpx.Response(
                    200,
                    json={
                        "tasks": [
                            {
                                "id": "tk1",
                                "name": "Do",
                                "status": {"status": "in progress"},
                                "due_date": "1700",
                                "date_created": "1600",
                                "date_updated": "200",
                            }
                        ]
                    },
                )
            return httpx.Response(200, json={"tasks": []})
        if path.endswith("/list/l1/comment"):
            return httpx.Response(
                200,
                json={
                    "comments": [
                        {
                            "id": "cm1",
                            "comment_text": "nice work",
                            "user": {"id": "u2", "username": "Bo"},
                            "date": "300",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"path": path})

    return handle


async def test_list_comments_flatten_derive_body_author_and_parent() -> None:
    record = _flat(await _fetch("list_comments", _shaped_handler()), "list_comments/cm1")
    assert record["body"] == "nice work"
    assert record["author"] == "Bo"
    assert record["parent_external_id"] == "l1"


async def test_a_forbidden_team_listing_fails_the_run() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"err": "OAUTH_017"})

    with pytest.raises(httpx.HTTPStatusError):
        await _fetch("teams", handle)
