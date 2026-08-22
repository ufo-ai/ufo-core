"""The Brex connector over a mock transport: the `{items, next_cursor}` envelope followed to the
end, the `posted_at_date` watermark advancing across pages, and the default titled-JSON render (Brex
is a structured provider, not a content one). Offline — a canned transport, no DB, no token, no
broker."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.brex import BrexConnector

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


async def test_a_forbidden_response_fails_the_run_rather_than_skipping_it() -> None:
    """Brex declares no refuse-path: a 403 is a genuine fault the driver backs off on, not a
    `StreamSkipped`."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "forbidden"})

    with pytest.raises(httpx.HTTPStatusError):
        await _fetch("users", handle)
