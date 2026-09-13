"""The Attio connector over a mock transport: the records-query offset loop, the `flatten` that
lifts a nested composite id and reduces each `values` value-cell to its primitive (the point of this
provider — without it a record has no top-level primary key), its record timestamps reaching the
page projection, the full-snapshot `delete_missing` semantics, and the two refuse-paths
(`standard_object_disabled` on objects, `403 unauthorized` anywhere) mapping to `StreamSkipped`.
Offline — a canned transport, no DB, no token, no broker."""

from collections.abc import Callable, Mapping
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.attio import AttioConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
    syncing_streams,
)

Landed = Mapping[str, tuple[ParentRecord, ...]]
ParentsReader = Callable[[Landed], ParentPages]
MEETING_REF = "meetings/mt1"
MEETINGS: Landed = {"meetings": (ParentRecord(ref=MEETING_REF, fields={"id.meeting_id": "mt1"}),)}


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    reader: ParentsReader,
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    cursor: str | None = None,
    landed: Landed = MEETINGS,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler), parents=reader(landed))
    return await ConnectorBackend(connector=AttioConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


async def test_people_flatten_lifts_the_composite_id_and_value_cells(
    parents_reader: ParentsReader,
) -> None:
    person = {
        "id": {"record_id": "r1", "object_id": "o1", "workspace_id": "w1"},
        "created_at": "2026-01-15T10:00:00Z",
        "updated_at": "2026-01-20T11:00:00Z",
        "values": {
            "name": [{"first_name": "Ada", "last_name": "Lovelace", "full_name": "Ada Lovelace"}],
            "email_addresses": [{"email_address": "ada@example.com"}],
        },
    }

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.attio.com"
        assert request.method == "POST"
        assert request.url.path == "/v2/objects/people/records/query"
        return httpx.Response(200, json={"data": [person]})

    result = await _fetch(parents_reader, "people", handle)
    assert {page.source_ref for page in result.pages} == {"people/r1"}
    assert result.snapshot is True
    assert result.next_cursor is None
    assert result.pages[0].created_at == "2026-01-15T10:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-01-20T11:00:00.000000+00:00"

    body = result.pages[0].body
    assert "Ada Lovelace" in body
    assert "ada@example.com" in body


async def test_tasks_lift_the_task_id_and_snapshot(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/tasks"
        return httpx.Response(
            200, json={"data": [{"id": {"task_id": "tk1"}, "content_plaintext": "Follow up"}]}
        )

    result = await _fetch(parents_reader, "tasks", handle)
    assert {page.source_ref for page in result.pages} == {"tasks/tk1"}
    assert result.snapshot is True
    assert "Follow up" in result.pages[0].body


async def test_a_disabled_standard_object_skips_the_stream(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": "standard_object_disabled"})

    with pytest.raises(StreamSkipped):
        await _fetch(parents_reader, "deals", handle)


async def test_a_missing_scope_on_meetings_skips_the_stream(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/meetings"
        return httpx.Response(403, json={"code": "unauthorized", "message": "meeting:read"})

    with pytest.raises(StreamSkipped):
        await _fetch(parents_reader, "meetings", handle)


@pytest.mark.parametrize(
    ("stream", "path"), [("people", "/v2/objects/people/records/query"), ("tasks", "/v2/tasks")]
)
async def test_a_missing_scope_on_any_stream_skips_the_stream(
    stream: str, path: str, parents_reader: ParentsReader
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == path
        return httpx.Response(
            403, json={"code": "unauthorized", "message": "record_permission:read"}
        )

    with pytest.raises(StreamSkipped):
        await _fetch(parents_reader, stream, handle)


async def test_call_recordings_read_only_the_meetings_that_landed(
    parents_reader: ParentsReader,
) -> None:
    """The recordings hang under `/v2/meetings/{id}/call_recordings`, so their partitions are the
    meeting pages the `meetings` row landed: the meeting listing is walked by that row and never
    again here. Two requests for one meeting — the recordings under it and the one recording's
    transcript — and `/v2/meetings` is not among them."""
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/v2/meetings/mt1/call_recordings":
            return httpx.Response(
                200,
                json={
                    "data": [{"id": {"call_recording_id": "cr1"}, "web_url": "https://a/rec"}],
                    "pagination": {"next_cursor": None},
                },
            )
        if request.url.path == "/v2/meetings/mt1/call_recordings/cr1/transcript":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "transcript": [{"speaker": {"name": "Ada"}, "speech": "shipping today"}]
                    }
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(parents_reader, "call_recordings", handle)

    assert calls == [
        "/v2/meetings/mt1/call_recordings",
        "/v2/meetings/mt1/call_recordings/cr1/transcript",
    ]
    assert {page.source_identity for page in result.pages} == {"call_recordings/mt1/cr1"}
    assert "shipping today" in result.pages[0].body
    assert "https://a/rec" in result.pages[0].body
    assert '"parent_meeting_id": "mt1"' in result.pages[0].body


async def test_a_recording_is_addressed_under_the_meeting_that_holds_it(
    parents_reader: ParentsReader,
) -> None:
    """Attio keys a recording inside its meeting, so the same recording id under two meetings is
    two pages, not one."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/call_recordings"):
            return httpx.Response(
                200,
                json={
                    "data": [{"id": {"call_recording_id": "cr1"}}],
                    "pagination": {"next_cursor": None},
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    two = {
        "meetings": (
            ParentRecord(ref="meetings/mt1", fields={"id.meeting_id": "mt1"}),
            ParentRecord(ref="meetings/mt2", fields={"id.meeting_id": "mt2"}),
        )
    }
    result = await _fetch(parents_reader, "call_recordings", handle, landed=two)

    assert {page.source_identity for page in result.pages} == {
        "call_recordings/mt1/cr1",
        "call_recordings/mt2/cr1",
    }


async def test_a_meeting_carrying_no_meeting_id_raises_at_the_fan_out(
    parents_reader: ParentsReader,
) -> None:
    """A half-filled path would ask `/v2/meetings//call_recordings` and land nothing, which reads
    as a quiet stream rather than a broken one."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"path": request.url.path})

    landed = {"meetings": (ParentRecord(ref="meetings/mt1", fields={}),)}
    with pytest.raises(RuntimeError, match=r"id\.meeting_id"):
        await _fetch(parents_reader, "call_recordings", handle, landed=landed)


def test_the_recordings_re_key_restamps_nothing_that_ever_landed() -> None:
    """A recording is addressed under its meeting now, where it used to be addressed by its own id
    alone. Nothing restamps, because only a canonical stream and the ancestors it fans from
    register a source row: `call_recordings` is neither, and neither is `meetings`, so no
    connection has ever synced either and there is no page of either to re-key."""
    streams = {stream.name: stream for stream in AttioConnector().streams()}

    assert streams["call_recordings"].canonical is False
    assert streams["meetings"].canonical is False
    assert syncing_streams(list(streams.values())).isdisjoint({"call_recordings", "meetings"})


def test_the_meeting_listing_is_the_root_the_recordings_hang_under() -> None:
    streams = {stream.name: stream for stream in AttioConnector().streams()}

    assert streams["meetings"].parents == ()
    edge = streams["call_recordings"].parents[0]
    assert (edge.stream, edge.path) == ("meetings", "/v2/meetings/{id.meeting_id}/call_recordings")
    assert edge.carry == {"parent_meeting_id": "id.meeting_id"}
