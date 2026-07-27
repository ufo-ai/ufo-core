"""Durable turn execution: partitioned queue, the turn workflow, per-process runtime."""

import asyncio
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from dbos import DBOS, DBOSClient, EnqueueOptions, Queue

from ufo.blob import BlobStore, FilesystemBlobStore, S3BlobStore
from ufo.browser import CdpProvider
from ufo.config import Config
from ufo.connectors import CliCredential, ConnectorRegistry
from ufo.credentials import (
    CredentialRequests,
    CredentialStore,
    HostChoice,
    credential_host,
    slot_is_set,
)
from ufo.db import workspace_tx
from ufo.ext.loader import (
    connector_clis,
    injecting_slots,
    turn_hooks,
    turn_runtime_skills,
    turn_tools,
)
from ufo.ext.manifest import CredentialSlot, Manifest
from ufo.grants import GrantStore, grant_sentinel
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
from ufo.loop.subagents import SubagentRegistry, Subagents, subagent_system_prompt
from ufo.loop.transcript import Transcript
from ufo.memory import MemorySearch
from ufo.models.registry import ModelRegistry
from ufo.o11y import log, warn
from ufo.sandbox.fs_creds import (
    SandboxFsCredentialMinter,
    ensure_workspace_marker,
    workspace_key_prefix,
)
from ufo.sandbox.session import (
    SANDBOX_GID,
    SANDBOX_UID,
    Carrier,
    MountSpec,
    ProxyEndpoint,
    RunToken,
    RunTokenCodec,
    SandboxHandle,
    SandboxSession,
    SandboxSpec,
    format_sandbox_handle,
    sandbox_handle_id,
)
from ufo.schema import tables
from ufo.schema.records import (
    DBOS_APP_VERSION,
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
from ufo.tools.registry import ToolRegistry
from ufo.workspace import ws

SANDBOX_IMAGE_REF = "ufo-sandbox:latest"
GIT_PROXY_AUTH_CONFIG = (("http.proxyAuthMethod", "basic"),)
TURN_QUEUE_POLL_SECONDS = 0.1
FAILED_TERMINAL_RETRY_SECONDS = 1.0
FAILED_TERMINAL_RETRY_MAX_SECONDS = 30.0
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
    workspace_fs: SandboxFsCredentialMinter | None
    hub: Hub
    carrier: Carrier
    cdp_provider: CdpProvider | None
    search_provider: SearchProvider | None
    connectors: ConnectorRegistry
    proxy: ProxyEndpoint
    run_tokens: RunTokenCodec
    dbos: DBOSClient
    subagents: SubagentRegistry
    subagent_grants: dict[str, frozenset[str]]
    manifests: tuple[Manifest, ...]
    registry: ModelRegistry
    skills: SkillRegistry
    credentials: CredentialStore | None
    index: IndexBackend
    embed: EmbedClient
    artifact_token_secret: str
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
    with ws(UUID(workspace_id)):
        return await _run_turn(runtime, turn_id)


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
        turn, agent, audience_member_id = await _load_turn(UUID(turn_id))
        subagents = Subagents(client=runtime.dbos, registry=runtime.subagents, parent=turn)
        all_tools, tool_ext = turn_tools(
            runtime.manifests, runtime.credentials, runtime.index, runtime.embed
        )
        hooks = turn_hooks(runtime.manifests, runtime.credentials, runtime.index, runtime.embed)
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
            )
            allowed = set(profile.tool_names) | runtime.subagent_grants.get(
                profile.name, frozenset()
            )
            tools = ToolRegistry(
                tuple(tool for tool in all_tools if tool.name in allowed or tool.subagent_default)
            )
            system_prompt = rendered_prompt(resolved.prompt)
            max_rounds = MAIN_ROUND_LIMIT if payload.get("extended_context") else profile.max_rounds
            output_model = profile.output_model
        model = await runtime.registry.client_for(resolved.model)
        handle = await _open_sandbox(
            runtime.carrier,
            runtime.config.sandbox.backend,
            runtime.blob,
            runtime.workspace_fs,
            runtime.proxy,
            runtime.run_tokens,
            turn,
            GrantStore() if runtime.credentials is not None else None,
            connector_clis(runtime.manifests),
            runtime.credentials,
            injecting_slots(runtime.manifests),
        )
        sandbox = SandboxSession(carrier=runtime.carrier, handle=handle)
        for entry in preload:
            await mount_skill(sandbox, entry.skill)
        engine = TurnEngine(
            turn=turn,
            agent=resolved,
            system_prompt=system_prompt,
            model=model,
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
                audience_member_id=audience_member_id,
                speaker_member_id=turn.speaker_member_id,
            ),
            hub=runtime.hub,
            sandbox=sandbox,
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
                )
            ),
            public_base_url=runtime.config.connect.public_base_url,
            hooks=hooks,
            blob=runtime.blob,
            spawn=subagents.spawn,
            subagents=subagents,
            audience_member_id=audience_member_id,
            artifact_token_secret=runtime.artifact_token_secret,
            grants=(GrantStore() if runtime.credentials is not None else None),
            pricing=runtime.registry.pricing,
            reasoning=runtime.config.models.reasoning_effort,
            attempt=attempt,
            max_rounds=max_rounds,
            skills=skills,
            preload=preload,
            output_model=output_model,
        )
        frame = await engine.run()
        return "superseded" if frame is None else frame.status
    except TurnParked:
        return "parked"
    except asyncio.CancelledError:
        raise
    except Exception as error:
        await _commit_failed_terminal(runtime.hub, UUID(turn_id), error)
        return "failed"


async def _commit_failed_terminal(hub: Hub, turn_id: UUID, error: BaseException) -> None:
    """The backstop for failures outside the engine: retries until the wait can end."""
    frame = TerminalFrame(
        status="failed",
        error_class=type(error).__name__,
        error_message=str(error)[:TERMINAL_ERROR_MESSAGE_MAX_CHARS] or None,
    )
    delay = FAILED_TERMINAL_RETRY_SECONDS
    while True:
        try:
            async with workspace_tx() as connection:
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


async def _load_turn(turn_id: UUID) -> tuple[Turn, Agent, UUID | None]:
    """The turn, its agent, and the conversation's member (the memory subject the turn recalls
    and commits under) — member lives on the conversation, not the turn."""
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
                    tables.turn.c.traceparent,
                    tables.agent.c.prompt,
                    tables.agent.c.model,
                    tables.conversation.c.member_id,
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
        traceparent=row.traceparent,
    )
    return turn, Agent(prompt=row.prompt, model=row.model), row.member_id


async def _open_sandbox(
    carrier: Carrier,
    backend: str,
    blob: BlobStore,
    workspace_fs: SandboxFsCredentialMinter | None,
    proxy: ProxyEndpoint,
    run_tokens: RunTokenCodec,
    turn: Turn,
    grants: GrantStore | None,
    clis: Mapping[str, CliCredential],
    credentials: CredentialStore | None,
    slots: tuple[CredentialSlot, ...],
) -> SandboxHandle:
    """Create-or-resume the conversation's sandbox and keep its durable handle on the conversation
    row. A prior process's handle survives there, so a fresh serve reattaches the same sandbox from
    the stored id (seeded into `resume_id`) rather than stranding it; a row with no handle — or one
    another backend wrote — creates fresh. The returned handle's id is written back as
    `<backend>:<id>` only when it differs from what the row already holds, so a resume (same id)
    costs no write and a fresh create (or an overwrite of a reaped id) persists once — the reaper
    and the next process read this same column.

    git is the one sandbox client that will not present the run token unprompted: its default
    `http.proxyAuthMethod=anyauth` waits for a `407` challenge the proxy never sends, so its CONNECT
    arrives unattributed and is rejected before rule resolution. `GIT_PROXY_AUTH_CONFIG` presents
    the signed token on the first CONNECT as every other client already does, and rides every turn
    whether or not it holds a grant or a key. A workspace holding a git credential adds that host's
    extraheader to the same config, so `git clone` and `git push` authenticate off the sentinel the
    proxy swaps."""
    stored = await _stored_sandbox_handle(turn.conversation_id, turn.workspace_id)
    resume_id = None if stored is None else sandbox_handle_id(backend, stored)
    run = RunToken(workspace_id=turn.workspace_id, turn_id=turn.id)
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=turn.conversation_id,
            image_ref=SANDBOX_IMAGE_REF,
            mount=await _workspace_mount(
                blob, workspace_fs, turn.conversation_id, run, fresh_sandbox=resume_id is None
            ),
            proxy=proxy,
            run_token=run_tokens.encode(run),
            resume_id=resume_id,
            env={
                **_git_config_env(
                    (
                        *GIT_PROXY_AUTH_CONFIG,
                        *await _git_credential_config(credentials, slots, turn.workspace_id),
                    )
                ),
                **await _grant_cli_env(grants, clis, turn),
                **await _keyed_provider_env(credentials, slots, turn.workspace_id),
            },
        )
    )
    persisted = format_sandbox_handle(backend, handle.container_id)
    if persisted != stored:
        await _persist_sandbox_handle(turn.conversation_id, turn.workspace_id, persisted)
    return handle


def _git_config_env(settings: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """git's own env channel for configuration, which is how the turn reaches a git it never
    writes a config file for: one indexed key/value pair per setting, and the count git reads."""
    env = {"GIT_CONFIG_COUNT": str(len(settings))}
    for index, (key, value) in enumerate(settings):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


async def _git_credential_config(
    credentials: CredentialStore | None,
    slots: tuple[CredentialSlot, ...],
    workspace_id: UUID,
) -> tuple[tuple[str, str], ...]:
    """Each git host this workspace holds a credential for, as an `extraheader` carrying the slot's
    sentinel — never the secret, which the egress proxy swaps for `Basic` on the wire. git has no
    env var to read auth from, so a header it is; the proxy admits and MITMs the host off the same
    slot, which is why a slot with nothing stored must configure nothing: the sentinel would reach
    the provider verbatim over an opaque tunnel, failing a clone anonymous git would serve."""
    if credentials is None:
        return ()
    settings: list[tuple[str, str]] = []
    for slot in slots:
        target = slot.injection
        if target is None or target.git_basic_user is None:
            continue
        if not await slot_is_set(slot.name, slot.source, workspace_id, credentials):
            continue
        host = await credential_host(credentials, workspace_id, target.host)
        if host is None:
            warn("sandbox.git_host_unavailable", slot=slot.name)
            continue
        settings.append(
            (f"http.https://{host}/.extraheader", f"{target.header}: {target.sentinel}")
        )
    return tuple(settings)


async def _keyed_provider_env(
    credentials: CredentialStore | None,
    slots: tuple[CredentialSlot, ...],
    workspace_id: UUID,
) -> dict[str, str]:
    """Each keyed provider this workspace has a secret for, as the sandbox sees it: the declared env
    var set to the slot's sentinel — never the secret, which the egress proxy swaps in on the wire —
    and the resolved provider host, so the agent's own client authenticates and addresses the right
    region without holding or guessing either. A slot with nothing stored exports nothing, so the
    agent finds no half-usable variable for a provider the member has not keyed yet. A selection the
    declaration does not offer exports nothing and warns here as well as at the proxy, because the
    two roles withhold at different moments — the export when the sandbox opens, the egress when a
    request is made — and the member would otherwise see a variable that never appeared."""
    if credentials is None:
        return {}
    env: dict[str, str] = {}
    for slot in slots:
        target = slot.injection
        if target is None:
            continue
        host_env = target.host.env if isinstance(target.host, HostChoice) else None
        if target.env is None and host_env is None:
            continue
        if not await slot_is_set(slot.name, slot.source, workspace_id, credentials):
            continue
        host = await credential_host(credentials, workspace_id, target.host)
        if host is None:
            warn("sandbox.keyed_host_unavailable", slot=slot.name)
            continue
        if target.env is not None:
            env[target.env] = target.sentinel
        if host_env is not None:
            env[host_env] = host
    return env


async def _grant_cli_env(
    grants: GrantStore | None, clis: Mapping[str, CliCredential], turn: Turn
) -> dict[str, str]:
    """Each connector-declared CLI env var whose provider this turn may use — the acting member's
    own grant preferred, one shared with the agent's audience as the fallback — set to that
    grant's sentinel, so the CLI inside the sandbox authenticates and the proxy forwards by the
    same sentinel (engine and proxy derive it independently from the grant, no shared
    registration). The acting member is the speaker, else the member the turn acts on behalf of,
    mirroring `connector_account`. A static env var names no account, so two accounts in the
    winning tier cannot be disambiguated per request: rather than silently pick one —
    `connector_account` fails loud on the same ambiguity — the export is skipped and logged,
    so the CLI fails visibly to authenticate instead of acting as an unintended account."""
    if grants is None or not clis:
        return {}
    acting = (
        turn.speaker_member_id
        if turn.speaker_member_id is not None
        else turn.on_behalf_of_member_id
    )
    granted = await grants.active_grants(turn.workspace_id, turn.agent_id)
    env: dict[str, str] = {}
    for provider, cli in clis.items():
        private = sorted(
            grant.account_id
            for grant in granted
            if grant.provider == provider and not grant.shared and grant.grantor_member_id == acting
        )
        shared = sorted(
            grant.account_id for grant in granted if grant.provider == provider and grant.shared
        )
        accounts = private or shared
        if len(accounts) > 1:
            log(
                "sandbox.cli_grant_ambiguous",
                provider=provider,
                turn_id=str(turn.id),
                accounts=len(accounts),
            )
            continue
        if accounts:
            env[cli.env] = grant_sentinel(accounts[0])
    return env


async def _stored_sandbox_handle(conversation_id: UUID, workspace_id: UUID) -> str | None:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.conversation.c.sandbox_handle).where(
                    tables.conversation.c.id == conversation_id,
                    tables.conversation.c.workspace_id == workspace_id,
                )
            )
        ).scalar_one()


async def _persist_sandbox_handle(conversation_id: UUID, workspace_id: UUID, handle: str) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.conversation)
            .values(sandbox_handle=handle)
            .where(
                tables.conversation.c.id == conversation_id,
                tables.conversation.c.workspace_id == workspace_id,
            )
        )


async def _workspace_mount(
    blob: BlobStore,
    workspace_fs: SandboxFsCredentialMinter | None,
    conversation_id: UUID,
    run: RunToken,
    fresh_sandbox: bool,
) -> MountSpec:
    """The workspace is only the conversation's `workspace/` subtree — a sibling of the transcript
    under `conversations/<id>/`, never the transcript itself. The filesystem backend reaches it as a
    host bind mount; the S3 backend mounts it over s3fs with an STS credential scoped to the
    `workspace/` prefix. Neither backend lets the sandbox reach the transcript above it.

    filesystem: a host-path carrier (the core `local` carrier's cwd, the Docker carrier's bind
    mount) reaches the subtree as an absolute host path — Docker reads a relative `-v` source as a
    named volume, not a host directory, and a relative blob root (the default `./blobs`) would fail
    at container create. A container runs as the non-root `sandbox` user, so the mount must be owned
    by it or the file tools cannot write: serve creates the dir under its own uid, and when it runs
    as root (the bundle default) it holds CAP_CHOWN and hands the dir to the sandbox user. Off root
    — dev, where the mount is not uid-enforced — the chown is skipped.

    s3: issue an opaque turn-bound token for `conversations/<id>/workspace/*` and carry it, with the
    prefix and sandbox-reachable endpoint, into the MountSpec. The dedicated s3fs daemon redeems it
    through the sandbox proxy initially and whenever its short-lived credential nears expiry."""
    match blob:
        case FilesystemBlobStore():
            host_path = (blob.root / "conversations" / str(conversation_id) / "workspace").resolve()
            await asyncio.to_thread(host_path.mkdir, parents=True, exist_ok=True)
            if os.geteuid() == 0:
                await asyncio.to_thread(os.chown, host_path, SANDBOX_UID, SANDBOX_GID)
            return MountSpec(kind="filesystem", host_path=str(host_path))
        case S3BlobStore():
            if workspace_fs is None:
                raise RuntimeError(
                    "the s3 blob backend requires the sandbox-fs credential minter "
                    "(set blob.sts_role_arn, blob.s3_url)"
                )
            # The directory marker is this backend's form of the filesystem branch's mkdir.
            # Only a conversation's first sandbox can face an empty prefix — the marker outlives
            # every later mount — so a resume skips the write.
            if fresh_sandbox:
                await ensure_workspace_marker(blob, conversation_id)
            return MountSpec(
                kind="s3",
                bucket=workspace_fs.bucket,
                key_prefix=workspace_key_prefix(conversation_id),
                credential_token=workspace_fs.issue(conversation_id, run),
                s3_url=workspace_fs.s3_url,
                region=workspace_fs.region,
                path_style=workspace_fs.path_style,
            )
        case _:
            raise RuntimeError(
                f"unsupported blob backend for the sandbox workspace: {type(blob).__name__}"
            )
