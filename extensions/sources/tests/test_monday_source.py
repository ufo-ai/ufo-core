"""monday.com connector over a mock transport: the GraphQL `POST /` endpoint, page-numbered root
collections, the `updated_at` watermark filter on an incremental stream, and the GraphQL `errors`
array surfacing as `StreamSkipped`. Offline — a canned transport, no DB, no token."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.monday import MondayConnector

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
    return await ConnectorBackend(connector=MondayConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, _auth(handler)
    )


def _graphql(request: httpx.Request) -> dict:
    assert request.method == "POST"
    assert request.url.host == "api.monday.com"
    return json.loads(request.content)


def _flat(result, ref: str) -> dict:
    body = next(page.body for page in result.pages if page.source_ref == ref)
    return json.loads(body.split("\n\n", 1)[1])


def _users_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        body = _graphql(request)
        page = body["variables"].get("page")
        if "users(" in body["query"] and page == 1:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "users": [
                            {
                                "id": "u1",
                                "name": "Ada",
                                "email": "ada@x.com",
                                "created_at": "2026-01-01",
                            }
                        ]
                    }
                },
            )
        return httpx.Response(200, json={"data": {"users": []}})

    return handle


async def test_users_paginates_and_keys_by_id() -> None:
    result = await _fetch("users", _users_handler())
    assert {page.source_ref for page in result.pages} == {"users/u1"}
    assert result.snapshot is False


def _boards_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        body = _graphql(request)
        page = body["variables"].get("page")
        if "boards(" in body["query"] and page == 1:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "boards": [
                            {
                                "id": "b1",
                                "name": "Roadmap",
                                "state": "active",
                                "updated_at": "2026-02-01T00:00:00Z",
                            }
                        ]
                    }
                },
            )
        return httpx.Response(200, json={"data": {"boards": []}})

    return handle


async def test_boards_incremental_advances_watermark() -> None:
    result = await _fetch("boards", _boards_handler())
    assert {page.source_ref for page in result.pages} == {"boards/b1"}
    assert result.next_cursor == "2026-02-01T00:00:00Z"


async def test_boards_flatten_derives_api_url() -> None:
    record = _flat(await _fetch("boards", _boards_handler()), "boards/b1")
    assert record["name"] == "Roadmap"
    assert record["api_url"] == "https://api.monday.com/v2/boards/b1"


def _items_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        variables = _graphql(request)["variables"]
        if "page" in variables:
            if variables["page"] == 1:
                return httpx.Response(
                    200, json={"data": {"boards": [{"id": "b1", "name": "Board"}]}}
                )
            return httpx.Response(200, json={"data": {"boards": []}})
        if "board_ids" in variables:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "boards": [
                            {
                                "items_page": {
                                    "cursor": None,
                                    "items": [
                                        {
                                            "id": "it1",
                                            "name": "Task A",
                                            "state": "done",
                                            "created_at": "2026-01-01T00:00:00Z",
                                            "column_values": [],
                                        }
                                    ],
                                }
                            }
                        ]
                    }
                },
            )
        return httpx.Response(200, json={"data": {}})

    return handle


async def test_items_flatten_derives_name_and_status_from_state() -> None:
    record = _flat(await _fetch("items", _items_handler()), "items/it1")
    assert record["name"] == "Task A"
    assert record["status"] == "done"
    assert record["created_at"] == "2026-01-01T00:00:00Z"


def _updates_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        body = _graphql(request)
        if "updates(" in body["query"] and body["variables"].get("page") == 1:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "updates": [
                            {
                                "id": "up1",
                                "text_body": "hello there",
                                "body": "<p>hello there</p>",
                                "creator": {"email": "bo@example.com", "name": "Bo", "id": "u2"},
                                "item_id": "it1",
                                "created_at": "2026-02-01T00:00:00Z",
                            }
                        ]
                    }
                },
            )
        return httpx.Response(200, json={"data": {"updates": []}})

    return handle


async def test_updates_flatten_derive_body_author_and_parent() -> None:
    record = _flat(await _fetch("updates", _updates_handler()), "updates/up1")
    assert record["body"] == "hello there"
    assert record["author"] == "bo@example.com"
    assert record["parent_external_id"] == "it1"


async def test_graphql_error_maps_to_stream_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "not authorized"}]})

    with pytest.raises(StreamSkipped):
        await _fetch("users", handle)
