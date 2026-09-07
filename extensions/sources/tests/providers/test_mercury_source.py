"""The Mercury connector over a mock transport: the accounts read, the per-account transaction
fan-out with the day-granular `start` filter and `offset` paging, the counterparty title, the
`postedAt` watermark, and a refusal as `StreamSkipped`. Offline — a canned transport, no DB, no
token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.mercury import MercuryConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(bearer="mercury-key", transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=MercuryConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


def _accounts() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "accounts": [
                {"id": "acc-1", "name": "Operating"},
                {"id": "acc-2", "name": "Payroll"},
            ]
        },
    )


async def test_accounts_land_as_pages() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.mercury.com"
        assert request.url.path == "/api/v1/accounts"
        return _accounts()

    result = await _fetch("accounts", handle)
    assert {page.source_ref for page in result.pages} == {"accounts/acc-1", "accounts/acc-2"}
    assert [page.title for page in result.pages] == ["Operating", "Payroll"]


async def test_transactions_fan_out_over_accounts_and_carry_the_start_filter() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/accounts":
            return _accounts()
        seen.append(str(request.url))
        account_id = request.url.path.split("/")[4]
        return httpx.Response(
            200,
            json={
                "transactions": [
                    {
                        "id": f"tx-{account_id}",
                        "counterpartyName": "Stripe",
                        "createdAt": "2026-02-01T00:00:00Z",
                        "postedAt": "2026-02-02T00:00:00Z",
                    }
                ]
            },
        )

    result = await _fetch("transactions", handle, cursor="2026-01-30T12:00:00Z")

    assert {page.source_ref for page in result.pages} == {
        "transactions/tx-acc-1",
        "transactions/tx-acc-2",
    }
    assert {page.title for page in result.pages} == {"Stripe"}
    assert result.next_cursor == "2026-02-02T00:00:00Z"
    assert all("start=2026-01-30" in url for url in seen)
    assert all("limit=100" in url for url in seen)
    assert "/api/v1/account/acc-1/transactions" in seen[0]
    assert "/api/v1/account/acc-2/transactions" in seen[1]
    assert "acc-1" in result.pages[0].body


async def test_a_full_page_is_followed_by_the_next_offset() -> None:
    offsets: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/accounts":
            return httpx.Response(200, json={"accounts": [{"id": "acc-1"}]})
        offsets.append(request.url.params.get("offset"))
        if request.url.params.get("offset") == "100":
            return httpx.Response(200, json={"transactions": []})
        return httpx.Response(
            200,
            json={
                "transactions": [
                    {"id": f"tx-{index}", "postedAt": "2026-02-02T00:00:00Z"}
                    for index in range(100)
                ]
            },
        )

    result = await _fetch("transactions", handle)
    assert offsets == ["0", "100"]
    assert len(result.pages) == 100


async def test_a_refused_stream_is_skipped_rather_than_failed() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errors": {"message": "invalid token"}})

    with pytest.raises(StreamSkipped):
        await _fetch("transactions", handle)
