"""The Ramp connector over a mock transport: the `{data, page.next}` envelope followed by absolute
next link, the `from_date` filter and `user_transaction_time` watermark on transactions, the
merchant title, and a refusal as `StreamSkipped`. Offline — a canned transport, no DB, no token, no
broker."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.ramp import RampConnector

from ufo.access.connectors import Credential
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
    return await ConnectorBackend(connector=RampConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_transactions_follow_the_absolute_next_link_and_advance_the_watermark() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.ramp.com"
        seen.append(str(request.url))
        if request.url.params.get("start") == "tx1":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "tx2",
                            "merchant_name": "Delta",
                            "user_transaction_time": "2026-02-05T10:00:00Z",
                        }
                    ],
                    "page": {},
                },
            )
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "tx1",
                        "merchant_name": "AWS",
                        "user_transaction_time": "2026-02-01T10:00:00Z",
                    }
                ],
                "page": {"next": "https://api.ramp.com/developer/v1/transactions?start=tx1"},
            },
        )

    result = await _fetch("transactions", handle, cursor="2026-01-01T00:00:00Z")

    assert _refs(result) == {"transactions/tx1", "transactions/tx2"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-05T10:00:00Z"
    assert "from_date=2026-01-01T00%3A00%3A00Z" in seen[0]
    assert "order_by_date_asc=true" in seen[0]
    assert "page_size=100" in seen[0]
    # the second request is the provider's own next link, so it carries no rebuilt query
    assert seen[1].endswith("/developer/v1/transactions?start=tx1")


async def test_a_transaction_is_titled_by_its_merchant() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "tx1",
                        "merchant_name": "Notion Labs",
                        "user_transaction_time": "2026-02-01T10:00:00Z",
                    }
                ],
                "page": {"next": None},
            },
        )

    result = await _fetch("transactions", handle)
    assert [page.title for page in result.pages] == ["Notion Labs"]
    assert result.pages[0].created_at == "2026-02-01T10:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_users_take_no_time_filter_and_advance_no_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/developer/v1/users"
        assert "from_date" not in request.url.params
        return httpx.Response(
            200, json={"data": [{"id": "u1", "first_name": "Ada"}], "page": {"next": None}}
        )

    result = await _fetch("users", handle, cursor="prior")
    assert _refs(result) == {"users/u1"}
    assert result.next_cursor == "prior"


async def test_a_refused_stream_is_skipped_rather_than_failed() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "insufficient scope"})

    with pytest.raises(StreamSkipped):
        await _fetch("bills", handle)
