"""The Recurly connector over a mock transport: the `{data, has_more, next}` cursor walk with the
watermark advancing over `updated_at`, the per-parent fanout (`/accounts/{id}/notes`) stamping the
parent id, and a refusal surfacing as `StreamSkipped`. Offline — a canned transport, no DB, no
token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.recurly import RecurlyConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=RecurlyConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_accounts_walk_next_and_advance_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/accounts"
        if request.url.params.get("cursor") == "c2":
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "a2", "updated_at": "2026-02-02T00:00:00Z"}],
                    "has_more": False,
                },
            )
        return httpx.Response(
            200,
            json={
                "data": [{"id": "a1", "updated_at": "2026-02-01T00:00:00Z"}],
                "has_more": True,
                "next": "/accounts?cursor=c2",
            },
        )

    result = await _fetch("accounts", handle)
    assert _refs(result) == {"accounts/a1", "accounts/a2"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-02T00:00:00Z"
    assert {page.updated_at for page in result.pages} == {
        "2026-02-01T00:00:00.000000+00:00",
        "2026-02-02T00:00:00.000000+00:00",
    }


async def test_account_notes_fan_out_per_parent() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/accounts":
            return httpx.Response(200, json={"data": [{"id": "a1"}], "has_more": False})
        if request.url.path == "/accounts/a1/notes":
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "n1", "created_at": "2026-02-01T00:00:00Z"}],
                    "has_more": False,
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("account_notes", handle)
    assert _refs(result) == {"account_notes/n1"}
    assert result.next_cursor == "2026-02-01T00:00:00Z"
    assert result.pages[0].created_at == "2026-02-01T00:00:00.000000+00:00"


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch("accounts", handle)
