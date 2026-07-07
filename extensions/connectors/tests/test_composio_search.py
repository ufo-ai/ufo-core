"""Composio's Tool Router semantic search: projecting a router result into the connector-scoped
shape the agent reads, sharing one router session across concurrent searches, and parsing the MCP
tool result. The Tool Router HTTP is stubbed — no live API — so the projection and session-sharing
logic run against canned responses."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
import ufo_ext_connectors.composio as composio
import ufo_ext_connectors.mcp_session as mcp_session

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
