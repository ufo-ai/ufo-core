"""The Deel connector over a mock transport: the `{data, total}` offset envelope walked until a
short page, the `?updated_after` incremental filter threaded onto a cursor stream (and not onto a
full-refresh one), the `updated_at` watermark advancing, and the default titled-JSON render.
Offline — a canned transport, no DB, no token, no broker."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.deel import PAGE_SIZE, DeelConnector

from ufo.access.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, SyncResult

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
    return await ConnectorBackend(connector=DeelConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


async def test_contracts_walk_offset_pages_and_advance_the_watermark() -> None:
    seen_offsets: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.letsdeel.com"
        assert request.url.path == "/rest/v2/contracts"
        offset = request.url.params.get("offset")
        seen_offsets.append(offset)
        if offset == str(PAGE_SIZE):
            return httpx.Response(
                200, json={"data": [{"id": "c-last", "updated_at": "2026-02-09"}], "total": 101}
            )
        full = [{"id": f"c{n}", "updated_at": "2026-02-01"} for n in range(PAGE_SIZE)]
        return httpx.Response(200, json={"data": full, "total": 101})

    result = await _fetch("contracts", handle)
    assert seen_offsets == ["0", str(PAGE_SIZE)]
    assert len(result.pages) == PAGE_SIZE + 1
    assert "contracts/c-last" in {page.source_ref for page in result.pages}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-09"
    last = next(page for page in result.pages if page.source_ref == "contracts/c-last")
    assert last.updated_at == "2026-02-09T00:00:00.000000+00:00"


async def test_cursor_streams_thread_updated_after_and_forms_never_do() -> None:
    def contracts(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("updated_after") == "2026-02-01"
        return httpx.Response(200, json={"data": [{"id": "c1", "updated_at": "2026-02-02"}]})

    result = await _fetch("contracts", contracts, cursor="2026-02-01")
    assert {page.source_ref for page in result.pages} == {"contracts/c1"}

    def forms(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/rest/v2/forms"
        assert request.url.params.get("updated_after") is None
        return httpx.Response(200, json={"data": [{"id": "f1", "name": "NDA"}]})

    # `forms` carries no cursor_field — a stored cursor sends no filter and advances no watermark
    result = await _fetch("forms", forms, cursor="2026-02-01")
    assert {page.source_ref for page in result.pages} == {"forms/f1"}
    assert result.next_cursor == "2026-02-01"


async def test_a_forbidden_response_fails_the_run() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    with pytest.raises(httpx.HTTPStatusError):
        await _fetch("tasks", handle)
