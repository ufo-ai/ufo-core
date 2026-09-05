"""The Attio connector over a mock transport: the records-query offset loop, the `flatten` that
lifts a nested composite id and reduces each `values` value-cell to its primitive (the point of this
provider — without it a record has no top-level primary key), its record timestamps reaching the
page projection, the full-snapshot `delete_missing` semantics, and the two refuse-paths
(`standard_object_disabled` on objects, `403 unauthorized` anywhere) mapping to `StreamSkipped`.
Offline — a canned transport, no DB, no token, no broker."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.attio import AttioConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

ACCOUNT = "acct-1"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=AttioConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


async def test_people_flatten_lifts_the_composite_id_and_value_cells() -> None:
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

    result = await _fetch("people", handle)
    # the record_id is nested under `id`; flatten lifts it to the top-level primary key
    assert {page.source_ref for page in result.pages} == {"people/r1"}
    # a full records-query run is an authoritative snapshot
    assert result.snapshot is True
    assert result.next_cursor is None
    assert result.pages[0].created_at == "2026-01-15T10:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-01-20T11:00:00.000000+00:00"

    body = result.pages[0].body
    assert "Ada Lovelace" in body
    assert "ada@example.com" in body


async def test_tasks_lift_the_task_id_and_snapshot() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/tasks"
        return httpx.Response(
            200, json={"data": [{"id": {"task_id": "tk1"}, "content_plaintext": "Follow up"}]}
        )

    result = await _fetch("tasks", handle)
    assert {page.source_ref for page in result.pages} == {"tasks/tk1"}
    assert result.snapshot is True
    assert "Follow up" in result.pages[0].body


async def test_a_disabled_standard_object_skips_the_stream() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": "standard_object_disabled"})

    with pytest.raises(StreamSkipped):
        await _fetch("deals", handle)


async def test_a_missing_scope_on_meetings_skips_the_stream() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/meetings"
        return httpx.Response(403, json={"code": "unauthorized", "message": "meeting:read"})

    with pytest.raises(StreamSkipped):
        await _fetch("meetings", handle)


@pytest.mark.parametrize(
    ("stream", "path"), [("people", "/v2/objects/people/records/query"), ("tasks", "/v2/tasks")]
)
async def test_a_missing_scope_on_any_stream_skips_the_stream(stream: str, path: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == path
        return httpx.Response(
            403, json={"code": "unauthorized", "message": "record_permission:read"}
        )

    with pytest.raises(StreamSkipped):
        await _fetch(stream, handle)
