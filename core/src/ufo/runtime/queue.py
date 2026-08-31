"""Durable turn execution: partitioned queue, the turn workflow, per-process runtime."""

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from dbos import DBOS, DBOSClient, EnqueueOptions, Queue
from pydantic import BaseModel

from ufo.blob import WorkspaceBlobStore
from ufo.browser import CdpProvider
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.harness.models.interface import AUTO_MODEL
from ufo.harness.models.pricing import ModelPrice, Pricing
from ufo.harness.models.registry import ModelRegistry
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
    GIT_PROXY_AUTH_CONFIG,
    _git_config_env,
    _git_credential_config,
    _grant_cli_env,
    _keyed_provider_env,
)
from ufo.harness.sandbox.session import (
    RunToken,
    RunTokenCodec,
    Sandbox,
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
from ufo.runtime.agent_scope import agent
from ufo.runtime.billing.accounting import workspace_owns_the_key
from ufo.runtime.compaction import Compaction
from ufo.runtime.engine import (
    ADOPTED_CLAIM,
    MAIN_ROUND_LIMIT,
    AdoptionReplay,
    RunLineage,
    TranscriptRepair,
    TurnEngine,
    TurnParked,
    _claim_turn_with_handoff,
)
from ufo.runtime.environment import (
    EnvironmentRequest,
    EnvironmentTool,
    apply_environment_overrides,
    fetch_environment_overrides,
)
from ufo.runtime.ext.context import ExtensionContext, ModelAccess, TurnInvoker
from ufo.runtime.ext.hooks import HookChain
from ufo.runtime.ext.manifest import CredentialSlot, Manifest, SubagentProfile
from ufo.runtime.ext.surface import TurnTailer
from ufo.runtime.hub import Hub, Terminal
from ufo.runtime.indexing import EmbedClient, IndexBackend
from ufo.runtime.kinds.agent_setup import setup_skill
from ufo.runtime.kinds.provisioning import AgentProvisioning
from ufo.runtime.media.site_previewer import SitePreviewer
from ufo.runtime.memory import MemorySearch
from ufo.runtime.object_name import ObjectRef
from ufo.runtime.objects import BoundAction, ObjectVerbs
from ufo.runtime.prompts.render import render_system_prompt, rendered_prompt
from ufo.runtime.search import SearchProvider
from ufo.runtime.skills.runtime import (
    LoadedSkill,
    SkillCard,
    SkillMaterializer,
    SkillRegistry,
    SystemSkillBundle,
    load_skills,
)
from ufo.runtime.skills.selection import (
    SKILL_QUERY_MAX_CHARS,
    SKILL_TOP_K,
    MemberVisibility,
    member_visibility,
    prompt_index,
    select_top_k,
)
from ufo.runtime.spawn_catalog import spawn_catalog_skill
from ufo.runtime.subagents import (
    FINISH_CONTRACT,
    SubagentRegistry,
    SubagentResult,
    Subagents,
    subagent_system_prompt,
)
from ufo.runtime.tools.bridge import TOOL_BRIDGE_URL, TOOL_BRIDGE_URL_ENV
from ufo.runtime.tools.context import Spawn, UnknownSubagentProfile
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
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import (
    DBOS_APP_VERSION,
    INTENT_ADMISSION,
    INTERNAL_ADMISSION,
    SCHEDULED_ADMISSION,
    TERMINAL_ERROR_MESSAGE_MAX_CHARS,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    Agent,
    TerminalFrame,
    Turn,
    TurnAdmissionSource,
    TurnContext,
    TurnRuntimeConfig,
)

TURN_QUEUE_POLL_SECONDS = 0.1
MEMBER_TURN_CONCURRENCY = 8
SCHEDULED_TURN_CONCURRENCY = 4
_turn_slot_registry: dict[tuple[asyncio.AbstractEventLoop, str], asyncio.Semaphore] = {}


def _turn_gates(
    parent_turn_id: UUID | None, admission_source: TurnAdmissionSource
) -> tuple[asyncio.Semaphore, ...]:
    """The slots a turn must hold to run, held per event loop the way `db.py` holds its engines.
    DBOS cannot bound this: `worker_concurrency` is refused beside the turns queue's
    per-conversation `concurrency=1` and would count per partition if it stood, so an executor
    claims every due turn and a 66-turn wave lands whole on one loop — the CPU saturation that
    starves the connection pools. Root turns hold one of `MEMBER_TURN_CONCURRENCY` slots for their
    model loop. A scheduled turn first holds one of `SCHEDULED_TURN_CONCURRENCY` — acquired before
    the shared slot, so at most that many scheduled turns ever compete for shared slots and one
    workspace's scheduled wave cannot park a member's message behind a full process. Two kinds of
    turn hold nothing. A spawned turn's slot is held by its ancestors: a foreground parent waits
    inside its own slot for the child, so a gated child could wait forever. A prepared intent runs
    one typed verb with no model round while a portal panel waits a bounded 120 seconds on it —
    parked behind eight chat turns' model loops, every intent would time out exactly when the
    fleet is busiest."""
    if parent_turn_id is not None or admission_source == INTENT_ADMISSION:
        return ()
    loop = asyncio.get_running_loop()

    def slots(name: str, limit: int) -> asyncio.Semaphore:
        gate = _turn_slot_registry.get((loop, name))
        if gate is None:
            gate = _turn_slot_registry[(loop, name)] = asyncio.Semaphore(limit)
        return gate

    shared = slots("member", MEMBER_TURN_CONCURRENCY)
    if admission_source == SCHEDULED_ADMISSION:
        return (slots("scheduled", SCHEDULED_TURN_CONCURRENCY), shared)
    return (shared,)


FAILED_TERMINAL_RETRY_SECONDS = 1.0
FAILED_TERMINAL_RETRY_MAX_SECONDS = 30.0
SKILL_OWNER_KIND = "skill"
SKILL_SUBJECT = "workspace"
SKILL_SHADOW_TIMEOUT_SECONDS = 4.0


class _BillingIdentity(BaseModel):
    attempt: str
    model: str
    price_digest: str
    input: int
    output: int
    cache_read: int
    cache_write_5m: int
    cache_write_30m: int
    cache_write_1h: int


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
) -> tuple[ToolDef, ...]:
    """Hold the action verbs to the grant rule: a turn holding at least one canonical action id
    rides the wire with `object_action` and the two reads that publish an action's envelope —
    `object_list` for a collection action, `object_get` for an instance action — because an
    allowlist never names the dispatcher, and one naming only canonical ids would otherwise hold
    actions it can dispatch but never discover. `object_apply` and `object_delete` stay the
    allowlist's own to grant."""
    without = tuple(tool for tool in selected if tool.name != OBJECT_ACTION_TOOL)
    if not granted_actions:
        return without
    held = {tool.name for tool in without}
    by_name = {tool.name: tool for tool in all_tools}
    verbs = tuple(
        by_name[name]
        for name in (OBJECT_ACTION_TOOL, *ACTION_READ_TOOLS)
        if name in by_name and name not in held
    )
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
    authority, admitted through the panel's own gate. A speakerless intent comes from the sandbox
    tool bridge and remains inside the agent's allowlist because the model reaches it through
    `bash`."""
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
    concurrency=1,
    partition_queue=True,
    polling_interval_sec=TURN_QUEUE_POLL_SECONDS,
)


class TurnEnvironment(Protocol):
    """The environment a turn runs in, bound per turn from what the host layer discovered: the
    authorized tool offer with each tool's owning context and the object verbs, the reactive hook
    chain, the agent's member skills, and the connector plumbing the sandbox mounts. The runtime
    consumes it; only a composition root constructs an implementation."""

    def tools(
        self, *, audience: Audience, scheduled_member_id: UUID | None
    ) -> tuple[tuple[ToolDef, ...], dict[str, ExtensionContext], ObjectVerbs]: ...

    def hooks(self, *, audience: Audience) -> HookChain: ...

    async def member_skills(
        self, *, agent_name: str
    ) -> tuple[tuple[SkillCard, ...], SkillMaterializer]: ...

    def clis(self) -> dict[str, CliCredential]: ...

    def slots(self) -> tuple[CredentialSlot, ...]: ...


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
    the model-round, tool-dispatch, arrival-drain, and compaction steps inside `engine.run()` are
    the workflow's own steps and memoize for crash-recovery replay. Setup (claim, load, sandbox
    create-or-attach, engine build) re-runs each recovery and is idempotent; messages that arrive
    after the claim land on the conversation's inbound queue, which the engine drains at each round
    boundary. A fault outside the engine commits the terminal
    through the backstop so the client's wait still ends. The workspace is bound from the workflow
    argument for the whole body via `with ws(...)`: every query, credential read, and model call
    inside runs under it — the RLS scope on the shared RLS-subject role, the workspace's BYOK keys,
    and the ledger it bills. One fleet serves many workspaces from one pool; a single-workspace
    deploy binds its sole one."""
    runtime = _runtime
    if runtime is None:
        raise RuntimeError("runtime not initialized (init_runtime runs in serve)")
    workspace_uuid = UUID(workspace_id)
    turn_uuid = UUID(turn_id)
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
                        ).where(
                            tables.turn.c.id == turn_uuid,
                            tables.turn.c.workspace_id == workspace_uuid,
                        )
                    )
                ).one()
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
            await _commit_failed_terminal(runtime.hub, turn_uuid, error)
            status = "failed"
        await _deliver_to_parent(runtime, turn_uuid)
        return status


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


async def _enqueue_handoff(
    client: DBOSClient,
    workspace_id: UUID,
    turn_id: UUID,
    conversation_id: UUID,
    workflow_id: str,
) -> None:
    options: EnqueueOptions = {
        "queue_name": TURN_QUEUE_NAME,
        "workflow_name": TURN_WORKFLOW_NAME,
        "workflow_id": workflow_id,
        "queue_partition_key": str(conversation_id),
        "app_version": DBOS_APP_VERSION,
    }
    try:
        await client.enqueue_async(options, str(workspace_id), str(turn_id))
    except asyncio.CancelledError:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .values(dispatch_enqueued_at=None, updated_at=sa.func.now())
                .where(tables.turn.c.id == turn_id, tables.turn.c.status == "queued")
            )
        raise
    except Exception as error:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .values(dispatch_enqueued_at=None, updated_at=sa.func.now())
                .where(tables.turn.c.id == turn_id, tables.turn.c.status == "queued")
            )
        log(
            "turn.enqueue_deferred",
            turn_id=str(turn_id),
            error_class=type(error).__name__,
        )


async def _run_turn(runtime: Runtime, turn_id: str) -> str:
    try:
        attempt = DBOS.workflow_id or turn_id
        with span("turn.claim"):
            claim, handoff = await _claim_turn_with_handoff(UUID(turn_id), attempt)
        if claim is None:
            turn, _, _ = await _load_turn(UUID(turn_id))
            await TranscriptRepair(
                turn=turn,
                transcript=Transcript(blob=runtime.blob, conversation_id=turn.conversation_id),
                hub=runtime.hub,
            ).resolve()
            return "superseded"
        if handoff is not None:
            await _enqueue_handoff(
                runtime.dbos,
                handoff.workspace_id,
                handoff.id,
                handoff.conversation_id,
                handoff.workflow_id,
            )
        with span("turn.load"):
            turn, agent, audience = await _load_turn(UUID(turn_id))
        if turn.runtime_config is not None:
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
            key_slot_for=runtime.registry.key_slot_for,
            billing_url=runtime.billing_url,
        )

        def subagents_for(
            acting_member_id: UUID | None,
        ) -> tuple[Spawn, Subagents]:
            authorized = subagents.authorize(acting_member_id)
            return authorized.spawn, authorized

        with span("extensions.load"):
            all_tools, tool_ext, verbs = runtime.environment.tools(
                audience=audience,
                scheduled_member_id=(
                    turn.on_behalf_of_member_id
                    if turn.admission_source == SCHEDULED_ADMISSION
                    else None
                ),
            )
            hooks = runtime.environment.hooks(audience=audience)
            member_cards: tuple[SkillCard, ...] = ()
            materialize_member: SkillMaterializer = _without_workspace_skills
            if agent.use_workspace_skills:
                member_cards, materialize_member = await runtime.environment.member_skills(
                    agent_name=agent.name
                )
            skills = runtime.skills.merged_with(
                (
                    await spawn_catalog_skill(
                        runtime.subagents,
                        turn.speaker_member_id or turn.on_behalf_of_member_id,
                    ),
                )
            ).with_member(member_cards, materialize_member)
            sections = tuple(
                (section.name, section.body)
                for manifest in runtime.manifests
                for section in manifest.prompt_sections
            )
        preload: tuple[LoadedSkill, ...] = ()
        member_skill_block = ""
        connector_read_only = False
        if turn.subagent_profile is None:
            resolved = agent.model_copy(
                update={
                    "model": (
                        runtime.registry.resolve(agent.model)
                        if turn.runtime_config is None
                        else turn.runtime_config.model
                    ),
                    "internet_access_allowed": internet_access_allowed,
                }
            )
            granted_actions = _agent_actions(
                verbs.actions,
                agent.tools,
                turn.admission_source,
                turn.speaker_member_id,
            )
            tools = ToolRegistry(
                _with_action_verbs(
                    _agent_tools(
                        all_tools,
                        agent.tools,
                        turn.admission_source,
                        turn.speaker_member_id,
                    ),
                    all_tools,
                    granted_actions,
                )
            )
            waiting = await setup_skill(turn.agent_id, agent.is_main, turn.speaker_member_id)
            if waiting is not None:
                skills = skills.merged_with((waiting,))
            cards = tuple(skills.member_cards.values())
            view = member_visibility(turn.inbound, cards)
            member_skill_block = _member_skill_block(turn, view, runtime.config.skills.member_block)
            if _member_skill_turn(turn) and cards and not view.catalog_fits:
                _fire_shadow_selection(runtime.index, runtime.embed, turn, cards)
            max_rounds = MAIN_ROUND_LIMIT
            output_model: Contract | None = None
            if turn.spawned:
                output_model = output_contract(agent.output_schema)
        else:
            profile = _resolve_profile(runtime.subagents, turn_id, turn.subagent_profile)
            payload = json.loads(turn.inbound) if turn.seq == 1 else {}
            preload = await skills.materialize(
                skills.closure(*(payload.get("preload_skills") or ()))
            )
            member_skill_block = _member_skill_block(
                turn,
                member_visibility(turn.inbound, tuple(skills.member_cards.values())),
                runtime.config.skills.member_block,
            )
            resolved = Agent(
                prompt=subagent_system_prompt(
                    profile,
                    skills=_prompt_skill_index(skills, runtime.config.skills.member_block),
                    preload=preload,
                ),
                model=(
                    runtime.registry.resolve(profile.model or agent.model)
                    if turn.runtime_config is None
                    else turn.runtime_config.model
                ),
                reasoning=profile.reasoning or agent.reasoning,
                internet_access_allowed=internet_access_allowed,
            )
            granted_actions = _subagent_actions(
                verbs.actions, profile, runtime.subagent_grants.get(profile.name, frozenset())
            )
            tools = ToolRegistry(
                _with_action_verbs(
                    _subagent_tools(
                        all_tools,
                        profile,
                        runtime.subagent_grants.get(profile.name, frozenset()),
                    ),
                    all_tools,
                    granted_actions,
                )
            )
            system_prompt = rendered_prompt(resolved.prompt)
            max_rounds = MAIN_ROUND_LIMIT if payload.get("extended_context") else profile.max_rounds
            output_model = profile.output_model
            connector_read_only = profile.connector_read_only
        current_price = runtime.registry.pricing.prices[resolved.model]
        billing = await _frozen_billing_identity(
            turn.id,
            _BillingIdentity(
                attempt=attempt,
                model=resolved.model,
                price_digest=runtime.registry.pricing.digest,
                input=current_price.input,
                output=current_price.output,
                cache_read=current_price.cache_read,
                cache_write_5m=current_price.cache_write_5m,
                cache_write_30m=current_price.cache_write_30m,
                cache_write_1h=current_price.cache_write_1h,
            ),
        )
        resolved = resolved.model_copy(update={"model": billing.model})
        if turn.subagent_profile is None:
            system_prompt = render_system_prompt(
                agent.prompt,
                sections,
                skills=_prompt_skill_index(skills, runtime.config.skills.member_block),
                knowledge_cutoff=runtime.registry.spec(resolved.model).knowledge_cutoff,
            )
        model = await runtime.registry.client_for(resolved.model)
        pricing = Pricing(
            prices={
                billing.model: ModelPrice(
                    input=billing.input,
                    output=billing.output,
                    cache_read=billing.cache_read,
                    cache_write_5m=billing.cache_write_5m,
                    cache_write_30m=billing.cache_write_30m,
                    cache_write_1h=billing.cache_write_1h,
                )
            },
            digest=billing.price_digest,
        )
        byok = await _frozen_byok(
            turn.workspace_id,
            turn.id,
            runtime.registry.spec(resolved.model).key_slot or None,
            attempt,
        )
        grants = GrantStore() if runtime.credentials is not None else None
        clis = runtime.environment.clis()
        sandbox = _LateSandbox(
            conversation_id=turn.sandbox_conversation_id or turn.conversation_id,
            turn_id=turn.id,
            open=lambda: _open_sandbox(
                runtime.sandboxes,
                runtime.run_tokens,
                turn,
                grants,
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
        if preload:
            with span("skills.mount", count=len(preload)):
                await load_skills(sandbox, preload)
        prompt_replaced = False
        if turn.runtime_config is not None and turn.runtime_config.environment_host is not None:
            if not runtime.config.environment.dev_host_allowed:
                raise RuntimeError(
                    "the turn pins an environment host but this deployment does not allow one"
                )
            with span("environment.overrides"):
                overrides = await fetch_environment_overrides(
                    turn.runtime_config.environment_host,
                    EnvironmentRequest(
                        agent_name=agent.name,
                        model=resolved.model,
                        system_prompt=system_prompt.content,
                        tools=tuple(
                            EnvironmentTool(
                                name=tool.name,
                                description=tool.description,
                                extension=(
                                    None
                                    if tool.name not in tool_ext
                                    else tool_ext[tool.name].store.extension
                                ),
                            )
                            for tool in tools.tools
                        ),
                        subagent_profile=turn.subagent_profile,
                    ),
                )
                prompt_replaced = overrides.system_prompt is not None
                system_prompt, tools = apply_environment_overrides(system_prompt, tools, overrides)
        if turn.spawned and (turn.subagent_profile is None or prompt_replaced):
            system_prompt = rendered_prompt(f"{system_prompt.content}\n\n{FINISH_CONTRACT}")
        engine = TurnEngine(
            turn=turn,
            agent=resolved,
            byok=byok,
            system_prompt=system_prompt,
            model=model,
            activity_summarizer=ActivitySummarizer(
                ModelAccess(
                    replace(
                        runtime.registry,
                        auto_model=runtime.config.models.background_jobs_model,
                    ),
                    ACTIVITY_JOB,
                )
            ),
            provider=runtime.registry.spec(resolved.model).provider,
            reasoning=runtime.registry.spec(resolved.model).reasoning,
            transcript=Transcript(blob=runtime.blob, conversation_id=turn.conversation_id),
            compaction=Compaction(
                client=model,
                model=resolved.model,
                context_window=runtime.registry.spec(resolved.model).context_window,
                blob=runtime.blob,
                conversation_id=turn.conversation_id,
                hooks=hooks,
                turn=turn,
                agent=resolved,
                reasoning=runtime.registry.spec(resolved.model).reasoning,
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
            requestable_credentials=(
                None
                if runtime.credentials is None
                else CredentialRequests(
                    fernet=runtime.credentials.fernet,
                    declared=frozenset(
                        slot.name for manifest in runtime.manifests for slot in manifest.credentials
                    ),
                    fillable=frozenset(
                        slot.name
                        for manifest in runtime.manifests
                        for slot in manifest.credentials
                        if slot.member_filled
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
            previous_turn_ended_at=previous_turn_ended_at,
            pricing=pricing,
            attempt=attempt,
            max_rounds=max_rounds,
            skills=skills,
            member_skill_block=member_skill_block,
            preload=preload,
            output_model=output_model,
            adoption=AdoptionReplay(
                replaying=claim == ADOPTED_CLAIM
                and not turn.spawned
                and turn.admission_source != INTENT_ADMISSION
            ),
            verbs=verbs,
            granted_actions=granted_actions,
        )
        run = engine.run_intent if turn.admission_source == INTENT_ADMISSION else engine.run
        frame = await run()
        return "superseded" if frame is None else frame.status
    except TurnParked:
        return "parked"
    except asyncio.CancelledError:
        raise
    except Exception as error:
        await _commit_failed_terminal(runtime.hub, UUID(turn_id), error)
        return "failed"


async def _commit_failed_terminal(hub: Hub, turn_id: UUID, error: BaseException) -> None:
    """The backstop for failures outside the engine: retries until the wait can end. A setup fault
    — loading the turn, attaching the sandbox — has no engine to count it, so the terminal it writes
    is counted here, and only when this write is the transition: the engine's own failures commit
    their terminal first and leave nothing for this update to match.

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
                            tables.turn.c.status.in_(("queued", "running")),
                        )
                        .returning(tables.turn.c.subagent_profile, tables.turn.c.parent_turn_id)
                    )
                ).one_or_none()
            if transitioned is not None:
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
                    tables.turn.c.speaker_member_id,
                    tables.turn.c.on_behalf_of_member_id,
                    tables.turn.c.created_at,
                    tables.turn.c.updated_at,
                    tables.turn.c.context,
                    tables.turn.c.terminal,
                    tables.turn.c.created_refs,
                    tables.turn.c.parent_turn_id,
                    tables.turn.c.subagent_profile,
                    tables.turn.c.subagent_name,
                    tables.turn.c.result_delivery,
                    tables.conversation.c.sandbox_conversation_id,
                    tables.turn.c.runtime_config,
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
        speaker_member_id=row.speaker_member_id,
        on_behalf_of_member_id=row.on_behalf_of_member_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
        context=None if row.context is None else TurnContext.model_validate(row.context),
        terminal=None if row.terminal is None else TerminalFrame.model_validate(row.terminal),
        created_refs=tuple(ObjectRef.model_validate(ref) for ref in row.created_refs or ()),
        parent_turn_id=row.parent_turn_id,
        subagent_profile=row.subagent_profile,
        subagent_name=row.subagent_name,
        result_delivery=row.result_delivery,
        sandbox_conversation_id=row.sandbox_conversation_id,
        traceparent=row.traceparent,
        runtime_config=(
            None
            if row.runtime_config is None
            else TurnRuntimeConfig.model_validate(row.runtime_config)
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


async def _frozen_byok(
    workspace_id: UUID, turn_id: UUID, key_slot: str | None, attempt: str
) -> bool:
    """Whether the workspace's own key serves this run attempt, decided once per attempt and kept
    on the turn row.

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
        decided = await workspace_owns_the_key(connection, workspace_id, key_slot)
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
    grants: GrantStore | None,
    clis: Mapping[str, CliCredential],
    credentials: CredentialStore | None,
    slots: tuple[CredentialSlot, ...],
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
    whether or not it holds a grant or a key. A workspace holding a git credential adds that host's
    extraheader to the same config, so `git clone` and `git push` authenticate off the sentinel the
    proxy swaps.

    The run token names the turn to the proxy, and carries the workspace, the turn and the acting
    member but no conversation id, so a process in the container cannot recover the conversation
    from it. `CONVERSATION_ID_ENV` states the conversation in plain text, which is what lets work a
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
    run = RunToken(workspace_id=turn.workspace_id, turn_id=turn.id)
    cache_config = cache_git_config() if cache_rewrite else ()
    with span("sandbox.open"):
        return await sandboxes.open(
            turn.sandbox_conversation_id or turn.conversation_id,
            turn.id,
            run_tokens.encode(run),
            {
                CONVERSATION_ID_ENV: str(turn.conversation_id),
                TOOL_BRIDGE_URL_ENV: TOOL_BRIDGE_URL,
                **_git_config_env(
                    (
                        *GIT_PROXY_AUTH_CONFIG,
                        *cache_config,
                        *await _git_credential_config(credentials, slots, turn.workspace_id),
                    )
                ),
                **await _grant_cli_env(grants, clis, None, turn.id),
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

    async def authorize(self, acting_member_id: UUID | None) -> Sandbox:
        run_token = self.run_tokens.encode(
            RunToken(
                workspace_id=self.turn.workspace_id,
                turn_id=self.turn.id,
                acting_member_id=acting_member_id,
            )
        )
        return self.sandbox.authorize(
            run_token,
            frozenset(cli.env for cli in self.clis.values()),
            await _grant_cli_env(self.grants, self.clis, acting_member_id, self.turn.id),
        )
