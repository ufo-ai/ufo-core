"""The Deel connector over a mock transport: contracts walked by the cursor each page names, tasks
read under each contract, the offset envelope walked until a short page, the `?updated_after`
incremental filter threaded onto a cursor stream (and not onto a full-refresh one), the `updated_at`
watermark advancing, and the default titled-JSON render.
Offline — a canned transport, no DB, no token, no broker."""

from collections.abc import Callable, Mapping
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.deel import PAGE_SIZE, DeelConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
)

ParentsReader = Callable[[Mapping[str, tuple[ParentRecord, ...]]], ParentPages]
CONTRACTS: Mapping[str, tuple[ParentRecord, ...]] = {
    "contracts": (
        ParentRecord(ref="contracts/c1", fields={"id": "c1"}),
        ParentRecord(ref="contracts/c2", fields={"id": "c2"}),
    )
}


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    parents: ParentPages,
    cursor: str | None = None,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler), parents=parents)
    return await ConnectorBackend(connector=DeelConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


async def test_contracts_walk_cursor_pages_and_advance_the_watermark(
    parents_reader: ParentsReader,
) -> None:
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

    result = await _fetch("contracts", handle, parents=parents_reader(CONTRACTS))
    assert cursors == [None, "cur1"]
    assert len(result.pages) == PAGE_SIZE + 1
    assert "contracts/c-last" in {page.source_ref for page in result.pages}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-09"
    last = next(page for page in result.pages if page.source_ref == "contracts/c-last")
    assert last.updated_at == "2026-02-09T00:00:00.000000+00:00"


def _tasks(request: httpx.Request) -> httpx.Response:
    """One task `t-1` under each of the two landed contracts. Deel's spec types a task `id` as a
    bare string, marks a uuid where it means one, and publishes no `GET /tasks/{id}`, so a task id
    is only ever unique inside its contract (`developer.deel.com/openapi/endpoints-5.json`)."""
    if request.url.path in ("/rest/contracts/c1/tasks", "/rest/contracts/c2/tasks"):
        return httpx.Response(200, json={"data": [{"id": "t-1", "updated_at": "2026-02-03"}]})
    return httpx.Response(404, json={"path": request.url.path})


async def test_one_task_key_under_two_contracts_is_two_pages(
    parents_reader: ParentsReader,
) -> None:
    result = await _fetch("tasks", _tasks, parents=parents_reader(CONTRACTS))

    assert {page.source_ref for page in result.pages} == {"tasks/c1/t-1", "tasks/c2/t-1"}


async def test_a_task_carries_the_contract_that_holds_it(parents_reader: ParentsReader) -> None:
    """The contract is the only thing tying a task to a worker and the task record does not name
    it, so the edge writes it onto every row before the body is rendered."""
    result = await _fetch("tasks", _tasks, parents=parents_reader(CONTRACTS))

    first = next(page for page in result.pages if page.source_ref == "tasks/c1/t-1")
    assert '"contract_id": "c1"' in first.body


async def test_a_task_pass_is_an_authoritative_snapshot(parents_reader: ParentsReader) -> None:
    """Every contract's tasks are re-read whole each run — the per-contract read pages not at all
    and takes no filter — so the pass is a full collection the driver tombstones against, and the
    flat rows an earlier declaration addressed are absent from it."""
    result = await _fetch("tasks", _tasks, parents=parents_reader(CONTRACTS))

    assert result.snapshot is True
    assert result.next_cursor is None
    assert "tasks/t-1" not in {page.source_ref for page in result.pages}


async def test_a_task_run_asks_for_no_contract_listing(
    parents_reader: ParentsReader,
) -> None:
    """The contracts a task hangs under are the `contracts` row's own landed pages, so a task run
    asks for no contract listing and no flat task collection."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return _tasks(request)

    await _fetch("tasks", handle, parents=parents_reader(CONTRACTS))

    assert asked == ["/rest/contracts/c1/tasks", "/rest/contracts/c2/tasks"]


async def test_cursor_streams_thread_updated_after_and_forms_never_do(
    parents_reader: ParentsReader,
) -> None:
    def contracts(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("updated_after") == "2026-02-01"
        return httpx.Response(200, json={"data": [{"id": "c1", "updated_at": "2026-02-02"}]})

    result = await _fetch(
        "contracts", contracts, parents=parents_reader(CONTRACTS), cursor="2026-02-01"
    )
    assert {page.source_ref for page in result.pages} == {"contracts/c1"}

    def forms(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/forms"
        assert request.url.params.get("updated_after") is None
        return httpx.Response(200, json={"data": [{"id": "f1", "name": "NDA"}]})

    result = await _fetch("forms", forms, parents=parents_reader(CONTRACTS), cursor="2026-02-01")
    assert {page.source_ref for page in result.pages} == {"forms/f1"}
    assert result.next_cursor == "2026-02-01"


async def test_a_refused_stream_is_skipped_rather_than_failed(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch("tasks", handle, parents=parents_reader(CONTRACTS))
