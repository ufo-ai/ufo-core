"""The connector action tool: one HTTP-through-proxy request tool per registered provider.

A tool never holds the provider's token. It asks the turn for the per-account sentinel
`Authorization` value and sends it to the provider's own host through the sandbox's egress proxy —
which admits only a granted host (an ungranted one is refused at CONNECT) and swaps the sentinel for
the turn-agent's real token on the wire. `account_id` targets a specific account when the agent
holds several for one provider; omitted, the turn resolves any of its grants for the provider."""

import shlex
from typing import Literal

from pydantic import BaseModel

from selfhost.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from selfhost_ext_connectors.composio import ConnectorSpec


class ConnectorRequestInput(BaseModel):
    path: str = ""
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "GET"
    account_id: str | None = None
    body: str | None = None


def connector_request_tool(provider: str, spec: ConnectorSpec) -> ToolDef:
    async def handler(ctx: ToolContext, args: ConnectorRequestInput) -> ToolResult:
        authorization = await ctx.connector_authorization(provider, args.account_id)
        header = shlex.quote(f"Authorization: {authorization}")
        url = shlex.quote(f"https://{spec.host}/{args.path.lstrip('/')}")
        command = f"curl -sS -X {args.method} -o /dev/null -w '%{{http_code}}' -H {header} {url}"
        if args.body is not None:
            command += f" --data {shlex.quote(args.body)}"
        result = await ctx.sandbox.bash(command)
        return ToolResult(
            content=(TextContent(text=result.stdout),), is_error=result.exit_code != 0
        )

    return ToolDef(
        name=f"{provider}_request",
        description=f"Call the {spec.label} API for a granted account through the egress proxy.",
        input_model=ConnectorRequestInput,
        handler=handler,
    )
