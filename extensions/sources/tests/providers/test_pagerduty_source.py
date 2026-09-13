"""PagerDuty connector over a mock transport: the offset/limit envelope driven by the response's own
`more` flag + `limit` echo, the versioned Accept header, the `updated_at` incremental watermark on
incidents, notes read under the incident that holds them, and a refusal surfacing as
`StreamSkipped`. Offline — a canned transport, no DB, no token."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.pagerduty import INCIDENT_NOTES, PagerDutyConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
    no_parents,
)

ParentsReader = Callable[[Mapping[str, tuple[ParentRecord, ...]]], ParentPages]

INCIDENTS: Mapping[str, tuple[ParentRecord, ...]] = {
    "incidents": (
        ParentRecord(ref="incidents/PINC1", fields={"id": "PINC1"}),
        ParentRecord(ref="incidents/PINC2", fields={"id": "PINC2"}),
    )
}


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    cursor: str | None = None,
    parents: ParentPages = no_parents,
):
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler), parents=parents)
    return await ConnectorBackend(connector=PagerDutyConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
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


async def test_an_incident_projects_the_id_its_notes_read() -> None:
    result = await _fetch("incidents", _incidents_handler())
    assert result.pages[0].parent_fields == {"id": "i1"}


async def test_notes_are_addressed_under_the_incident_that_holds_them(
    parents_reader: ParentsReader,
) -> None:
    """One request per landed incident and no incident walk of its own: the `/incidents` page the
    incidents row already spends is not spent again here. A note id is unique account-wide, so the
    incident in the address scopes rather than disambiguates it — the stream is not canonical, so
    no page has ever landed under the unscoped key."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        incident = request.url.path.split("/")[2]
        return httpx.Response(
            200,
            json={
                "notes": [
                    {
                        "id": f"NOTE-{incident}",
                        "content": "ack",
                        "created_at": "2026-02-01T01:00:00Z",
                    }
                ]
            },
        )

    result = await _fetch("incident_notes", handle, parents=parents_reader(INCIDENTS))

    assert seen == ["/incidents/PINC1/notes", "/incidents/PINC2/notes"]
    assert [page.source_identity for page in result.pages] == [
        "incident_notes/PINC1/NOTE-PINC1",
        "incident_notes/PINC2/NOTE-PINC2",
    ]
    assert [page.source_ref for page in result.pages] == [
        "incident_notes/PINC1/NOTE-PINC1",
        "incident_notes/PINC2/NOTE-PINC2",
    ]
    assert INCIDENT_NOTES.canonical is False


async def test_an_incident_that_has_landed_nothing_yet_spends_no_request(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.url}")

    result = await _fetch("incident_notes", handle, parents=no_parents)
    assert result.pages == ()


async def test_refusal_maps_to_stream_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(StreamSkipped):
        await _fetch("users", handle)
