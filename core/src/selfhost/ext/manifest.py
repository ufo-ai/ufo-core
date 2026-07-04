"""What an extension declares: the Manifest and its value objects.

An extension's entry point returns a Manifest — a frozen bundle of declared points the loader reads
and derives from, never a registration API it calls. Each point is a value object a core subsystem
consumes: tools enter the turn's registry, routes mount under the app, jobs register on the
scheduler, credential slots drive proxy injection, hooks fire on turn-lifecycle events as a runtime
policy filter over the tools grants already admit."""

from collections.abc import Awaitable, Callable
from dataclasses import KW_ONLY, dataclass
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import Response

from selfhost.ext.context import CredentialAccess, ExtensionContext
from selfhost.ext.surface import SurfaceSpec
from selfhost.grants import OAuthProvider
from selfhost.memory.embed import EmbedClient
from selfhost.memory.index import IndexBackend
from selfhost.memory.sources import SourceBackend
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
    `memory.index_backend`) and the `factory` core calls at boot to build the `IndexBackend`, given
    the deploy embed client and a credential reader scoped to this manifest's declared slots. The
    index runs in the jobs/serve role, never in the sandbox, so a BYOK backend reads its key from
    the slot in-process rather than through the egress proxy. `serve` layers the named backend over
    the dialect-native default (SQLite FTS5 + local cosine, Postgres tsvector + pgvector)."""

    name: str
    factory: Callable[[EmbedClient, CredentialAccess], IndexBackend]


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
    hooks: tuple[HookSpec, ...] = ()
    prompt_sections: tuple[PromptSection, ...] = ()
    subagents: tuple[SubagentProfile, ...] = ()
    surfaces: tuple[SurfaceSpec, ...] = ()
