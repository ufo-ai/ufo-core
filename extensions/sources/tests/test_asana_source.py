"""The Asana connector over a mock transport: the `{data, next_page.offset}` envelope followed to
the end, the `?modified_since` incremental filter threaded onto `tasks` (and not onto a
full-refresh stream), the `modified_at` watermark advancing, and the default titled-JSON render —
Asana is a structured provider, not a content one, so it takes no `render` override. No conftest:
the shared `ufo_testsupport` plugin covers fixtures, and these tests are offline (a canned
transport, no DB, no token, no broker)."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
from ufo_ext_sources.asana import AsanaConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, SyncResult

ACCOUNT = "acct-1"


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler))
    return await ConnectorBackend(connector=AsanaConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _workspaces_handler(
    page_by_offset: dict[str | None, dict[str, object]],
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "app.asana.com"
        assert request.url.path.endswith("/workspaces")
        assert request.url.params.get("limit") == "100"
        return httpx.Response(200, json=page_by_offset[request.url.params.get("offset")])

    return handle


async def test_workspaces_follow_offset_pagination_and_render_titled_json() -> None:
    paged: dict[str | None, dict[str, object]] = {
        None: {"data": [{"gid": "1", "name": "Acme HQ"}], "next_page": {"offset": "o2"}},
        "o2": {"data": [{"gid": "2", "name": "Beta"}], "next_page": None},
    }
    result = await _fetch("workspaces", _workspaces_handler(paged))

    assert {page.source_ref for page in result.pages} == {"workspaces/1", "workspaces/2"}
    assert result.snapshot is False
    assert result.deletes == ()

    # the default render titles from `name` and dumps the record's JSON beneath it
    body = next(page.body for page in result.pages if page.source_ref == "workspaces/1")
    assert "Acme HQ" in body


def _tasks_handler(seen: list[str | None]) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/tasks")
        seen.append(request.url.params.get("modified_since"))
        return httpx.Response(
            200,
            json={
                "data": [{"gid": "t1", "name": "Ship", "modified_at": "2026-02-05T00:00:00.000Z"}],
                "next_page": None,
            },
        )

    return handle


async def test_tasks_incremental_threads_modified_since_and_advances_the_watermark() -> None:
    seen: list[str | None] = []
    result = await _fetch("tasks", _tasks_handler(seen), cursor="2026-02-01T00:00:00.000Z")

    assert seen == ["2026-02-01T00:00:00.000Z"]
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-05T00:00:00.000Z"
    assert {page.source_ref for page in result.pages} == {"tasks/t1"}


async def test_full_refresh_stream_never_sends_the_modified_since_filter() -> None:
    """`users` is not a `modified_since` stream — even handed a stored cursor it sends no
    server-side filter and advances no watermark (it carries no `cursor_field`)."""
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("modified_since"))
        return httpx.Response(200, json={"data": [{"gid": "u1", "name": "Ada"}], "next_page": None})

    result = await _fetch("users", handle, cursor="2026-02-01T00:00:00.000Z")
    assert seen == [None]
    # no cursor_field to advance over — the watermark stays put rather than moving
    assert result.next_cursor == "2026-02-01T00:00:00.000Z"
    assert {page.source_ref for page in result.pages} == {"users/u1"}
