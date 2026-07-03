"""The conformance sample: a real installed extension that exercises the whole public seam.

It imports only `selfhost.sdk` — the surface a CI gate pins — and its entry point returns a Manifest
declaring exactly the landed points: one tool, one job, one route, one credential slot carrying a
wire-injection target, and one onboarding step. Each handler records the call it received through
its own `ExtensionContext.store` (durable `ext_store` rows, never a mock log), so the tests read
those rows back through the same public surfaces core writes them by. `UNDECLARED_SLOT` names a slot
the Manifest never declares — the probe that a handler asking for an undeclared slot is refused."""

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel

from selfhost.sdk.connectors import OAuthAccount
from selfhost.sdk.context import AgentChange, ExtensionContext
from selfhost.sdk.http import PlainTextResponse, Request, Response
from selfhost.sdk.jobs import JobSpec
from selfhost.sdk.manifest import (
    ConnectorProvider,
    CredentialSlot,
    InjectionTarget,
    Manifest,
    OnboardingStep,
    RouteSpec,
)
from selfhost.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

NAME = "sample"
VERSION = "0.1.0"
TOOL_NAME = "sample_echo"
JOB_NAME = "sample_tick"
ROUTE_PATH = "hook"
ONBOARDING_NAME = "sample_setup"
API_SLOT = "sample_api"
UNDECLARED_SLOT = "sample_unset"
INJECTION_HOST = "api.sample.test"
INJECTION_HEADER = "authorization"
INJECTION_SENTINEL = "Bearer sentinel-sample-key"
INJECTION_DIMENSION = "requests"
CONNECTOR_PROVIDER = "sample_connector"
CONNECTOR_HOST = "api.connector.sample.test"
CONNECTOR_ACCOUNT = "sample-account-1"
CONNECTOR_AUTHORIZE_URL = "https://connect.sample.test/oauth"
CONNECTOR_EXECUTE_TOOL_NAME = "sample_connector_execute"
TOOL_KEY = "tool:echo"
JOB_KEY = "job:ran"
TRAJECTORY_KEY = "job:trajectories"
PROPOSAL_KEY = "job:proposal"
ROUTE_KEY = "route:hit"
ONBOARDING_KEY = "onboarding:done"
CONNECTOR_EXECUTE_KEY = "connector:executed"
PROPOSAL_SUFFIX = "\nBe concise."


class EchoInput(BaseModel):
    message: str


async def _echo(ctx: ToolContext, args: EchoInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("sample tool dispatched without its ExtensionContext")
    await ctx.ext.store.put(TOOL_KEY, args.model_dump())
    return ToolResult(content=(TextContent(text=args.message),))


async def _tick(ctx: ExtensionContext) -> None:
    await ctx.store.put(JOB_KEY, {"ran": True})
    if ctx.corpus is None:
        return
    trajectories = await ctx.trajectories()
    await ctx.store.put(TRAJECTORY_KEY, {"count": len(trajectories)})
    if not trajectories:
        return
    target = trajectories[0]
    ref = await ctx.propose_change(
        AgentChange(
            agent_id=target.agent_id,
            new_prompt=target.agent_prompt + PROPOSAL_SUFFIX,
            from_digest=target.agent_prompt_digest,
        )
    )
    await ctx.store.put(PROPOSAL_KEY, {"proposal_id": str(ref.proposal_id)})


async def _hook(ctx: ExtensionContext, request: Request) -> Response:
    body = (await request.body()).decode()
    await ctx.store.put(ROUTE_KEY, {"body": body})
    return PlainTextResponse(body)


async def _setup(ctx: ExtensionContext) -> None:
    await ctx.store.put(ONBOARDING_KEY, {"onboarded": True})


@dataclass(frozen=True)
class _SampleConnectorOAuth:
    """The stub connector's OAuth descriptor: a canned authorize URL and a canned account exchange
    stand in for a real provider's handoff, so the connect seam runs end to end without a live
    provider. `host` is the one the derived grant admits and meters at the egress proxy. The broker
    holds the account's token, so the exchange yields only the connected-account id — no secret."""

    provider: str = CONNECTOR_PROVIDER
    host: str = CONNECTOR_HOST

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"{CONNECTOR_AUTHORIZE_URL}?state={state}&redirect_uri={redirect_uri}"

    async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID) -> OAuthAccount:
        return OAuthAccount(account_id=CONNECTOR_ACCOUNT)


class ConnectorExecuteInput(BaseModel):
    tool_name: str = "sample_list"


async def _connector_execute(ctx: ToolContext, args: ConnectorExecuteInput) -> ToolResult:
    """The stub connector's server-side execute path: it resolves the turn-agent's bound
    connected-account id through `connector_account` — the broker's account id a server-side
    execution API takes, holding the token itself — and records it through the extension's scoped
    store so the seam is read back through a public surface. An agent with no grant for the provider
    fails loud here, before any execution."""
    if ctx.ext is None:
        raise RuntimeError("sample connector tool dispatched without its ExtensionContext")
    account = await ctx.connector_account(CONNECTOR_PROVIDER)
    await ctx.ext.store.put(
        CONNECTOR_EXECUTE_KEY, {"account": account, "tool_name": args.tool_name}
    )
    return ToolResult(content=(TextContent(text=account),))


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
        onboarding_steps=(OnboardingStep(name=ONBOARDING_NAME, handler=_setup),),
        credentials=(
            CredentialSlot(
                name=API_SLOT,
                description="BYOK key the egress proxy swaps onto the sample host.",
                injection=InjectionTarget(
                    host=INJECTION_HOST,
                    header=INJECTION_HEADER,
                    sentinel=INJECTION_SENTINEL,
                    dimension=INJECTION_DIMENSION,
                ),
            ),
        ),
        connectors=(
            ConnectorProvider(
                oauth=_SampleConnectorOAuth(),
                tools=(
                    ToolDef(
                        name=CONNECTOR_EXECUTE_TOOL_NAME,
                        description="Resolve the bound connected-account id for server-side exec.",
                        input_model=ConnectorExecuteInput,
                        handler=_connector_execute,
                    ),
                ),
            ),
        ),
    )
