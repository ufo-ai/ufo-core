"""The Wrike connector over a mock transport: the `nextPageToken` body-cursor walk, the client-side
`updatedDate` filter past the stored watermark, and a refusal as `StreamSkipped`. Offline —
a canned transport, no DB, no token."""

import json
from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.wrike import WrikeConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"


def _flat(result: SyncResult, ref: str) -> dict:
    body = next(page.body for page in result.pages if page.source_ref == ref)
    return json.loads(body.split("\n\n", 1)[1])


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=WrikeConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


def _handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v4/tasks"
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "t1", "title": "Old", "updatedDate": "2026-01-01T00:00:00Z"},
                    {"id": "t2", "title": "New", "updatedDate": "2026-02-02T00:00:00Z"},
                ]
            },
        )

    return handle


async def test_tasks_walk_and_advance_watermark() -> None:
    result = await _fetch("tasks", _handler())
    assert _refs(result) == {"tasks/t1", "tasks/t2"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-02T00:00:00Z"


async def test_tasks_incremental_filters_past_watermark() -> None:
    result = await _fetch("tasks", _handler(), cursor="2026-01-15T00:00:00Z")
    assert _refs(result) == {"tasks/t2"}


async def test_tasks_flatten_derives_name_status_due_date_and_created_at() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v4/tasks"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "t1",
                        "title": "Ship",
                        "status": "Completed",
                        "dates": {"due": "2026-03-01"},
                        "createdDate": "2026-01-01T00:00:00Z",
                        "updatedDate": "2026-02-02T00:00:00Z",
                    }
                ]
            },
        )

    result = await _fetch("tasks", handle)
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-02T00:00:00.000000+00:00"
    record = _flat(result, "tasks/t1")
    assert record["name"] == "Ship"
    assert record["status"] == "Completed"
    assert record["due_date"] == "2026-03-01"
    assert record["created_at"] == "2026-01-01T00:00:00Z"


async def test_contacts_folders_and_comments_flatten_derive_their_fields() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/v4/contacts":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "c1",
                            "firstName": "Ada",
                            "lastName": "Lovelace",
                            "profiles": [{"email": "ada@example.com"}],
                            "createdDate": "2026-01-01T00:00:00Z",
                        }
                    ]
                },
            )
        if path == "/api/v4/folders":
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "f1", "title": "Docs", "createdDate": "2026-01-02T00:00:00Z"}]
                },
            )
        if path == "/api/v4/comments":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "cm1",
                            "text": "LGTM",
                            "authorId": "u9",
                            "taskId": "t1",
                            "createdDate": "2026-01-03T00:00:00Z",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"path": path})

    contact = _flat(await _fetch("contacts", handle), "contacts/c1")
    assert contact["name"] == "Ada Lovelace"
    assert contact["email"] == "ada@example.com"

    folder = _flat(await _fetch("folders", handle), "folders/f1")
    assert folder["name"] == "Docs"
    assert folder["api_url"] == "https://www.wrike.com/api/v4/folders/f1"

    comment = _flat(await _fetch("comments", handle), "comments/cm1")
    assert comment["body"] == "LGTM"
    assert comment["author"] == "u9"
    assert comment["parent_external_id"] == "t1"


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errorDescription": "unauthorized"})

    with pytest.raises(StreamSkipped):
        await _fetch("tasks", handle)
