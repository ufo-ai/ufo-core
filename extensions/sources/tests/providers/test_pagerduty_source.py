"""PagerDuty connector over a mock transport: the offset/limit envelope driven by the response's own
`more` flag + `limit` echo, the versioned Accept header, the `updated_at` incremental watermark on
incidents, and a refusal surfacing as `StreamSkipped`. Offline — a canned transport, no DB, no
token."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.pagerduty import PagerDutyConnector

from ufo.access.connectors import Credential
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
    return await ConnectorBackend(connector=PagerDutyConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, _auth(handler)
    )


def _users_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.pagerduty.com"
        assert request.headers.get("Accept") == "application/vnd.pagerduty+json;version=2"
        if request.url.path == "/users":
            return httpx.Response(
                200,
                json={
                    "users": [{"id": "u1", "name": "Ada", "email": "ada@x.com"}],
                    "more": False,
                    "limit": 25,
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_users_offset_page_keys_by_id() -> None:
    result = await _fetch("users", _users_handler())
    assert {page.source_ref for page in result.pages} == {"users/u1"}
    assert result.snapshot is False


def _incidents_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/incidents":
            return httpx.Response(
                200,
                json={
                    "incidents": [
                        {
                            "id": "i1",
                            "title": "Disk full",
                            "status": "resolved",
                            "updated_at": "2026-02-01T00:00:00Z",
                        }
                    ],
                    "more": False,
                    "limit": 25,
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_incidents_advance_watermark() -> None:
    result = await _fetch("incidents", _incidents_handler())
    assert {page.source_ref for page in result.pages} == {"incidents/i1"}
    assert result.next_cursor == "2026-02-01T00:00:00Z"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"


async def test_refusal_maps_to_stream_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(StreamSkipped):
        await _fetch("users", handle)
