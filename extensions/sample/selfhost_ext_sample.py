"""The conformance sample: a real installed extension that exercises the whole public seam.

It imports only `selfhost.sdk` — the surface a CI gate pins — and its entry point returns a Manifest
declaring exactly the landed points: one tool, one job, one route, and one credential slot carrying
a wire-injection target. Each handler records the call it received through its own
`ExtensionContext.store` (durable `ext_store` rows, never a mock log), so the conformance tests read
those rows back through the same public surfaces core writes them by. `UNDECLARED_SLOT` names a slot
the Manifest never declares — the probe that a handler asking for an undeclared slot is refused."""

from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from selfhost.sdk.context import ExtensionContext
from selfhost.sdk.jobs import JobSpec
from selfhost.sdk.manifest import CredentialSlot, InjectionTarget, Manifest, RouteSpec
from selfhost.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

NAME = "sample"
VERSION = "0.1.0"
TOOL_NAME = "sample_echo"
JOB_NAME = "sample_tick"
ROUTE_PATH = "hook"
API_SLOT = "sample_api"
UNDECLARED_SLOT = "sample_unset"
INJECTION_HOST = "api.sample.test"
INJECTION_HEADER = "authorization"
INJECTION_SENTINEL = "Bearer sentinel-sample-key"
TOOL_KEY = "tool:echo"
JOB_KEY = "job:ran"
ROUTE_KEY = "route:hit"


class EchoInput(BaseModel):
    message: str


async def _echo(ctx: ToolContext, args: EchoInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("sample tool dispatched without its ExtensionContext")
    await ctx.ext.store.put(TOOL_KEY, args.model_dump())
    return ToolResult(content=(TextContent(text=args.message),))


async def _tick(ctx: ExtensionContext) -> None:
    await ctx.store.put(JOB_KEY, {"ran": True})


async def _hook(ctx: ExtensionContext, request: Request) -> Response:
    body = (await request.body()).decode()
    await ctx.store.put(ROUTE_KEY, {"body": body})
    return PlainTextResponse(body)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(
            ToolDef(
                name=TOOL_NAME,
                description="Echo a message, recording it through the extension's scoped store.",
                input_model=EchoInput,
                handler=_echo,
            ),
        ),
        jobs=(JobSpec(name=JOB_NAME, schedule=None, handler=_tick),),
        routes=(RouteSpec(method="POST", path=ROUTE_PATH, handler=_hook),),
        credentials=(
            CredentialSlot(
                name=API_SLOT,
                description="BYOK key the egress proxy swaps onto the sample host.",
                injection=InjectionTarget(
                    host=INJECTION_HOST,
                    header=INJECTION_HEADER,
                    sentinel=INJECTION_SENTINEL,
                ),
            ),
        ),
    )
