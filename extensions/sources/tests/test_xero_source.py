"""The Xero connector over a mock transport: the resource-plural envelope, page-number pagination,
the typed `*ID` lifted to `id` by `flatten`, the single-shot non-paged collections, the
`If-Modified-Since` incremental header, and a refusal as `StreamSkipped`. Offline — a canned
transport, no DB, no token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.xero import XeroConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=XeroConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_accounts_lift_typed_id_and_advance_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/Accounts")
        assert request.url.params.get("page") == "1"
        return httpx.Response(
            200,
            json={
                "Accounts": [
                    {"AccountID": "A1", "Name": "Cash", "UpdatedDateUTC": "2026-02-01T00:00:00Z"}
                ]
            },
        )

    result = await _fetch("accounts", handle)
    assert _refs(result) == {"accounts/A1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-01T00:00:00Z"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"


async def test_accounts_incremental_sends_if_modified_since() -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("If-Modified-Since"))
        return httpx.Response(200, json={"Accounts": [{"AccountID": "A1"}]})

    await _fetch("accounts", handle, cursor="2026-01-01T00:00:00Z")
    assert seen and seen[0] is not None and "GMT" in seen[0]


async def test_currencies_single_shot_no_page_param() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/Currencies")
        assert "page" not in request.url.params
        return httpx.Response(200, json={"Currencies": [{"Code": "USD"}]})

    result = await _fetch("currencies", handle)
    assert _refs(result) == {"currencies/USD"}


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"Message": "forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch("accounts", handle)
