"""What an extension declares (the Manifest) and what a pack declares (the Pack): the frozen value
objects the loader reads and derives from, never registration APIs it calls.

An extension's entry point returns a Manifest — a bundle of declared points a core subsystem
consumes: tools enter the turn's registry, routes mount under the app, jobs register on the
scheduler, credential slots drive proxy injection, hooks fire on turn-lifecycle events as a runtime
policy filter over the tools grants already admit. A pack's entry point returns a Pack — the set of
installed extensions it bundles plus any pack-level skills and onboarding steps of its own — so
activating one named pack brings a coherent product config up together."""

from collections.abc import Awaitable, Callable
from dataclasses import KW_ONLY, dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import Response

from selfhost.accounting import ModelPrice
from selfhost.browser.backend import BrowserBackend
from selfhost.ext.context import CredentialAccess, ExtensionContext
from selfhost.ext.surface import SurfaceSpec
from selfhost.grants import OAuthProvider
from selfhost.hub import Hub
from selfhost.indexing import EmbedClient, IndexBackend
from selfhost.memory.sources import SourceBackend
from selfhost.models.interface import ModelClient
from selfhost.sandbox.session import Carrier
from selfhost.schema.records import Agent, Turn
from selfhost.tools.registry import ToolDef


@dataclass(frozen=True)
class InjectionTarget:
    """The wire-injection descriptor a credential slot may carry: on the wire to `host`, the proxy
    swaps the `sentinel` value of `header` for the real secret. A set `dimension` also meters it."""

    host: str
    header: str
    sentinel: str
    dimension: str | None = None


@dataclass(frozen=True)
class CredentialSlot:
    """A named secret an extension needs. With an InjectionTarget the proxy swaps it onto the
    wire so the sandbox never holds it; without one it is readable only in-process."""

    name: str
    description: str
    injection: InjectionTarget | None = None


@dataclass(frozen=True)
class JobSpec:
    """Recurring or one-shot background work. `schedule` is a cron string (6 fields, seconds first)
    for recurring jobs, or None to fire once at boot; `handler` runs with the extension's scoped
    ExtensionContext, never a raw handle."""

    name: str
    schedule: str | None
    handler: Callable[[ExtensionContext], Awaitable[None]]


@dataclass(frozen=True)
class RouteSpec:
    """An HTTP endpoint an extension serves. The app mounts `handler` for `method` at
    `/ext/<name>/<path>`; each request is handed the extension's scoped ExtensionContext and the
    incoming Request, and the handler returns the Response."""

    method: Literal["GET", "POST"]
    path: str
    handler: Callable[[ExtensionContext, Request], Awaitable[Response]]


@dataclass(frozen=True)
class ConnectorProvider:
    """One connector an extension registers: the OAuth descriptor behind `/connect` for this
    provider and the agent tools that call its granted account. The descriptor's `provider` keys the
    connect-flow registry `serve` builds; its `host` is the one the derived grant admits, injects,
    and meters at the egress proxy. The tools join the turn's tool set scoped to the extension, so a
    connector call reaches the provider host only for an agent the grant covers."""

    oauth: OAuthProvider
    tools: tuple[ToolDef, ...] = ()


@dataclass(frozen=True)
class PromptSection:
    """A capability section a pack contributes to the agent's system prompt. The loop renders every
    active manifest's sections into the shell's `{{sections}}` slot, ordered by `name`, so a pack's
    rules (web search, browsing, office docs, ...) reach the agent without core naming the
    capability. `body` is the section's Markdown; `name` orders it and identifies it in the rendered
    prompt's digest."""

    name: str
    body: str


@dataclass(frozen=True)
class CarrierSpec:
    """One sandbox backend an extension registers: the `name` the `[sandbox] backend` config selects
    and the `factory` `serve` calls once to build the Carrier. Core ships `docker` and `e2b`; an
    extension contributes another (a remote executor, a pooled or bring-your-own backend) without
    core naming it, and a name that collides with a built-in or another extension's fails loud at
    boot. The selected carrier is held for the process's life as `Runtime.carrier`."""

    name: str
    factory: Callable[[], Carrier]


@dataclass(frozen=True)
class SourceProvider:
    """One content-source backend an extension registers: the `backend` name that keys its `source`
    rows and the `SourceBackend` the core sync driver drives for them. `serve` sources these into
    the driver's backend map, so a row with this backend name syncs through this backend and its
    pages land in memory via the derivation pipeline exactly as the core folder source's do. A
    source row is created in chat (an extension calls `ExtensionContext.register_source`), never a
    deploy-config knob."""

    backend: str
    source: SourceBackend


@dataclass(frozen=True)
class IndexBackendSpec:
    """One vector-index backend an extension registers: the `name` a deploy selects it by (config
    `memory.index_backend`, default `"default"`) and the `factory` core calls at boot to build the
    `IndexBackend`, given the deploy embed client and the extension's workspace-scoped
    `ExtensionContext` — from which it opens transactions for a table-owning backend or reads a BYOK
    key through `context.credentials`. The index runs in the jobs/serve role, never in the sandbox,
    so a BYOK backend reads its key in-process rather than through the egress proxy. Every deploy
    ships the base-pinned `index-default` extension registering name `"default"` (SQLite FTS5 +
    local cosine, Postgres tsvector + pgvector), which core resolves when the knob is unset."""

    name: str
    factory: Callable[[EmbedClient, ExtensionContext], IndexBackend]


@dataclass(frozen=True)
class EmbedBackendSpec:
    """One embedding backend an extension registers, mirroring `IndexBackendSpec`: the `name` a
    deploy selects it by (config `memory.embed_backend`, default `"default"`) and the `factory` core
    calls once at boot to build the `EmbedClient`, given the extension's workspace-scoped
    `ExtensionContext` (from which it reads its provider key through `context.credentials`). The
    embed client runs in the jobs/serve role on the deploy key, never through the sandbox proxy.
    Every deploy ships the base-pinned `embed-openai` extension registering name `"default"`, which
    core resolves when the config knob is unset."""

    name: str
    factory: Callable[[ExtensionContext], EmbedClient]


@dataclass(frozen=True)
class ModelProviderSpec:
    """One model backend an extension contributes. `matches` claims the model ids this backend
    serves — an explicit-slug test, a prefix test, or a catch-all router; `client` builds the
    `ModelClient` for a served id, resolving its own API key when the turn selects it (once per
    turn, never at boot); `prices` are the `(model_id, ModelPrice)` rates it knows, merged over
    core's table so its slugs are billed and stamped. The registry tries providers in order — core's
    direct Anthropic + OpenAI first — so a contributed router serves only what core does not
    claim."""

    name: str
    matches: Callable[[str], bool]
    client: Callable[[str], ModelClient]
    prices: tuple[tuple[str, ModelPrice], ...] = ()


@dataclass(frozen=True)
class HubSpec:
    """A live-frame hub backend an extension registers. `backend` is the name `config.hub.backend`
    selects it by; `build` constructs the process-wide Hub from `config.hub.url`, called once at
    boot and only when this backend is selected. A shared (cross-process) backend lifts core's
    single-instance boot guard, so scale-out is an extension, not a core change."""

    backend: str
    build: Callable[[str | None], Hub]


@dataclass(frozen=True)
class BrowserBackendSpec:
    """A browser backend an extension registers, selected at the tool-surface level. `backend` is
    the name `config.browser.backend` selects it by; `build` constructs the process-wide
    BrowserBackend once at boot, only when selected, given a credential reader scoped to this
    manifest's slots (a remote provider reads its key in-process, host-side, never in the sandbox).
    Core's default `bua` backend drives Chrome over the CDP endpoint an endpoint provider yields; an
    extension reuses that engine with its own endpoint provider (browserbase) or replaces the whole
    surface (browser-use)."""

    backend: str
    build: Callable[[CredentialAccess], BrowserBackend]


@dataclass(frozen=True)
class OnboardingStep:
    """A first-run step an extension contributes to workspace onboarding. `handler` runs once, after
    the core steps, with the extension's scoped ExtensionContext — the same handle its jobs receive,
    so a step can seed the extension's store or read a credential slot it declared."""

    name: str
    handler: Callable[[ExtensionContext], Awaitable[None]]


HookEvent = Literal["pre_tool_use", "post_tool_use", "on_inbound"]


@dataclass(frozen=True)
class PreToolUse:
    """A tool call about to dispatch. `tool_input` is the validated argument model; a hook may Deny
    the call (it never dispatches) or ModifyInput (fold the args the handler receives)."""

    tool_name: str
    tool_input: BaseModel


@dataclass(frozen=True)
class PostToolUse:
    """A tool call that dispatched, including the error path (`is_error`). A hook may ModifyOutput
    (replace the result the model sees) or InjectContext (append guidance to it)."""

    tool_name: str
    tool_input: BaseModel
    output: str
    is_error: bool


@dataclass(frozen=True)
class OnInbound:
    """A member message opening a turn. A hook may Deny it (the turn refuses) or InjectContext
    (append to the turn's system context — the generalized recall-injection point)."""

    text: str


HookPayload = PreToolUse | PostToolUse | OnInbound


@dataclass(frozen=True)
class Deny:
    """Refuse the pending act — a pre_tool_use call or an on_inbound turn. The reason surfaces to
    the member as the terminal frame; a tool Deny is the is_error result the model recovers from.
    Narrow-only: Deny cannot admit a tool grants withheld, it only refuses one already admitted."""

    reason: str


@dataclass(frozen=True)
class ModifyInput:
    """Replace the argument model a pre_tool_use call dispatches with (pre_tool_use only)."""

    tool_input: BaseModel


@dataclass(frozen=True)
class ModifyOutput:
    """Replace the result a post_tool_use call returns to the model (post_tool_use only)."""

    output: str


@dataclass(frozen=True)
class InjectContext:
    """Append text to the turn's context — the system prompt on on_inbound, the tool result on
    post_tool_use (on_inbound and post_tool_use only)."""

    text: str


HookOutcome = Deny | ModifyInput | ModifyOutput | InjectContext | None


@dataclass(frozen=True)
class HookContext:
    """What a hook handler receives: the same workspace-scoped `ExtensionContext` a job or route
    gets (its store, declared credential slots, memory), the frozen turn and agent it fires under,
    the conversation's member, and the per-event payload. Deliberately no raw SandboxSession,
    ToolContext, DB handle, spawn, admit, or invoke — a hook observes and filters, it cannot act
    outside its scope or fire work that would re-enter the loop it runs inside."""

    ext: ExtensionContext
    turn: Turn
    agent: Agent
    member_id: UUID | None
    payload: HookPayload


@dataclass(frozen=True)
class HookSpec:
    """One reactive lifecycle hook. `handler` runs with the extension's scoped context on `event`;
    for the `*_tool_use` events `tools` matches by tool name (empty = every tool). A hook is a
    runtime policy filter over the turn's granted tools, never a second grant path."""

    event: HookEvent
    handler: Callable[[HookContext], Awaitable[HookOutcome]]
    tools: tuple[str, ...] = ()


SUBAGENT_ROUND_LIMIT = 50


@dataclass(frozen=True)
class SubagentProfile:
    """A typed subagent an extension registers. `prompt` is the child's own instructions and
    `tool_names` the subset of the turn's tool set the child may call; a spawn validates its payload
    against `input_model` and its final answer against `output_model`. The loader collects every
    manifest's profiles into the SubagentRegistry `spawn_subagent` dispatches against. `max_rounds`
    caps the child's agentic tool-use rounds; on exhaustion it produces a best-effort final answer
    rather than failing, so a runaway child never detonates its parent. A deep profile lifts it to
    the main ceiling; the default suits an ordinary focused subagent. `model` runs the child under
    a model distinct from its parent — possibly a different provider — while `None` inherits the
    parent's; a spawn resolves and bills the child under whichever model answers it."""

    name: str
    prompt: str
    tool_names: tuple[str, ...]
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    max_rounds: int = SUBAGENT_ROUND_LIMIT
    model: str | None = None


@dataclass(frozen=True)
class SkillSpec:
    """A skill a pack contributes: the directory holding its `SKILL.md` and any bundled scripts and
    assets. The loader parses each into the skill registry `load_skill` and the `{{skill_index}}`
    consult, so a pack's workflow joins the loadable set beside core's own; its files mount into
    the sandbox under `.skills/<name>/` exactly as a core skill's do. `path` is resolved by the
    extension against its own package (`Path(__file__).parent / "skills" / <name>`), so the content
    ships and is digested with the extension."""

    path: Path


@dataclass(frozen=True)
class Manifest:
    """What one extension declares, returned by its `selfhost.extension` entry point."""

    name: str
    version: str
    _: KW_ONLY
    tools: tuple[ToolDef, ...] = ()
    jobs: tuple[JobSpec, ...] = ()
    routes: tuple[RouteSpec, ...] = ()
    credentials: tuple[CredentialSlot, ...] = ()
    connectors: tuple[ConnectorProvider, ...] = ()
    sources: tuple[SourceProvider, ...] = ()
    onboarding_steps: tuple[OnboardingStep, ...] = ()
    indexes: tuple[IndexBackendSpec, ...] = ()
    embeds: tuple[EmbedBackendSpec, ...] = ()
    hooks: tuple[HookSpec, ...] = ()
    prompt_sections: tuple[PromptSection, ...] = ()
    subagents: tuple[SubagentProfile, ...] = ()
    surfaces: tuple[SurfaceSpec, ...] = ()
    models: tuple[ModelProviderSpec, ...] = ()
    hubs: tuple[HubSpec, ...] = ()
    skills: tuple[SkillSpec, ...] = ()
    browsers: tuple[BrowserBackendSpec, ...] = ()
    carriers: tuple[CarrierSpec, ...] = ()


@dataclass(frozen=True)
class Pack:
    """What one pack declares, returned by its `selfhost.pack` entry point. A pack is a workspace
    member under `packs/<name>/` that names the installed extensions it bundles and, through the
    same fields an extension manifest carries, any pack-level skills and onboarding steps of its
    own. A deploy names the active pack in `[pack] name`; activating it narrows the deploy to
    exactly the bundled extensions' manifests plus one manifest of the pack's own contributions, so
    the pack fully determines a coherent product config. `extensions` are manifest names resolved
    against the installed set — one naming an uninstalled extension fails loud at boot."""

    name: str
    version: str
    _: KW_ONLY
    extensions: tuple[str, ...] = ()
    skills: tuple[SkillSpec, ...] = ()
    onboarding_steps: tuple[OnboardingStep, ...] = ()
