"""The Deel connector over a mock transport: contracts walked by the cursor each page names, tasks
read under each contract, the offset envelope walked until a short page, the `?updated_after`
incremental filter threaded onto a cursor stream (and not onto a full-refresh one), the `updated_at`
watermark advancing, and the default titled-JSON render.
Offline — a canned transport, no DB, no token, no broker."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.deel import PAGE_SIZE, DeelConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=DeelConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


async def test_contracts_walk_cursor_pages_and_advance_the_watermark() -> None:
    """Contracts page by cursor: each page names the one the next starts after. An `offset` is a
    parameter this endpoint does not read, so sending it answers with the first page every time —
    the walk would re-land the same records until the run's own cap stopped it, and every contract
    past the first page would stay invisible."""
    cursors: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.letsdeel.com"
        assert request.url.path == "/rest/contracts"
        assert request.url.params.get("offset") is None
        after = request.url.params.get("after_cursor")
        cursors.append(after)
        if after == "cur1":
            return httpx.Response(
                200,
                json={"data": [{"id": "c-last", "updated_at": "2026-02-09"}], "page": {}},
            )
        full = [{"id": f"c{n}", "updated_at": "2026-02-01"} for n in range(PAGE_SIZE)]
        return httpx.Response(200, json={"data": full, "page": {"cursor": "cur1"}})

    result = await _fetch("contracts", handle)
    assert cursors == [None, "cur1"]
    assert len(result.pages) == PAGE_SIZE + 1
    assert "contracts/c-last" in {page.source_ref for page in result.pages}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-09"
    last = next(page for page in result.pages if page.source_ref == "contracts/c-last")
    assert last.updated_at == "2026-02-09T00:00:00.000000+00:00"


async def test_tasks_are_read_under_each_contract() -> None:
    """Tasks answer under one contract at a time, never as a collection of their own, so the walk
    enumerates contracts and reads each one's. The row carries the contract it came from, which the
    task record does not name and which is the only thing tying it to a worker."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/rest/contracts":
            return httpx.Response(200, json={"data": [{"id": "c1"}], "page": {}})
        if request.url.path == "/rest/contracts/c1/tasks":
            return httpx.Response(200, json={"data": [{"id": "t1", "updated_at": "2026-02-03"}]})
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("tasks", handle)

    assert {page.source_ref for page in result.pages} == {"tasks/t1"}
    assert '"contract_id": "c1"' in result.pages[0].body
    assert "/rest/tasks" not in asked


async def test_a_stored_task_cursor_never_narrows_the_contract_enumeration() -> None:
    """A task changing does not touch its contract, so filtering the contract list by a task
    watermark stops asking the contracts that did not change and loses their tasks for good — the
    per-contract read carries no filter to make up for it and the watermark advances regardless.
    The enumeration goes out unfiltered however far the stream has already synced."""
    contract_queries: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/rest/contracts":
            contract_queries.append(request.url.params.get("updated_after"))
            return httpx.Response(200, json={"data": [{"id": "c1"}], "page": {}})
        if request.url.path == "/rest/contracts/c1/tasks":
            return httpx.Response(200, json={"data": [{"id": "t-old"}]})
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("tasks", handle, cursor="2026-02-08")

    assert contract_queries == [None]
    assert {page.source_ref for page in result.pages} == {"tasks/t-old"}


async def test_cursor_streams_thread_updated_after_and_forms_never_do() -> None:
    def contracts(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("updated_after") == "2026-02-01"
        return httpx.Response(200, json={"data": [{"id": "c1", "updated_at": "2026-02-02"}]})

    result = await _fetch("contracts", contracts, cursor="2026-02-01")
    assert {page.source_ref for page in result.pages} == {"contracts/c1"}

    def forms(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/forms"
        assert request.url.params.get("updated_after") is None
        return httpx.Response(200, json={"data": [{"id": "f1", "name": "NDA"}]})

    result = await _fetch("forms", forms, cursor="2026-02-01")
    assert {page.source_ref for page in result.pages} == {"forms/f1"}
    assert result.next_cursor == "2026-02-01"


async def test_a_refused_stream_is_skipped_rather_than_failed() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch("tasks", handle)
