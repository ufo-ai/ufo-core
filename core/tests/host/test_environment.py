import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ufo.host.devhost import overrides_app
from ufo.runtime import environment
from ufo.runtime.environment import (
    ENVIRONMENT_PATH,
    EnvironmentOverrides,
    EnvironmentRequest,
    EnvironmentTool,
    apply_environment_overrides,
    fetch_environment_overrides,
)
from ufo.runtime.prompts.render import rendered_prompt
from ufo.runtime.tools.context import ToolContext, ToolResult
from ufo.runtime.tools.registry import ToolDef, ToolRegistry


class _Input(BaseModel):
    pass


async def _never(context: ToolContext, payload: _Input) -> ToolResult:
    raise AssertionError("environment tests never dispatch")


def _registry() -> ToolRegistry:
    return ToolRegistry(
        (
            ToolDef(name="bash", description="run a command", input_model=_Input, handler=_never),
            ToolDef(name="web", description="fetch a page", input_model=_Input, handler=_never),
        )
    )


PROMPT = rendered_prompt("You are the default agent.")


def test_a_description_rewrite_touches_only_the_named_tool() -> None:
    prompt, tools = apply_environment_overrides(
        PROMPT, _registry(), EnvironmentOverrides(tool_descriptions={"bash": "patched"})
    )
    assert prompt == PROMPT
    assert tools.get("bash").description == "patched"
    assert tools.get("web").description == "fetch a page"
    assert tools.get("bash").handler is _never


def test_a_disabled_tool_leaves_the_offer() -> None:
    _prompt, tools = apply_environment_overrides(
        PROMPT, _registry(), EnvironmentOverrides(disabled_tools=("web",))
    )
    assert [tool.name for tool in tools.tools] == ["bash"]


def test_a_prompt_replacement_changes_content_and_digest() -> None:
    prompt, _tools = apply_environment_overrides(
        PROMPT, _registry(), EnvironmentOverrides(system_prompt="OVERRIDDEN")
    )
    assert prompt.content == "OVERRIDDEN"
    assert prompt.digest != PROMPT.digest


def test_a_name_the_offer_does_not_hold_fails_loud() -> None:
    with pytest.raises(ValueError, match="does not offer: spawn"):
        apply_environment_overrides(
            PROMPT, _registry(), EnvironmentOverrides(tool_descriptions={"spawn": "granted"})
        )
    with pytest.raises(ValueError, match="does not offer: spawn"):
        apply_environment_overrides(
            PROMPT, _registry(), EnvironmentOverrides(disabled_tools=("spawn",))
        )


def test_overrides_carry_no_field_that_could_add_a_tool() -> None:
    assert set(EnvironmentOverrides.model_fields) == {
        "system_prompt",
        "tool_descriptions",
        "disabled_tools",
    }


async def test_the_reference_host_serves_its_document() -> None:
    document = EnvironmentOverrides(
        system_prompt="OVERRIDDEN", tool_descriptions={"bash": "patched"}
    )
    transport = httpx.ASGITransport(app=overrides_app(document))
    request = EnvironmentRequest(
        agent_name="main",
        model="claude-opus-4-8",
        system_prompt=PROMPT.content,
        tools=(EnvironmentTool(name="bash", description="run a command"),),
    )
    json_content = {"content-type": "application/json"}
    async with httpx.AsyncClient(transport=transport, base_url="http://devhost") as client:
        answered = await client.post(
            ENVIRONMENT_PATH, content=request.model_dump_json(), headers=json_content
        )
        assert answered.status_code == 200
        assert EnvironmentOverrides.model_validate_json(answered.content) == document
        refused = await client.post(
            ENVIRONMENT_PATH, content=b'{"agent_name": 3}', headers=json_content
        )
        assert refused.status_code == 422


def _subagent_request() -> EnvironmentRequest:
    return EnvironmentRequest(
        agent_name="main",
        model="claude-opus-4-8",
        system_prompt=PROMPT.content,
        tools=(),
        subagent_profile="researcher",
    )


async def test_the_reference_host_answers_a_subagent_request_with_no_change() -> None:
    document = EnvironmentOverrides(system_prompt="OVERRIDDEN")
    transport = httpx.ASGITransport(app=overrides_app(document))
    async with httpx.AsyncClient(transport=transport, base_url="http://devhost") as client:
        answered = await client.post(
            ENVIRONMENT_PATH,
            content=_subagent_request().model_dump_json(),
            headers={"content-type": "application/json"},
        )
    assert answered.status_code == 200
    assert EnvironmentOverrides.model_validate_json(answered.content) == EnvironmentOverrides()


@asynccontextmanager
async def _served(app: object) -> AsyncIterator[str]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    while not server.started:
        if serving.done():
            serving.result()
            raise AssertionError("host exited before it started")
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await serving


async def test_fetch_returns_the_hosts_answer_over_a_real_socket() -> None:
    document = EnvironmentOverrides(tool_descriptions={"bash": "patched"})
    async with _served(overrides_app(document)) as host:
        request = EnvironmentRequest(
            agent_name="main",
            model="claude-opus-4-8",
            system_prompt=PROMPT.content,
            tools=(EnvironmentTool(name="bash", description="run a command"),),
        )
        assert await fetch_environment_overrides(host, request) == document


async def test_fetch_stops_reading_at_the_response_byte_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    oversized = FastAPI()

    @oversized.post(ENVIRONMENT_PATH)
    async def answer() -> PlainTextResponse:
        return PlainTextResponse("x" * 4096)

    monkeypatch.setattr(environment, "ENVIRONMENT_RESPONSE_BYTE_CAP", 64)
    async with _served(oversized) as host:
        with pytest.raises(ValueError, match="64-byte cap"):
            await fetch_environment_overrides(host, _subagent_request())
