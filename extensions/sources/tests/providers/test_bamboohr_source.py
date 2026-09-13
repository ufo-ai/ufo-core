"""The BambooHR connector over a mock transport: the single-shot directory (list under `employees`)
hitting the per-tenant gateway host, per-employee detail hydrated one request per directory row,
the HTTP Basic auth built from a direct key (password `"x"`), and a refusal surfacing as
`StreamSkipped`. The class base URL is empty (per-tenant), so the tenant host is bound through
`SourceAuth.base_url`. Offline — a canned transport, no token."""

import base64
from collections.abc import Callable, Mapping
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.bamboohr import BambooHRConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig, ParentPages, ParentRecord

ParentsReader = Callable[[Mapping[str, tuple[ParentRecord, ...]]], ParentPages]
BASE_URL = "https://api.bamboohr.com/api/gateway.php/acme"
LANDED: Mapping[str, tuple[ParentRecord, ...]] = {
    "employees_directory": (
        ParentRecord(ref="employees_directory/42", fields={"id": 42}),
        ParentRecord(ref="employees_directory/7", fields={"id": 7}),
    )
}


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    parents: ParentPages,
) -> SyncResult:
    auth = SourceAuth(
        workspace_id=uuid4(),
        auth_proxy=_MockProxy(handler),
        base_url=BASE_URL,
        parents=parents,
    )
    return await ConnectorBackend(connector=BambooHRConnector()).fetch(
        ConnectorSourceConfig(stream=stream), None, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_directory_single_shot_hits_the_tenant_gateway_host(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.bamboohr.com"
        assert request.url.path == "/api/gateway.php/acme/v1/employees/directory"
        return httpx.Response(200, json={"employees": [{"id": 42, "displayName": "Ada"}]})

    result = await _fetch("employees_directory", handle, parents=parents_reader(LANDED))
    assert _refs(result) == {"employees_directory/42"}
    assert result.snapshot is False
    assert result.pages[0].parent_fields == {"id": 42}


async def test_employee_detail_is_one_request_per_directory_row(
    parents_reader: ParentsReader,
) -> None:
    """A detail view is the degenerate fan-out: one request per parent, one record back. The
    directory it hydrates is the parent stream's own walk, so this run never asks for it, and an
    employee id is the account's own, so the detail settles on the address a flat declaration gave
    it rather than repeating the id it was reached under."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        eid = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json={"id": eid, "displayName": f"employee {eid}"})

    result = await _fetch("employees", handle, parents=parents_reader(LANDED))
    assert asked == [
        "/api/gateway.php/acme/v1/employees/42",
        "/api/gateway.php/acme/v1/employees/7",
    ]
    assert _refs(result) == {"employees/42", "employees/7"}


async def test_time_off_creation_is_not_reported_as_an_update(
    parents_reader: ParentsReader,
) -> None:
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

    result = await _fetch("time_off_requests", handle, parents=parents_reader(LANDED))
    assert result.next_cursor == "2026-01-01T00:00:00Z"
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_timesheet_start_projects_as_record_creation_time(
    parents_reader: ParentsReader,
) -> None:
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

    result = await _fetch("timesheet_entries", handle, parents=parents_reader(LANDED))
    assert result.next_cursor == "2026-01-01T09:00:00Z"
    assert result.pages[0].created_at == "2026-01-01T09:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_basic_auth_built_from_a_direct_key() -> None:
    client = BambooHRConnector()._make_client(BASE_URL, Credential(bearer="key-123"))
    authed = next(client.auth.auth_flow(httpx.Request("GET", BASE_URL)))
    expected = "Basic " + base64.b64encode(b"key-123:x").decode()
    assert authed.headers["Authorization"] == expected


async def test_stream_skipped_on_refusal(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(StreamSkipped):
        await _fetch("employees_directory", handle, parents=parents_reader(LANDED))
