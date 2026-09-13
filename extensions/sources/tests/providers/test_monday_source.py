"""monday.com connector over a mock transport: the GraphQL `POST /` endpoint, page-numbered root
collections, the `updated_at` watermark filter on an incremental stream, and the GraphQL `errors`
array surfacing as `StreamSkipped`. Offline — a canned transport, no DB, no token."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.monday import ALL_STREAMS, MondayConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
)

Landed = Mapping[str, tuple[ParentRecord, ...]]
ParentsReader = Callable[[Landed], ParentPages]

LANDED: Mapping[str, tuple[ParentRecord, ...]] = {
    "boards": (ParentRecord(ref="boards/b1", fields={"id": "b1"}),)
}


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(
    handler: Callable[[httpx.Request], httpx.Response], parents: ParentPages | None
) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler), parents=parents)


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    cursor: str | None = None,
    parents: ParentPages | None = None,
):
    return await ConnectorBackend(connector=MondayConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, _auth(handler, parents)
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
                                "created_at": "2026-01-01T00:00:00Z",
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
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"


async def test_boards_flatten_derives_api_url() -> None:
    record = _flat(await _fetch("boards", _boards_handler()), "boards/b1")
    assert record["name"] == "Roadmap"
    assert record["api_url"] == "https://api.monday.com/v2/boards/b1"


def _items_handler(asked: list[dict] | None = None) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        variables = _graphql(request)["variables"]
        if asked is not None:
            asked.append(variables)
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
                                            "updated_at": "2026-02-01T00:00:00Z",
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


async def test_items_flatten_derives_name_and_status_from_state(
    parents_reader: ParentsReader,
) -> None:
    result = await _fetch("items", _items_handler(), parents=parents_reader(LANDED))
    record = _flat(result, "items/it1")
    assert record["name"] == "Task A"
    assert record["status"] == "done"
    assert record["created_at"] == "2026-01-01T00:00:00Z"
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"


async def test_items_hang_on_their_board_and_keep_an_item_id_address(
    parents_reader: ParentsReader,
) -> None:
    """A monday item id is unique across the account, so an item asks its board's items page and is
    addressed by its own id."""
    asked: list[dict] = []

    result = await _fetch("items", _items_handler(asked), parents=parents_reader(LANDED))

    assert {page.source_ref for page in result.pages} == {"items/it1"}
    assert {page.source_identity for page in result.pages} == {"items/it1"}
    assert asked == [{"board_ids": ["b1"]}]


async def test_activity_logs_fan_out_over_landed_boards(parents_reader: ParentsReader) -> None:
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        body = _graphql(request)
        asked.append(body["query"])
        assert body["variables"] == {"board_ids": ["b1"]}
        return httpx.Response(
            200,
            json={
                "data": {
                    "boards": [
                        {
                            "id": "b1",
                            "activity_logs": [
                                {
                                    "id": "log1",
                                    "event": "update_column_value",
                                    "data": "{}",
                                    "user_id": "u2",
                                    "created_at": "2026-02-01T00:00:00Z",
                                }
                            ],
                        }
                    ]
                }
            },
        )

    result = await _fetch("activity_logs", handle, parents=parents_reader(LANDED))

    assert {page.source_ref for page in result.pages} == {"activity_logs/b1/log1"}
    assert len(asked) == 1
    assert "activity_logs(" in asked[0]
    record = _flat(result, "activity_logs/b1/log1")
    assert record["subject"] == "update_column_value"
    assert record["author"] == "u2"


async def test_board_children_declare_the_board_whose_id_their_query_names() -> None:
    declared = {stream.name: stream for stream in ALL_STREAMS}
    edges = {
        name: tuple((edge.stream, edge.path) for edge in stream.parents)
        for name, stream in declared.items()
        if stream.parents
    }
    assert edges == {
        "items": (("boards", "{id}"),),
        "activity_logs": (("boards", "{id}"),),
    }
    assert declared["items"].key_scope == "global"
    assert declared["activity_logs"].key_scope == "local"


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
