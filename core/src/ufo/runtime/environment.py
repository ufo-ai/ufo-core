"""The environment override rail: a turn that pins an environment host has its assembled
environment finalized by that host before the engine runs.

The runtime assembles the default environment exactly as it always has — agent prompt, manifest
sections, skill index, and the authorized tool offer — then sends that assembly to the pinned host
and applies what comes back: a replacement system prompt, rewritten descriptions for offered
tools, and removals from the offer. A locally running host can therefore drive prompt and
tool-description experiments against a shared stack, one overrides document per arm, without a
stack per variant.

Overrides narrow; they cannot grant. A rewrite touches only the text the model reads, a removal
only shrinks the offer, and a name the offer does not hold fails the turn loud — so the handler,
capability context, and authorization of every surviving tool are the ones the runtime bound, and
no override can reach a tool, credential, or scope the member could not already use. The fetch
also fails loud: an eval arm whose overrides did not apply must never measure as the default."""

from dataclasses import replace

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ufo.runtime.prompts.render import RenderedPrompt, rendered_prompt
from ufo.runtime.tools.registry import ToolRegistry

ENVIRONMENT_PATH = "/environment"
ENVIRONMENT_TIMEOUT_SECONDS = 30.0
ENVIRONMENT_RESPONSE_BYTE_CAP = 2_000_000


class EnvironmentTool(BaseModel):
    """One offered tool as the host sees it: the wire name and description the model would read,
    and the extension that contributed it (None for a builtin), so a host can address one
    extension's whole contribution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    extension: str | None = None


class EnvironmentRequest(BaseModel):
    """The default assembly, sent to the pinned host to finalize."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_name: str
    model: str
    system_prompt: str
    tools: tuple[EnvironmentTool, ...]
    subagent_profile: str | None = None


class EnvironmentOverrides(BaseModel):
    """What a host may change: the prompt text, the description of an offered tool, and which
    offered tools to withhold. Every field defaults to no change."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    system_prompt: str | None = None
    tool_descriptions: dict[str, str] = Field(default_factory=dict)
    disabled_tools: tuple[str, ...] = ()


async def fetch_environment_overrides(
    host: str, request: EnvironmentRequest
) -> EnvironmentOverrides:
    async with (
        httpx.AsyncClient(timeout=ENVIRONMENT_TIMEOUT_SECONDS) as client,
        client.stream(
            "POST",
            host.rstrip("/") + ENVIRONMENT_PATH,
            content=request.model_dump_json(),
            headers={"content-type": "application/json"},
        ) as response,
    ):
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > ENVIRONMENT_RESPONSE_BYTE_CAP:
                raise ValueError(
                    f"environment host answered more than the "
                    f"{ENVIRONMENT_RESPONSE_BYTE_CAP}-byte cap"
                )
    return EnvironmentOverrides.model_validate_json(bytes(body))


def apply_environment_overrides(
    prompt: RenderedPrompt, tools: ToolRegistry, overrides: EnvironmentOverrides
) -> tuple[RenderedPrompt, ToolRegistry]:
    offered = {tool.name for tool in tools.tools}
    unknown = sorted((set(overrides.tool_descriptions) | set(overrides.disabled_tools)) - offered)
    if unknown:
        raise ValueError(
            f"environment overrides name tools the turn does not offer: {', '.join(unknown)}"
        )
    disabled = frozenset(overrides.disabled_tools)
    survivors = tuple(
        replace(tool, description=overrides.tool_descriptions.get(tool.name, tool.description))
        for tool in tools.tools
        if tool.name not in disabled
    )
    content = prompt.content if overrides.system_prompt is None else overrides.system_prompt
    return rendered_prompt(content), ToolRegistry(survivors)
