"""The Wrike connector over a mock transport: the `nextPageToken` body-cursor walk, the client-side
`updatedDate` filter past the stored watermark, and a refusal as `StreamSkipped`. Offline —
a canned transport, no DB, no token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from selfhost_ext_sources.wrike import WrikeConnector

from selfhost.connectors import Credential
from selfhost.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from selfhost.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"


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


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errorDescription": "unauthorized"})

    with pytest.raises(StreamSkipped):
        await _fetch("tasks", handle)
