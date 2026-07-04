"""The Typeform connector over a mock transport: the `page_count` page-number loop (`forms`),
the per-form response fan-out through the `next_page_token` body cursor, and a refusal surfacing as
`StreamSkipped`. Offline — a canned transport, no DB, no token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from selfhost_ext_sources.typeform import TypeformConnector

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
    return await ConnectorBackend(connector=TypeformConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_forms_page_count_loop_and_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/forms"
        assert request.url.params.get("page_size") == "200"
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": "f1", "title": "Survey", "last_updated_at": "2026-02-01T00:00:00Z"}
                ],
                "page_count": 1,
            },
        )

    result = await _fetch("forms", handle)
    assert _refs(result) == {"forms/f1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-01T00:00:00Z"


async def test_responses_fan_out_per_form() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/forms":
            return httpx.Response(
                200, json={"items": [{"id": "f1", "title": "Survey"}], "page_count": 1}
            )
        if request.url.path == "/forms/f1/responses":
            return httpx.Response(
                200, json={"items": [{"token": "t1", "submitted_at": "2026-02-01T00:00:00Z"}]}
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("responses", handle)
    assert _refs(result) == {"responses/t1"}
    assert result.next_cursor == "2026-02-01T00:00:00Z"


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": "AUTHORIZATION_ERROR"})

    with pytest.raises(StreamSkipped):
        await _fetch("forms", handle)
