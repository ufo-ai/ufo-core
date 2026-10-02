"""What an extension declares (the Manifest) and what a pack declares (the Pack): the frozen value
objects the loader reads and derives from, never registration APIs it calls.

An extension's entry point returns a Manifest — a bundle of declared points a core subsystem
consumes: tools enter the turn's registry, routes mount under the app, jobs register on the
scheduler, credential slots drive proxy injection, hooks fire on turn-lifecycle events as a runtime
policy filter over the tools grants already admit. A pack's entry point returns a Pack — the set of
installed extensions it bundles plus any pack-level skills and onboarding steps of its own — so
activating one named pack brings a coherent product config up together."""

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import KW_ONLY, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import UUID

from openfeature.provider import FeatureProvider
from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import Response

from ufo.blob import BlobStore
from ufo.browser import CdpProvider
from ufo.harness.models.spec import ModelSpec
from ufo.harness.o11y import MetricSpec
from ufo.harness.sandbox.session import Carrier, Sandbox
from ufo.harness.sandbox.terminal import TerminalTransport
from ufo.product import CensusSpec
from ufo.runtime.access.connectors import (
    AuthProxy,
    CliCredential,
    ConnectorBroker,
    ConnectorResolver,
)
from ufo.runtime.access.credentials import DeclaredSlot, HostChoice
from ufo.runtime.access.grants import ConnectionRecorded, OAuthProvider, OAuthProviderResolver
from ufo.runtime.billing.spend import SpendGateSpec
from ufo.runtime.candidates import WorkspaceCandidates
from ufo.runtime.ext.context import CredentialAccess, ExtensionContext
from ufo.runtime.ext.conversation_slots import (
    PORTAL_ICONS,
    SUPPORTED_CONVERSATION_SLOT_PAYLOADS,
    ConversationSlotProvider,
)
from ufo.runtime.ext.deploy import CommandSpec, DeployRouteSpec
from ufo.runtime.ext.operator import OperatorRuleSpec
from ufo.runtime.ext.surface import SurfaceSpec
from ufo.runtime.hub import Hub, SourceRef
from ufo.runtime.indexing import EmbedClient, IndexBackend
from ufo.runtime.kinds.agent_setup import AgentSetup
from ufo.runtime.kinds.agents import AgentSpec
from ufo.runtime.memory import MemorySearchProvider
from ufo.runtime.object_scope import ObjectActionRequestTarget, ObjectActionTarget
from ufo.runtime.objects import ObjectKind
from ufo.runtime.provisioning import WorkspaceFoundedSpec
from ufo.runtime.search import SearchProvider
from ufo.runtime.seats import MemberAddedSpec
from ufo.runtime.skills.runtime import RuntimeSkill, SkillCard
from ufo.runtime.sources.sync import PageChange, SourceBackend
from ufo.runtime.tools.registry import OBJECT_ACTION_TOOL, ToolDef
from ufo.runtime.turns.audience import SHARED_AUDIENCE, Audience
from ufo.runtime.turns.delivery_register import SUBAGENT_RESULT_DESCRIPTION
from ufo.schema.records import (
    TABLER_ICON_MAX_LENGTH,
    TABLER_ICON_PATTERN,
    Agent,
    ReasoningEffort,
    Turn,
)

if TYPE_CHECKING:
    from ufo.runtime.context_boundary import BoundaryInputs, ContextBoundary


@dataclass(frozen=True)
class InjectionTarget:
    """The wire-injection descriptor a credential slot may carry: on the wire to `host`, the proxy
    swaps the `sentinel` value of `header` for the real secret, per workspace and only for a live
    turn. A set `dimension` also meters the host.

    `env` is the sandbox variable the sentinel is exported as, so the agent's own HTTP client
    authenticates the provider by sending it as ordinary auth — the GitHub `GH_TOKEN` pattern, for a
    key this deploy holds rather than a broker. `host` is a fixed hostname, or a `HostChoice` for a
    provider that pins its API host per account (a Datadog site, an OpsGenie region): the member
    selects from the closed set the declaration offers, so what reaches the wire is always a literal
    the row wrote. Two slots naming one host each inject their own header, which is how a provider
    taking more than one key on the wire is expressed."""

    host: str | HostChoice
    header: str
    sentinel: str
    env: str | None = None
    dimension: str | None = None


@dataclass(frozen=True)
class WorkspaceCredentials:
    """Credential slots an extension resolves per workspace, beside the ones its manifest declares
    for the whole deploy. `read` answers one workspace's slots from the extension's own rows, and
    core hands them to exactly the code a manifest's slot already drives: the egress proxy's
    injection, the sandbox's exported sentinel, the `credential` object kind, and the portal panel.

    The host such a slot names is written inside the workspace rather than by this deploy's own
    code, so the scope core derives for it is pinned: the proxy resolves the name and refuses the
    CONNECT when it answers a private address, which an exact scope otherwise skips."""

    read: Callable[[ExtensionContext, UUID], Awaitable[tuple["CredentialSlot", ...]]]


@dataclass(frozen=True)
class CredentialSlot:
    """A named secret an extension needs. With an InjectionTarget the proxy swaps it onto the
    wire so the sandbox never holds it; without one it is readable only in-process.

    A slot holds one secret, and its fill state is the whole of what a read can say."""

    name: str
    description: str
    injection: InjectionTarget | None = None


JOB_FAULT_MAX_CHARS = 500


class JobFault(RuntimeError):
    """A job handler raises this when its work failed for a reason the handler itself can name.
    `reason` reaches `jobs.failed` as the record's `fault`, bounded to `JOB_FAULT_MAX_CHARS`, and
    no other exception puts anything there.

    A stack says where a job died and never why. `formatted_stack` carries frames and classes and
    no exception message, because a message is text this process did not write — a sandbox
    command's stderr arrives as a `RuntimeError` carrying the run token the sandbox echoed into
    `HTTP_PROXY`, and a field name is all redaction matches — so a provider outage, a revoked
    token, and a bug leave one indistinguishable record and the answer lives only in a pod's
    stderr. This is the seam that closes that, and it closes it the way `StreamFault` does for a
    source stream: the handler authored the text against the call it made, so it names the status
    and the request without carrying the provider's payload."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class JobSpec:
    """Recurring or one-shot background work. `schedule` is a cron string (6 fields, seconds first)
    for recurring jobs, or None to fire once at boot; `handler` runs with the extension's scoped
    ExtensionContext, never a raw handle. `candidates` names the workspaces this job has work in —
    the dispatcher binds each with `with ws(...)` and runs `handler` scoped to it, so a handler
    never runs unbound and never fans the fleet itself. It is required and has no fleet-wide value:
    an extension declares it through `owner_candidates` (a per-tick builder of a select over its
    own tables projecting distinct `workspace_id`, run for it under the one cross-workspace read),
    so a job that would fire across every workspace regardless of work cannot be expressed.

    `needs_deploy_model` keeps this job's `ctx.model` on `models.auto_model` rather than the cheaper
    `models.background_jobs_model` every other job's seam resolves through. A job declares it when
    one of its calls carries a payload only the deploy default's context window holds — a whole
    archived transcript, say, which was compacted against that window and which nothing bounds
    again.

    `spends` drops from `candidates` every workspace the deploy's spend gates hold, so a job whose
    work costs money never opens one. The extension declares that its work spends; the gates own
    which workspaces are held, and core the own-key exemption. A job that fires into a held
    workspace does not merely waste the tick — a scheduled task advances past its occurrence on any
    fire, so the run is lost rather than deferred."""

    name: str
    schedule: str | None
    handler: Callable[[ExtensionContext], Awaitable[None]]
    candidates: WorkspaceCandidates
    needs_deploy_model: bool = False
    spends: bool = False


@dataclass(frozen=True)
class RouteSpec:
    """An HTTP endpoint an extension serves. The app mounts `handler` for `method` at
    `/ext/<name>/<path>`; each request is handed the extension's scoped ExtensionContext and the
    incoming Request, and the handler returns the Response. `identify` verifies and returns a
    request's workspace, which core binds for the handler; it is required because the shared fleet
    is the only runtime and every request must resolve its workspace before touching any data."""

    method: Literal["GET", "POST"]
    path: str
    handler: Callable[[ExtensionContext, Request], Awaitable[Response]]
    identify: Callable[[Request], UUID | None]


@dataclass(frozen=True)
class ConnectorProvider:
    """One connector an extension registers: the OAuth descriptor behind `/connect` for this
    provider, the member-facing `label` the discovery tool lists it by, the `broker` serving its
    catalog, server-side execution, and feed-sync credential, and any fixed agent tools of its own.
    The descriptor's `provider` keys both the connect-flow registry and the `ConnectorRegistry`
    `serve` builds; its `host` is the one the derived grant admits, injects, and meters at the
    egress proxy. `transfer_hosts` are the broker's own file-store hosts a grant for this provider
    additionally admits and meters (tunnelled, never injected), so the sandbox itself fetches a
    tool's presigned file outputs and stages its file inputs — bytes never cross the serve
    process. The tools join the turn's tool set scoped to the extension, so a connector call
    reaches the provider host only for an agent the grant covers. `cli` authenticates the
    provider's CLI at the egress proxy: the sandbox exports its env var with the grant's sentinel
    and the proxy forwards a matching request through the broker under the granted account."""

    oauth: OAuthProvider
    label: str
    broker: ConnectorBroker
    tools: tuple[ToolDef, ...] = ()
    transfer_hosts: tuple[str, ...] = ()
    cli: CliCredential | None = None


class OpenConnectorNamespace(OAuthProviderResolver, ConnectorResolver, Protocol):
    """An extension's open connector namespace, declared once through `Manifest.connector_resolver`:
    the broker serves any provider slug it brokers without registering each as an explicit
    `ConnectorProvider`. It is both halves of the seam — the connect flow resolves a slug's OAuth
    descriptor through it (`claims`/`descriptor`), and the connector registry resolves the slug's
    routing entry and searches its live service catalog through it (`entry`/`catalog`). The connect
    flow and the registry gate the closed set of explicitly registered connectors first, so a
    namespace only serves the slugs no `ConnectorProvider` claimed."""


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
class WorkspaceFact:
    """One capability this workspace already holds, stated in the system prompt only where its own
    read says so. A `PromptSection` is the same text for every workspace; this is the conditional
    half — a fact rather than a rule, so it is one `line` of data and never a paragraph an extension
    writes.

    `holds` runs at turn assembly under the bound workspace, with the extension's own scoped
    context, so an extension answers for the leg its own connect act writes and core names no
    provider. Core renders every true line as one block and states once, for all of them, that the
    workspace already holds them — which is what keeps a second capability from repeating a first
    one's prose."""

    name: str
    line: str
    holds: Callable[[ExtensionContext], Awaitable[bool]]


@dataclass(frozen=True)
class CarrierSpec:
    """One sandbox backend an extension registers: the `name` the `[sandbox] backend` config selects
    and the `factory` `serve` calls once to build the Carrier. Core ships `docker` and `e2b`; an
    extension contributes another (a remote executor, a pooled or bring-your-own backend) without
    core naming it, and a name that collides with a built-in or another extension's fails loud at
    boot. The selected carrier is held for the process's life as `Runtime.carrier`. `off_cluster`
    marks a backend whose sandbox runs outside the serve pod's network (e2b) and so cannot reach
    the in-pod egress proxy over a host-local address: selecting one with no `[sandbox]
    proxy_public_url` fails loud at boot, since its sandbox would otherwise egress open and
    unmetered."""

    name: str
    factory: Callable[[], Carrier]
    off_cluster: bool = False
    sizes: tuple[str, ...] = ()
    """The sandbox sizes this backend provisions (`SANDBOX_SIZES` for one template per size, empty
    for a single-shape backend) — what decides whether a portal offers the agent's `sandbox_size`
    setting on this deploy."""
    runtime_digest: Callable[[], str] | None = None
    """A digest of carrier-owned runtime inputs absent from core config, such as a remote
    provider's published template references."""


@dataclass(frozen=True)
class SourceProvider:
    """One content-source backend an extension registers: the `backend` name that keys its `source`
    rows and the factory that builds the `SourceBackend` with access to that extension's declared
    credential slots. `serve` sources these into the driver's backend map, so a row with this
    backend name syncs through this backend and its pages land in memory via the derivation pipeline
    exactly as the core folder source's do. A source row is created in chat through
    `ExtensionContext.register_source`."""

    backend: str
    build: Callable[[CredentialAccess], SourceBackend]


@dataclass(frozen=True)
class IndexBackendSpec:
    """One vector-index backend an extension registers: the `name` a deploy selects it by (config
    `memory.index_backend`, default `"default"`) and the `factory` core calls at boot to build the
    `IndexBackend`, given the extension's workspace-scoped `ExtensionContext` — from which it opens
    transactions for a table-owning backend or reads a BYOK key through `context.credentials`. The
    index runs in the jobs/serve role, never in the sandbox,
    so a BYOK backend reads its key in-process rather than through the egress proxy. Every deploy
    ships the base-pinned `index_default` extension registering name `"default"` (SQLite FTS5 +
    local cosine, Postgres tsvector + pgvector), which core resolves when the knob is unset."""

    name: str
    factory: Callable[[ExtensionContext], IndexBackend]


@dataclass(frozen=True)
class EmbedBackendSpec:
    """One embedding backend an extension registers, mirroring `IndexBackendSpec`: the `name` a
    deploy selects it by (config `memory.embed_backend`, default `"default"`) and the `factory` core
    calls once at boot to build the `EmbedClient`, given the extension's workspace-scoped
    `ExtensionContext` (from which it reads its provider key through `context.credentials`). The
    embed client runs in the jobs/serve role on the deploy key, never through the sandbox proxy.
    Every deploy ships the base-pinned `embed_openai` extension registering name `"default"`, which
    core resolves when the config knob is unset."""

    name: str
    factory: Callable[[ExtensionContext], EmbedClient]


@dataclass(frozen=True)
class HubSpec:
    """A live-frame hub backend an extension registers. `backend` is the name `config.hub.backend`
    selects it by; `build` constructs the process-wide Hub from `config.hub.url`, called once at
    boot and only when this backend is selected. A shared (cross-process) backend lifts core's
    single-instance boot guard, so scale-out is an extension, not a core change."""

    backend: str
    build: Callable[[str | None], Hub]


@dataclass(frozen=True)
class TerminalTransportSpec:
    """A terminal-rendezvous transport an extension registers, mirroring `HubSpec`. `backend` is
    the name `config.terminal.backend` selects it by; `build` constructs the process-wide
    `TerminalTransport` from `config.hub.url` (reused, so the fleet's one Redis serves both hub and
    terminal) and the deploy's `BlobStore` (the op's copy-in body and any large copy-out reply ride
    the blob, never Redis), called once at boot and only when this backend is selected. Core ships
    the in-process transport; a cross-pod backend is what makes terminals work on a shared fleet
    where the held connection and the turn's workflow land on different pods."""

    backend: str
    build: Callable[[str | None, BlobStore], TerminalTransport]


@dataclass(frozen=True)
class AuthProxySpec:
    """One auth-proxy backend an extension registers, mirroring `CdpProviderSpec`: the `backend`
    name a deploy selects it by (`config.connectors.auth_backend`) when several are installed and
    the `build` core calls once at boot, given a credential reader scoped to this manifest's slots.
    A sole backend is automatic. The selected backend is the `ConnectorRegistry`'s fallback for
    providers no installed broker claims — the direct/BYOK backend reads a member-added provider
    key through that reader host-side and returns a bearer; a brokered provider resolves through
    its own broker's `credential` instead, never this seam."""

    backend: str
    build: Callable[[CredentialAccess], AuthProxy]


@dataclass(frozen=True)
class FlagSpec:
    """A flag an extension reads, declared by the extension that reads it — or, for a key core
    reads, by `ufo.runtime.context_boundary.CORE_FLAGS`, since the extension a core read selects is
    one a pack can leave out.

    The declaration is the set each hosted environment's terraform must declare, held to it by a
    gate: a key the code reads and the flag service does not hold evaluates to its call-site default
    forever, which on a screen looks exactly like a state somebody chose. `what` states what turning
    it on offers, in one line, because whoever decides that is reading the terraform, not this.
    `open` is what the `open` flag backend — the one a dev or eval stack selects to have the whole
    product without a flag service — serves for this key: on for a flag that offers a withheld
    feature, which is every flag but one, and off for a flag that selects between two shapes the
    fleet already serves one of, so such a stack lands on the fleet's shape."""

    key: str
    what: str
    open: bool = True


@dataclass(frozen=True)
class FlagProviderSpec:
    """A feature-flag provider an extension registers, selected by `config.flags.backend`. `backend`
    is the name; `build` constructs the process-wide OpenFeature provider once at boot, only when
    selected, from `config.flags.cache_ttl_seconds` — the window it may answer a flag out of its own
    response cache — and the flags core and every active extension declared, keyed by flag key, for
    a backend that answers from declarations rather than a service. Core ships no backend and reads
    every flag through `ufo.flags.flag_enabled`, so a deploy swaps providers with a config line.

    `build` returns None when the deploy carries no credential for the backend. Flags then resolve
    to the default each call site passes rather than failing the boot: a flag says whether a feature
    is offered, and a deploy that cannot read one offers what its code defaults to."""

    backend: str
    build: Callable[[float, Mapping[str, FlagSpec]], FeatureProvider | None]


@dataclass(frozen=True)
class CdpProviderSpec:
    """A cdp provider an extension registers, selected by `config.browser.cdp_provider`. `backend`
    is the name; `build` constructs the process-wide CdpProvider once at boot, only when selected,
    given a credential reader scoped to this manifest's slots (a remote provider reads its key
    in-process, host-side, never in the sandbox). Core ships no provider: the `sandbox_chrome`
    extension leases CDP from the turn's own sandbox, and the `browserbase` extension mints a hosted
    session per browser run. The one BUA engine (the browser extension) connects whatever endpoint
    the selected provider's per-turn lease yields, and reaches a workspace file through that lease's
    `place_file` — only the transport is configurable, never the engine."""

    backend: str
    build: Callable[[CredentialAccess], CdpProvider]


@dataclass(frozen=True)
class ContextBoundarySpec:
    """A context-boundary strategy, selected by `config.context.strategy`. `strategy` is the name;
    `build` constructs the boundary once per turn from the `BoundaryInputs` the loop hands it;
    `tools` names the tools this strategy implements — its own extension's and the builtins it
    speaks to — and the turn's registry drops every tool another registered strategy claims, so a
    deploy never offers one its boundary cannot honor. `prompt` is the `{{context_window}}` block
    the system prompt carries: the words that tell the agent what happens at the boundary and which
    tools it has there, so the prose belongs to the strategy that owns the behavior rather than to
    the shell. Core registers no strategy of its own; `context_rollover` and `context_compact`
    each ship one at this seam, and exactly one is active per deploy, so a third strategy replaces
    them rather than running beside them."""

    strategy: str
    build: "Callable[[BoundaryInputs], ContextBoundary]"
    tools: tuple[str, ...] = ()
    prompt: str = ""


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
    "connection_recorded",
]
"""The lifecycle events a hook binds to, named after Claude Code's taxonomy. Seven fire on the turn
loop; `page_change` is the data-plane event the core page-change runner fires off the source-page
feed; `connection_recorded` is the control-plane event the connect flow fires as a member's provider
connection lands, so the state a connection implies is created with it rather than noticed later.
Claude Code names further events (`session_start`, `session_end`, `permission_request`,
`subagent_stop`, `notification`) that this system has no producer for — no session, interactive
permission prompt, or parent-side subagent boundary — so they are deliberately not members here: a
declared event with no fire-point is exactly the crippling this taxonomy avoids. Each gains a member
the change that lands its real fire-point and a consumer together."""


@dataclass(frozen=True)
class PreToolUse:
    """A tool call about to dispatch. `tool_input` is the validated argument model; a hook may Deny
    the call (it never dispatches) or ModifyInput (fold the args the handler receives). `call` is
    the semantic identity selectors match — the canonical action id for an object action riding
    the `object_action` wire tool, the tool name itself otherwise — and `target` is its validated,
    unresolved wire target."""

    tool_name: str
    tool_input: BaseModel
    call: str = ""
    target: ObjectActionRequestTarget | None = None

    def __post_init__(self) -> None:
        if not self.call:
            object.__setattr__(self, "call", self.tool_name)


@dataclass(frozen=True)
class PostToolUse:
    """A tool call that dispatched successfully. A hook may ModifyOutput (replace the result the
    model sees) or InjectContext (append guidance to it). A call that errored fires
    post_tool_use_failure instead, never this. `call` carries the semantic identity and `target`
    is the resolved object the handler acted on."""

    tool_name: str
    tool_input: BaseModel
    output: str
    call: str = ""
    target: ObjectActionTarget | None = None

    def __post_init__(self) -> None:
        if not self.call:
            object.__setattr__(self, "call", self.tool_name)


@dataclass(frozen=True)
class PostToolUseFailure:
    """A dispatched tool call that failed — the observe counterpart to post_tool_use. Fires only for
    a tool that actually ran and errored (its handler raised, or returned an error result); a bad
    tool name or an argument-validation failure is caught before dispatch and becomes an is_error
    result with no hook, so this never fires for a call that never ran. `output` is the error
    content the model will see; the event takes no outcome, it only notifies. `call` carries the
    semantic identity and `target` is the resolved object the handler acted on."""

    tool_name: str
    tool_input: BaseModel
    output: str
    call: str = ""
    target: ObjectActionTarget | None = None

    def __post_init__(self) -> None:
        if not self.call:
            object.__setattr__(self, "call", self.tool_name)


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
    """The turn's context window is about to be reset to a recovery record. `reason` is `auto` when
    the window crossed the line or the agent asked, `force` when a provider overflow forced it;
    `before_tokens` is the window estimate before the reset. Observe-only, fired only when the reset
    will occur."""

    reason: Literal["auto", "force"]
    before_tokens: int


@dataclass(frozen=True)
class PostCompact:
    """The turn's context window has just been reset. `record` is the recovery record the fresh
    window opened with; `before_tokens`/`after_tokens` bracket the window it replaced.
    Observe-only."""

    record: str
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


PAGE_CHANGE_CURSOR_KEY = "page_change_cursor"
"""The prefix the runner keys a `page_change` consumer's cursor by inside the declaring extension's
own ScopedStore, `{prefix}:{handler name}`. It sits with the payload rather than with the runner
because an extension sending its own consumer back over pages it already drained clears that key,
and the format has one home whichever side reads it."""


HookPayload = (
    PreToolUse
    | PostToolUse
    | PostToolUseFailure
    | UserPromptSubmit
    | Stop
    | PreCompact
    | PostCompact
    | PageChangeBatch
    | ConnectionRecorded
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
    """Append text to the turn's context — after the submitted message in the model context on
    user_prompt_submit, the tool result on post_tool_use (user_prompt_submit and post_tool_use
    only). `sources` names what the injection was drawn from, for the live surface to draw."""

    text: str
    sources: tuple[SourceRef, ...] = ()


HookOutcome = Deny | ModifyInput | ModifyOutput | InjectContext | None


@dataclass(frozen=True)
class HookContext:
    """What a hook handler receives: the same workspace-scoped `ExtensionContext` a job or route
    gets (its store, declared credential slots, memory) and the per-event payload. A turn-lifecycle
    event carries the frozen turn and agent it fires under and its speaking member; the exact
    conversation audience is on `ext`. Its
    context deliberately wires no model or invoker — inside the loop a hook observes and filters,
    never firing work that would re-enter the turn it runs within. A data-plane event (page_change)
    fires outside any turn — its turn/member fields are None — and the page-change runner
    builds its context in the jobs role with the metered off-turn model and the admit-turn invoker
    wired: a listener classifies and invokes exactly as a scheduled fire does, fed only by the
    source pipeline so it can never fire on work it caused. A control-plane event
    (connection_recorded) fires outside any turn as well — inside the request that completed the
    OAuth handoff, with that workspace bound and the connection already committed.

    `sandbox` is the turn's own `/workspace`, so a hook that must read the tree the turn works in
    reads what the turn's own tools do rather than opening a session of its own. The engine passes
    it at every fire point of a turn; an event fired without one — the compaction events an
    extension fires, and every event outside a turn — leaves it None."""

    ext: ExtensionContext
    payload: HookPayload
    turn: Turn | None = None
    agent: Agent | None = None
    audience: Audience = SHARED_AUDIENCE
    speaker_member_id: UUID | None = None
    sandbox: Sandbox | None = None


@dataclass(frozen=True)
class HookSpec:
    """One reactive lifecycle hook. `handler` runs with the extension's scoped context on `event`;
    for the `*_tool_use` events `tools` matches the payload's semantic `call` — a global tool's
    name, or a bound action's canonical `action:<kind>:<name>` id (empty = every call). A hook is a
    runtime policy filter over the turn's granted tools, never a second grant path.

    A best-effort user-prompt hook may still return Deny, but a handler fault drops only its
    injection instead of denying the turn. No tool gate can fail open. `page_change_failure_scope`
    selects whether the runner narrows a failed batch at once or retries it whole before bounded
    page-by-page recovery."""

    event: HookEvent
    handler: Callable[[HookContext], Awaitable[HookOutcome]]
    tools: tuple[str, ...] = ()
    best_effort: bool = False
    page_change_failure_scope: Literal["page", "batch"] = "page"

    def __post_init__(self) -> None:
        if self.best_effort and self.event != "user_prompt_submit":
            raise ValueError("only user_prompt_submit hooks may be best effort")
        if self.page_change_failure_scope == "batch" and self.event != "page_change":
            raise ValueError("only page_change hooks may hold a failed batch")


AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}[a-z0-9]$")
AGENT_ICON_RE = re.compile(rf"^{TABLER_ICON_PATTERN}$")
SETUP_TOOLS = ("load_skill", "connect_account", "object_apply")


@dataclass(frozen=True)
class AgentProvision:
    """A durable workspace agent an extension ships. Activation creates the ordinary `agent` row and
    stops owning it: the workspace's copy is the live configuration, and no later version of this
    extension writes it again. The row records the version that created it.
    `tools` None is the member-facing tool set; a tuple is an allowlist,
    intersected with the live registry at turn load so an inactive extension contributes nothing.
    It sits here rather than on `AgentSpec` because the spec is what `object_apply agent` writes: an
    allowlist may name a primitive held back from ordinary turns, so it is shipped, never typed.
    `setup` names the grants a member must still make for the agent to work, and what the
    main agent should do to obtain them.
    `icon` names the portal mark the created row draws — a tabler outline slug or one of the
    portal's own pack, never a URL or markup; unset, the row is dealt one from the pack.
    `spec.purpose` is required here and nowhere else: an agent a member built has its author to
    ask, and a shipped one has nobody.
    `main` applies the provision to the workspace's main agent instead of creating another row."""

    name: str
    spec: AgentSpec
    tools: tuple[str, ...] | None = None
    setup: AgentSetup = field(default_factory=AgentSetup)
    icon: str | None = None
    main: bool = False

    def __post_init__(self) -> None:
        if not AGENT_NAME_RE.match(self.name):
            raise ValueError(f"agent provision name {self.name!r} is not an object name")
        if self.tools is not None and OBJECT_ACTION_TOOL in self.tools:
            raise ValueError(
                f"agent provision {self.name!r} allowlists {OBJECT_ACTION_TOOL!r} — an allowlist "
                "grants canonical action ids (action:<kind>:<name>), never the dispatcher"
            )
        if self.icon is not None and (
            len(self.icon) > TABLER_ICON_MAX_LENGTH or not AGENT_ICON_RE.match(self.icon)
        ):
            raise ValueError(f"agent provision {self.name!r} icon {self.icon!r} names no mark")
        if not (self.spec.prompt or "").strip():
            raise ValueError(f"agent provision {self.name!r} has no prompt")
        if not (self.spec.purpose or "").strip():
            raise ValueError(
                f"agent provision {self.name!r} has no purpose — a member meets a shipped agent "
                "with nobody to ask what it is for, so the one sentence that says it ships with it"
            )
        if not self.setup.connectors or self.tools is None:
            return
        if missing := [name for name in SETUP_TOOLS if name not in self.tools]:
            raise ValueError(
                f"agent provision {self.name!r} declares setup but its allowlist omits "
                f"{', '.join(missing)} — an agent asked to obtain its own grants must hold the "
                "verbs that obtain them"
            )


SUBAGENT_ROUND_LIMIT = 50


@dataclass(frozen=True)
class SubagentProfile:
    """A typed subagent an extension registers. `prompt` is the child's own instructions and
    `tool_names` the subset of the turn's tool set the child may call; a spawn validates its payload
    against `input_model`, and the child ends its turn by calling the engine's finish tool, whose
    input schema is `output_model` — the terminal a spawn validates is schema-shaped by
    construction. The loader collects every
    manifest's profiles into the SubagentRegistry `spawn` dispatches against. `max_rounds`
    caps the child's agentic tool-use rounds; on exhaustion a forced finish call produces a
    best-effort, schema-shaped final answer — the turn fails only if that forced call still
    violates the schema, and the parent receives a failure as a tool error, never a crash. A deep
    profile lifts it to
    the main ceiling; the default suits an ordinary focused subagent. `models` runs the child under
    an ordered route distinct from its parent; an empty route inherits the parent's model. Before
    the final model, an entry is available when the speaking member connected its provider account;
    the final model resolves through the workspace or deploy key. A spawn's explicit model replaces
    the route and prefers that member's matching account when connected. `reasoning` does the same
    for the model's reasoning effort. A spawn resolves and bills the child under those settings.
    `untrusted_output` declares the child's answer derives from untrusted content (web pages, third
    parties): every path that returns it to a parent — a foreground spawn, and the
    arrival a background child delivers — walls it as data, exactly as an untrusted tool's own
    result is walled. `concise_parent_handoff` gives a member-facing profile the shared short
    result discipline; machine-readable and workspace-agent results keep their full contract.
    `isolated_tools` makes
    `tool_names` exact by excluding cross-extension grants and subagent defaults."""

    name: str
    prompt: str
    tool_names: tuple[str, ...]
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    max_rounds: int = SUBAGENT_ROUND_LIMIT
    models: tuple[str, ...] = ()
    reasoning: ReasoningEffort | None = None
    untrusted_output: bool = False
    concise_parent_handoff: bool = False
    isolated_tools: bool = False
    connector_read_only: bool = False

    def __post_init__(self) -> None:
        if any(not model for model in self.models) or len(set(self.models)) != len(self.models):
            raise ValueError(f"subagent profile {self.name!r} models must be unique nonempty ids")
        if OBJECT_ACTION_TOOL in self.tool_names:
            raise ValueError(
                f"subagent profile {self.name!r} allowlists {OBJECT_ACTION_TOOL!r} — an "
                "allowlist grants canonical action ids (action:<kind>:<name>), never the "
                "dispatcher"
            )
        fields = self.output_model.model_fields
        concise_contract = (
            tuple(fields) == ("result",)
            and fields["result"].annotation is str
            and fields["result"].description == SUBAGENT_RESULT_DESCRIPTION
        )
        if self.concise_parent_handoff and not concise_contract:
            raise ValueError(
                f"subagent profile {self.name!r} marks a concise parent handoff but its output "
                "does not carry the concise result contract"
            )
        if concise_contract and not self.concise_parent_handoff:
            raise ValueError(
                f"subagent profile {self.name!r} carries the concise result contract but does "
                "not mark a concise parent handoff"
            )


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

    def __post_init__(self) -> None:
        if OBJECT_ACTION_TOOL in self.tool_names:
            raise ValueError(
                f"subagent tool grant for {self.profile!r} names {OBJECT_ACTION_TOOL!r} — a "
                "grant names canonical action ids (action:<kind>:<name>), never the dispatcher"
            )


@dataclass(frozen=True)
class SkillSpec:
    """A skill a pack contributes: the directory holding its `SKILL.md` and any bundled scripts and
    assets. The loader parses each into the skill registry `load_skill` and the `{{skill_index}}`
    consult, so a pack's workflow joins the loadable set beside core's own; its files load under
    `$UFO_HOME/skills/<name>/` exactly as a core skill's do. `path` is resolved by the extension
    against its package (`Path(__file__).parent / "skills" / <name>`), so the content ships and is
    digested with the extension. `flag` withholds this skill and its nested children when it reads
    off or returns no answer for the turn's workspace."""

    path: Path
    flag: str | None = None


@dataclass(frozen=True)
class MemberSkillsSpec:
    """A source of the bound agent's member-authored skills, in three verbs: `cards` answers the
    turn's routing set (name, description, depends, pinned — never file bytes), `materialize`
    parses one named skill's stored files into the `RuntimeSkill` a load resolves, and
    `materialize_all` reads every saved skill whole in one store read — the portal's listing
    projection, where a corrupt row is skipped with a log rather than failing the page. Core calls
    each under the turn's workspace and agent scope with the extension's ExtensionContext and
    merges the cards into that turn's SkillRegistry as its member tier. It is not a boot-time
    registration because member-authored skills are agent state."""

    cards: Callable[[ExtensionContext], Awaitable[tuple[SkillCard, ...]]]
    materialize: Callable[[ExtensionContext, str], Awaitable[RuntimeSkill | None]]
    materialize_all: Callable[[ExtensionContext], Awaitable[tuple[RuntimeSkill, ...]]]


@dataclass(frozen=True)
class MemorySearchProviderSpec:
    """One named memory-search provider built with its extension's scoped context."""

    name: str
    build: Callable[[ExtensionContext], MemorySearchProvider]


@dataclass(frozen=True)
class Manifest:
    """What one extension declares, returned by its `ufo.extension` entry point. `requires`
    names the sub-seams this extension consumes from another (a browser extension `requires` the
    `cdp_providers` seam); `serve` eagerly resolves each at boot and fails loud — naming the
    extension and the seam — if the backend is absent or unkeyed, so a missing dependency stops the
    process at startup rather than on the first tool call. `sandbox_internet` derives metered public
    egress for live turns when the extension's sandbox tools require it. `deploy_keys` names the
    environment keys this extension needs the deploy to carry — a provider key nobody can mint, so
    `init` reports the ones a fresh checkout is missing rather than leaving them to be discovered
    when the first job that needs one raises. `deploy_bearer_env` names the deploy key whose
    value gates this extension's `deploy_routes`, so it is also one of `deploy_keys`.
    `operator_rules` registers who may operate the deploy's operator surfaces; boot builds only the
    rule `[operator] rule` names, and only a first-party distribution may register one."""

    name: str
    version: str
    _: KW_ONLY
    tools: tuple[ToolDef, ...] = ()
    objects: tuple[ObjectKind, ...] = ()
    jobs: tuple[JobSpec, ...] = ()
    routes: tuple[RouteSpec, ...] = ()
    credentials: tuple[CredentialSlot, ...] = ()
    connectors: tuple[ConnectorProvider, ...] = ()
    connector_resolver: OpenConnectorNamespace | None = None
    sources: tuple[SourceProvider, ...] = ()
    onboarding_steps: tuple[OnboardingStep, ...] = ()
    indexes: tuple[IndexBackendSpec, ...] = ()
    embeds: tuple[EmbedBackendSpec, ...] = ()
    hooks: tuple[HookSpec, ...] = ()
    prompt_sections: tuple[PromptSection, ...] = ()
    workspace_facts: tuple[WorkspaceFact, ...] = ()
    agents: tuple[AgentProvision, ...] = ()
    subagents: tuple[SubagentProfile, ...] = ()
    member_skills: MemberSkillsSpec | None = None
    subagent_tool_grants: tuple[SubagentToolGrant, ...] = ()
    surfaces: tuple[SurfaceSpec, ...] = ()
    models: tuple[ModelSpec, ...] = ()
    hubs: tuple[HubSpec, ...] = ()
    terminal_transports: tuple[TerminalTransportSpec, ...] = ()
    skills: tuple[SkillSpec, ...] = ()
    cdp_providers: tuple[CdpProviderSpec, ...] = ()
    carriers: tuple[CarrierSpec, ...] = ()
    auth_proxies: tuple[AuthProxySpec, ...] = ()
    search_providers: tuple[SearchProviderSpec, ...] = ()
    context_boundaries: tuple[ContextBoundarySpec, ...] = ()
    flag_providers: tuple[FlagProviderSpec, ...] = ()
    flags: tuple[FlagSpec, ...] = ()
    memory_search: tuple[MemorySearchProviderSpec, ...] = ()
    conversation_slots: tuple[ConversationSlotProvider, ...] = ()
    workspace_credentials: WorkspaceCredentials | None = None
    member_context_read: bool = False
    sandbox_internet: bool = False
    requires: tuple[str, ...] = field(default_factory=tuple)
    deploy_keys: tuple[str, ...] = field(default_factory=tuple)
    workspace_founded: tuple[WorkspaceFoundedSpec, ...] = ()
    deploy_routes: tuple[DeployRouteSpec, ...] = ()
    deploy_bearer_env: str | None = None
    commands: tuple[CommandSpec, ...] = ()
    metrics: tuple[MetricSpec, ...] = ()
    census: tuple[CensusSpec, ...] = ()
    operator_rules: tuple[OperatorRuleSpec, ...] = ()
    member_added: MemberAddedSpec | None = None
    spend_gates: tuple[SpendGateSpec, ...] = ()


CONVERSATION_SLOT_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def conversation_slot_declarations(
    manifests: tuple[Manifest, ...],
) -> tuple[tuple[Manifest, ConversationSlotProvider], ...]:
    """The deploy's typed conversation-slot providers, validated as one global namespace."""
    declarations: list[tuple[Manifest, ConversationSlotProvider]] = []
    owners: dict[str, str] = {}
    for manifest in manifests:
        for provider in manifest.conversation_slots:
            if CONVERSATION_SLOT_ID.fullmatch(provider.id) is None:
                raise RuntimeError(f"invalid conversation slot id {provider.id!r}")
            if not provider.label.strip() or len(provider.label) > 40:
                raise RuntimeError(f"conversation slot {provider.id!r} has an invalid label")
            if provider.icon not in PORTAL_ICONS:
                raise RuntimeError(f"conversation slot {provider.id!r} has an invalid icon")
            if not callable(provider.summarize) or not callable(provider.read):
                raise RuntimeError(f"conversation slot {provider.id!r} needs read callbacks")
            if provider.content not in SUPPORTED_CONVERSATION_SLOT_PAYLOADS:
                raise RuntimeError(
                    f"conversation slot {provider.id!r} registers unsupported payload "
                    f"{provider.content.__name__!r}"
                )
            prior = owners.get(provider.id)
            if prior is not None:
                raise RuntimeError(
                    f"two extensions register conversation slot {provider.id!r}: "
                    f"{prior!r} and {manifest.name!r}"
                )
            owners[provider.id] = manifest.name
            declarations.append((manifest, provider))
    return tuple(declarations)


def open_connector_namespace(manifests: tuple[Manifest, ...]) -> OpenConnectorNamespace | None:
    """The one open connector namespace across every manifest, or None. Two fail loud: a namespace
    is the catch-all for unregistered slugs, so a second leaves the connect flow, the registry, and
    the egress transfer-host derivation unable to decide which broker owns a slug. Every derivation
    that resolves an unregistered slug routes through here, so the check holds on all at once."""
    found: OpenConnectorNamespace | None = None
    for manifest in manifests:
        if manifest.connector_resolver is None:
            continue
        if found is not None:
            raise RuntimeError("two extensions register an open connector namespace")
        found = manifest.connector_resolver
    return found


@dataclass(frozen=True)
class Pack:
    """What one pack declares, returned by its `ufo.pack` entry point. A pack is a workspace
    member under `packs/<name>/` that names the installed extensions it bundles and, through the
    same fields an extension manifest carries, any pack-level skills, onboarding steps, and prompt
    sections of its own. A deploy names the active pack in `[pack] name`; activating it narrows
    the deploy to
    exactly the bundled extensions' manifests plus one manifest of the pack's own contributions, so
    the pack fully determines a coherent product config. `extensions` are manifest names resolved
    against the installed set — one naming an uninstalled extension fails loud at boot."""

    name: str
    version: str
    _: KW_ONLY
    extensions: tuple[str, ...] = ()
    skills: tuple[SkillSpec, ...] = ()
    onboarding_steps: tuple[OnboardingStep, ...] = ()
    prompt_sections: tuple[PromptSection, ...] = ()


def declared_slot(slot: CredentialSlot, extension: str) -> DeclaredSlot:
    """One declaration as every projection over the declarations reads it — the shape the
    `credential` object kind and the portal's credentials panel render, whether the slot came from
    a manifest or from an extension that resolves its slots per workspace."""
    return DeclaredSlot(
        name=slot.name,
        description=slot.description,
        extension=extension,
        host=None if slot.injection is None else slot.injection.host,
        env="" if slot.injection is None else slot.injection.env or "",
        header="" if slot.injection is None else slot.injection.header,
    )


def declared_slots(manifests: tuple[Manifest, ...]) -> tuple[DeclaredSlot, ...]:
    """Every active manifest's declared BYOK slots, the one assembly every projection over the
    declarations shares — the `credential` object kind and the portal's credentials panel."""
    return tuple(
        declared_slot(slot, manifest.name)
        for manifest in manifests
        for slot in manifest.credentials
    )


class NotRegisteredError(RuntimeError):
    """A backend-selection seam was handed a name no active extension registers. Distinct from the
    other boot-fail-loud RuntimeErrors these seams raise (a name collision, a key-gated factory
    declining) so a caller — the registration-evidence test — asserts on the type, not on a message
    substring a reword could silently drift past."""
