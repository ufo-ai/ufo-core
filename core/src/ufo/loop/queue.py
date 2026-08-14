"""Durable turn execution: partitioned queue, the turn workflow, per-process runtime."""

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from dbos import DBOS, DBOSClient, EnqueueOptions, Queue

from ufo.agent_scope import agent
from ufo.audience import Audience, parse_audience
from ufo.blob import BlobStore
from ufo.browser import CdpProvider
from ufo.config import Config
from ufo.connectors import CliCredential, ConnectorRegistry
from ufo.credentials import (
    CredentialRequests,
    CredentialStore,
)
from ufo.db import workspace_tx
from ufo.ext.context import TurnInvoker
from ufo.ext.loader import (
    connector_clis,
    injecting_slots,
    turn_hooks,
    turn_runtime_skills,
    turn_tools,
)
from ufo.ext.manifest import CredentialSlot, Manifest, SubagentProfile
from ufo.ext.surface import TurnTailer
from ufo.grants import GrantStore
from ufo.hub import Hub, Terminal
from ufo.indexing import EmbedClient, IndexBackend
from ufo.loop.compaction import Compaction
from ufo.loop.engine import (
    MAIN_ROUND_LIMIT,
    TranscriptRepair,
    TurnEngine,
    TurnParked,
    _claim_turn_with_handoff,
)
from ufo.loop.prompts.render import render_system_prompt, rendered_prompt
from ufo.loop.subagents import (
    SubagentRegistry,
    SubagentResult,
    Subagents,
    subagent_system_prompt,
)
from ufo.loop.transcript import Transcript
from ufo.memory import MemorySearch
from ufo.models.registry import ModelRegistry
from ufo.o11y import emit_metric, formatted_stack, log, log_error, turn_profile
from ufo.sandbox.conversation import ConversationSandbox
from ufo.sandbox.exec_env import (
    CONVERSATION_ID_ENV,
    GIT_PROXY_AUTH_CONFIG,
    _git_config_env,
    _git_credential_config,
    _grant_cli_env,
    _keyed_provider_env,
)
from ufo.sandbox.session import (
    RunToken,
    RunTokenCodec,
    SandboxSession,
)
from ufo.schema import tables
from ufo.schema.records import (
    DBOS_APP_VERSION,
    INTENT_ADMISSION,
    TERMINAL_ERROR_MESSAGE_MAX_CHARS,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    Agent,
    TerminalFrame,
    Turn,
    TurnContext,
)
from ufo.search import SearchProvider
from ufo.skills.runtime import LoadedSkill, SkillRegistry, mount_skill
from ufo.tools.context import Spawn
from ufo.tools.registry import ToolDef, ToolRegistry
from ufo.workspace import ws

TURN_QUEUE_POLL_SECONDS = 0.1
FAILED_TERMINAL_RETRY_SECONDS = 1.0
FAILED_TERMINAL_RETRY_MAX_SECONDS = 30.0


def _subagent_tools(
    all_tools: tuple[ToolDef, ...], profile: SubagentProfile, grants: frozenset[str]
) -> tuple[ToolDef, ...]:
    allowed = set(profile.tool_names)
    if not profile.isolated_tools:
        allowed.update(grants)
    selected = tuple(
        tool
        for tool in all_tools
        if tool.name in allowed or (tool.subagent_default and not profile.isolated_tools)
    )
    return selected


TURN_QUEUE = Queue(
    TURN_QUEUE_NAME,
    concurrency=1,
    partition_queue=True,
    polling_interval_sec=TURN_QUEUE_POLL_SECONDS,
)


@dataclass(frozen=True)
class Runtime:
    config: Config
    blob: BlobStore
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
    registry: ModelRegistry
    skills: SkillRegistry
    credentials: CredentialStore | None
    index: IndexBackend
    embed: EmbedClient
    artifact_token_secret: str
    tailer: TurnTailer | None = None
    memory: MemorySearch | None = None


_runtime: Runtime | None = None


def init_runtime(runtime: Runtime) -> None:
    global _runtime
    if _runtime is not None:
        raise RuntimeError("runtime already initialized")
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
                agent_id = (
                    await connection.execute(
                        sa.select(tables.turn.c.agent_id).where(
                            tables.turn.c.id == turn_uuid,
                            tables.turn.c.workspace_id == workspace_uuid,
                        )
                    )
                ).scalar_one()
            with agent(agent_id):
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
        claimed, handoff = await _claim_turn_with_handoff(UUID(turn_id), attempt)
        if not claimed:
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
        turn, agent, audience = await _load_turn(UUID(turn_id))
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
        )

        def subagents_for(
            acting_member_id: UUID | None,
        ) -> tuple[Spawn, Subagents]:
            authorized = subagents.authorize(acting_member_id)
            return authorized.spawn, authorized

        all_tools, tool_ext = turn_tools(
            runtime.manifests,
            runtime.credentials,
            runtime.index,
            runtime.embed,
            audience=audience,
            public_base_url=runtime.config.connect.public_base_url,
        )
        hooks = turn_hooks(
            runtime.manifests,
            runtime.credentials,
            runtime.index,
            runtime.embed,
            runtime.tailer,
            audience=audience,
            public_base_url=runtime.config.connect.public_base_url,
        )
        skills = runtime.skills.merged_with(
            await turn_runtime_skills(
                runtime.manifests, runtime.credentials, runtime.index, runtime.embed
            )
        )
        sections = tuple(
            (section.name, section.body)
            for manifest in runtime.manifests
            for section in manifest.prompt_sections
        )
        preload: tuple[LoadedSkill, ...] = ()
        if turn.subagent_profile is None:
            resolved = agent.model_copy(update={"model": runtime.registry.resolve(agent.model)})
            tools = ToolRegistry(tuple(tool for tool in all_tools if not tool.profile_only))
            system_prompt = render_system_prompt(
                agent.prompt,
                sections,
                skills=skills.index(),
                knowledge_cutoff=runtime.registry.spec(resolved.model).knowledge_cutoff,
            )
            max_rounds = MAIN_ROUND_LIMIT
            output_model = None
        else:
            profile = runtime.subagents.get(turn.subagent_profile)
            payload = json.loads(turn.inbound) if turn.seq == 1 else {}
            preload = skills.closure(*(payload.get("preload_skills") or ()))
            resolved = Agent(
                prompt=subagent_system_prompt(profile, skills=skills.index(), preload=preload),
                model=runtime.registry.resolve(profile.model or agent.model),
                reasoning=agent.reasoning,
            )
            tools = ToolRegistry(
                _subagent_tools(
                    all_tools,
                    profile,
                    runtime.subagent_grants.get(profile.name, frozenset()),
                )
            )
            system_prompt = rendered_prompt(resolved.prompt)
            max_rounds = MAIN_ROUND_LIMIT if payload.get("extended_context") else profile.max_rounds
            output_model = profile.output_model
        model = await runtime.registry.client_for(resolved.model)
        grants = GrantStore() if runtime.credentials is not None else None
        clis = connector_clis(runtime.manifests)
        sandbox = await _open_sandbox(
            runtime.sandboxes,
            runtime.run_tokens,
            turn,
            grants,
            clis,
            runtime.credentials,
            injecting_slots(runtime.manifests),
        )
        sandbox_authorizer = SandboxAuthorizer(
            sandbox=sandbox,
            run_tokens=runtime.run_tokens,
            grants=grants,
            clis=clis,
            turn=turn,
        )
        for entry in preload:
            await mount_skill(sandbox, entry.skill)
        engine = TurnEngine(
            turn=turn,
            agent=resolved,
            system_prompt=system_prompt,
            model=model,
            provider=runtime.registry.spec(resolved.model).provider,
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
                speaker_member_id=None,
            ),
            hub=runtime.hub,
            sandbox=sandbox,
            sandbox_for=(
                None if turn.admission_source == INTENT_ADMISSION else sandbox_authorizer.authorize
            ),
            cdp_provider=runtime.cdp_provider,
            search_provider=runtime.search_provider,
            memory=runtime.memory,
            connectors=runtime.connectors,
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
            public_base_url=runtime.config.connect.public_base_url,
            hooks=hooks,
            blob=runtime.blob,
            spawn=subagents.spawn,
            subagents=subagents,
            subagents_for=subagents_for,
            audience=audience,
            artifact_token_secret=runtime.artifact_token_secret,
            grants=grants,
            previous_turn_ended_at=previous_turn_ended_at,
            pricing=runtime.registry.pricing,
            attempt=attempt,
            max_rounds=max_rounds,
            skills=skills,
            preload=preload,
            output_model=output_model,
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
                        .returning(tables.turn.c.subagent_profile)
                    )
                ).one_or_none()
            if transitioned is not None:
                emit_metric(
                    "turn_terminal_total",
                    status="failed",
                    error_class=type(error).__name__,
                    profile=turn_profile(transitioned.subagent_profile),
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
                    tables.turn.c.parent_turn_id,
                    tables.turn.c.subagent_profile,
                    tables.turn.c.result_delivery,
                    tables.conversation.c.sandbox_conversation_id,
                    tables.turn.c.traceparent,
                    tables.agent.c.prompt,
                    tables.agent.c.model,
                    tables.agent.c.reasoning,
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
        parent_turn_id=row.parent_turn_id,
        subagent_profile=row.subagent_profile,
        result_delivery=row.result_delivery,
        sandbox_conversation_id=row.sandbox_conversation_id,
        traceparent=row.traceparent,
    )
    return (
        turn,
        Agent(prompt=row.prompt, model=row.model, reasoning=row.reasoning),
        parse_audience(row.audience),
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


async def _open_sandbox(
    sandboxes: ConversationSandbox,
    run_tokens: RunTokenCodec,
    turn: Turn,
    grants: GrantStore | None,
    clis: Mapping[str, CliCredential],
    credentials: CredentialStore | None,
    slots: tuple[CredentialSlot, ...],
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
    unchanged."""
    run = RunToken(workspace_id=turn.workspace_id, turn_id=turn.id)
    return await sandboxes.open(
        turn.sandbox_conversation_id or turn.conversation_id,
        run_tokens.encode(run),
        {
            CONVERSATION_ID_ENV: str(turn.conversation_id),
            **_git_config_env(
                (
                    *GIT_PROXY_AUTH_CONFIG,
                    *await _git_credential_config(credentials, slots, turn.workspace_id),
                )
            ),
            **await _grant_cli_env(grants, clis, None, turn.id),
            **await _keyed_provider_env(credentials, slots, turn.workspace_id),
        },
    )


@dataclass(frozen=True)
class SandboxAuthorizer:
    sandbox: SandboxSession
    run_tokens: RunTokenCodec
    grants: GrantStore | None
    clis: Mapping[str, CliCredential]
    turn: Turn

    async def authorize(self, acting_member_id: UUID | None) -> SandboxSession:
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
