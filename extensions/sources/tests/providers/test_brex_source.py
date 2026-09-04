"""The Brex connector over a mock transport: the `{items, next_cursor}` envelope followed to the
end, the `posted_at_date` watermark advancing across pages, a refusal as `StreamSkipped`, and the
default titled-JSON render (Brex is a structured provider, not a content one). Offline — a canned
transport, no DB, no token, no broker."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.brex import BrexConnector

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
    return await ConnectorBackend(connector=BrexConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _transactions_handler(
    seen_limits: list[str | None],
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "platform.brexapis.com"
        assert request.url.path == "/v2/transactions/card/primary"
        seen_limits.append(request.url.params.get("limit"))
        if request.url.params.get("cursor") == "c2":
            return httpx.Response(
                200,
                json={
                    "items": [{"id": "tx2", "posted_at_date": "2026-01-05"}],
                    "next_cursor": None,
                },
            )
        return httpx.Response(
            200,
            json={"items": [{"id": "tx1", "posted_at_date": "2026-01-01"}], "next_cursor": "c2"},
        )

    return handle


async def test_transactions_follow_cursor_pagination_and_advance_watermark() -> None:
    seen_limits: list[str | None] = []
    result = await _fetch("transactions", _transactions_handler(seen_limits))

    assert {page.source_ref for page in result.pages} == {"transactions/tx1", "transactions/tx2"}
    assert result.snapshot is False
    assert result.deletes == ()
    assert result.next_cursor == "2026-01-05"
    assert seen_limits == ["100", "100"]
    assert {page.created_at for page in result.pages} == {
        "2026-01-01T00:00:00.000000+00:00",
        "2026-01-05T00:00:00.000000+00:00",
    }
    assert {page.updated_at for page in result.pages} == {None}

    body = next(page.body for page in result.pages if page.source_ref == "transactions/tx2")
    assert "tx2" in body


async def test_budgets_use_the_budget_id_primary_key_and_advance_no_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/budgets"
        return httpx.Response(
            200, json={"items": [{"budget_id": "b1", "name": "Ops"}], "next_cursor": None}
        )

    result = await _fetch("budgets", handle, cursor="prior")
    assert {page.source_ref for page in result.pages} == {"budgets/b1"}
    # no cursor_field to advance over — the watermark stays where it was
    assert result.next_cursor == "prior"


async def test_transfers_read_the_payments_list_endpoint() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/transfers"
        return httpx.Response(
            200,
            json={
                "items": [{"id": "tr1", "description": "Payroll", "status": "PROCESSED"}],
                "next_cursor": None,
            },
        )

    result = await _fetch("transfers", handle)
    assert {page.source_ref for page in result.pages} == {"transfers/tr1"}


async def test_a_refused_stream_is_skipped_rather_than_failed() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/expenses/card"
        return httpx.Response(403, json={"message": "forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch("expenses", handle)
