"""The Rippling connector over a mock transport: the `next`-link cursor walk, the resource-named
records envelope, the `?updatedAfter` incremental filter over `updatedAt`, and a refusal as
`StreamSkipped`. Offline — a canned transport, no DB, no token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.rippling import RipplingConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

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
    return await ConnectorBackend(connector=RipplingConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_workers_follow_next_and_advance_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/workers"
        if request.url.params.get("cursor") == "c2":
            return httpx.Response(
                200, json={"workers": [{"id": "w2", "updatedAt": "2026-02-02T00:00:00Z"}]}
            )
        return httpx.Response(
            200,
            json={
                "workers": [{"id": "w1", "updatedAt": "2026-02-01T00:00:00Z"}],
                "next": "https://rest.ripplingapis.com/workers?cursor=c2",
            },
        )

    result = await _fetch("workers", handle)
    assert _refs(result) == {"workers/w1", "workers/w2"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-02T00:00:00Z"


async def test_workers_incremental_sends_updated_after() -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("updatedAfter"))
        return httpx.Response(
            200, json={"workers": [{"id": "w1", "updatedAt": "2026-02-01T00:00:00Z"}]}
        )

    await _fetch("workers", handle, cursor="2026-01-01T00:00:00Z")
    assert seen == ["2026-01-01T00:00:00Z"]


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch("workers", handle)
