"""The conformance sample: a real installed extension that exercises the whole public seam.

It imports only `selfhost.sdk` — the surface a CI gate pins — and its entry point returns a Manifest
declaring exactly the landed points: one tool, one job, one route, one credential slot carrying a
wire-injection target, one onboarding step, and one typed subagent profile. Each handler records
the call it received through its own `ExtensionContext.store` (durable `ext_store` rows, never a
mock log), so the tests read those rows back through the same public surfaces core writes them by.
`UNDECLARED_SLOT` names a slot the Manifest never declares — the probe that a handler asking for an
undeclared slot is refused."""

import shlex
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel

from selfhost.sdk.connectors import OAuthAccount
from selfhost.sdk.context import AgentChange, ExtensionContext
from selfhost.sdk.http import JSONResponse, PlainTextResponse, Request, Response
from selfhost.sdk.jobs import JobSpec
from selfhost.sdk.manifest import (
    ConnectorProvider,
    CredentialSlot,
    Deny,
    HookContext,
    HookOutcome,
    HookSpec,
    InjectionTarget,
    Manifest,
    OnboardingStep,
    PostToolUse,
    PromptSection,
    RouteSpec,
    SubagentProfile,
)
from selfhost.sdk.surfaces import SurfaceContext, SurfaceSpec, Writeback
from selfhost.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

NAME = "sample"
VERSION = "0.1.0"
TOOL_NAME = "sample_echo"
JOB_NAME = "sample_tick"
ROUTE_PATH = "hook"
ONBOARDING_NAME = "sample_setup"
SUBAGENT_NAME = "sample_probe"
API_SLOT = "sample_api"
UNDECLARED_SLOT = "sample_unset"
INJECTION_HOST = "api.sample.test"
INJECTION_HEADER = "authorization"
INJECTION_SENTINEL = "Bearer sentinel-sample-key"
INJECTION_DIMENSION = "requests"
CONNECTOR_PROVIDER = "sample_connector"
CONNECTOR_HOST = "api.connector.sample.test"
CONNECTOR_ACCOUNT = "sample-account-1"
CONNECTOR_TOKEN = "sample-connector-token"
CONNECTOR_AUTHORIZE_URL = "https://connect.sample.test/oauth"
CONNECTOR_TOOL_NAME = "sample_connector_call"
TOOL_KEY = "tool:echo"
JOB_KEY = "job:ran"
TRAJECTORY_KEY = "job:trajectories"
PROPOSAL_KEY = "job:proposal"
ROUTE_KEY = "route:hit"
ONBOARDING_KEY = "onboarding:done"
CONNECTOR_KEY = "connector:called"
HOOK_POST_KEY = "hook:post"
PROPOSAL_SUFFIX = "\nBe concise."
HOOK_DENY_REASON = "the sample pre_tool_use hook refuses its sentinel tool"
SECTION_NAME = "sample_capability"
SECTION_BODY = (
    "<sample_capability>\n"
    "The sample pack contributes this capability section to the agent's system prompt.\n"
    "</sample_capability>"
)
SURFACE_NAME = "sample_surface"
SURFACE_INBOX_REL = "sample-inbox/note.txt"
SURFACE_DELIVERED_PREFIX = "sample-delivered"
SURFACE_POST_REF = "sample-posted-ref"


class EchoInput(BaseModel):
    message: str


class ProbeTask(BaseModel):
    task: str


class ProbeFinding(BaseModel):
    finding: str


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


async def _deny_echo(ctx: HookContext) -> HookOutcome:
    """A pre_tool_use gate matched to TOOL_NAME: it refuses that call, so the tool's handler never
    runs and never records TOOL_KEY. The probe that a Deny short-circuits before dispatch."""
    return Deny(reason=HOOK_DENY_REASON)


async def _record_post(ctx: HookContext) -> HookOutcome:
    """A post_tool_use observer over every dispatched call: it records the payload through the
    extension's own scoped store, so the test reads back through a public surface that the
    post payload arrived. The probe that a call which was not denied reaches the post point."""
    match ctx.payload:
        case PostToolUse(tool_name=tool_name, is_error=is_error):
            await ctx.ext.store.put(HOOK_POST_KEY, {"tool": tool_name, "is_error": is_error})
    return None


@dataclass(frozen=True)
class _SampleConnectorOAuth:
    """The stub connector's OAuth descriptor: a canned authorize URL and a canned account/token
    exchange stand in for a real provider's handoff, so the connect seam runs end to end without a
    live provider. `host` is the one the derived grant admits and meters at the egress proxy."""

    provider: str = CONNECTOR_PROVIDER
    host: str = CONNECTOR_HOST

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"{CONNECTOR_AUTHORIZE_URL}?state={state}&redirect_uri={redirect_uri}"

    async def exchange(self, code: str, redirect_uri: str, workspace_id: UUID) -> OAuthAccount:
        return OAuthAccount(account_id=CONNECTOR_ACCOUNT, token=CONNECTOR_TOKEN)


class ConnectorCallInput(BaseModel):
    path: str = "me"


async def _connector_call(ctx: ToolContext, args: ConnectorCallInput) -> ToolResult:
    """The stub connector action: authenticate to the provider by asking the turn's context for the
    per-account sentinel `Authorization` value, send it as the request's `Authorization` header, and
    reach the provider host through the sandbox's egress proxy. The proxy admits the granted host
    (an ungranted host is refused at CONNECT) and swaps the sentinel for the turn-agent's real token
    on the wire, so the raw secret never enters the sandbox. Records the call through the
    extension's scoped store so the seam is read back through a public surface; an agent with no
    grant for the provider fails loud here, before any egress."""
    if ctx.ext is None:
        raise RuntimeError("sample connector tool dispatched without its ExtensionContext")
    authorization = await ctx.connector_authorization(CONNECTOR_PROVIDER)
    header = shlex.quote(f"Authorization: {authorization}")
    result = await ctx.sandbox.bash(
        f"curl -sS -o /dev/null -w '%{{http_code}}' -H {header} https://{CONNECTOR_HOST}/{args.path}"
    )
    await ctx.ext.store.put(CONNECTOR_KEY, {"host": CONNECTOR_HOST, "path": args.path})
    return ToolResult(
        content=(TextContent(text=result.stdout),), is_error=result.exit_code != 0
    )


class SurfaceIngestInput(BaseModel):
    external_id: str
    email: str | None = None
    message: str
    inbound_text: str | None = None


async def _one_chunk(data: bytes) -> AsyncIterator[bytes]:
    yield data


async def _surface_ingest(ctx: SurfaceContext, request: Request) -> Response:
    """Exercise the whole surface seam: resolve (and link) a member identity, get-or-create the
    conversation, optionally stream an inbound file into the workspace, then admit a turn — all
    read back by the conformance test through the durable rows core writes here."""
    args = SurfaceIngestInput.model_validate_json(await request.body())
    member_id = await ctx.linked_member(args.external_id)
    if member_id is None and args.email is not None:
        member_id = await ctx.link_member(args.external_id, args.email)
    conversation_id = await ctx.conversation_for(args.external_id, member_id)
    if args.inbound_text is not None:
        await ctx.write_workspace_file(
            conversation_id, SURFACE_INBOX_REL, _one_chunk(args.inbound_text.encode())
        )
    agent_id = await ctx.default_agent()
    turn_id = await ctx.admit(
        conversation_id, agent_id, args.message, idempotency_key=args.external_id
    )
    return JSONResponse({"turn_id": str(turn_id), "conversation_id": str(conversation_id)})


async def _surface_post(ctx: SurfaceContext, writeback: Writeback) -> str:
    return SURFACE_POST_REF


async def _surface_attach(ctx: SurfaceContext, writeback: Writeback, reply_ref: str) -> None:
    """Stream each shared file out of the blob store and back into a delivered key, so the test
    reads the round-tripped bytes through the blob store — the streaming get is exercised here."""
    for artifact in writeback.artifacts:
        delivered_key = f"{SURFACE_DELIVERED_PREFIX}/{writeback.turn_id}/{artifact.filename}"
        await ctx.blob.put_stream(delivered_key, ctx.blob.get_stream(artifact.blob_key))


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
        prompt_sections=(PromptSection(name=SECTION_NAME, body=SECTION_BODY),),
        subagents=(
            SubagentProfile(
                name=SUBAGENT_NAME,
                prompt="Probe subagent: restate the task as a finding.",
                tool_names=(TOOL_NAME,),
                input_model=ProbeTask,
                output_model=ProbeFinding,
            ),
        ),
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
                        name=CONNECTOR_TOOL_NAME,
                        description="Call the sample connector's provider host through the proxy.",
                        input_model=ConnectorCallInput,
                        handler=_connector_call,
                    ),
                ),
            ),
        ),
        hooks=(
            HookSpec(event="pre_tool_use", handler=_deny_echo, tools=(TOOL_NAME,)),
            HookSpec(event="post_tool_use", handler=_record_post),
        ),
        surfaces=(
            SurfaceSpec(
                name=SURFACE_NAME,
                ingest=_surface_ingest,
                post=_surface_post,
                attach=_surface_attach,
            ),
        ),
    )
