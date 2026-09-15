"""Durable turn execution: capacity-claimed queues, the turn workflow, per-process runtime."""

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from typing import Any, Protocol
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOS, DBOSClient, Queue
from pydantic import BaseModel

from ufo.blob import WorkspaceBlobStore
from ufo.browser import CdpProvider
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.harness.models.interface import AUTO_MODEL
from ufo.harness.models.pricing import ModelPrice, Pricing, pricing_from
from ufo.harness.models.registry import MemberAccounts, ModelRegistry, ServingModel
from ufo.harness.o11y import (
    emit_histogram,
    emit_metric,
    formatted_stack,
    log,
    log_error,
    span,
    turn_profile,
    turn_span,
)
from ufo.harness.sandbox.cache import cache_git_config
from ufo.harness.sandbox.conversation import ConversationSandbox
from ufo.harness.sandbox.exec_env import (
    CONVERSATION_ID_ENV,
    GIT_IDENTITY_ENV,
    GIT_PROXY_AUTH_CONFIG,
    _git_config_env,
    _grant_cli_env,
    _keyed_provider_env,
    cli_git_config,
)
from ufo.harness.sandbox.session import (
    RunToken,
    RunTokenCodec,
    Sandbox,
    SandboxProviderUnavailable,
    SandboxSession,
    SystemSkillSeeding,
    _LateSandbox,
)
from ufo.runtime.access.connectors import CliCredential, ConnectorRegistry
from ufo.runtime.access.credentials import (
    CredentialRequests,
    CredentialStore,
)
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.access.member_authorization import (
    MEMBER_AUTHORIZATION_JOB,
    MEMBER_AUTHORIZATION_MODEL,
    MemberAuthorization,
)
from ufo.runtime.access.workspace_slots import WorkspaceSlots
from ufo.runtime.agent_scope import agent
from ufo.runtime.context_boundary import (
    BoundaryInputs,
    context_boundary_tools,
    flagged_context_boundary,
)
from ufo.runtime.engine import (
    ADOPTED_CLAIM,
    MAIN_ROUND_LIMIT,
    AdoptionReplay,
    RunLineage,
    SandboxAccess,
    TranscriptRepair,
    TurnEngine,
    TurnParked,
    _claim_turn,
    sandbox_provider_park,
)
from ufo.runtime.ext.context import ExtensionContext, ModelAccess, TurnInvoker
from ufo.runtime.ext.hooks import HookChain
from ufo.runtime.ext.manifest import Manifest, SubagentProfile
from ufo.runtime.ext.surface import TurnTailer
from ufo.runtime.hub import Hub, Parked, Terminal
from ufo.runtime.indexing import EmbedClient, IndexBackend
from ufo.runtime.kinds.provisioning import AgentProvisioning
from ufo.runtime.media.site_previewer import SitePreviewer
from ufo.runtime.memory import MemorySearch
from ufo.runtime.object_name import ObjectRef
from ufo.runtime.objects import BoundAction, ObjectVerbs
from ufo.runtime.prompts.render import RenderedPrompt
from ufo.runtime.search import SearchProvider
from ufo.runtime.skills.runtime import (
    LoadedSkill,
    SkillCard,
    SkillRegistry,
    SystemSkillBundle,
    load_skills,
)
from ufo.runtime.skills.selection import (
    SKILL_QUERY_MAX_CHARS,
    SKILL_TOP_K,
    MemberVisibility,
    prompt_index,
    select_top_k,
)
from ufo.runtime.subagents import (
    SubagentRegistry,
    SubagentResult,
    Subagents,
)
from ufo.runtime.tools.bridge import TOOL_BRIDGE_URL, TOOL_BRIDGE_URL_ENV
from ufo.runtime.tools.context import SPAWN_CONNECT_PATH, Spawn, UnknownSubagentProfile
from ufo.runtime.tools.registry import ACTION_READ_TOOLS, OBJECT_ACTION_TOOL, ToolDef, ToolRegistry
from ufo.runtime.transcript import Transcript
from ufo.runtime.turns.activity import (
    ACTIVITY_JOB,
    SKILL_LOAD_TOOL,
    SKILL_SEARCH_ACTION_ID,
    ActivitySummarizer,
)
from ufo.runtime.turns.audience import Audience, parse_audience
from ufo.runtime.turns.contracts import Contract, output_contract
from ufo.runtime.turns.dispatch import dispatch_next_turn
from ufo.runtime.workspace import (
    PLAN_FUNDED,
    PLATFORM_FUNDED,
    Funding,
    ModelFundingChanged,
    ResolvedModelClient,
    model_credentials,
    ws,
)
from ufo.schema import tables
from ufo.schema.records import (
    EXPRESS_QUEUE_NAME,
    INTENT_ADMISSION,
    INTERNAL_ADMISSION,
    SCHEDULED_ADMISSION,
    TERMINAL_ERROR_MESSAGE_MAX_CHARS,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    UNSCOPED_EXPRESS_QUEUE_NAME,
    UNSCOPED_TURN_QUEUE_NAME,
    Agent,
    ModelAccountCapability,
    TerminalFrame,
    Turn,
    TurnAdmissionSource,
    TurnContext,
    TurnRuntimeConfig,
)

TURN_QUEUE_POLL_SECONDS = 0.1
TURN_WORKER_CONCURRENCY = 40
SCHEDULED_TURN_CONCURRENCY = 20
_turn_slot_registry: dict[tuple[asyncio.AbstractEventLoop, str], asyncio.Semaphore] = {}


def _turn_gates(
    parent_turn_id: UUID | None, admission_source: TurnAdmissionSource
) -> tuple[asyncio.Semaphore, ...]:
    """The slots a turn must hold to run, held per event loop the way `db.py` holds its engines.
    Capacity itself is the turns queue's `worker_concurrency` — an executor claims only the
    remainder of what it runs, so nothing here bounds load. What remains is fairness: a scheduled
    turn holds one of `SCHEDULED_TURN_CONCURRENCY` slots, so one workspace's scheduled wave can
    fill at most half a process and a member's message always finds claimed capacity."""
    if parent_turn_id is not None or admission_source != SCHEDULED_ADMISSION:
        return ()
    loop = asyncio.get_running_loop()
    gate = _turn_slot_registry.get((loop, "scheduled"))
    if gate is None:
        gate = _turn_slot_registry[(loop, "scheduled")] = asyncio.Semaphore(
            SCHEDULED_TURN_CONCURRENCY
        )
    return (gate,)


FAILED_TERMINAL_RETRY_SECONDS = 1.0
FAILED_TERMINAL_RETRY_MAX_SECONDS = 30.0
SKILL_OWNER_KIND = "skill"
SKILL_SUBJECT = "workspace"
SKILL_SHADOW_TIMEOUT_SECONDS = 4.0

"""The rate card a plan-funded turn freezes. A connected account serves the turn under a
subscription its holder already bought, so no per-token money exists to record: the tokens are
metered as they are for any other turn and every rate that converts them to dollars is zero. The
card is what the whole turn prices against — its rounds, its live cost ticks, its terminal frame,
its cap arithmetic — so one card keeps every one of them saying the same true thing."""
PLAN_SERVED_PRICE = ModelPrice(
    input=0, output=0, cache_read=0, cache_write_5m=0, cache_write_30m=0, cache_write_1h=0
)


class _Rates(BaseModel):
    input: int
    output: int
    cache_read: int
    cache_write_5m: int
    cache_write_30m: int
    cache_write_1h: int

    @classmethod
    def of(cls, price: ModelPrice) -> "_Rates":
        return cls(
            input=price.input,
            output=price.output,
            cache_read=price.cache_read,
            cache_write_5m=price.cache_write_5m,
            cache_write_30m=price.cache_write_30m,
            cache_write_1h=price.cache_write_1h,
        )

    def price(self) -> ModelPrice:
        return ModelPrice(
            input=self.input,
            output=self.output,
            cache_read=self.cache_read,
            cache_write_5m=self.cache_write_5m,
            cache_write_30m=self.cache_write_30m,
            cache_write_1h=self.cache_write_1h,
        )


class _BillingIdentity(_Rates):
    """The rate card one turn attempt bills against, frozen on the turn row: the model the attempt
    began on with its rates, and — for a turn on the member's own account — the rates of every other
    account's model the attempt may move onto, read off the same card, so a moved round bills at
    the rate of the model that served it and a recovery re-prices nothing."""

    attempt: str
    model: str
    price_digest: str
    funding: Funding | None = None
    payer: str | None = None
    alternates: dict[str, _Rates] = {}

    def pricing(self) -> Pricing:
        return Pricing(
            prices={
                self.model: self.price(),
                **{model: rates.price() for model, rates in self.alternates.items()},
            },
            digest=self.price_digest,
        )


def _plan_pricing(models: tuple[str, ...]) -> Pricing:
    return pricing_from(dict.fromkeys(models, PLAN_SERVED_PRICE))


@dataclass(frozen=True)
class _TurnBilling:
    registry: ModelRegistry
    turn_id: UUID
    attempt: str
    candidate_model: str
    alternates: tuple[str, ...] = ()

    async def resolve(self) -> tuple[_BillingIdentity, ResolvedModelClient, bool]:
        billing = await _stored_billing_identity(self.turn_id, self.attempt)
        if billing is None:
            model = await self.registry.client_for(self.candidate_model)
            candidate = self._identity(self.candidate_model, model)
            billing = await _frozen_billing_identity(self.turn_id, candidate)
            if billing != candidate:
                model = await self.registry.client_for(billing.model)
        else:
            model = await self.registry.client_for(billing.model)
        self._validate(billing, model)
        byok = await _frozen_byok(self.turn_id, model.funding != PLATFORM_FUNDED, self.attempt)
        if byok != (model.funding != PLATFORM_FUNDED):
            raise ModelFundingChanged("model payer changed during turn attempt")
        return billing, model, byok

    def _identity(self, model: str, resolved: ResolvedModelClient) -> _BillingIdentity:
        card = (
            _plan_pricing((model, *self.alternates))
            if resolved.funding == PLAN_FUNDED
            else self.registry.pricing
        )
        return _BillingIdentity(
            **_Rates.of(card.prices[model]).model_dump(),
            attempt=self.attempt,
            model=model,
            price_digest=card.digest,
            funding=resolved.funding,
            payer=resolved.payer,
            alternates={
                alternate: _Rates.of(card.prices[alternate]) for alternate in self.alternates
            },
        )

    @staticmethod
    def _validate(billing: _BillingIdentity, model: ResolvedModelClient) -> None:
        if billing.funding is not None and billing.funding != model.funding:
            raise ModelFundingChanged("model funding changed during turn attempt")
        if billing.payer is not None and billing.payer != model.payer:
            raise ModelFundingChanged("model payer changed during turn attempt")
        plan_digest = _plan_pricing((billing.model, *billing.alternates)).digest
        if (billing.price_digest == plan_digest) != (model.funding == PLAN_FUNDED):
            raise ModelFundingChanged("model funding changed during turn attempt")


async def _without_workspace_skills(name: str) -> None:
    """The member tier of an agent whose `use_workspace_skills` setting is off: no card routes here
    and no name loads, so the workspace's saved skills reach neither its prompt nor its tools."""
    return None


def _member_skill_turn(turn: Turn) -> bool:
    """The turns the saved-skills block renders on — exactly the turns memory's recall_hook injects
    on (the extension's user_prompt_submit gate): every turn except a speakerless root admitted
    INTERNAL_ADMISSION, a machine fold with no topical text. A prepared intent fires no
    user_prompt_submit at all, so it sits outside both gates by construction; the shadow selector
    keys on the same turns, so its evidence covers every turn the block could have served."""
    if turn.admission_source == INTENT_ADMISSION:
        return False
    return not (
        turn.speaker_member_id is None
        and turn.admission_source == INTERNAL_ADMISSION
        and turn.parent_turn_id is None
    )


def _member_skill_block(turn: Turn, view: MemberVisibility, enabled: bool) -> str:
    """The saved-skills block this turn's founding message carries — the one-pass view's block on a
    recall-parity turn while the config switch holds, so an eval stack can ablate it (the bias
    ablation family in evals/skill_loading). A tier below the fold carries an empty block in the
    view itself: it lists in the prompt instead."""
    if not enabled or not _member_skill_turn(turn):
        return ""
    return view.block


def _prompt_skill_index(skills: SkillRegistry, enabled: bool) -> tuple[tuple[str, str], ...]:
    """What `{{skill_index}}` renders this turn: the fold-aware index while the member tier is
    enabled, the deploy tier alone when the ablation switch is off — off, member skills reach the
    model only through the skill kind's `skill_search` action."""
    return prompt_index(skills) if enabled else skills.index()


_shadow_selection_tasks: set[asyncio.Task[None]] = set()


def _fire_shadow_selection(
    index: IndexBackend, embed: EmbedClient, turn: Turn, cards: tuple[SkillCard, ...]
) -> None:
    """Launch the shadow selector for one member turn — fire-and-forget, so the turn never waits on
    it; the held reference keeps the task alive until it logs or gives up."""
    task = asyncio.create_task(_shadow_skill_selection(index, embed, turn, cards))
    _shadow_selection_tasks.add(task)
    task.add_done_callback(_shadow_selection_tasks.discard)


async def _shadow_skill_selection(
    index: IndexBackend, embed: EmbedClient, turn: Turn, cards: tuple[SkillCard, ...]
) -> None:
    """Score both retrieval legs against one member turn's inbound and log what each would have
    injected — the vector leg's promote-or-delete evidence. Bounded and best-effort: every failure
    is a log line, never a turn's problem."""
    try:
        async with asyncio.timeout(SKILL_SHADOW_TIMEOUT_SECONDS):
            [embedding] = await embed.embed((turn.inbound[:SKILL_QUERY_MAX_CHARS],))
            hits = await index.vector(
                embedding,
                subjects=frozenset({SKILL_SUBJECT}),
                owner_kind=SKILL_OWNER_KIND,
                limit=SKILL_TOP_K,
            )
        log(
            "skill.shadow_selection",
            turn_id=str(turn.id),
            lexical=[card.name for card in select_top_k(turn.inbound, cards)],
            vector=[hit.owner_id for hit in hits],
        )
    except Exception as error:
        log(
            "skill.shadow_selection_failed",
            turn_id=str(turn.id),
            error_class=type(error).__name__,
        )


IMPLIED_GRANTS: dict[str, tuple[str, ...]] = {SKILL_LOAD_TOOL: (SKILL_SEARCH_ACTION_ID,)}
"""Allowlist names that imply companions: naming the key grants what rides beside it — a skill
loader without its search would be directed at names it cannot reach. A companion is a wire tool
name or a canonical action id; every selector intersects the widened set against the live
registry it selects from, so an implied name nothing registers is inert."""


def with_implied_grants(names: set[str]) -> set[str]:
    for name in tuple(names):
        names.update(IMPLIED_GRANTS.get(name, ()))
    return names


def _agent_actions(
    actions: Mapping[str, Mapping[str, BoundAction]],
    allowed: tuple[str, ...] | None,
    admission: TurnAdmissionSource,
    speaker_member_id: UUID | None = None,
) -> frozenset[str]:
    """The canonical action ids this turn's agent holds — `_agent_tools`' mirror over the action
    registry, under the same rules: no allowlist (or a speaking prepared intent) grants every
    non-`profile_only` action, an allowlist grants exactly the canonical ids it names, and a name
    no active extension answers is simply absent. Naming the dispatcher itself is refused where
    allowlists are written, never here."""
    declared = tuple(bound.action for held in actions.values() for bound in held.values())
    if allowed is None or (admission == INTENT_ADMISSION and speaker_member_id is not None):
        return frozenset(action.canonical_id for action in declared if not action.profile_only)
    names = with_implied_grants(set(allowed))
    return frozenset(action.canonical_id for action in declared if action.canonical_id in names)


class SubagentKeyWithdrawn(Exception):
    """A profile that runs on the speaking member's own provider account reached execution with no
    account able to serve it. Either the member disconnected the account after the spawn gate
    admitted the turn, or every account they connected is rate limited and the work has nowhere
    left to move. The deploy's key is not a fallback for either: it is exactly what this profile
    exists not to spend.

    Both arms carry the same address the spawn refusal does, because the member reading either is
    in the same position — no account of theirs can run the work, and the screen that connects or
    replaces one is what they need. `rate_limited` names which arm they landed on, so the message
    tells them what to fix without sending them to a second screen."""

    def __init__(
        self, profile: str, connect_url: str | None = None, rate_limited: bool = False
    ) -> None:
        connect = f"{connect_url.rstrip('/')}{SPAWN_CONNECT_PATH}" if connect_url else "the portal"
        cause = (
            "every account they connected is rate limited right now"
            if rate_limited
            else "that account is no longer connected"
        )
        super().__init__(
            f"subagent profile {profile!r} runs the coding agent on the member's own ChatGPT or "
            f"Claude account, and {cause}, so this task cannot run. "
            f"Send them to {connect}"
        )
        self.profile = profile
        self.rate_limited = rate_limited


def _member_accounts_connectable(runtime: "Runtime") -> bool:
    """Whether any installed extension can store a member's own provider account."""
    return any(manifest.connects_member_accounts for manifest in runtime.manifests)


def _subagent_model(
    profile: SubagentProfile,
    connected: str | None,
    agent: Agent,
    runtime: "Runtime",
    pinned: str | None,
    document: str | None,
) -> str:
    """The model a subagent turn runs on.

    A profile bound to the member's own account takes the model that account serves — a bare pin
    cannot reach it, because the pinned id is not one the account was bound to serve and the turn
    would quietly fall through to the deploy's key, which is the spend this profile exists to
    prevent. A stored environment document naming this profile's model is the one exception: the
    document is the workspace's explicit, digest-attested choice of what runs and what it spends,
    made by a speaker who could already run any model as the main agent through the same document,
    so it outranks the account binding and the turn bills the workspace. Every other profile takes
    the turn's pin, else its own, else the agent's."""
    if profile.needs_own_model_key:
        if document is not None:
            return runtime.registry.resolve(document)
        own = profile.own_key_models.get(connected or "")
        if own is not None:
            return runtime.registry.resolve(own)
        if _member_accounts_connectable(runtime):
            raise SubagentKeyWithdrawn(profile.name, runtime.config.connect.public_base_url)
        if profile.model is None:
            raise SubagentKeyWithdrawn(profile.name, runtime.config.connect.public_base_url)
        return runtime.registry.resolve(profile.model)
    if pinned is not None:
        return pinned
    return runtime.registry.resolve(profile.model or agent.model)


def _own_account_alternates(
    profile: SubagentProfile,
    connected: tuple[str, ...],
    chosen: str,
    document: str | None,
    runtime: "Runtime",
) -> tuple[str, ...] | None:
    """The models a turn on the member's own account moves onto when the provider rate-limits the
    account it started on — one per other account the member connected, in the same declaration
    order the first was chosen by — or None for a turn that runs on no account of theirs.

    A member who connected both a Claude and a ChatGPT account bought two subscriptions, and the
    coding work they asked for should spend the one still serving rather than stop at the limit of
    the one it happened to start on. A turn running a model the environment document names spends
    the workspace, not the member, so it moves nowhere: the document is the choice, and no account
    of the member's was asked for. A deploy that can hold no member account runs the profile on its
    own key, so the model `_subagent_model` chose there is none the member's accounts serve: that
    turn holds no accounts either, and a rate limit on it is the provider's fault like any other."""
    if not profile.needs_own_model_key or document is not None:
        return None
    own = tuple(
        runtime.registry.resolve(model)
        for provider in connected
        if (model := profile.own_key_models.get(provider)) is not None
    )
    if chosen not in own:
        return None
    return tuple(model for model in own if model != chosen)


def _model_routes(
    profile: SubagentProfile | None,
    accounts: tuple[ModelAccountCapability, ...],
    runtime: "Runtime",
) -> Mapping[str, str]:
    if profile is None:
        return {}
    return {
        runtime.registry.resolve(model): account.slot
        for account in accounts
        if (model := profile.own_key_models.get(account.provider)) is not None
    }


def _subagent_actions(
    actions: Mapping[str, Mapping[str, BoundAction]],
    profile: SubagentProfile,
    grants: frozenset[str],
) -> frozenset[str]:
    """`_subagent_tools`' mirror over the action registry: the profile's own names plus (unless
    isolated) cross-extension grants select canonical ids, and a `subagent_default` action rides
    along exactly as a `subagent_default` tool does."""
    allowed = set(profile.tool_names)
    if not profile.isolated_tools:
        allowed.update(grants)
    allowed = with_implied_grants(allowed)
    return frozenset(
        action.canonical_id
        for held in actions.values()
        for bound in held.values()
        if (action := bound.action).canonical_id in allowed
        or (action.subagent_default and not profile.isolated_tools)
    )


def _with_action_verbs(
    selected: tuple[ToolDef, ...],
    all_tools: tuple[ToolDef, ...],
    granted_actions: frozenset[str],
    *,
    discovery: bool = True,
) -> tuple[ToolDef, ...]:
    """Hold the action verbs to the grant rule: a turn holding at least one canonical action id
    rides the wire with `object_action` and the two reads that publish an action's envelope —
    `object_list` for a collection action, `object_get` for an instance action — because an
    allowlist never names the dispatcher, and one naming only canonical ids would otherwise hold
    actions it can dispatch but never discover. `object_apply` and `object_delete` stay the
    allowlist's own to grant.

    `discovery` is off for a tool set that is already exact: a profile declaring `isolated_tools`
    names every tool it holds and reads its one call out of its skill, so the two reads are not a
    companion it needs but a surface it wanders into. The dispatcher still rides along, because
    without it the action the profile was granted could not be called at all."""
    without = tuple(tool for tool in selected if tool.name != OBJECT_ACTION_TOOL)
    if not granted_actions:
        return without
    held = {tool.name for tool in without}
    by_name = {tool.name: tool for tool in all_tools}
    wanted = (OBJECT_ACTION_TOOL, *ACTION_READ_TOOLS) if discovery else (OBJECT_ACTION_TOOL,)
    verbs = tuple(by_name[name] for name in wanted if name in by_name and name not in held)
    return (*without, *verbs)


def _agent_tools(
    all_tools: tuple[ToolDef, ...],
    allowed: tuple[str, ...] | None,
    admission: TurnAdmissionSource,
    speaker_member_id: UUID | None = None,
) -> tuple[ToolDef, ...]:
    """The tool set a turn runs with, intersected with the live registry so a name no active
    extension answers is simply absent.

    An agent naming no allowlist runs the member-facing set, which withholds every `profile_only`
    primitive — a repository checkout bound to an admitted comparison is not a tool the workspace's
    general agent may reach. An allowlist *is* the naming: a specialist that declares the checkout
    holds it, and holds nothing else. So the primitive is reachable exactly where a declaration
    says so, and the agent that never mentions it can neither hold it nor ask for it. The
    structural pairs in `IMPLIED_GRANTS` ride along, exactly as a subagent profile's do.

    An allowlist governs what a model may call, so it does not reach a speaking prepared intent,
    which takes no model round: the panel's verb dispatches verbatim under the submitting member's
    live requester, admitted through the panel's own gate. A speakerless intent comes from the
    sandbox tool bridge and remains inside the agent's allowlist because the model reaches it
    through `bash`."""
    if allowed is None or (admission == INTENT_ADMISSION and speaker_member_id is not None):
        return tuple(tool for tool in all_tools if not tool.profile_only)
    names = with_implied_grants(set(allowed))
    return tuple(tool for tool in all_tools if tool.name in names)


def _resolve_profile(registry: SubagentRegistry, turn_id: str, name: str) -> SubagentProfile:
    """The profile a subagent turn runs under. Every admission resolves the name before it writes a
    child turn, so a name that fails here is one the registry stopped holding under a child already
    queued — the deploy that dropped the extension declaring it. The requested name and the set that
    was registered ride their own log event, because the failure the backstop reports names a class
    and a stack, and neither says which profile the fleet no longer has."""
    try:
        return registry.get(name)
    except UnknownSubagentProfile as error:
        log_error(
            "turn.unknown_subagent_profile",
            turn_id=turn_id,
            requested_profile=error.requested,
            registered_profiles=", ".join(error.registered),
        )
        raise


def _subagent_tools(
    all_tools: tuple[ToolDef, ...], profile: SubagentProfile, grants: frozenset[str]
) -> tuple[ToolDef, ...]:
    allowed = set(profile.tool_names)
    if not profile.isolated_tools:
        allowed.update(grants)
    allowed = with_implied_grants(allowed)
    selected = tuple(
        tool
        for tool in all_tools
        if tool.name in allowed or (tool.subagent_default and not profile.isolated_tools)
    )
    return selected


_provisioned_workspaces: set[UUID] = set()


async def _apply_provisions(runtime: "Runtime", workspace_id: UUID) -> None:
    """Give a workspace the agents the active extensions ship, once per workspace per process.

    Onboarding covers a new workspace. The first turn of each workspace also applies the idempotent
    provisions. A name the workspace already uses sends the shipped agent to a free variant, so a
    collision costs the member's turn nothing."""
    if workspace_id in _provisioned_workspaces:
        return
    await AgentProvisioning(runtime.manifests).apply(workspace_id)
    _provisioned_workspaces.add(workspace_id)


TURN_QUEUE = Queue(
    TURN_QUEUE_NAME,
    worker_concurrency=TURN_WORKER_CONCURRENCY,
    polling_interval_sec=TURN_QUEUE_POLL_SECONDS,
)
EXPRESS_QUEUE = Queue(
    EXPRESS_QUEUE_NAME,
    polling_interval_sec=TURN_QUEUE_POLL_SECONDS,
)
UNSCOPED_TURN_QUEUE = Queue(
    UNSCOPED_TURN_QUEUE_NAME,
    worker_concurrency=TURN_WORKER_CONCURRENCY,
    polling_interval_sec=TURN_QUEUE_POLL_SECONDS,
)
UNSCOPED_EXPRESS_QUEUE = Queue(
    UNSCOPED_EXPRESS_QUEUE_NAME,
    polling_interval_sec=TURN_QUEUE_POLL_SECONDS,
)


@dataclass(frozen=True)
class AssembleRequest:
    """The facts one turn hands the host to compose its environment from: the turn row and its
    resolved agent or profile, the billing-resolved model, the deploy's skill registry and grants,
    and the digest of the environment document the turn pins — gate-checked here, loaded and
    applied by the host, which owns what a document means.

    `context_window` is the `{{context_window}}` prompt block of the boundary spec this turn's flag
    read selected, so the words the prompt carries describe the boundary this turn actually
    crosses."""

    turn: Turn
    agent: Agent
    audience: Audience
    profile: SubagentProfile | None
    model: str
    knowledge_cutoff: str
    preload_names: tuple[str, ...]
    skills: SkillRegistry
    subagents: SubagentRegistry
    subagent_grants: dict[str, frozenset[str]]
    member_block: bool
    environment: str | None
    context_window: str


@dataclass(frozen=True)
class EnvironmentFile:
    """One file the turn's pinned document seeds into its sandbox before the model runs."""

    path: str
    content: bytes


@dataclass(frozen=True)
class AssembledTurn:
    """What the host composed for one turn: the prompt the model reads, the tool offer it may
    call with each tool's owning context and the object verbs, the reactive hook chain, the
    skills to mount, and the member-skill view the founding message carries."""

    system_prompt: RenderedPrompt
    tools: ToolRegistry
    tool_ext: dict[str, ExtensionContext]
    verbs: ObjectVerbs
    granted_actions: frozenset[str]
    hooks: HookChain
    skills: SkillRegistry
    preload: tuple[LoadedSkill, ...]
    files: tuple[EnvironmentFile, ...]
    member_skill_block: str
    cards: tuple[SkillCard, ...]
    view: MemberVisibility | None


class TurnEnvironment(Protocol):
    """The environment a turn runs in. The host layer composes the whole bundle — prompt, tools,
    skills, hooks — from what it discovered, reshaped by the turn's pinned environment document;
    the runtime consumes it. `environment_model` reports the document's model choice for one
    target so the runtime can resolve, validate, and bill it before assembly — the runtime never
    reads a document itself. Only a composition root constructs an implementation."""

    async def assemble(self, request: AssembleRequest) -> AssembledTurn: ...

    async def environment_model(self, environment: str, profile: str | None) -> str | None: ...

    def clis(self) -> dict[str, CliCredential]: ...

    def slots(self) -> WorkspaceSlots: ...


@dataclass(frozen=True)
class Runtime:
    config: Config
    blob: WorkspaceBlobStore
    sandboxes: ConversationSandbox
    hub: Hub
    cdp_provider: CdpProvider | None
    search_provider: SearchProvider | None
    connectors: ConnectorRegistry
    run_tokens: RunTokenCodec
    dbos: DBOSClient
    invoker_for: Callable[[UUID], TurnInvoker]
    subagents: SubagentRegistry
    subagent_grants: dict[str, frozenset[str]]
    manifests: tuple[Manifest, ...]
    environment: TurnEnvironment
    registry: ModelRegistry
    skills: SkillRegistry
    credentials: CredentialStore | None
    index: IndexBackend
    embed: EmbedClient
    artifact_token_secret: str
    site_previewer: SitePreviewer | None = None
    billing_url: str | None = None
    home_surface: str | None = None
    tailer: TurnTailer | None = None
    memory: MemorySearch | None = None


_runtime: Runtime | None = None


def init_runtime(runtime: Runtime) -> None:
    global _runtime
    if _runtime is not None:
        raise RuntimeError("runtime already initialized")
    bundle = SystemSkillBundle.from_skills(runtime.skills.bundled_skills())
    for carrier in (
        runtime.sandboxes.carrier,
        *(entry[0] for entry in runtime.sandboxes.resume_carriers.values()),
    ):
        if isinstance(carrier, SystemSkillSeeding):
            carrier.seed_system_skills(bundle.archive)
    _runtime = runtime


def reset_runtime() -> None:
    """Clear the process runtime so a fresh one can be installed. `serve` installs exactly once and
    never resets; this is the seam a test uses to swap in its own runtime without tripping the
    single-init guard, replacing what would otherwise be a poke at the module global."""
    global _runtime
    _runtime = None


async def _execute_turn(workspace_id: str, turn_id: str) -> str:
    """The turn body, run directly in the `turn_workflow` DBOS workflow — not wrapped in a step, so
    the model-round, tool-dispatch, arrival-drain, and rollover steps inside `engine.run()` are
    the workflow's own steps and memoize for crash-recovery replay. Setup (claim, load, sandbox
    create-or-attach, engine build) re-runs each recovery and is idempotent; messages that arrive
    after the claim land on the conversation's inbound queue, which the engine drains at each round
    boundary. A fault outside the engine commits the terminal
    through the backstop so the client's wait still ends. The workspace is bound from the workflow
    argument for the whole body via `with ws(...)`: every query, credential read, and model call
    inside runs under it — the RLS scope on the shared RLS-subject role, the workspace's BYOK keys,
    and the ledger it bills. One fleet serves many workspaces from one pool; a single-workspace
    deploy binds its sole one. A profile that runs on a connected provider account binds only the
    exact credential slots persisted on its turn."""
    runtime = _runtime
    if runtime is None:
        raise RuntimeError("runtime not initialized (init_runtime runs in serve)")
    workspace_uuid = UUID(workspace_id)
    turn_uuid = UUID(turn_id)
    attempt = DBOS.workflow_id or turn_id
    conversation_id: UUID | None = None
    with ws(workspace_uuid):
        try:
            async with workspace_tx() as connection:
                row = (
                    await connection.execute(
                        sa.select(
                            tables.turn.c.agent_id,
                            tables.turn.c.conversation_id,
                            tables.turn.c.traceparent,
                            tables.turn.c.subagent_profile,
                            tables.turn.c.parent_turn_id,
                            tables.turn.c.admission_source,
                            tables.turn.c.model_accounts,
                        ).where(
                            tables.turn.c.id == turn_uuid,
                            tables.turn.c.workspace_id == workspace_uuid,
                        )
                    )
                ).one()
            conversation_id = row.conversation_id
            profile = (
                runtime.subagents.find(row.subagent_profile)
                if row.subagent_profile is not None
                else None
            )
            gates = _turn_gates(row.parent_turn_id, row.admission_source)
            queued = time.monotonic()
            async with AsyncExitStack() as held:
                for gate in gates:
                    await held.enter_async_context(gate)
                if gates:
                    emit_histogram("turn_slot_wait_ms", round((time.monotonic() - queued) * 1000))
                await _apply_provisions(runtime, workspace_uuid)
                with (
                    agent(row.agent_id),
                    model_credentials(
                        _model_routes(
                            profile,
                            tuple(
                                ModelAccountCapability.model_validate(account)
                                for account in row.model_accounts
                            ),
                            runtime,
                        )
                    ),
                    turn_span(
                        turn_uuid,
                        row.conversation_id,
                        row.traceparent,
                        row.subagent_profile,
                        row.parent_turn_id,
                    ),
                ):
                    status = await _run_turn(runtime, turn_id)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await _commit_failed_terminal(runtime.hub, turn_uuid, attempt, error)
            status = "failed"
        await _deliver_to_parent(runtime, turn_uuid)
        if conversation_id is not None:
            await _offer_next_turn(runtime, workspace_uuid, conversation_id, turn_uuid)
        return status


async def _offer_next_turn(
    runtime: Runtime, workspace_id: UUID, conversation_id: UUID, turn_id: UUID
) -> None:
    """Hand the conversation on as this workflow ends — every exit passes here, so the handoff
    needs no per-path wiring: its next queued turn is dispatched, then any arrival the ended turn
    left pending is re-admitted, so a message folded into a turn that then failed or was cancelled
    is read by the next one. It swallows its own fault for the same reason `_deliver_to_parent`
    does: the ended turn's terminal already stands, and the dispatcher sweep re-offers within its
    grace whatever this call missed."""
    try:
        await dispatch_next_turn(runtime.dbos, conversation_id)
        await runtime.invoker_for(workspace_id).redispatch(conversation_id, turn_id)
    except Exception as error:
        log(
            "turn.offer_deferred",
            conversation_id=str(conversation_id),
            turn_id=str(turn_id),
            error_class=type(error).__name__,
        )


async def _deliver_to_parent(runtime: Runtime, turn_id: UUID) -> None:
    """Hand a finished child's output to the conversation that spawned it. Read back rather than
    taken from the execution that just ran, so the terminal delivered is the one that is durable —
    a turn this execution found already claimed elsewhere, or already terminal, delivers exactly
    what stands, and a child that parked mid-run delivers nothing until the resume that finishes
    it. The delivery is keyed on the child, so the executions that both observe one terminal post
    one arrival between them.

    It runs outside the turn's own failure handling and swallows its own fault. The child's
    terminal already stands: letting a delivery error reach the handler above would re-label a
    finished turn as failed, and letting it raise from the handler would escape the workflow into
    a recovery loop that fails identically every time. What is lost by swallowing is latency, not
    the result — the sweep finds any child whose delivery did not land."""
    async with workspace_tx() as connection:
        row = (
            (await connection.execute(sa.select(tables.turn).where(tables.turn.c.id == turn_id)))
            .mappings()
            .one()
        )
    if row["parent_turn_id"] is None or row["terminal"] is None:
        return
    try:
        await SubagentResult(
            invoker=runtime.invoker_for(row["workspace_id"]), registry=runtime.subagents
        ).deliver(Turn.model_validate(dict(row)))
    except asyncio.CancelledError:
        raise
    except Exception as error:
        log_error(
            "subagent.delivery_deferred",
            turn_id=str(turn_id),
            error_class=type(error).__name__,
        )


@dataclass(frozen=True)
class _SandboxSetup:
    hub: Hub
    blob: WorkspaceBlobStore
    turn: Turn
    attempt: str

    async def run(
        self,
        sandbox: Sandbox,
        preload: tuple[LoadedSkill, ...],
        files: tuple[EnvironmentFile, ...],
    ) -> None:
        try:
            if preload:
                with span("skills.mount", count=len(preload)):
                    await load_skills(sandbox, preload)
            if files:
                with span("environment.files", count=len(files)):
                    for seeded in files:
                        await sandbox.write_file(seeded.path, seeded.content)
        except SandboxProviderUnavailable as error:
            parked = sandbox_provider_park(self.turn)
            if parked is None:
                raise
            stored = await Transcript(
                blob=self.blob, conversation_id=self.turn.conversation_id
            ).read()
            absorbed = (
                stored.parked.absorbed
                if stored is not None and stored.seq == self.turn.seq and stored.parked is not None
                else ()
            )
            await self._park(parked, absorbed)
            raise parked from error

    async def _park(self, parked: TurnParked, absorbed: tuple[UUID, ...]) -> None:
        async with workspace_tx() as connection:
            updated = await connection.execute(
                sa.update(tables.turn)
                .values(
                    status="parked",
                    retry_at=parked.retry_at,
                    external_retry_count=parked.external_retry_count,
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.turn.c.id == self.turn.id,
                    tables.turn.c.status == "running",
                    tables.turn.c.running_attempt == self.attempt,
                )
            )
            if updated.rowcount == 1:
                await connection.execute(
                    sa.update(tables.inbound_message)
                    .values(consumed_turn_id=None)
                    .where(
                        tables.inbound_message.c.consumed_turn_id == self.turn.id,
                        ~tables.inbound_message.c.id.in_(absorbed),
                    )
                )
        if updated.rowcount != 1:
            return
        try:
            await self.hub.publish(self.turn.id, Parked(message=parked.message))
        except Exception as error:
            log(
                "hub.publish_failed",
                turn_id=str(self.turn.id),
                error_class=type(error).__name__,
            )
        emit_metric(
            "turn_parked_total",
            profile=turn_profile(self.turn.subagent_profile, self.turn.spawned),
        )
        log("turn.parked", turn_id=str(self.turn.id))


async def _run_turn(runtime: Runtime, turn_id: str) -> str:
    attempt = DBOS.workflow_id or turn_id
    try:
        with span("turn.claim"):
            claim = await _claim_turn(UUID(turn_id), attempt)
        if claim is None:
            turn, _, _ = await _load_turn(UUID(turn_id))
            await TranscriptRepair(
                turn=turn,
                transcript=Transcript(blob=runtime.blob, conversation_id=turn.conversation_id),
                hub=runtime.hub,
            ).resolve()
            return "superseded"
        with span("turn.load"):
            turn, agent, audience = await _load_turn(UUID(turn_id))
        if turn.runtime_config is not None and turn.runtime_config.model is not None:
            runtime.registry.spec(turn.runtime_config.model)
        internet_access_allowed = agent.internet_access_allowed and (
            turn.runtime_config is None or turn.runtime_config.internet_access is None
        )
        lineage = await _run_lineage(turn)
        previous_turn_ended_at = (
            None
            if turn.admission_source == INTENT_ADMISSION
            else await _previous_turn_ended_at(turn)
        )
        subagents = Subagents(
            client=runtime.dbos,
            registry=runtime.subagents,
            parent=turn,
            audience=audience,
            hub=runtime.hub,
            invoker=runtime.invoker_for(turn.workspace_id),
            key_slot_for=runtime.registry.key_slot_for,
            billing_url=runtime.billing_url,
            connect_url=runtime.config.connect.public_base_url,
            models=tuple(runtime.registry.specs),
            member_accounts_connectable=_member_accounts_connectable(runtime),
        )

        def subagents_for(
            connections: tuple[UUID, ...],
        ) -> tuple[Spawn, Subagents]:
            scoped = replace(subagents, connection_scope=connections)
            return scoped.spawn, scoped

        payload: dict[str, Any] = (
            json.loads(turn.inbound) if turn.subagent_profile is not None and turn.seq == 1 else {}
        )
        profile = (
            None
            if turn.subagent_profile is None
            else _resolve_profile(runtime.subagents, turn_id, turn.subagent_profile)
        )
        environment = None if turn.runtime_config is None else turn.runtime_config.environment
        pinned_model = None if turn.runtime_config is None else turn.runtime_config.model
        document_model = None
        if environment is not None:
            document_model = await runtime.environment.environment_model(
                environment, turn.subagent_profile
            )
            if document_model is not None:
                runtime.registry.spec(document_model)
                pinned_model = document_model
        if profile is None:
            resolved_model = (
                pinned_model if pinned_model is not None else runtime.registry.resolve(agent.model)
            )
        else:
            connected = tuple(account.provider for account in turn.model_accounts)
            resolved_model = _subagent_model(
                profile,
                connected[0] if connected else None,
                agent,
                runtime,
                pinned_model,
                document_model,
            )
        alternates = (
            None
            if profile is None
            else _own_account_alternates(
                profile, connected, resolved_model, document_model, runtime
            )
        )
        billing, model, byok = await _TurnBilling(
            registry=runtime.registry,
            turn_id=turn.id,
            attempt=attempt,
            candidate_model=resolved_model,
            alternates=alternates or (),
        ).resolve()
        serving = ServingModel(
            model=billing.model,
            spec=runtime.registry.spec(billing.model),
            client=model.client,
            accounts=(
                None
                if profile is None or alternates is None
                else MemberAccounts(
                    registry=runtime.registry,
                    funding=model.funding,
                    alternates=tuple(billing.alternates),
                    exhausted=partial(
                        SubagentKeyWithdrawn,
                        profile.name,
                        runtime.config.connect.public_base_url,
                        rate_limited=True,
                    ),
                )
            ),
        )
        boundary = await flagged_context_boundary(runtime.config, runtime.manifests)
        with span("environment.assemble"):
            assembled = await runtime.environment.assemble(
                AssembleRequest(
                    turn=turn,
                    agent=agent,
                    audience=audience,
                    profile=profile,
                    model=billing.model,
                    knowledge_cutoff=runtime.registry.spec(billing.model).knowledge_cutoff,
                    preload_names=tuple(payload.get("preload_skills") or ()),
                    skills=runtime.skills,
                    subagents=runtime.subagents,
                    subagent_grants=runtime.subagent_grants,
                    member_block=runtime.config.skills.member_block,
                    environment=environment,
                    context_window=boundary.prompt,
                )
            )
        system_prompt = assembled.system_prompt
        offers_tool = context_boundary_tools(boundary, runtime.manifests)
        tools = ToolRegistry(
            tuple(tool for tool in assembled.tools.tools if offers_tool(tool.name))
        )
        tool_ext = assembled.tool_ext
        verbs = assembled.verbs
        hooks = assembled.hooks
        preload = assembled.preload
        member_skill_block = assembled.member_skill_block
        if profile is None:
            resolved = agent.model_copy(
                update={
                    "model": billing.model,
                    "internet_access_allowed": internet_access_allowed,
                }
            )
            max_rounds = MAIN_ROUND_LIMIT
            output_model: Contract | None = (
                output_contract(agent.output_schema) if turn.spawned else None
            )
            connector_read_only = False
            if (
                _member_skill_turn(turn)
                and assembled.cards
                and assembled.view is not None
                and not assembled.view.catalog_fits
            ):
                _fire_shadow_selection(runtime.index, runtime.embed, turn, assembled.cards)
        else:
            resolved = Agent(
                prompt=system_prompt.content,
                model=billing.model,
                reasoning=profile.reasoning or agent.reasoning,
                internet_access_allowed=internet_access_allowed,
            )
            max_rounds = (
                max(MAIN_ROUND_LIMIT, profile.max_rounds)
                if payload.get("extended_context")
                else profile.max_rounds
            )
            output_model = profile.output_model
            connector_read_only = profile.connector_read_only
        grants = GrantStore() if runtime.credentials is not None else None
        clis = runtime.environment.clis()
        sandbox = _LateSandbox(
            conversation_id=turn.sandbox_conversation_id or turn.conversation_id,
            turn_id=turn.id,
            open=lambda: _open_sandbox(
                runtime.sandboxes,
                runtime.run_tokens,
                turn,
                clis,
                runtime.credentials,
                runtime.environment.slots(),
                cache_rewrite=(
                    runtime.config.sandbox.cache_daemon is not None
                    and resolved.internet_access_allowed
                ),
            ),
            existing=lambda: runtime.sandboxes.existing(
                turn.sandbox_conversation_id or turn.conversation_id
            ),
        )
        sandbox_authorizer = SandboxAuthorizer(
            sandbox=sandbox,
            run_tokens=runtime.run_tokens,
            grants=grants,
            clis=clis,
            turn=turn,
        )
        await _SandboxSetup(runtime.hub, runtime.blob, turn, attempt).run(
            sandbox, preload, assembled.files
        )
        engine = TurnEngine(
            turn=turn,
            agent=resolved,
            byok=byok,
            system_prompt=system_prompt,
            serving=serving,
            activity_summarizer=ActivitySummarizer(
                ModelAccess(
                    replace(
                        runtime.registry,
                        auto_model=runtime.config.models.background_jobs_model,
                    ),
                    ACTIVITY_JOB,
                )
            ),
            transcript=Transcript(blob=runtime.blob, conversation_id=turn.conversation_id),
            context=boundary.build(
                BoundaryInputs(
                    serving=serving,
                    blob=runtime.blob,
                    conversation_id=turn.conversation_id,
                    hooks=hooks,
                    turn=turn,
                    agent=resolved,
                    sandbox=sandbox,
                )
            ),
            hub=runtime.hub,
            lineage=lineage,
            sandbox=sandbox,
            sandbox_for=(
                None if turn.admission_source == INTENT_ADMISSION else sandbox_authorizer.authorize
            ),
            cdp_provider=runtime.cdp_provider,
            search_provider=runtime.search_provider,
            memory=runtime.memory,
            connectors=runtime.connectors,
            connector_read_only=connector_read_only,
            tools=tools,
            tool_ext=tool_ext,
            workspace_slots=runtime.environment.slots(),
            requestable_credentials=(
                None
                if runtime.credentials is None
                else CredentialRequests(
                    fernet=runtime.credentials.fernet,
                    declared=frozenset(
                        slot.name for manifest in runtime.manifests for slot in manifest.credentials
                    ),
                )
            ),
            models=(AUTO_MODEL, *sorted(runtime.registry.specs)),
            model_specs=runtime.registry.specs,
            auto_model=runtime.registry.auto_model,
            public_base_url=runtime.config.connect.public_base_url,
            billing_url=runtime.billing_url,
            hooks=hooks,
            blob=runtime.blob,
            spawn=subagents.spawn,
            subagents=subagents,
            subagents_for=subagents_for,
            audience=audience,
            artifact_token_secret=runtime.artifact_token_secret,
            site_previewer=runtime.site_previewer,
            grants=grants,
            member_authorization=MemberAuthorization(
                ModelAccess(
                    replace(runtime.registry, auto_model=MEMBER_AUTHORIZATION_MODEL),
                    MEMBER_AUTHORIZATION_JOB,
                ),
                runtime.run_tokens.secret,
            ),
            previous_turn_ended_at=previous_turn_ended_at,
            pricing=billing.pricing(),
            attempt=attempt,
            max_rounds=max_rounds,
            skills=assembled.skills,
            member_skill_block=member_skill_block,
            preload=preload,
            output_model=output_model,
            adoption=AdoptionReplay(
                replaying=claim == ADOPTED_CLAIM
                and not turn.spawned
                and turn.admission_source != INTENT_ADMISSION
            ),
            verbs=verbs,
            granted_actions=assembled.granted_actions,
        )
        run = engine.run_intent if turn.admission_source == INTENT_ADMISSION else engine.run
        frame = await run()
        return "superseded" if frame is None else frame.status
    except TurnParked:
        return "parked"
    except asyncio.CancelledError:
        raise
    except Exception as error:
        await _commit_failed_terminal(runtime.hub, UUID(turn_id), attempt, error)
        return "failed"


async def _commit_failed_terminal(
    hub: Hub, turn_id: UUID, attempt: str, error: BaseException
) -> None:
    """The backstop for failures outside the engine: retries until the wait can end. A setup fault
    — loading the turn, attaching the sandbox — has no engine to count it, so the terminal it writes
    is counted here, and only when this attempt owns the running row or the row is still queued. A
    stale execution cannot terminate the live attempt that beat it to the claim. The engine's own
    failures commit their terminal first and leave nothing for this update to match.

    The `profile` rides back off that same write, so the count carries the turn's real profile
    without a second read to fail in a path that has already run out of ways to report. The stack
    is logged on that same transition: a setup fault leaves no step behind to read the failure off,
    so a class name names no call. An engine failure reaches here too, having already committed its
    own terminal and logged its own stack — it matches no row, so it is neither counted nor logged
    twice."""
    frame = TerminalFrame(
        status="failed",
        error_class=type(error).__name__,
        error_message=str(error)[:TERMINAL_ERROR_MESSAGE_MAX_CHARS] or None,
    )
    delay = FAILED_TERMINAL_RETRY_SECONDS
    while True:
        try:
            async with workspace_tx() as connection:
                transitioned = (
                    await connection.execute(
                        sa.update(tables.turn)
                        .values(
                            status="failed",
                            terminal=frame.model_dump(mode="json"),
                            updated_at=sa.func.now(),
                        )
                        .where(
                            tables.turn.c.id == turn_id,
                            sa.or_(
                                tables.turn.c.status == "queued",
                                sa.and_(
                                    tables.turn.c.status == "running",
                                    tables.turn.c.running_attempt == attempt,
                                ),
                            ),
                        )
                        .returning(tables.turn.c.subagent_profile, tables.turn.c.parent_turn_id)
                    )
                ).one_or_none()
            if transitioned is None:
                return
            emit_metric(
                "turn_terminal_total",
                status="failed",
                error_class=type(error).__name__,
                profile=turn_profile(
                    transitioned.subagent_profile,
                    spawned=transitioned.parent_turn_id is not None,
                ),
            )
            log_error(
                "turn.setup_failed",
                turn_id=str(turn_id),
                error_class=type(error).__name__,
                stack=formatted_stack(error),
            )
            await hub.publish(turn_id, Terminal(frame=frame))
            return
        except Exception as retried:
            log(
                "turn.terminal_backstop_retry",
                turn_id=str(turn_id),
                error_class=type(retried).__name__,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, FAILED_TERMINAL_RETRY_MAX_SECONDS)


@DBOS.workflow(name=TURN_WORKFLOW_NAME)
async def turn_workflow(workspace_id: str, turn_id: str) -> str:
    return await _execute_turn(workspace_id, turn_id)


async def _load_turn(turn_id: UUID) -> tuple[Turn, Agent, Audience]:
    """Load a turn and derive its exact audience from the bound conversation."""
    member_name = sa.func.coalesce(tables.agent.c.archived_name, tables.agent.c.name)
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.turn.c.id,
                    tables.turn.c.workspace_id,
                    tables.turn.c.conversation_id,
                    tables.turn.c.agent_id,
                    tables.turn.c.seq,
                    tables.turn.c.status,
                    tables.turn.c.inbound,
                    tables.turn.c.admission_source,
                    tables.turn.c.idempotency_key,
                    tables.turn.c.speaker_member_id,
                    tables.turn.c.created_at,
                    tables.turn.c.updated_at,
                    tables.turn.c.context,
                    tables.turn.c.terminal,
                    tables.turn.c.created_refs,
                    tables.turn.c.parent_turn_id,
                    tables.turn.c.subagent_profile,
                    tables.turn.c.subagent_name,
                    tables.turn.c.result_delivery,
                    tables.turn.c.external_retry_count,
                    tables.conversation.c.sandbox_conversation_id,
                    tables.turn.c.runtime_config,
                    tables.turn.c.model_accounts,
                    tables.turn.c.traceparent,
                    tables.agent.c.prompt,
                    tables.agent.c.model,
                    tables.agent.c.reasoning,
                    tables.agent.c.is_main,
                    tables.agent.c.tools,
                    tables.agent.c.output_schema,
                    tables.agent.c.internet_access_allowed,
                    tables.agent.c.use_workspace_skills,
                    member_name.label("name"),
                    tables.conversation.c.audience,
                )
                .select_from(
                    tables.turn.join(
                        tables.agent, tables.turn.c.agent_id == tables.agent.c.id
                    ).join(
                        tables.conversation,
                        tables.turn.c.conversation_id == tables.conversation.c.id,
                    )
                )
                .where(tables.turn.c.id == turn_id)
            )
        ).one()
    turn = Turn(
        id=row.id,
        workspace_id=row.workspace_id,
        conversation_id=row.conversation_id,
        agent_id=row.agent_id,
        seq=row.seq,
        status=row.status,
        inbound=row.inbound,
        admission_source=row.admission_source,
        idempotency_key=row.idempotency_key,
        speaker_member_id=row.speaker_member_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        context=None if row.context is None else TurnContext.model_validate(row.context),
        terminal=None if row.terminal is None else TerminalFrame.model_validate(row.terminal),
        created_refs=tuple(ObjectRef.model_validate(ref) for ref in row.created_refs or ()),
        parent_turn_id=row.parent_turn_id,
        subagent_profile=row.subagent_profile,
        subagent_name=row.subagent_name,
        result_delivery=row.result_delivery,
        external_retry_count=row.external_retry_count,
        sandbox_conversation_id=row.sandbox_conversation_id,
        traceparent=row.traceparent,
        runtime_config=(
            None
            if row.runtime_config is None
            else TurnRuntimeConfig.model_validate(row.runtime_config)
        ),
        model_accounts=tuple(
            ModelAccountCapability.model_validate(account) for account in row.model_accounts
        ),
    )
    return (
        turn,
        Agent(
            prompt=row.prompt,
            model=row.model,
            reasoning=row.reasoning,
            is_main=row.is_main,
            tools=None if row.tools is None else tuple(row.tools),
            output_schema=row.output_schema,
            internet_access_allowed=row.internet_access_allowed,
            use_workspace_skills=row.use_workspace_skills,
            name=row.name,
        ),
        parse_audience(row.audience),
    )


async def _run_lineage(turn: Turn) -> RunLineage | None:
    """Where a spawned turn's live activity publishes: the admitted ancestor whose stream every
    surface tails, found by following parent links to the turn that has none. None for a turn no
    spawn admitted — its own stream is the tailed one. An agent child publishes under its
    qualified target so the surface names which agent ran."""
    if turn.parent_turn_id is None:
        return None
    root = turn.parent_turn_id
    async with workspace_tx() as connection:
        while True:
            parent = (
                await connection.execute(
                    sa.select(tables.turn.c.parent_turn_id).where(tables.turn.c.id == root)
                )
            ).scalar_one()
            if parent is None:
                break
            root = parent
        profile = turn.subagent_profile
        if profile is None:
            member_name = sa.func.coalesce(tables.agent.c.archived_name, tables.agent.c.name)
            agent_name = (
                await connection.execute(
                    sa.select(member_name).where(tables.agent.c.id == turn.agent_id)
                )
            ).scalar_one()
            profile = f"agent:{agent_name}"
    return RunLineage(
        root_turn_id=root,
        parent_turn_id=turn.parent_turn_id,
        profile=profile,
        name=turn.subagent_name or "",
    )


async def _previous_turn_ended_at(turn: Turn) -> datetime | None:
    if turn.seq == 1:
        return None
    async with workspace_tx() as connection:
        ended_at = (
            await connection.execute(
                sa.select(tables.turn.c.updated_at).where(
                    tables.turn.c.conversation_id == turn.conversation_id,
                    tables.turn.c.seq == turn.seq - 1,
                )
            )
        ).scalar_one()
    return ended_at if ended_at.tzinfo is not None else ended_at.replace(tzinfo=UTC)


async def _frozen_billing_identity(turn_id: UUID, candidate: _BillingIdentity) -> _BillingIdentity:
    async with workspace_tx() as connection:
        stored = (
            await connection.execute(
                sa.select(tables.turn.c.billing_identity)
                .where(tables.turn.c.id == turn_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if stored is not None:
            identity = _BillingIdentity.model_validate(stored)
            if identity.attempt == candidate.attempt:
                return identity
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == turn_id)
            .values(
                billing_identity=candidate.model_dump(mode="json"),
                updated_at=sa.func.now(),
            )
        )
    return candidate


async def _stored_billing_identity(turn_id: UUID, attempt: str) -> _BillingIdentity | None:
    async with workspace_tx() as connection:
        stored = (
            await connection.execute(
                sa.select(tables.turn.c.billing_identity).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one_or_none()
    if stored is None:
        return None
    identity = _BillingIdentity.model_validate(stored)
    return identity if identity.attempt == attempt else None


async def _frozen_byok(turn_id: UUID, decided: bool, attempt: str) -> bool:
    """Whether a key of the workspace's own serves this run attempt, held to `decided` once per
    attempt on the turn row. The caller reads it off the same resolution the model client's key
    read walks, under the same speaker binding, so the verdict names the key that actually paid:
    a member's connected account bills nothing on the coding run it serves, and a turn the
    platform default serves is billed however many members have connected an account of their own.

    The money keys on the attempt: a turn parked and resumed re-runs every round for real under a
    fresh workflow id, and each burn is billed under its own attempt. So the verdict has to key on
    the attempt too. Frozen against the turn instead, a resume bills its whole re-run under the
    situation that held when the turn first started — a key added during the pause charges the
    workspace for calls its own key paid, one removed makes the entire re-run free, and either is
    repeatable with ordinary workspace permissions.

    Within one attempt it stays frozen, which is what a crash recovery needs: the re-execution
    rebuilds the engine from scratch and must bill the tokens already burned under the verdict they
    were burned under. The write claims the attempt only if it is unclaimed, and the answer is
    always the row's rather than the one this execution computed, so two recoveries of one attempt
    cannot bill it two ways."""
    async with workspace_tx() as connection:
        stored = (
            await connection.execute(
                sa.select(tables.turn.c.byok, tables.turn.c.byok_attempt).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).one_or_none()
        if stored is not None and stored.byok is not None and stored.byok_attempt == attempt:
            return bool(stored.byok)
        await connection.execute(
            sa.update(tables.turn)
            .where(
                tables.turn.c.id == turn_id,
                sa.or_(
                    tables.turn.c.byok_attempt.is_(None),
                    tables.turn.c.byok_attempt != attempt,
                ),
            )
            .values(byok=decided, byok_attempt=attempt, updated_at=sa.func.now())
        )
        settled = (
            await connection.execute(
                sa.select(tables.turn.c.byok, tables.turn.c.byok_attempt).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).one_or_none()
    if settled is not None and settled.byok is not None and settled.byok_attempt == attempt:
        return bool(settled.byok)
    return decided


async def _open_sandbox(
    sandboxes: ConversationSandbox,
    run_tokens: RunTokenCodec,
    turn: Turn,
    clis: Mapping[str, CliCredential],
    credentials: CredentialStore | None,
    slots: WorkspaceSlots,
    cache_rewrite: bool = False,
) -> SandboxSession:
    """Open the sandbox this turn runs in, under the turn's signed run token and the env its
    credentials derive.

    `sandbox_conversation_id` is the conversation that owns it, which a subagent inherits from the
    turn that spawned it at admission — so a delegation reuses the member's sandbox rather than
    provisioning another, and the files a child writes are the ones the parent reads. Unset means
    the turn's own conversation.

    git is the one sandbox client that will not present the run token unprompted: its default
    `http.proxyAuthMethod=anyauth` waits for a `407` challenge the proxy never sends, so its CONNECT
    arrives unattributed and is rejected before rule resolution. `GIT_PROXY_AUTH_CONFIG` presents
    the signed token on the first CONNECT as every other client already does, and rides every turn
    whether or not it holds a grant or a key. Each connector git host's credential helper rides the
    same config, so `git clone` and `git push` authenticate off the CLI sentinel the proxy swaps.

    The run token names the turn to the proxy and carries no conversation id, so a process in the
    container cannot recover the conversation from it. `CONVERSATION_ID_ENV` states the
    conversation in plain text, which is what lets work a
    turn leaves outside the workspace — a branch, a commit, a pull request — be joined back to the
    record that produced it. The conversation is the id worth stating rather than the turn: this
    opener runs every turn but keys the container on the conversation, so a follow-up turn resumes
    the same clone and a turn id would rename work the turn before it already pushed.
    Re-authorization keeps the export: `SandboxSession.authorize` rewrites only `PROXY_ENV_NAMES`
    and drops only the connector CLI vars it is handed, carrying everything else across
    unchanged.

    The turn is stated all the same, and only to the carrier: the container is the conversation's,
    so the commands one turn leaves running in it are told from a sibling turn's by nothing else,
    and a cancel must stop its own turn's alone.

    The credential derivations run here rather than at the turn's start, so a turn that never
    touches the sandbox reads no credential slot either."""
    run = RunToken(
        workspace_id=turn.workspace_id,
        turn_id=turn.id,
    )
    cache_config = cache_git_config() if cache_rewrite else ()
    with span("sandbox.open"):
        return await sandboxes.open(
            turn.sandbox_conversation_id or turn.conversation_id,
            turn.id,
            run_tokens.encode(run),
            {
                CONVERSATION_ID_ENV: str(turn.conversation_id),
                TOOL_BRIDGE_URL_ENV: TOOL_BRIDGE_URL,
                **_git_config_env((*GIT_PROXY_AUTH_CONFIG, *cache_config, *cli_git_config(clis))),
                **await _keyed_provider_env(credentials, slots, turn.workspace_id),
            },
        )


@dataclass(frozen=True)
class SandboxAuthorizer:
    sandbox: Sandbox
    run_tokens: RunTokenCodec
    grants: GrantStore | None
    clis: Mapping[str, CliCredential]
    turn: Turn

    async def authorize(
        self,
        connections: tuple[UUID, ...],
        call: str,
    ) -> SandboxAccess:
        env = await _grant_cli_env(
            self.grants,
            self.clis,
            self.turn.id,
            connections,
        )
        runtime_connections = (
            ()
            if self.turn.runtime_config is None or self.turn.runtime_config.connections is None
            else self.turn.runtime_config.connections
        )
        capability_id: UUID | None = None
        if connections != runtime_connections:
            async with workspace_tx() as connection:
                retained = sa.exists(
                    sa.select(tables.detached_task.c.turn_id).where(
                        tables.detached_task.c.capability_id == tables.sandbox_call_capability.c.id
                    )
                )
                capability_id = (
                    await connection.execute(
                        sa.select(tables.sandbox_call_capability.c.id).where(
                            tables.sandbox_call_capability.c.workspace_id == self.turn.workspace_id,
                            tables.sandbox_call_capability.c.turn_id == self.turn.id,
                            tables.sandbox_call_capability.c.call == call,
                            retained,
                        )
                    )
                ).scalar_one_or_none()
                if capability_id is None:
                    capability_id = uuid4()
                    await connection.execute(
                        sa.delete(tables.sandbox_call_capability).where(
                            tables.sandbox_call_capability.c.workspace_id == self.turn.workspace_id,
                            tables.sandbox_call_capability.c.turn_id == self.turn.id,
                            tables.sandbox_call_capability.c.call == call,
                        )
                    )
                    await connection.execute(
                        sa.insert(tables.sandbox_call_capability).values(
                            id=capability_id,
                            workspace_id=self.turn.workspace_id,
                            turn_id=self.turn.id,
                            call=call,
                            connections=[str(connection) for connection in connections],
                            created_at=datetime.now(UTC),
                        )
                    )
        run = RunToken(
            workspace_id=self.turn.workspace_id,
            turn_id=self.turn.id,
            capability_id=capability_id,
        )
        sandbox = self.sandbox.authorize(
            self.run_tokens.encode(run),
            frozenset(cli.env for cli in self.clis.values()) | GIT_IDENTITY_ENV,
            env,
        )
        if capability_id is None:
            return SandboxAccess(sandbox)
        return SandboxAccess(
            sandbox,
            partial(self._revoke, capability_id),
            capability_id,
        )

    async def _revoke(self, capability_id: UUID) -> None:
        async with workspace_tx() as connection:
            retained = sa.exists(
                sa.select(tables.detached_task.c.turn_id).where(
                    tables.detached_task.c.capability_id == capability_id
                )
            )
            await connection.execute(
                sa.delete(tables.sandbox_call_capability).where(
                    tables.sandbox_call_capability.c.id == capability_id,
                    tables.sandbox_call_capability.c.workspace_id == self.turn.workspace_id,
                    tables.sandbox_call_capability.c.turn_id == self.turn.id,
                    ~retained,
                )
            )
