"""The Apollo connector over a mock transport: the POST search body, page paging bounded by
`pagination.total_pages`, the newest-first stop at the run's watermark, the `X-Api-Key` scheme for
a member-added key, and a refusal as `StreamSkipped`. Offline — a canned transport, no DB, no
token."""

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.apollo import ApolloConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

KEY = "apollo-key"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=ApolloConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


def _contact(name: str, created_at: str) -> dict[str, Any]:
    return {"id": name, "name": name, "created_at": created_at, "updated_at": created_at}


async def test_contacts_search_newest_first_over_pages() -> None:
    bodies: list[bytes] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/contacts/search"
        bodies.append(request.content)
        page = 2 if b'"page":2' in request.content else 1
        return httpx.Response(
            200,
            json={
                "contacts": [_contact(f"c{page}", f"2026-02-0{page}T00:00:00Z")],
                "pagination": {"page": page, "total_pages": 2},
            },
        )

    result = await _fetch("contacts", handle)

    assert {page.source_ref for page in result.pages} == {"contacts/c1", "contacts/c2"}
    assert result.next_cursor == "2026-02-02T00:00:00Z"
    assert b'"sort_by_field":"contact_created_at"' in bodies[0]
    assert b'"sort_ascending":false' in bodies[0]
    assert b'"per_page":100' in bodies[0]
    assert len(bodies) == 2


async def test_the_walk_stops_at_the_first_page_holding_a_record_below_the_watermark() -> None:
    pages: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        page = 2 if b'"page":2' in request.content else 1
        pages.append(page)
        return httpx.Response(
            200,
            json={
                "contacts": [
                    _contact("new", "2026-02-05T00:00:00Z"),
                    _contact("old", "2026-01-01T00:00:00Z"),
                ],
                "pagination": {"page": page, "total_pages": 5},
            },
        )

    result = await _fetch("contacts", handle, cursor="2026-02-01T00:00:00Z")

    assert pages == [1]
    assert {page.source_ref for page in result.pages} == {"contacts/new"}
    assert result.next_cursor == "2026-02-05T00:00:00Z"


async def test_accounts_read_their_own_search_and_sort_field() -> None:
    bodies: list[bytes] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/accounts/search"
        bodies.append(request.content)
        return httpx.Response(
            200,
            json={
                "accounts": [{"id": "a1", "name": "Acme", "created_at": "2026-02-01T00:00:00Z"}],
                "pagination": {"page": 1, "total_pages": 1},
            },
        )

    result = await _fetch("accounts", handle)
    assert {page.source_ref for page in result.pages} == {"accounts/a1"}
    assert b'"sort_by_field":"account_created_at"' in bodies[0]


async def test_a_member_added_key_rides_the_x_api_key_header() -> None:
    client = ApolloConnector()._make_client("https://api.apollo.io", Credential(bearer=KEY))
    try:
        assert client.headers["X-Api-Key"] == KEY
        assert "Authorization" not in client.headers
    finally:
        await client.aclose()


async def test_a_refused_stream_is_skipped_rather_than_failed() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "master key required"})

    with pytest.raises(StreamSkipped):
        await _fetch("contacts", handle)
