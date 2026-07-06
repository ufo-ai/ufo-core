"""Durable turn execution: partitioned queue, the turn workflow, per-process runtime."""

import asyncio
import os
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa
from dbos import DBOS, DBOSClient, Queue

from selfhost.blob import BlobStore, FilesystemBlobStore, S3BlobStore
from selfhost.browser import CdpProvider
from selfhost.config import Config
from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.loader import turn_hooks, turn_tools
from selfhost.ext.manifest import Manifest
from selfhost.grants import GrantStore
from selfhost.hub import Hub, Terminal
from selfhost.indexing import EmbedClient, IndexBackend
from selfhost.loop.compaction import Compaction
from selfhost.loop.engine import MAIN_ROUND_LIMIT, TurnEngine, TurnParked
from selfhost.loop.prompts.render import render_system_prompt, rendered_prompt
from selfhost.loop.subagents import SubagentRegistry, Subagents, subagent_system_prompt
from selfhost.loop.transcript import Transcript
from selfhost.models.registry import ModelRegistry
from selfhost.o11y import log
from selfhost.sandbox.fs_creds import SandboxFsCredentialMinter, workspace_key_prefix
from selfhost.sandbox.session import (
    SANDBOX_GID,
    SANDBOX_UID,
    Carrier,
    MountSpec,
    ProxyEndpoint,
    RunToken,
    SandboxSession,
    SandboxSpec,
)
from selfhost.schema import tables
from selfhost.schema.records import (
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    Agent,
    TerminalFrame,
    Turn,
)
from selfhost.search import SearchProvider
from selfhost.skills.runtime import SkillRegistry
from selfhost.skills.store import UserSkillStore
from selfhost.tools.registry import ToolRegistry

SANDBOX_IMAGE_REF = "selfhost-sandbox:latest"
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
    cdp_provider: CdpProvider
    search_provider: SearchProvider | None
    proxy: ProxyEndpoint
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


@DBOS.step(preemptible=True)
async def _execute_turn(turn_id: str) -> str:
    runtime = _runtime
    if runtime is None:
        raise RuntimeError("runtime not initialized (init_runtime runs in serve)")
    try:
        turn, agent, member_id = await _load_turn(UUID(turn_id))
        subagents = Subagents(client=runtime.dbos, registry=runtime.subagents, parent=turn)
        all_tools, tool_ext = turn_tools(
            runtime.manifests, turn.workspace_id, runtime.credentials, runtime.index, runtime.embed
        )
        hooks = turn_hooks(
            runtime.manifests, turn.workspace_id, runtime.credentials, runtime.index, runtime.embed
        )
        skills = runtime.skills.merged_with(
            await UserSkillStore(runtime.blob).load_all(turn.workspace_id)
        )
        sections = tuple(
            (section.name, section.body)
            for manifest in runtime.manifests
            for section in manifest.prompt_sections
        )
        if turn.subagent_profile is None:
            resolved, tools = agent, ToolRegistry(all_tools)
            system_prompt = render_system_prompt(agent.prompt, sections, skills=skills.index())
            max_rounds = MAIN_ROUND_LIMIT
        else:
            profile = runtime.subagents.get(turn.subagent_profile)
            resolved = Agent(
                prompt=subagent_system_prompt(profile), model=profile.model or agent.model
            )
            allowed = set(profile.tool_names) | runtime.subagent_grants.get(
                profile.name, frozenset()
            )
            tools = ToolRegistry(
                tuple(tool for tool in all_tools if tool.name in allowed or tool.subagent_default)
            )
            system_prompt = rendered_prompt(resolved.prompt)
            max_rounds = profile.max_rounds
        resolved = resolved.model_copy(update={"model": runtime.registry.resolve(resolved.model)})
        model = runtime.registry.client_for(resolved.model)
        handle = await runtime.carrier.create(
            SandboxSpec(
                conversation_id=turn.conversation_id,
                image_ref=SANDBOX_IMAGE_REF,
                mount=await _workspace_mount(
                    runtime.blob, runtime.workspace_fs, turn.conversation_id
                ),
                proxy=runtime.proxy,
                run_token=RunToken(workspace_id=turn.workspace_id, turn_id=turn.id).encode(),
            )
        )
        engine = TurnEngine(
            turn=turn,
            agent=resolved,
            system_prompt=system_prompt,
            model=model,
            transcript=Transcript(blob=runtime.blob, conversation_id=turn.conversation_id),
            compaction=Compaction(
                client=model,
                model=resolved.model,
                blob=runtime.blob,
                conversation_id=turn.conversation_id,
            ),
            hub=runtime.hub,
            sandbox=SandboxSession(carrier=runtime.carrier, handle=handle),
            cdp_provider=runtime.cdp_provider,
            search_provider=runtime.search_provider,
            tools=tools,
            tool_ext=tool_ext,
            hooks=hooks,
            blob=runtime.blob,
            spawn=subagents.spawn,
            subagents=subagents,
            member_id=member_id,
            artifact_token_secret=runtime.artifact_token_secret,
            grants=(GrantStore() if runtime.credentials is not None else None),
            pricing=runtime.registry.pricing,
            reasoning=runtime.config.models.reasoning_effort,
            attempt=DBOS.workflow_id or turn_id,
            max_rounds=max_rounds,
            skills=skills,
        )
        frame = await engine.run()
        return "superseded" if frame is None else frame.status
    except TurnParked:
        return "parked"
    except asyncio.CancelledError:
        raise
    except Exception as error:
        await _commit_failed_terminal(runtime.hub, UUID(turn_id), type(error).__name__)
        raise


async def _commit_failed_terminal(hub: Hub, turn_id: UUID, error_class: str) -> None:
    """The backstop for failures outside the engine: retries until the wait can end."""
    frame = TerminalFrame(status="failed", error_class=error_class)
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
async def turn_workflow(turn_id: str) -> str:
    return await _execute_turn(turn_id)


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
                    tables.turn.c.terminal,
                    tables.turn.c.parent_turn_id,
                    tables.turn.c.subagent_profile,
                    tables.agent.c.prompt,
                    tables.agent.c.model,
                    tables.conversation.c.member_id,
                )
                .select_from(tables.turn.join(tables.agent).join(tables.conversation))
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
        terminal=None if row.terminal is None else TerminalFrame.model_validate(row.terminal),
        parent_turn_id=row.parent_turn_id,
        subagent_profile=row.subagent_profile,
    )
    return turn, Agent(prompt=row.prompt, model=row.model), row.member_id


async def _workspace_mount(
    blob: BlobStore, workspace_fs: SandboxFsCredentialMinter | None, conversation_id: UUID
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

    s3: mint a fresh credential scoped to `conversations/<id>/workspace/*` and carry it, with the
    prefix and the sandbox-reachable endpoint, into the MountSpec the carrier writes as the
    sandbox's AWS credentials before running s3fs."""
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
            credentials = await workspace_fs.mint(conversation_id)
            return MountSpec(
                kind="s3",
                bucket=workspace_fs.bucket,
                key_prefix=workspace_key_prefix(conversation_id),
                credentials=credentials,
                s3_url=workspace_fs.s3_url,
                region=workspace_fs.region,
                path_style=workspace_fs.path_style,
            )
        case _:
            raise RuntimeError(
                f"unsupported blob backend for the sandbox workspace: {type(blob).__name__}"
            )
