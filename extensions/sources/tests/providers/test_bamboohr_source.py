"""The BambooHR connector over a mock transport: the single-shot directory (list under `employees`)
hitting the per-tenant gateway host, the HTTP Basic auth built from a direct key (password `"x"`),
and a refusal surfacing as `StreamSkipped`. The class base URL is empty (per-tenant), so the tenant
host is bound through `SourceAuth.base_url`. Offline — a canned transport, no token."""

import base64
from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.bamboohr import BambooHRConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

BASE_URL = "https://api.bamboohr.com/api/gateway.php/acme"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(stream: str, handler: Callable[[httpx.Request], httpx.Response]) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler), base_url=BASE_URL)
    return await ConnectorBackend(connector=BambooHRConnector()).fetch(
        ConnectorSourceConfig(stream=stream), None, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_directory_single_shot_hits_the_tenant_gateway_host() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.bamboohr.com"
        assert request.url.path == "/api/gateway.php/acme/v1/employees/directory"
        return httpx.Response(200, json={"employees": [{"id": 42, "displayName": "Ada"}]})

    result = await _fetch("employees_directory", handle)
    assert _refs(result) == {"employees_directory/42"}
    assert result.snapshot is False


async def test_time_off_creation_is_not_reported_as_an_update() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/time_off/requests/")
        return httpx.Response(
            200,
            json=[
                {
                    "id": "request-1",
                    "created": "2026-01-01T00:00:00Z",
                }
            ],
        )

    result = await _fetch("time_off_requests", handle)
    assert result.next_cursor == "2026-01-01T00:00:00Z"
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_timesheet_start_projects_as_record_creation_time() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/time_tracking/timesheet_entries")
        return httpx.Response(
            200,
            json={
                "entries": [
                    {
                        "id": "entry-1",
                        "start": "2026-01-01T09:00:00Z",
                    }
                ]
            },
        )

    result = await _fetch("timesheet_entries", handle)
    assert result.next_cursor == "2026-01-01T09:00:00Z"
    assert result.pages[0].created_at == "2026-01-01T09:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_basic_auth_built_from_a_direct_key() -> None:
    client = BambooHRConnector()._make_client(BASE_URL, Credential(bearer="key-123"))
    authed = next(client.auth.auth_flow(httpx.Request("GET", BASE_URL)))
    expected = "Basic " + base64.b64encode(b"key-123:x").decode()
    assert authed.headers["Authorization"] == expected


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(StreamSkipped):
        await _fetch("employees_directory", handle)
