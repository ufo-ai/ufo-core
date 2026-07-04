"""The Calendly connector over a mock transport: the org-scoped fan-out (read `/users/me` for
`current_organization`, then filter every collection by it), the `collection` /
`pagination.next_page_token` envelope, the incremental `updated_since` filter, and the
`StreamSkipped` raised when the account exposes no organization. Offline — a canned transport, no
DB, no token, no broker."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from selfhost_ext_sources.calendly import CalendlyConnector

from selfhost.connectors import Credential
from selfhost.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from selfhost.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"
ORG = "https://api.calendly.com/organizations/ORG1"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=CalendlyConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _me() -> dict[str, object]:
    return {"resource": {"uri": "u1", "name": "Ada", "current_organization": ORG}}


async def test_api_user_reads_the_current_user() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.calendly.com"
        assert request.url.path == "/users/me"
        return httpx.Response(200, json=_me())

    result = await _fetch("api_user", handle)
    assert {page.source_ref for page in result.pages} == {"api_user/u1"}
    assert "Ada" in result.pages[0].body


async def test_event_types_scope_to_org_thread_updated_since_and_advance_watermark() -> None:
    seen_updated_since: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/users/me":
            return httpx.Response(200, json=_me())
        assert request.url.path == "/event_types"
        assert request.url.params.get("organization") == ORG
        seen_updated_since.append(request.url.params.get("updated_since"))
        return httpx.Response(
            200,
            json={
                "collection": [{"uri": "et1", "name": "Intro", "updated_at": "2026-02-05"}],
                "pagination": {"next_page_token": None},
            },
        )

    result = await _fetch("event_types", handle, cursor="2026-02-01")
    assert seen_updated_since == ["2026-02-01"]
    assert {page.source_ref for page in result.pages} == {"event_types/et1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-05"


async def test_missing_organization_skips_the_stream() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/users/me"
        return httpx.Response(200, json={"resource": {"uri": "u1", "name": "Ada"}})

    with pytest.raises(StreamSkipped):
        await _fetch("scheduled_events", handle)
