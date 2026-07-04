"""monday.com connector over a mock transport: the GraphQL `POST /` endpoint, page-numbered root
collections, the `updated_at` watermark filter on an incremental stream, and the GraphQL `errors`
array surfacing as `StreamSkipped`. Offline — a canned transport, no DB, no token."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from selfhost_ext_sources.monday import MondayConnector

from selfhost.connectors import Credential
from selfhost.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from selfhost.sources.sync import SourceAuth, StreamSkipped

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
                            {"id": "u1", "name": "Ada", "email": "ada@x.com", "created_at": "2026"}
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


async def test_graphql_error_maps_to_stream_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "not authorized"}]})

    with pytest.raises(StreamSkipped):
        await _fetch("users", handle)
