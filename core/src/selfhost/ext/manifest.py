"""What an extension declares (the Manifest) and what a pack declares (the Pack): the frozen value
objects the loader reads and derives from, never registration APIs it calls.

An extension's entry point returns a Manifest — a bundle of declared points a core subsystem
consumes: tools enter the turn's registry, routes mount under the app, jobs register on the
scheduler, credential slots drive proxy injection, hooks fire on turn-lifecycle events as a runtime
policy filter over the tools grants already admit. A pack's entry point returns a Pack — the set of
installed extensions it bundles plus any pack-level skills and onboarding steps of its own — so
activating one named pack brings a coherent product config up together."""

from collections.abc import Awaitable, Callable
from dataclasses import KW_ONLY, dataclass, field
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import Response

from selfhost.accounting import ModelPrice
from selfhost.browser import CdpProvider
from selfhost.connectors import AuthProxy
from selfhost.ext.context import CredentialAccess, ExtensionContext
from selfhost.ext.surface import SurfaceSpec
from selfhost.grants import OAuthProvider
from selfhost.hub import Hub
from selfhost.indexing import EmbedClient, IndexBackend
from selfhost.models.interface import ModelClient
from selfhost.sandbox.session import Carrier
from selfhost.schema.records import Agent, Turn
from selfhost.search import SearchProvider
from selfhost.skills.runtime import RuntimeSkill
from selfhost.sources.sync import PageChange, SourceBackend
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
class AuthProxySpec:
    """One auth-proxy backend an extension registers, mirroring `CdpProviderSpec`: the `backend`
    name a deploy selects it by (`config.connectors.auth_backend`, default `"composio"`) and the
    `build` core calls once at boot, only when selected, given a credential reader scoped to this
    manifest's slots. A broker backend (Composio) needs no slot and returns a proxying transport; a
    direct/BYOK backend reads a member-added provider key through that reader host-side and returns
    a bearer. The selected proxy is threaded onto the sync runner's `SourceAuth`, so a connector
    source resolves its provider credential through it without core minting or holding a token."""

    backend: str
    build: Callable[[CredentialAccess], AuthProxy]


@dataclass(frozen=True)
class CdpProviderSpec:
    """A cdp provider an extension registers, selected by `config.browser.cdp_provider`. `backend`
    is the name; `build` constructs the process-wide CdpProvider once at boot, only when selected,
    given a credential reader scoped to this manifest's slots (a remote provider reads its key
    in-process, host-side, never in the sandbox). Core's default `sandbox-cdp` provider wraps the
    `BROWSER_CDP_URL` endpoint in a static lease; a browserbase extension mints a fresh hosted
    session per turn. The one BUA engine (the browser extension) connects whatever endpoint the
    selected provider's per-turn lease yields — only the transport is configurable, never the
    engine."""

    backend: str
    build: Callable[[CredentialAccess], CdpProvider]


@dataclass(frozen=True)
class SearchProviderSpec:
    """A search provider an extension registers, selected by `config.research.search_provider`.
    `backend` is the name; `build` constructs the process-wide SearchProvider once at boot, only
    when selected, given a credential reader scoped to this manifest's slots — the backend reads its
    BYOK key in-process, host-side, never in the sandbox. Core ships no default: every search
    backend is an extension, and the research extension `requires` this seam so a deploy with
    research active but no search backend fails at boot rather than on the first search."""

    backend: str
    build: Callable[[CredentialAccess], SearchProvider]


@dataclass(frozen=True)
class OnboardingStep:
    """A first-run step an extension contributes to workspace onboarding. `handler` runs once, after
    the core steps, with the extension's scoped ExtensionContext — the same handle its jobs receive,
    so a step can seed the extension's store or read a credential slot it declared."""

    name: str
    handler: Callable[[ExtensionContext], Awaitable[None]]


HookEvent = Literal[
    "pre_tool_use",
    "post_tool_use",
    "post_tool_use_failure",
    "user_prompt_submit",
    "stop",
    "pre_compact",
    "post_compact",
    "page_change",
]
"""The lifecycle events a hook binds to, named after Claude Code's taxonomy. Seven fire on the turn
loop; `page_change` is the data-plane event the core page-change runner fires off the source-page
feed. Claude Code names further events (`session_start`, `session_end`, `permission_request`,
`subagent_stop`, `notification`) that this system has no producer for — no session, interactive
permission prompt, or parent-side subagent boundary — so they are deliberately not members here: a
declared event with no fire-point is exactly the crippling this taxonomy avoids. Each gains a member
the change that lands its real fire-point and a consumer together."""


@dataclass(frozen=True)
class PreToolUse:
    """A tool call about to dispatch. `tool_input` is the validated argument model; a hook may Deny
    the call (it never dispatches) or ModifyInput (fold the args the handler receives)."""

    tool_name: str
    tool_input: BaseModel


@dataclass(frozen=True)
class PostToolUse:
    """A tool call that dispatched successfully. A hook may ModifyOutput (replace the result the
    model sees) or InjectContext (append guidance to it). A call that errored fires
    post_tool_use_failure instead, never this."""

    tool_name: str
    tool_input: BaseModel
    output: str


@dataclass(frozen=True)
class PostToolUseFailure:
    """A dispatched tool call that failed — the observe counterpart to post_tool_use. Fires only for
    a tool that actually ran and errored (its handler raised, or returned an error result); a bad
    tool name or an argument-validation failure is caught before dispatch and becomes an is_error
    result with no hook, so this never fires for a call that never ran. `output` is the error
    content the model will see; the event takes no outcome, it only notifies."""

    tool_name: str
    tool_input: BaseModel
    output: str


@dataclass(frozen=True)
class UserPromptSubmit:
    """A member message opening a turn. A hook may Deny it (the turn refuses) or InjectContext
    (append to the turn's system context — the generalized recall-injection point)."""

    text: str


@dataclass(frozen=True)
class Stop:
    """The agent has produced its final answer and the turn is about to commit. Observe-only — a
    hook sees the answer but cannot alter the terminal outcome."""

    answer: str


@dataclass(frozen=True)
class PreCompact:
    """The turn's context window is about to be compacted (its head summarized). `reason` is `auto`
    when the window crossed the trigger or `force` when a provider overflow forced it;
    `before_tokens` is the pre-compaction window estimate. Observe-only, fired only when compaction
    will occur."""

    reason: Literal["auto", "force"]
    before_tokens: int


@dataclass(frozen=True)
class PostCompact:
    """The turn's context window has just been compacted. `summary` is the condensed history that
    replaced the head; `before_tokens`/`after_tokens` bracket the window it shrank. Observe-only."""

    summary: str
    before_tokens: int
    after_tokens: int


@dataclass(frozen=True)
class PageChangeBatch:
    """A batch of source-page changes the core page-change runner replays to a data-plane hook off
    that extension's own cursor — the generalized data → memory seam. `changes` are the pages
    changed since the extension's cursor (a tombstone carries an empty body); the handler derives
    from each and the runner advances the cursor. Off-turn, so its HookContext carries no turn,
    agent, or member. Observe-only."""

    changes: tuple[PageChange, ...]


HookPayload = (
    PreToolUse
    | PostToolUse
    | PostToolUseFailure
    | UserPromptSubmit
    | Stop
    | PreCompact
    | PostCompact
    | PageChangeBatch
)


@dataclass(frozen=True)
class Deny:
    """Refuse the pending act — a pre_tool_use call or a user_prompt_submit turn. The reason
    surfaces to the member as the terminal frame; a tool Deny is the is_error result the model
    recovers from. Narrow-only: Deny cannot admit a tool grants withheld, it only refuses one
    already admitted."""

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
    """Append text to the turn's context — the system prompt on user_prompt_submit, the tool result
    on post_tool_use (user_prompt_submit and post_tool_use only)."""

    text: str


HookOutcome = Deny | ModifyInput | ModifyOutput | InjectContext | None


@dataclass(frozen=True)
class HookContext:
    """What a hook handler receives: the same workspace-scoped `ExtensionContext` a job or route
    gets (its store, declared credential slots, memory) and the per-event payload. A turn-lifecycle
    event carries the frozen turn and agent it fires under and the conversation's member; a
    data-plane event (page_change) fires outside any turn, so `turn`, `agent`, and `member_id` are
    None and the payload alone carries the event's arguments. Deliberately no raw SandboxSession,
    ToolContext, DB handle, spawn, admit, or invoke — a hook observes and filters, it cannot act
    outside its scope or fire work that would re-enter the loop it runs inside."""

    ext: ExtensionContext
    payload: HookPayload
    turn: Turn | None = None
    agent: Agent | None = None
    member_id: UUID | None = None


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
class SubagentToolGrant:
    """An extension exposing one of its own tools to a subagent profile it does not own. The tool's
    owning extension declares where the tool should appear rather than the profile hard-coding a
    foreign tool name, so a capability attaches its tools to the profiles the research docs list.
    The loader unions every grant onto the target profile's own `tool_names`; a grant only ever
    widens a profile and is add-only. A grant naming a profile or tool that is not installed is
    silently ignored — cross-extension exposure degrades exactly like an absent tool name in
    `tool_names` does, never a boot error, so grantor and target need not ship together. Grants
    affect subagents only; the main agent already dispatches the whole tool set unfiltered. For a
    tool that every subagent should hold, set `ToolDef.subagent_default` instead of granting it to
    each profile by name."""

    profile: str
    tool_names: tuple[str, ...]


@dataclass(frozen=True)
class SkillSpec:
    """A skill a pack contributes: the directory holding its `SKILL.md` and any bundled scripts and
    assets. The loader parses each into the skill registry `load_skill` and the `{{skill_index}}`
    consult, so a pack's workflow joins the loadable set beside core's own; its files mount into
    the sandbox under `.skills/<name>/` exactly as a core skill's do. `path` is resolved by the
    extension against its own package (`Path(__file__).parent / "skills" / <name>`), so the content
    ships and is digested with the extension."""

    path: Path


RuntimeSkillProvider = Callable[[ExtensionContext], Awaitable[tuple[RuntimeSkill, ...]]]
"""A per-turn source of a workspace's runtime skills. Core calls it with the extension's
workspace-scoped ExtensionContext each turn and merges the result into that turn's SkillRegistry,
so an extension can feed member-authored skills into the loadable set beside core's and the packs'
own — never a boot-time registration, since the skills are per-workspace state."""


@dataclass(frozen=True)
class Manifest:
    """What one extension declares, returned by its `selfhost.extension` entry point. `requires`
    names the sub-seams this extension consumes from another (a browser extension `requires` the
    `cdp_providers` seam); `serve` eagerly resolves each at boot and fails loud — naming the
    extension and the seam — if the backend is absent or unkeyed, so a missing dependency stops the
    process at startup rather than on the first tool call."""

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
    runtime_skills: RuntimeSkillProvider | None = None
    subagent_tool_grants: tuple[SubagentToolGrant, ...] = ()
    surfaces: tuple[SurfaceSpec, ...] = ()
    models: tuple[ModelProviderSpec, ...] = ()
    hubs: tuple[HubSpec, ...] = ()
    skills: tuple[SkillSpec, ...] = ()
    cdp_providers: tuple[CdpProviderSpec, ...] = ()
    carriers: tuple[CarrierSpec, ...] = ()
    auth_proxies: tuple[AuthProxySpec, ...] = ()
    search_providers: tuple[SearchProviderSpec, ...] = ()
    requires: tuple[str, ...] = field(default_factory=tuple)


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
