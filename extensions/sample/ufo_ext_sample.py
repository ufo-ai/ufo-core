"""The conformance sample: a real installed extension that exercises the whole public seam.

It imports only `ufo.sdk` — the surface a CI gate pins — and its entry point returns a Manifest
declaring exactly the landed points: one tool, one job, one route, one credential slot carrying a
wire-injection target, one onboarding step, one typed subagent profile, one hub backend, and one
contributed skill (a `SKILL.md` plus a bundled script under `skills/`). Each handler records the
call it received through its own `ExtensionContext.store` (durable `ext_store` rows, never a mock
log), so the tests read those rows back through the same public surfaces core writes them by.
`UNDECLARED_SLOT` names a slot the Manifest never declares — the probe that a handler asking for an
undeclared slot is refused."""

import hashlib
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import ClassVar
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel

from ufo.sdk.authproxy import AuthProxySpec, Credential
from ufo.sdk.browser import CdpEndpoint, CdpLease
from ufo.sdk.connectors import BrokerSearch, BrokerTool, OAuthAccount, UnknownBrokerTool
from ufo.sdk.context import AgentChange, ExtensionContext
from ufo.sdk.http import (
    JSONResponse,
    PlainTextResponse,
    Request,
    Response,
    StreamingResponse,
)
from ufo.sdk.hub import InProcessHub
from ufo.sdk.index import Chunk, EmbedClient, Hit, IndexScope
from ufo.sdk.jobs import JobSpec, owner_candidates
from ufo.sdk.manifest import (
    CdpProviderSpec,
    ConnectorProvider,
    CredentialSlot,
    Deny,
    EmbedBackendSpec,
    HookContext,
    HookOutcome,
    HookSpec,
    HubSpec,
    IndexBackendSpec,
    InjectionTarget,
    Manifest,
    MemorySearchProviderSpec,
    ModelProviderSpec,
    OnboardingStep,
    PageChangeBatch,
    PostCompact,
    PostToolUse,
    PostToolUseFailure,
    PreCompact,
    PromptSection,
    RouteSpec,
    SearchProviderSpec,
    SkillSpec,
    SourceProvider,
    Stop,
    SubagentProfile,
    SubagentToolGrant,
)
from ufo.sdk.memory import MemoryMatch
from ufo.sdk.models import ModelEvent, ModelPrice, ModelRequest, TextDelta, Usage
from ufo.sdk.sandbox import (
    BlobStore,
    CarrierSpec,
    ExecResult,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
)
from ufo.sdk.search import (
    FetchedPage,
    FetchRequest,
    SearchHit,
    SearchQuery,
    SearchResults,
)
from ufo.sdk.sources import SHARED_SUBJECT, Page, SourceAuth, SyncResult
from ufo.sdk.surfaces import SurfaceContext, SurfaceRoute, SurfaceSpec, Writeback
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult

NAME = "sample"
VERSION = "0.1.0"
TOOL_NAME = "sample_echo"
NOTE_TOOL_NAME = "sample_note"
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
CONNECTOR_LABEL = "Sample Connector"
CONNECTOR_HOST = "api.connector.sample.test"
CONNECTOR_ACCOUNT = "sample-account-1"
CONNECTOR_AUTHORIZE_URL = "https://connect.sample.test/oauth"
CONNECTOR_EXECUTE_TOOL_NAME = "sample_connector_execute"
BROKER_TOOL_SLUG = "SAMPLE_LIST_WIDGETS"
BROKER_TOOL_DESCRIPTION = "List the sample provider's widgets."
BROKER_SEARCH_PLAN = "call SAMPLE_LIST_WIDGETS first"
BROKER_BEARER_PREFIX = "sample-broker-token:"
HUB_BACKEND = "sample_hub"
TOOL_KEY = "tool:echo"
JOB_KEY = "job:ran"
TRAJECTORY_KEY = "job:trajectories"
PROPOSAL_KEY = "job:proposal"
ROUTE_KEY = "route:hit"
ONBOARDING_KEY = "onboarding:done"
CONNECTOR_EXECUTE_KEY = "connector:executed"
HOOK_POST_KEY = "hook:post"
HOOK_POST_FAILURE_KEY = "hook:post_failure"
HOOK_STOP_KEY = "hook:stop"
HOOK_PRE_COMPACT_KEY = "hook:pre_compact"
HOOK_POST_COMPACT_KEY = "hook:post_compact"
HOOK_PAGE_CHANGE_KEY = "hook:page_change"
PROPOSAL_SUFFIX = "\nBe concise."
HOOK_DENY_REASON = "the sample pre_tool_use hook refuses its sentinel tool"
SECTION_NAME = "sample_capability"
SECTION_BODY = (
    "<sample_capability>\n"
    "The sample pack contributes this capability section to the agent's system prompt.\n"
    "</sample_capability>"
)
SURFACE_NAME = "sample_surface"
SURFACE_LIVE_NAME = "sample_live"
SURFACE_INBOX_REL = "sample-inbox/note.txt"
SURFACE_DELIVERED_PREFIX = "sample-delivered"
SURFACE_POST_REF = "sample-posted-ref"
SURFACE_LIVE_PATH = "live"
SURFACE_LIVE_STREAM_PATH = "live/{turn_id}/stream"
SURFACE_PEER = "sample_peer"
SURFACE_SPEND_WINDOW_SECONDS = 3600
SOURCE_BACKEND = "sample_source"
SOURCE_REF = "sample/handbook"
SOURCE_TOPIC = "the sample source syncs a page about migrating the orbital widget fleet"
INDEX_BACKEND = "sample_index"
EMBED_BACKEND = "sample_embed"
SAMPLE_EMBED_VECTOR = (1.0, 0.0, 0.0)
MODEL_PROVIDER_NAME = "sample_models"
SAMPLE_MODEL = "sample-model-x1"
SAMPLE_MODEL_REPLY = "sample model backend reply"
SAMPLE_MODEL_PRICE = ModelPrice(input=2_000_000, output=4_000_000, cache_read=0, cache_write=0)
SKILL_NAME = "sample_skill"
SKILL_SCRIPT = "probe.py"
SKILL_SCRIPT_MARKER = "sample-skill-probe-ok"
SKILL_DIR = Path(__file__).parent / "skills" / SKILL_NAME
CDP_PROVIDER = "sample_cdp"
SAMPLE_CDP_URL = "wss://sample.test/cdp"
CARRIER_NAME = "sample_carrier"
CARRIER_CONTAINER = "sample-container"
AUTH_PROXY_BACKEND = "sample_auth_proxy"
AUTH_PROXY_BEARER = "sample"
SEARCH_PROVIDER = "sample_search"
SAMPLE_SEARCH_URL = "https://sample.test/result"
SAMPLE_SEARCH_TITLE = "Sample result"
SAMPLE_SEARCH_TEXT = "the sample search backend answers a canned hit"
SAMPLE_SEARCH_ANSWER = "the sample search backend answers directly"
SAMPLE_MEMORY_TEXT = "the sample memory provider returns a scoped result"
MEMORY_SEARCH_KEY = "memory_search"
MEMORY_SEARCH_PROVIDER = "sample"
SAMPLE_FETCH_TEXT = "the sample search backend fetched a canned page"


NOTE_TABLE = sa.Table(
    "sample_ext_note",
    sa.MetaData(),
    sa.Column("workspace_id", sa.Uuid(), primary_key=True),
    sa.Column("note", sa.Text(), nullable=False),
)


class EchoInput(BaseModel):
    message: str


class NoteInput(BaseModel):
    text: str


class ProbeTask(BaseModel):
    task: str


class ProbeFinding(BaseModel):
    finding: str


async def _echo(ctx: ToolContext, args: EchoInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("sample tool dispatched without its ExtensionContext")
    await ctx.ext.store.put(TOOL_KEY, args.model_dump())
    return ToolResult(content=(TextContent(text=args.message),))


async def _note(ctx: ToolContext, args: NoteInput) -> ToolResult:
    """Write a note into `sample_ext_note` — the table the sample's own migration creates — and read
    it back through the extension's workspace-scoped transaction. The migration seam end to end: an
    extension owns a table and its capability reads and writes it, scoped to this workspace."""
    if ctx.ext is None:
        raise RuntimeError("sample note tool dispatched without its ExtensionContext")
    workspace_id = ctx.ext.store.workspace_id
    async with ctx.ext.transaction() as connection:
        updated = await connection.execute(
            sa.update(NOTE_TABLE)
            .values(note=args.text)
            .where(NOTE_TABLE.c.workspace_id == workspace_id)
        )
        if updated.rowcount == 0:
            await connection.execute(
                sa.insert(NOTE_TABLE).values(workspace_id=workspace_id, note=args.text)
            )
        stored = (
            await connection.execute(
                sa.select(NOTE_TABLE.c.note).where(NOTE_TABLE.c.workspace_id == workspace_id)
            )
        ).one()
    return ToolResult(content=(TextContent(text=stored.note),))


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
        page = Page(source_ref=SOURCE_REF, digest=digest, subject=SHARED_SUBJECT, body=config.topic)
        return SyncResult(pages=(page,), next_cursor=None)


@dataclass(frozen=True)
class SampleIndex:
    """A trivial in-process IndexBackend the probe registers through the `indexes` Manifest point:
    it stores chunks in a dict, ranks lexical by term count and vector by dot product, each filtered
    to the queried owner kind and subjects, prunes a scope's chunks outside a keep-set, and reindex
    re-embeds a scope through the embed client core hands the factory. A real backend consumed
    through the protocol, so a test drives it
    exactly as core does; the dialect-native backends keep their own retrieval proofs."""

    embed: EmbedClient
    chunks: dict[str, Chunk] = field(default_factory=dict)

    async def upsert(self, chunks: tuple[Chunk, ...]) -> None:
        for chunk in chunks:
            self.chunks[chunk.chunk_digest] = chunk

    async def delete(self, scope: IndexScope) -> None:
        for digest in [digest for digest, chunk in self.chunks.items() if _in_scope(chunk, scope)]:
            del self.chunks[digest]

    async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None:
        for digest in [
            digest
            for digest, chunk in self.chunks.items()
            if _in_scope(chunk, scope) and digest not in keep
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


@dataclass(frozen=True)
class SampleEmbed:
    """A canned EmbedClient the probe registers through the `embeds` Manifest point: `embed` returns
    one fixed vector per text. A real client consumed through the protocol, so a test drives core's
    embed selection exactly as core does; the OpenAI backend keeps its own proof."""

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(SAMPLE_EMBED_VECTOR for _ in texts)


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
    """A post_tool_use observer over every dispatched call that SUCCEEDED: it records the payload
    through the extension's own scoped store, so the test reads back through a public surface that
    the post payload arrived. The probe that a non-denied, non-erroring call reaches the post
    point — an errored call reaches post_tool_use_failure instead."""
    match ctx.payload:
        case PostToolUse(tool_name=tool_name):
            await ctx.ext.store.put(HOOK_POST_KEY, {"tool": tool_name})
    return None


async def _record_post_failure(ctx: HookContext) -> HookOutcome:
    """A post_tool_use_failure observer: a dispatched call whose result was an error records here,
    never at post_tool_use. The probe that the success/failure split reaches distinct events."""
    match ctx.payload:
        case PostToolUseFailure(tool_name=tool_name):
            await ctx.ext.store.put(HOOK_POST_FAILURE_KEY, {"tool": tool_name})
    return None


async def _record_stop(ctx: HookContext) -> HookOutcome:
    """A stop observer: records the final answer the turn is about to commit, so the test reads back
    that the turn-end event fired with its answer."""
    match ctx.payload:
        case Stop(answer=answer):
            await ctx.ext.store.put(HOOK_STOP_KEY, {"answer": answer})
    return None


async def _record_pre_compact(ctx: HookContext) -> HookOutcome:
    """A pre_compact observer: records the reason and the pre-compaction token estimate."""
    match ctx.payload:
        case PreCompact(reason=reason, before_tokens=before_tokens):
            await ctx.ext.store.put(
                HOOK_PRE_COMPACT_KEY, {"reason": reason, "before_tokens": before_tokens}
            )
    return None


async def _record_post_compact(ctx: HookContext) -> HookOutcome:
    """A post_compact observer: records the summary and the tokens bracketing the compaction."""
    match ctx.payload:
        case PostCompact(summary=summary, before_tokens=before_tokens, after_tokens=after_tokens):
            await ctx.ext.store.put(
                HOOK_POST_COMPACT_KEY,
                {
                    "summary": summary,
                    "before_tokens": before_tokens,
                    "after_tokens": after_tokens,
                },
            )
    return None


async def _record_page_change(ctx: HookContext) -> HookOutcome:
    """The data-plane page_change probe: records the delivered page ids and whether the core
    page-change runner wired the model into the off-turn context, so the runner's delivery, cursor
    advance, and jobs-way context are read back through the scoped store."""
    match ctx.payload:
        case PageChangeBatch(changes=changes):
            await ctx.ext.store.put(
                HOOK_PAGE_CHANGE_KEY,
                {
                    "page_ids": [str(change.page_id) for change in changes],
                    "model_wired": ctx.ext.model is not None,
                },
            )
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

    async def exchange(
        self, code: str, redirect_uri: str, workspace_id: UUID, state: str
    ) -> OAuthAccount:
        return OAuthAccount(account_id=CONNECTOR_ACCOUNT)


@dataclass(frozen=True)
class _SampleBroker:
    """The stub broker: a one-tool canned catalog, an execute that echoes its whole call back as
    the provider response, and a bearer credential naming the account — so a test asserting the
    dynamic connector tools or feed-sync routing reads exactly what core dispatched through the
    seam, off public surfaces, with no live broker."""

    async def tools(self, workspace_id: UUID, provider: str, query: str) -> tuple[BrokerTool, ...]:
        return (BrokerTool(slug=BROKER_TOOL_SLUG, description=BROKER_TOOL_DESCRIPTION),)

    async def schema(self, workspace_id: UUID, provider: str, slug: str) -> BrokerTool:
        if slug != BROKER_TOOL_SLUG:
            raise UnknownBrokerTool(slug)
        return BrokerTool(
            slug=slug,
            description=BROKER_TOOL_DESCRIPTION,
            input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}},
        )

    async def execute(
        self,
        workspace_id: UUID,
        provider: str,
        slug: str,
        arguments: Mapping[str, object],
        account_id: str,
        idempotency_key: str | None,
    ) -> dict[str, object]:
        if slug != BROKER_TOOL_SLUG:
            raise UnknownBrokerTool(slug)
        return {
            "provider": provider,
            "slug": slug,
            "arguments": dict(arguments),
            "account": account_id,
            "idempotency_key": idempotency_key,
        }

    async def search(self, workspace_id: UUID, provider: str, query: str) -> BrokerSearch:
        return BrokerSearch(
            tools=await self.tools(workspace_id, provider, query), plan=(BROKER_SEARCH_PLAN,)
        )

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(bearer=f"{BROKER_BEARER_PREFIX}{account}")


class ConnectorExecuteInput(BaseModel):
    tool_name: str = "sample_list"


async def _connector_execute(ctx: ToolContext, args: ConnectorExecuteInput) -> ToolResult:
    """The stub connector's server-side execute path: it resolves the turn-agent's bound
    connected-account id through `connector_account` — the broker's account id a server-side
    execution API takes, holding the token itself — and records it, with the per-call
    `idempotency_key` core folds onto a side-effecting tool, through the extension's scoped store so
    both seams are read back through a public surface. An agent with no grant for the provider fails
    loud here, before any execution."""
    if ctx.ext is None:
        raise RuntimeError("sample connector tool dispatched without its ExtensionContext")
    account = await ctx.connector_account(CONNECTOR_PROVIDER)
    await ctx.ext.store.put(
        CONNECTOR_EXECUTE_KEY,
        {
            "account": account,
            "tool_name": args.tool_name,
            "idempotency_key": ctx.idempotency_key,
        },
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
        conversation_id,
        agent_id,
        args.message,
        idempotency_key=args.external_id,
        speaker_member_id=member_id,
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


async def _surface_live_admit(ctx: SurfaceContext, request: Request) -> Response:
    """Exercise the seam's LIVE mode on its own surface: adopt a member from a peer surface's
    identity, get-or-create the conversation, admit (a live surface declares no `post`, so
    admission registers nothing for the poller and the member tails the hub), then read back the
    turn's owner and the workspace spend rollup. The conformance test asserts no writeback row
    exists for this turn — the live/durable contrast against `_surface_ingest` on the durable
    surface."""
    args = SurfaceIngestInput.model_validate_json(await request.body())
    member_id = await ctx.linked_member(args.external_id)
    if member_id is None:
        member_id = await ctx.adopt_identity(SURFACE_PEER, args.external_id)
    conversation_id = await ctx.conversation_for(args.external_id, member_id)
    agent_id = await ctx.default_agent()
    turn_id = await ctx.admit(conversation_id, agent_id, args.message, speaker_member_id=member_id)
    owner = await ctx.turn_owner(turn_id)
    report = await ctx.spend_rollup(SURFACE_SPEND_WINDOW_SECONDS)
    return JSONResponse(
        {
            "turn_id": str(turn_id),
            "conversation_id": str(conversation_id),
            "owner": "" if owner is None else str(owner),
            "spend_total_micro_usd": report.total_micro_usd,
        }
    )


async def _surface_live_stream(ctx: SurfaceContext, request: Request) -> Response:
    """Drive the seam's `tail` capability: stream the turn's live frames off the hub as newline-
    delimited JSON, ending on its durable terminal-or-parked state."""
    turn_id = UUID(request.path_params["turn_id"])
    return StreamingResponse(_surface_frames(ctx, turn_id), media_type="application/x-ndjson")


async def _surface_frames(ctx: SurfaceContext, turn_id: UUID) -> AsyncIterator[bytes]:
    async for _cursor, frame in ctx.tail(turn_id):
        yield frame.model_dump_json().encode() + b"\n"


@dataclass(frozen=True)
class SampleModelClient:
    """The canned backend the sample's model provider builds: `complete` streams one text delta and
    a fixed Usage, so the registry seam — core selecting a manifest-contributed model client and
    pricing its id against the contributed rate — is exercised by a real client, never a mock."""

    model: str

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text=SAMPLE_MODEL_REPLY)
        yield Usage(input_tokens=1, output_tokens=1)


@dataclass(frozen=True)
class SampleCdpLease:
    """The canned per-turn lease the sample's cdp provider mints: `endpoint` returns a fixed
    `CdpEndpoint`, `token` the durable reattach handle (the fixed URL), `aclose` is a no-op — a real
    object exercised through the `CdpLease` protocol the browser engine drives, never a mock."""

    async def endpoint(self) -> CdpEndpoint:
        return CdpEndpoint(url=SAMPLE_CDP_URL)

    async def token(self) -> str:
        return SAMPLE_CDP_URL

    async def aclose(self) -> None:
        return None


@dataclass(frozen=True)
class SampleCdpProvider:
    """A trivial CdpProvider the probe registers through the `cdp_providers` Manifest point: `lease`
    mints a canned SampleCdpLease, `reattach` reconnects to the same fixed endpoint. A real object
    consumed through the protocol, so a test drives it as core selects and leases it; the BUA engine
    keeps its own live-CDP proof."""

    async def lease(self, sandbox: SandboxSession | None = None) -> CdpLease:
        return SampleCdpLease()

    async def reattach(self, token: str) -> CdpLease:
        return SampleCdpLease()


@dataclass(frozen=True)
class SampleAuthProxy:
    """A trivial AuthProxy the probe registers through the `auth_proxies` Manifest point:
    `credential` returns a fixed bearer, exercising the seam core drives (select a
    manifest-contributed auth-proxy backend by name and thread it onto the sync runner). A real
    object consumed through the protocol, so a test drives it exactly as core does; the Composio and
    direct backends keep their own proofs."""

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(bearer=AUTH_PROXY_BEARER)


@dataclass(frozen=True)
class SampleSearchProvider:
    """A trivial SearchProvider the probe registers through the `search_providers` Manifest point:
    `search` answers a canned SearchResults (carrying an `answer` to exercise that field) and
    `fetch` a canned FetchedPage. A real object consumed through the protocol, so a test drives it
    as core selects and the research tools call it; the exa backend keeps its own HTTP proof."""

    supports_fetch: bool = True

    async def search(self, query: SearchQuery) -> SearchResults:
        return SearchResults(
            hits=(
                SearchHit(
                    url=SAMPLE_SEARCH_URL, title=SAMPLE_SEARCH_TITLE, text=SAMPLE_SEARCH_TEXT
                ),
            ),
            answer=SAMPLE_SEARCH_ANSWER,
        )

    async def fetch(self, request: FetchRequest) -> FetchedPage:
        return FetchedPage(url=request.url, text=SAMPLE_FETCH_TEXT)


@dataclass(frozen=True)
class SampleMemorySearch:
    """Record a scoped search and return one result through the public provider seam."""

    ctx: ExtensionContext

    async def search(
        self,
        queries: tuple[str, ...],
        member_id: UUID | None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[MemoryMatch, ...]:
        await self.ctx.store.put(
            MEMORY_SEARCH_KEY,
            {
                "queries": list(queries),
                "member_id": None if member_id is None else str(member_id),
                "start": None if start is None else start.isoformat(),
                "end": None if end is None else end.isoformat(),
            },
        )
        return (MemoryMatch(kind="fact", text=SAMPLE_MEMORY_TEXT),)


class SampleCarrier:
    """A trivial in-process carrier the probe registers so `serve`'s backend selection has a
    manifest-contributed carrier to choose. It implements the whole Carrier protocol without a real
    container: `exec` echoes the argv it received (so a selection test can prove the carrier it got
    is this one), `export` copies the requested path into the blob store, and create/destroy are
    inert. It proves the `carriers` seam — that core selects an extension's carrier — never a real
    sandbox; the Docker and e2b carriers keep that proof."""

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return SandboxHandle(conversation_id=spec.conversation_id, container_id=CARRIER_CONTAINER)

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        return ExecResult(stdout=" ".join(argv), stderr="", exit_code=0)

    async def export(self, handle: SandboxHandle, path: str, blob: BlobStore, key: str) -> None:
        await blob.put(key, path.encode())

    async def destroy(self, handle: SandboxHandle) -> None:
        return None

    async def host(self, handle: SandboxHandle, port: int) -> str:
        return f"{CARRIER_CONTAINER}:{port}"


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
            ToolDef(
                name=NOTE_TOOL_NAME,
                description="Write and read a note in the sample's own migration-created table.",
                input_model=NoteInput,
                handler=_note,
            ),
        ),
        jobs=(
            JobSpec(
                name=JOB_NAME,
                schedule=None,
                handler=_tick,
                candidates=owner_candidates(
                    lambda: sa.select(NOTE_TABLE.c.workspace_id).distinct()
                ),
            ),
        ),
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
        subagent_tool_grants=(
            SubagentToolGrant(profile=SUBAGENT_NAME, tool_names=(NOTE_TOOL_NAME,)),
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
                label=CONNECTOR_LABEL,
                broker=_SampleBroker(),
                tools=(
                    ToolDef(
                        name=CONNECTOR_EXECUTE_TOOL_NAME,
                        description="Resolve the bound connected-account id for server-side exec.",
                        input_model=ConnectorExecuteInput,
                        handler=_connector_execute,
                        side_effecting=True,
                        subagent_default=True,
                    ),
                ),
            ),
        ),
        hooks=(
            HookSpec(event="pre_tool_use", handler=_deny_echo, tools=(TOOL_NAME,)),
            HookSpec(event="post_tool_use", handler=_record_post),
            HookSpec(event="post_tool_use_failure", handler=_record_post_failure),
            HookSpec(event="stop", handler=_record_stop),
            HookSpec(event="pre_compact", handler=_record_pre_compact),
            HookSpec(event="post_compact", handler=_record_post_compact),
            HookSpec(event="page_change", handler=_record_page_change),
        ),
        surfaces=(
            SurfaceSpec(
                name=SURFACE_NAME,
                routes=(SurfaceRoute(method="POST", path="", handler=_surface_ingest),),
                post=_surface_post,
                attach=_surface_attach,
            ),
            SurfaceSpec(
                name=SURFACE_LIVE_NAME,
                routes=(
                    SurfaceRoute(
                        method="POST", path=SURFACE_LIVE_PATH, handler=_surface_live_admit
                    ),
                    SurfaceRoute(
                        method="GET",
                        path=SURFACE_LIVE_STREAM_PATH,
                        handler=_surface_live_stream,
                    ),
                ),
            ),
        ),
        sources=(
            SourceProvider(backend=SOURCE_BACKEND, build=lambda _credentials: SampleSource()),
        ),
        indexes=(
            IndexBackendSpec(
                name=INDEX_BACKEND, factory=lambda embed, ctx: SampleIndex(embed=embed)
            ),
        ),
        embeds=(EmbedBackendSpec(name=EMBED_BACKEND, factory=lambda ctx: SampleEmbed()),),
        models=(
            ModelProviderSpec(
                name=MODEL_PROVIDER_NAME,
                matches=lambda model: model == SAMPLE_MODEL,
                client=lambda model, key: SampleModelClient(model=model),
                prices=((SAMPLE_MODEL, SAMPLE_MODEL_PRICE),),
            ),
        ),
        hubs=(HubSpec(backend=HUB_BACKEND, build=lambda _url: InProcessHub()),),
        skills=(SkillSpec(path=SKILL_DIR),),
        cdp_providers=(
            CdpProviderSpec(backend=CDP_PROVIDER, build=lambda credentials: SampleCdpProvider()),
        ),
        carriers=(CarrierSpec(name=CARRIER_NAME, factory=SampleCarrier),),
        auth_proxies=(
            AuthProxySpec(backend=AUTH_PROXY_BACKEND, build=lambda credentials: SampleAuthProxy()),
        ),
        search_providers=(
            SearchProviderSpec(
                backend=SEARCH_PROVIDER, build=lambda credentials: SampleSearchProvider()
            ),
        ),
        memory_search=(
            MemorySearchProviderSpec(name=MEMORY_SEARCH_PROVIDER, build=SampleMemorySearch),
        ),
    )
