"""The Mercury connector over a mock transport: the accounts read, the flat transactions walk with
its day-granular `postedStart` filter and `page.nextPage` cursor, the counterparty title, the
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


async def test_transactions_read_every_account_in_one_walk() -> None:
    """The flat collection lists every account's transactions, so the accounts read the per-account
    fan-out spent is gone and each transaction keeps the identity it already has."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "transactions": [
                    {
                        "id": "tx-1",
                        "accountId": "acc-1",
                        "counterpartyName": "Stripe",
                        "createdAt": "2026-02-01T00:00:00Z",
                        "postedAt": "2026-02-02T00:00:00Z",
                    },
                    {
                        "id": "tx-2",
                        "accountId": "acc-2",
                        "counterpartyName": "AWS",
                        "createdAt": "2026-02-03T00:00:00Z",
                        "postedAt": "2026-02-04T00:00:00Z",
                    },
                ],
                "page": {"nextPage": None},
            },
        )

    result = await _fetch("transactions", handle, cursor="2026-01-30T12:00:00Z")

    assert [page.source_identity for page in result.pages] == [
        "transactions/tx-1",
        "transactions/tx-2",
    ]
    assert {page.title for page in result.pages} == {"Stripe", "AWS"}
    assert result.next_cursor == "2026-02-04T00:00:00Z"
    assert len(seen) == 1
    assert "/api/v1/transactions" in seen[0]
    assert "postedStart=2026-01-30" in seen[0]
    assert "limit=100" in seen[0]


async def test_a_full_page_is_followed_by_the_next_page_cursor() -> None:
    """`page.nextPage` is the id to start the next page after, which is how the flat collection
    pages where the per-account one only reported a total."""
    starts: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        starts.append(request.url.params.get("start_after"))
        if request.url.params.get("start_after") == "tx-99":
            return httpx.Response(200, json={"transactions": [], "page": {"nextPage": None}})
        return httpx.Response(
            200,
            json={
                "transactions": [
                    {"id": f"tx-{index}", "postedAt": "2026-02-02T00:00:00Z"}
                    for index in range(100)
                ],
                "page": {"nextPage": "tx-99"},
            },
        )

    result = await _fetch("transactions", handle)
    assert starts == [None, "tx-99"]
    assert len(result.pages) == 100


async def test_a_refused_stream_is_skipped_rather_than_failed() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errors": {"message": "invalid token"}})

    with pytest.raises(StreamSkipped):
        await _fetch("transactions", handle)
