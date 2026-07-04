"""Greenhouse Harvest connector over a mock transport: the RFC 5988 Link-header cursor walk, the
per-parent substream fan-out (walk parents, then each parent's child collection stamped with the
parent id), the server-side `updated_after` incremental filter, and the refusal → `StreamSkipped`
boundary. Offline — a canned transport, no DB, no token, no broker."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from selfhost_ext_sources.greenhouse import GreenhouseConnector

from selfhost.connectors import Credential
from selfhost.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from selfhost.sources.sync import SourceAuth, StreamSkipped

ACCOUNT = "acct-1"
NEXT_JOBS_PAGE = "https://harvest.greenhouse.io/v1/jobs?per_page=500&page=2"


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
    return await ConnectorBackend(connector=GreenhouseConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, _auth(handler)
    )


JOB_1 = {"id": 1, "name": "Staff Engineer", "updated_at": "2026-01-01T00:00:00Z"}
JOB_2 = {"id": 2, "name": "Product Manager", "updated_at": "2026-02-01T00:00:00Z"}
JOB_3 = {"id": 3, "name": "Designer", "updated_at": "2026-03-01T00:00:00Z"}


def _jobs_handler(seen_cursor: list[str | None]) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "harvest.greenhouse.io"
        if request.url.path == "/v1/jobs":
            if request.url.params.get("page") == "2":
                return httpx.Response(200, json=[JOB_3])
            seen_cursor.append(request.url.params.get("updated_after"))
            return httpx.Response(
                200,
                json=[JOB_1, JOB_2],
                headers={"Link": f'<{NEXT_JOBS_PAGE}>; rel="next"'},
            )
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_jobs_link_header_walk_advances_the_watermark() -> None:
    result = await _fetch("jobs", _jobs_handler([]))

    assert {page.source_ref for page in result.pages} == {"jobs/1", "jobs/2", "jobs/3"}
    assert result.snapshot is False
    assert result.deletes == ()
    assert result.next_cursor == "2026-03-01T00:00:00Z"

    body = next(page.body for page in result.pages if page.source_ref == "jobs/1")
    assert "Staff Engineer" in body


async def test_incremental_sends_updated_after_from_the_cursor() -> None:
    seen_cursor: list[str | None] = []
    await _fetch("jobs", _jobs_handler(seen_cursor), cursor="2026-01-15T00:00:00Z")
    assert seen_cursor == ["2026-01-15T00:00:00Z"]


def _openings_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/jobs":
            return httpx.Response(200, json=[JOB_1])
        if request.url.path == "/v1/jobs/1/openings":
            return httpx.Response(200, json=[{"id": 100, "status": "open"}])
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_per_parent_fan_out_stamps_the_parent_id() -> None:
    result = await _fetch("jobs_openings", _openings_handler())
    assert {page.source_ref for page in result.pages} == {"jobs_openings/100"}
    assert '"job_id": 1' in result.pages[0].body


async def test_refusal_maps_to_stream_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Invalid Basic Auth credentials"})

    with pytest.raises(StreamSkipped):
        await _fetch("jobs", handle)
