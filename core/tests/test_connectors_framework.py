"""The connector sync framework end to end: the shared REST pagination strategies, the adapter that
collapses a connector's stream pages into a core `SyncResult` (snapshot vs delta/deletes), the
GitHub provider's org/repo fan-out + RFC-5988 Link pagination + `?since` incremental, and Composio's
Tool Router semantic search. Every provider call rides Composio's proxy-execute, mocked with an
`httpx.MockTransport` — no live API, no token, no direct provider call — so the real connector,
strategy loops, proxy transport, and adapter run against canned responses. The strategy loops run
against a probe connector whose `_make_client` injects a `MockTransport`: a real consumer of the
framework, so the loops are exercised, never faked."""

import json
from collections.abc import AsyncIterator, Callable
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import selfhost_ext_connectors.composio as composio
import selfhost_ext_connectors.composio_proxy as composio_proxy
import selfhost_ext_connectors.mcp_session as mcp_session
from selfhost_ext_connectors.backend import ConnectorBackend, ConnectorSourceConfig
from selfhost_ext_connectors.connector import (
    Connector,
    Pagination,
    PaginationStrategy,
    StreamPage,
    StreamSpec,
)
from selfhost_ext_connectors.github import GitHubConnector
from selfhost_ext_connectors.rest import RestConnector

from selfhost.memory.sources import SourceAuth, StreamSkipped

GITHUB_ACCOUNT = "ca_gh_1"


# --- The shared pagination strategies -----------------------------------------------------------


class _ProbeConnector(RestConnector):
    """A real RestConnector whose client is a MockTransport, so the strategy loops run against
    canned HTTP with no network and no proxy."""

    name = "probe"
    base_url = "https://api.probe.test"

    def __init__(
        self, stream: StreamSpec, handler: Callable[[httpx.Request], httpx.Response]
    ) -> None:
        self._stream = stream
        self._handler = handler

    def streams(self) -> list[StreamSpec]:
        return [self._stream]

    def _make_client(self, base_url: str, access_token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base_url.rstrip("/"), transport=httpx.MockTransport(self._handler)
        )


async def _pages(
    connector: Connector, cursor: str | None = None
) -> list[list[dict[str, Any]] | StreamPage]:
    stream = connector.streams()[0]
    return [
        page
        async for page in connector.fetch_page(
            stream, cursor=cursor, access_token="tok", base_url=""
        )
    ]


def _ids(pages: list[list[dict[str, Any]] | StreamPage]) -> set[Any]:
    ids: set[Any] = set()
    for page in pages:
        records = page.records if isinstance(page, StreamPage) else page
        ids.update(record["id"] for record in records)
    return ids


async def test_next_link_strategy_follows_the_link_header() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=[{"id": 2}])
        link = '<https://api.probe.test/items?page=2>; rel="next"'
        return httpx.Response(200, json=[{"id": 1}], headers={"link": link})

    stream = StreamSpec(
        name="items",
        source_object="items",
        pagination=Pagination(strategy=PaginationStrategy.next_link, path="/items"),
    )
    assert _ids(await _pages(_ProbeConnector(stream, handle))) == {1, 2}


async def test_next_cursor_strategy_follows_the_body_token() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") == "c2":
            return httpx.Response(200, json={"data": [{"id": 2}], "next": None})
        return httpx.Response(200, json={"data": [{"id": 1}], "next": "c2"})

    stream = StreamSpec(
        name="things",
        source_object="things",
        pagination=Pagination(
            strategy=PaginationStrategy.next_cursor,
            path="/things",
            record_path="data",
            cursor_path="next",
            cursor_param="cursor",
        ),
    )
    assert _ids(await _pages(_ProbeConnector(stream, handle))) == {1, 2}


async def test_offset_limit_strategy_advances_until_short_page() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("offset") == "2":
            return httpx.Response(200, json={"data": [{"id": 3}]})
        return httpx.Response(200, json={"data": [{"id": 1}, {"id": 2}]})

    stream = StreamSpec(
        name="rows",
        source_object="rows",
        pagination=Pagination(
            strategy=PaginationStrategy.offset_limit,
            path="/rows",
            record_path="data",
            offset_param="offset",
            limit_param="limit",
            page_size=2,
        ),
    )
    assert _ids(await _pages(_ProbeConnector(stream, handle))) == {1, 2, 3}


async def test_page_number_strategy_advances_until_short_page() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json={"rows": [{"id": 3}]})
        return httpx.Response(200, json={"rows": [{"id": 1}, {"id": 2}]})

    stream = StreamSpec(
        name="list",
        source_object="list",
        pagination=Pagination(
            strategy=PaginationStrategy.page_number,
            path="/list",
            record_path="rows",
            cursor_param="page",
            page_size=2,
        ),
    )
    assert _ids(await _pages(_ProbeConnector(stream, handle))) == {1, 2, 3}


async def test_time_window_strategy_sends_the_cursor_value() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"id": 1, "after": request.url.params.get("after")}]}
        )

    stream = StreamSpec(
        name="win",
        source_object="win",
        pagination=Pagination(
            strategy=PaginationStrategy.time_window,
            path="/win",
            record_path="data",
            cursor_param="after",
        ),
    )
    [page] = await _pages(_ProbeConnector(stream, handle), cursor="2026-01-01")
    assert isinstance(page, list) and page[0]["after"] == "2026-01-01"


async def test_undeclared_pagination_raises() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    stream = StreamSpec(name="bare", source_object="bare")
    with pytest.raises(NotImplementedError):
        await _pages(_ProbeConnector(stream, handle))


# --- The adapter: connector pages → SyncResult ---------------------------------------------------


class _CannedConnector(RestConnector):
    """A connector whose `paginate` yields pre-canned pages, so the adapter's collapse — snapshot vs
    delta, watermark, deletes → source refs — is what's under test, not an HTTP loop."""

    name = "canned"
    base_url = "https://api.canned.test"

    def __init__(self, stream: StreamSpec, pages: list[list[dict[str, Any]] | StreamPage]) -> None:
        self._stream = stream
        self._pages = pages

    def streams(self) -> list[StreamSpec]:
        return [self._stream]

    def _make_client(self, base_url: str, access_token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base_url, transport=httpx.MockTransport(lambda r: httpx.Response(200))
        )

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        for page in self._pages:
            yield page


def _composio_owner(owner: str) -> Callable[[], composio.ComposioClient]:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/connected_accounts/" in request.url.path:
            return httpx.Response(
                200, json={"id": GITHUB_ACCOUNT, "user_id": owner, "status": "ACTIVE"}
            )
        return httpx.Response(404, json={})

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handle))
    return lambda: client


async def _fetch(connector: Connector, stream_name: str, cursor: str | None, workspace_id: UUID):
    return await ConnectorBackend(connector=connector).fetch(
        ConnectorSourceConfig(account=GITHUB_ACCOUNT, stream=stream_name),
        cursor,
        SourceAuth(workspace_id=workspace_id),
    )


async def test_full_collection_stream_returns_a_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_id = uuid4()
    monkeypatch.setattr(
        composio,
        "composio_client",
        _composio_owner(f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"),
    )
    stream = StreamSpec(name="repos", source_object="repos", delete_missing=True)
    connector = _CannedConnector(stream, [[{"id": "1", "name": "Widgets"}]])

    result = await _fetch(connector, "repos", None, workspace_id)

    assert result.snapshot is True
    assert result.next_cursor is None
    assert result.deletes == ()
    assert result.pages[0].source_ref == "repos/1"
    assert "Widgets" in result.pages[0].body


async def test_incremental_stream_advances_watermark_and_tombstones_deletes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    monkeypatch.setattr(
        composio,
        "composio_client",
        _composio_owner(f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"),
    )
    stream = StreamSpec(
        name="tickets", source_object="tickets", cursor_field="updated_at", delete_missing=False
    )
    page = StreamPage(records=[{"id": "5", "updated_at": "2026-02-02T00:00:00Z"}], deletes=("9",))
    connector = _CannedConnector(stream, [page])

    result = await _fetch(connector, "tickets", "2026-02-01T00:00:00Z", workspace_id)

    assert result.snapshot is False
    assert result.next_cursor == "2026-02-02T00:00:00Z"
    assert result.deletes == ("tickets/9",)
    assert {p.source_ref for p in result.pages} == {"tickets/5"}


async def test_stream_page_cursor_wins_over_watermark(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_id = uuid4()
    monkeypatch.setattr(
        composio,
        "composio_client",
        _composio_owner(f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"),
    )
    stream = StreamSpec(name="tickets", source_object="tickets", cursor_field="updated_at")
    page = StreamPage(
        records=[{"id": "5", "updated_at": "2026-02-02T00:00:00Z"}], next_cursor="opaque-token"
    )
    result = await _fetch(_CannedConnector(stream, [page]), "tickets", None, workspace_id)
    assert result.next_cursor == "opaque-token"


async def test_backend_refuses_a_foreign_account(monkeypatch: pytest.MonkeyPatch) -> None:
    foreign = f"{composio.EXTERNAL_USER_PREFIX}{uuid4()}"
    monkeypatch.setattr(composio, "composio_client", _composio_owner(foreign))
    stream = StreamSpec(name="repos", source_object="repos", delete_missing=True)
    with pytest.raises(composio.ComposioError, match="owned by"):
        await _fetch(_CannedConnector(stream, []), "repos", None, uuid4())


# --- GitHub: fan-out + Link pagination + ?since --------------------------------------------------


def _envelope(data: object, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        200, json={"data": {"data": data, "status": 200, "headers": headers or {}}}
    )


def _github_client(
    owner: str, seen: list[tuple[str, dict[str, str]]]
) -> Callable[[], composio.ComposioClient]:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and "/connected_accounts/" in path:
            return httpx.Response(
                200, json={"id": GITHUB_ACCOUNT, "user_id": owner, "status": "ACTIVE"}
            )
        if request.method != "POST" or not path.endswith(composio_proxy.PROXY_EXECUTE_PATH):
            return httpx.Response(404, json={})
        payload = json.loads(request.content)
        endpoint = payload["endpoint"]
        params = {
            p["name"]: p["value"] for p in payload.get("parameters", []) if p["type"] == "query"
        }
        seen.append((endpoint, params))
        if endpoint.endswith("/user/orgs"):
            return _envelope([{"login": "acme"}])
        if endpoint.endswith("/orgs/acme/repos"):
            return _envelope([{"full_name": "acme/widgets"}])
        if endpoint.endswith("/repos/acme/widgets/issues"):
            if params.get("page") == "2":
                return _envelope(
                    [{"id": 3, "title": "Later", "updated_at": "2026-01-05T00:00:00Z"}]
                )
            link = '<https://api.github.com/repos/acme/widgets/issues?page=2>; rel="next"'
            return _envelope(
                [
                    {"id": 1, "title": "Bug", "updated_at": "2026-01-02T00:00:00Z"},
                    {
                        "id": 2,
                        "title": "PR",
                        "pull_request": {"url": "x"},
                        "updated_at": "2026-01-03T00:00:00Z",
                    },
                ],
                {"link": link},
            )
        return httpx.Response(404, json={})

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handle))
    return lambda: client


async def test_github_issues_fan_out_link_pagination_and_pr_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    seen: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        composio,
        "composio_client",
        _github_client(f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}", seen),
    )

    result = await _fetch(GitHubConnector(), "issues", None, workspace_id)

    assert {p.source_ref for p in result.pages} == {"issues/1", "issues/3"}
    assert any("Bug" in p.body for p in result.pages)
    assert result.snapshot is False
    assert result.next_cursor == "2026-01-05T00:00:00Z"
    assert result.deletes == ()


async def test_github_issues_send_since_when_a_cursor_is_stored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    seen: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        composio,
        "composio_client",
        _github_client(f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}", seen),
    )

    await _fetch(GitHubConnector(), "issues", "2026-01-01T00:00:00Z", workspace_id)

    issue_calls = [params for endpoint, params in seen if endpoint.endswith("/issues")]
    assert issue_calls and issue_calls[0].get("since") == "2026-01-01T00:00:00Z"
    assert issue_calls[0].get("state") == "all"


def _github_scope_gated_client(owner: str) -> Callable[[], composio.ComposioClient]:
    """A grant whose org enumeration is refused: `/user/orgs` answers 403, the code every runnable
    stream fans out from."""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and "/connected_accounts/" in path:
            return httpx.Response(
                200, json={"id": GITHUB_ACCOUNT, "user_id": owner, "status": "ACTIVE"}
            )
        if request.method != "POST" or not path.endswith(composio_proxy.PROXY_EXECUTE_PATH):
            return httpx.Response(404, json={})
        if json.loads(request.content)["endpoint"].endswith("/user/orgs"):
            return httpx.Response(
                200,
                json={"data": {"data": {"message": "insufficient scope"}, "status": 403}},
            )
        return httpx.Response(404, json={})

    client = composio.ComposioClient(api_key="test", transport=httpx.MockTransport(handle))
    return lambda: client


async def test_github_scope_gated_org_enumeration_skips_the_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A grant that can't enumerate orgs (`/user/orgs` → 403) can read no stream: the connector
    raises `StreamSkipped`, so the driver records a skip rather than failing the run and sweeping
    the source's pages."""
    workspace_id = uuid4()
    monkeypatch.setattr(
        composio,
        "composio_client",
        _github_scope_gated_client(f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"),
    )
    with pytest.raises(StreamSkipped, match="org"):
        await _fetch(GitHubConnector(), "issues", None, workspace_id)


# --- Composio Tool Router semantic search --------------------------------------------------------


_ROUTER_RESULT = {
    "data": {
        "results": [
            {
                "primary_tool_slugs": ["GITHUB_CREATE_ISSUE_COMMENT"],
                "related_tool_slugs": ["GITHUB_GET_ISSUE"],
                "recommended_plan_steps": ["find the issue", "post the comment"],
                "execution_guidance": "resolve the repo first",
                "known_pitfalls": ["issue number is not the id"],
            }
        ],
        "tool_schemas": {
            "GITHUB_CREATE_ISSUE_COMMENT": {
                "description": "Comment on an issue",
                "input_schema": {"type": "object"},
            }
        },
    }
}


def test_connector_search_payload_projects_the_router_result() -> None:
    payload = composio._connector_search_payload(_ROUTER_RESULT, "github")
    assert payload["connector"] == "github"
    slugs = [tool["slug"] for tool in payload["tools"]]
    assert slugs == ["GITHUB_CREATE_ISSUE_COMMENT", "GITHUB_GET_ISSUE"]
    assert payload["tools"][0]["description"] == "Comment on an issue"
    assert payload["plan"] == ["find the issue", "post the comment"]
    assert payload["guidance"] == ["resolve the repo first"]
    assert payload["pitfalls"] == ["issue number is not the id"]


async def test_search_connector_tools_shares_one_session_under_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from types import SimpleNamespace
    from typing import cast

    monkeypatch.setattr(composio, "_SEARCH_SESSIONS", {})
    sessions = {"count": 0}

    async def fake_session(user_id: str, toolkits: list[str]) -> composio.ToolRouterSession:
        sessions["count"] += 1
        return composio.ToolRouterSession(id="s", url="https://router.test/mcp")

    async def fake_call(
        endpoint: str,
        tool: str,
        arguments: dict[str, Any],
        headers: dict[str, str],
        timeout_seconds: float,
    ) -> dict[str, object]:
        assert tool == composio.COMPOSIO_SEARCH_TOOL
        return _ROUTER_RESULT

    monkeypatch.setattr(mcp_session, "mcp_call_tool", fake_call)
    client = cast(
        composio.ComposioClient, SimpleNamespace(tool_router_session=fake_session, api_key="k")
    )
    workspace_id = uuid4()

    payloads = await asyncio.gather(
        composio.search_connector_tools(client, workspace_id, "github", "comment on a pr"),
        composio.search_connector_tools(client, workspace_id, "github", "comment on a pr"),
    )

    assert sessions["count"] == 1
    assert all(p["plan"] == ["find the issue", "post the comment"] for p in payloads)


class _StubToolResult:
    def __init__(self) -> None:
        from mcp.types import TextContent

        self.data = None
        self.structured_content = None
        self.content = [TextContent(type="text", text='{"data":{"results":[]}}')]


class _StubClient:
    def __init__(self, transport: object, timeout: float) -> None:
        self._transport = transport

    async def __aenter__(self) -> "_StubClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def call_tool(
        self, tool: str, arguments: dict[str, Any], **kwargs: Any
    ) -> _StubToolResult:
        return _StubToolResult()


async def test_mcp_call_tool_parses_json_text_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_session, "Client", _StubClient)
    result = await mcp_session.mcp_call_tool(
        "https://router.test/mcp", "COMPOSIO_SEARCH_TOOLS", {}, {"x-api-key": "k"}, 30.0
    )
    assert result == {"data": {"results": []}}
