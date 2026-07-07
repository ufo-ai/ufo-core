"""The Attio connector over a mock transport: the records-query offset loop, the `flatten` that
lifts a nested composite id and reduces each `values` value-cell to its primitive (the point of this
provider — without it a record has no top-level primary key), the full-snapshot `delete_missing`
semantics, and the two refuse-paths (`standard_object_disabled` on objects, `403 unauthorized` on
meetings) mapping to `StreamSkipped`. Offline — a canned transport, no DB, no token, no broker."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.attio import AttioConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

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
