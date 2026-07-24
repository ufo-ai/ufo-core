"""The Zendesk connector over a mock transport: the incremental cursor export (with the sideloaded
`users` lifting `requester_email` onto each ticket), the `next_page`-linked default list, the
`ticket_events` feed transformed into comment rows stamped with `ticket_id`, and a refusal surfacing
as `StreamSkipped`. The class base URL is empty (per-subdomain), so the tenant host is bound through
`ConnectorSourceConfig.base_url` — the real per-tenant path. Offline — a canned transport, no
token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.zendesk import ZendeskConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"
BASE_URL = "https://acme.zendesk.com"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=ZendeskConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream, base_url=BASE_URL), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_tickets_incremental_cursor_with_sideload_email() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "acme.zendesk.com"
        assert request.url.path == "/api/v2/incremental/tickets/cursor.json"
        assert request.url.params.get("include") == "users"
        return httpx.Response(
            200,
            json={
                "tickets": [{"id": 1, "requester_id": 5, "updated_at": "2026-02-01T00:00:00Z"}],
                "users": [{"id": 5, "email": "ada@example.com"}],
                "end_of_stream": True,
            },
        )

    result = await _fetch("tickets", handle)
    assert _refs(result) == {"tickets/1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-01T00:00:00Z"
    body = result.pages[0].body
    assert "ada@example.com" in body


async def test_groups_default_next_page_walk() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/groups.json"
        return httpx.Response(
            200, json={"groups": [{"id": 2, "updated_at": "2026-02-01T00:00:00Z"}]}
        )

    result = await _fetch("groups", handle)
    assert _refs(result) == {"groups/2"}
    assert result.next_cursor == "2026-02-01T00:00:00Z"


async def test_ticket_comments_lift_comment_child_events() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/incremental/ticket_events.json"
        return httpx.Response(
            200,
            json={
                "ticket_events": [
                    {
                        "ticket_id": 9,
                        "timestamp": 1700000000,
                        "child_events": [
                            {"id": "c1", "event_type": "Comment", "body": "hello"},
                            {"id": "x2", "event_type": "Create"},
                        ],
                    }
                ],
                "end_of_stream": True,
            },
        )

    result = await _fetch("ticket_comments", handle)
    assert _refs(result) == {"ticket_comments/c1"}
    body = result.pages[0].body
    assert '"ticket_id": 9' in body or "'ticket_id': 9" in body
    assert result.pages[0].created_at == "2023-11-14T22:13:20.000000+00:00"


async def test_ticket_comments_preserve_a_string_event_timestamp() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/incremental/ticket_events.json"
        return httpx.Response(
            200,
            json={
                "ticket_events": [
                    {
                        "ticket_id": 9,
                        "timestamp": "2026-02-01T00:00:00Z",
                        "child_events": [
                            {"id": "c1", "event_type": "Comment", "body": "hello"},
                        ],
                    }
                ],
                "end_of_stream": True,
            },
        )

    result = await _fetch("ticket_comments", handle)
    assert result.pages[0].created_at == "2026-02-01T00:00:00.000000+00:00"


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch("tickets", handle)
