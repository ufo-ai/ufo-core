"""Instagram connector over a mock transport: the Page walk (`/me/accounts`), the linked
Instagram-business-account fan-out, a media collection paged by `data` + `paging.next`, the
`timestamp` watermark, and a refusal at the account walk surfacing as `StreamSkipped`. Offline — a
canned transport, no DB, no token."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from selfhost_ext_sources.instagram import InstagramConnector

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
    return await ConnectorBackend(connector=InstagramConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, _auth(handler)
    )


_PAGE = {
    "id": "p1",
    "name": "Acme Page",
    "instagram_business_account": {"id": "iga1", "username": "acme", "name": "Acme"},
}


def _handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "graph.facebook.com"
        path = request.url.path
        if path.endswith("/me/accounts"):
            return httpx.Response(200, json={"data": [_PAGE], "paging": {}})
        if path.endswith("/iga1/media"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "m1",
                            "caption": "Launch day",
                            "media_type": "IMAGE",
                            "permalink": "https://instagram.com/p/m1",
                            "timestamp": "2026-02-01T00:00:00+0000",
                        }
                    ],
                    "paging": {},
                },
            )
        return httpx.Response(404, json={"path": path})

    return handle


async def test_pages_walk_keys_by_id() -> None:
    result = await _fetch("pages", _handler())
    assert {page.source_ref for page in result.pages} == {"pages/p1"}
    assert result.snapshot is False


async def test_media_fans_out_and_advances_timestamp_watermark() -> None:
    result = await _fetch("media", _handler())
    assert {page.source_ref for page in result.pages} == {"media/m1"}
    assert result.next_cursor == "2026-02-01T00:00:00+0000"


async def test_refusal_at_account_walk_maps_to_stream_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "forbidden"}})

    with pytest.raises(StreamSkipped):
        await _fetch("media", handle)
