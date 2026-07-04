"""The conformance sample: a real installed extension that exercises the whole public seam.

It imports only `selfhost.sdk` — the surface a CI gate pins — and its entry point returns a Manifest
declaring exactly the landed points: one tool, one job, one route, one credential slot carrying a
wire-injection target, one onboarding step, and one typed subagent profile. Each handler records
the call it received through its own `ExtensionContext.store` (durable `ext_store` rows, never a
mock log), so the tests read those rows back through the same public surfaces core writes them by.
`UNDECLARED_SLOT` names a slot the Manifest never declares — the probe that a handler asking for an
undeclared slot is refused."""

import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from typing import ClassVar
from uuid import UUID

from pydantic import BaseModel

from selfhost.sdk.connectors import OAuthAccount
from selfhost.sdk.context import AgentChange, ExtensionContext
from selfhost.sdk.http import JSONResponse, PlainTextResponse, Request, Response
from selfhost.sdk.index import Chunk, EmbedClient, Hit, IndexScope
from selfhost.sdk.jobs import JobSpec
from selfhost.sdk.manifest import (
    ConnectorProvider,
    CredentialSlot,
    Deny,
    HookContext,
    HookOutcome,
    HookSpec,
    IndexBackendSpec,
    InjectionTarget,
    Manifest,
    ModelProviderSpec,
    OnboardingStep,
    PostToolUse,
    PromptSection,
    RouteSpec,
    SourceProvider,
    SubagentProfile,
)
from selfhost.sdk.models import ModelEvent, ModelPrice, ModelRequest, TextDelta, Usage
from selfhost.sdk.sources import SHARED_SUBJECT, Page, SourceAuth, SyncResult
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
CONNECTOR_AUTHORIZE_URL = "https://connect.sample.test/oauth"
CONNECTOR_EXECUTE_TOOL_NAME = "sample_connector_execute"
TOOL_KEY = "tool:echo"
JOB_KEY = "job:ran"
TRAJECTORY_KEY = "job:trajectories"
PROPOSAL_KEY = "job:proposal"
ROUTE_KEY = "route:hit"
ONBOARDING_KEY = "onboarding:done"
CONNECTOR_EXECUTE_KEY = "connector:executed"
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
SOURCE_BACKEND = "sample_source"
SOURCE_REF = "sample/handbook"
SOURCE_TOPIC = "the sample source syncs a page about migrating the orbital widget fleet"
INDEX_BACKEND = "sample_index"
MODEL_PROVIDER_NAME = "sample_models"
SAMPLE_MODEL = "sample-model-x1"
SAMPLE_MODEL_REPLY = "sample model backend reply"
SAMPLE_MODEL_PRICE = ModelPrice(input=2_000_000, output=4_000_000, cache_read=0, cache_write=0)


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


class SampleSourceConfig(BaseModel):
    """The sample source's typed per-source config — a distinct shape from the folder backend's, so
    it proves a backend carries its own typed parameters, never a shared bag."""

    topic: str


@dataclass(frozen=True)
class SampleSource:
    """A canned content-source backend: `fetch` renders one deterministic page from its typed
    config, exercising the seam core drives (register a source, poll it, land its page in memory,
    index it for recall). `auth` is threaded but unused — a folder-like local source resolves no
    provider token."""

    config_model: ClassVar[type[SampleSourceConfig]] = SampleSourceConfig

    async def fetch(
        self, config: SampleSourceConfig, cursor: str | None, auth: SourceAuth
    ) -> SyncResult:
        digest = "sha256:" + hashlib.sha256(config.topic.encode()).hexdigest()
        page = Page(
            source_ref=SOURCE_REF, digest=digest, subject=SHARED_SUBJECT, body=config.topic
        )
        return SyncResult(pages=(page,), next_cursor=None)


@dataclass(frozen=True)
class SampleIndex:
    """A trivial in-process IndexBackend the probe registers through the `indexes` Manifest point:
    it stores chunks in a dict, ranks lexical by term count and vector by dot product, each filtered
    to the queried owner kind and subjects, and reindex re-embeds a scope through the embed client
    core hands the factory. A real backend consumed through the protocol, so a test drives it
    exactly as core does; the dialect-native backends keep their own retrieval proofs."""

    embed: EmbedClient
    chunks: dict[str, Chunk] = field(default_factory=dict)

    async def upsert(self, chunks: tuple[Chunk, ...]) -> None:
        for chunk in chunks:
            self.chunks[chunk.chunk_digest] = chunk

    async def delete(self, scope: IndexScope) -> None:
        for digest in [
            digest for digest, chunk in self.chunks.items() if _in_scope(chunk, scope)
        ]:
            del self.chunks[digest]

    async def lexical(
        self, query: str, subjects: frozenset[str], owner_kind: str, limit: int
    ) -> tuple[Hit, ...]:
        terms = frozenset(query.lower().split())
        scored = [
            _hit(chunk, float(count))
            for chunk in self._scoped(subjects, owner_kind)
            if (count := sum(chunk.text.lower().count(term) for term in terms)) > 0
        ]
        return tuple(sorted(scored, key=lambda hit: hit.score, reverse=True)[:limit])

    async def vector(
        self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int
    ) -> tuple[Hit, ...]:
        scored = [
            _hit(chunk, score)
            for chunk in self._scoped(subjects, owner_kind)
            if (score := _dot(chunk.embedding, embedding)) > 0
        ]
        return tuple(sorted(scored, key=lambda hit: hit.score, reverse=True)[:limit])

    async def reindex(self, scope: IndexScope) -> None:
        scoped = [chunk for chunk in self.chunks.values() if _in_scope(chunk, scope)]
        if not scoped:
            return
        vectors = await self.embed.embed(tuple(chunk.text for chunk in scoped))
        for chunk, vector in zip(scoped, vectors, strict=True):
            self.chunks[chunk.chunk_digest] = replace(chunk, embedding=vector)

    def _scoped(self, subjects: frozenset[str], owner_kind: str) -> list[Chunk]:
        return [
            chunk
            for chunk in self.chunks.values()
            if chunk.owner_kind == owner_kind and chunk.subject in subjects
        ]


def _in_scope(chunk: Chunk, scope: IndexScope) -> bool:
    return chunk.owner_kind == scope.owner_kind and chunk.owner_id == scope.owner_id


def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


def _hit(chunk: Chunk, score: float) -> Hit:
    return Hit(
        chunk_digest=chunk.chunk_digest,
        owner_kind=chunk.owner_kind,
        owner_id=chunk.owner_id,
        subject=chunk.subject,
        ordinal=chunk.ordinal,
        text=chunk.text,
        score=score,
    )


async def _setup(ctx: ExtensionContext) -> None:
    await ctx.store.put(ONBOARDING_KEY, {"onboarded": True})
    await ctx.register_source(SOURCE_BACKEND, SampleSourceConfig(topic=SOURCE_TOPIC))


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


@dataclass(frozen=True)
class SampleModelClient:
    """The canned backend the sample's model provider builds: `complete` streams one text delta and
    a fixed Usage, so the registry seam — core selecting a manifest-contributed model client and
    pricing its id against the contributed rate — is exercised by a real client, never a mock."""

    model: str

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text=SAMPLE_MODEL_REPLY)
        yield Usage(input_tokens=1, output_tokens=1)


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
                        name=CONNECTOR_EXECUTE_TOOL_NAME,
                        description="Resolve the bound connected-account id for server-side exec.",
                        input_model=ConnectorExecuteInput,
                        handler=_connector_execute,
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
        sources=(SourceProvider(backend=SOURCE_BACKEND, source=SampleSource()),),
        indexes=(
            IndexBackendSpec(
                name=INDEX_BACKEND, factory=lambda embed, credentials: SampleIndex(embed=embed)
            ),
        ),
        models=(
            ModelProviderSpec(
                name=MODEL_PROVIDER_NAME,
                matches=lambda model: model == SAMPLE_MODEL,
                client=lambda model: SampleModelClient(model=model),
                prices=((SAMPLE_MODEL, SAMPLE_MODEL_PRICE),),
            ),
        ),
    )
