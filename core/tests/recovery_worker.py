"""Subprocess halves of the queued-turn recovery proof (driven by test_turn_recovery.py).

Two phases, two real processes against one shared DBOS system database — the prod topology a
deploy rollout creates. `crash` runs a turn through the real partitioned turns queue and dies with
`os._exit` mid-round-two, exactly a killed pod: the workflow stays PENDING with its queue
assignment and two recorded steps. `recover` boots the way `ufoctl serve` boots — runtime installed,
`DBOS.launch()` from the sync main thread (recovery fires there), then the main thread keeps running
`asyncio.run(...)` work the way serve's remaining boot does — and must drive the recovered turn to
its terminal. On timeout it dumps every task on the DBOS background loop, so a hang names its exact
suspension point in the output the test surfaces."""

import asyncio
import faulthandler
import json
import os
import signal
import sys
import threading
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOS, DBOSClient, EnqueueOptions
from ufo_ext_index_default import DefaultIndex

from ufo.accounting import CORE_PRICING
from ufo.blob import FilesystemBlobStore
from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.connectors import ConnectorRegistry
from ufo.db import init_db, workspace_tx
from ufo.ext.loader import skill_registry
from ufo.ext.manifest import ModelProviderSpec
from ufo.hub import InProcessHub
from ufo.loop import queue as loop_queue
from ufo.loop.subagents import SubagentRegistry
from ufo.models.interface import (
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
)
from ufo.models.registry import ModelRegistry
from ufo.runtime_instance import ExecutorRecovery, Heartbeat
from ufo.sandbox.session import ExecResult, ProxyEndpoint, SandboxHandle, SandboxSpec
from ufo.schema import tables
from ufo.schema.records import (
    DBOS_APP_NAME,
    DBOS_APP_VERSION,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    Usage,
)

CRASH_EXIT_CODE = 42
RECOVERED_EXIT_CODE = 0
TIMEOUT_EXIT_CODE = 7
CRASH_WAIT_SECONDS = 60
INCUMBENT_WAIT_SECONDS = 600
THIEF_WAIT_SECONDS = 600
RECOVERY_WAIT_SECONDS = 30
INCUMBENT_ROUND_TWO_SECONDS = 6.0
THIEF_ROUND_TWO_SECONDS = 12.0


def _rounds_completed(request: ModelRequest) -> int:
    return sum(
        1
        for message in request.messages
        if isinstance(message.content, tuple)
        and any(isinstance(block, ToolResultBlock) for block in message.content)
    )


def _tool_result_seen(request: ModelRequest) -> bool:
    return _rounds_completed(request) > 0


@dataclass(frozen=True)
class _PacedModel:
    """Round one: instant bash call. Round two: stream for `round_two_seconds`, then another bash
    call. Round three: endless deltas while `endless` (a live incumbent mid-turn) or an instant
    final answer (a recoverer that must be able to finish the turn)."""

    round_two_seconds: float
    endless: bool

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        match _rounds_completed(request):
            case 0:
                yield ToolCallStart(id="c1", name="bash")
                yield ToolCallDelta(id="c1", partial_json='{"command": "echo one"}')
                yield Usage(input_tokens=2, output_tokens=2)
            case 1:
                deadline = asyncio.get_running_loop().time() + self.round_two_seconds
                while asyncio.get_running_loop().time() < deadline:
                    yield TextDelta(text="pacing ")
                    await asyncio.sleep(0.2)
                yield ToolCallStart(id="c2", name="bash")
                yield ToolCallDelta(id="c2", partial_json='{"command": "echo two"}')
                yield Usage(input_tokens=2, output_tokens=2)
            case _:
                if self.endless:
                    while True:
                        yield TextDelta(text="thinking ")
                        await asyncio.sleep(0.5)
                yield TextDelta(text="recovered")
                yield Usage(input_tokens=1, output_tokens=1)


@dataclass(frozen=True)
class _CrashModel:
    """Round one calls bash; round two dies like a killed pod — no unwind, no cleanup."""

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if _tool_result_seen(request):
            yield TextDelta(text="about to die")
            os._exit(CRASH_EXIT_CODE)
        yield ToolCallStart(id="c1", name="bash")
        yield ToolCallDelta(id="c1", partial_json='{"command": "echo hi"}')
        yield Usage(input_tokens=2, output_tokens=2)


@dataclass(frozen=True)
class _AnswerModel:
    """The recovered process's model: round two answers; round one never runs live (replayed)."""

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if not _tool_result_seen(request):
            raise RuntimeError("round one must replay from the recorded step, never re-run")
        yield TextDelta(text="recovered")
        yield Usage(input_tokens=1, output_tokens=1)


@dataclass(frozen=True)
class _EchoCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return SandboxHandle(conversation_id=spec.conversation_id, container_id="test")

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        return ExecResult(stdout="hi\n", stderr="", exit_code=0)

    async def destroy(self, handle: SandboxHandle) -> None: ...


@dataclass(frozen=True)
class _StubEmbed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(() for _ in texts)


@dataclass(frozen=True)
class _Env:
    app_url: str
    system_url: str
    blob_root: Path
    workspace_id: UUID
    member_id: UUID
    agent_id: UUID
    conversation_id: UUID
    turn_id: UUID

    @staticmethod
    def load() -> "_Env":
        ids = json.loads(os.environ["RECOVERY_TEST_IDS"])
        return _Env(
            app_url=os.environ["RECOVERY_TEST_APP_URL"],
            system_url=os.environ["RECOVERY_TEST_SYSTEM_URL"],
            blob_root=Path(os.environ["RECOVERY_TEST_BLOB_ROOT"]),
            workspace_id=UUID(ids["workspace"]),
            member_id=UUID(ids["member"]),
            agent_id=UUID(ids["agent"]),
            conversation_id=UUID(ids["conversation"]),
            turn_id=UUID(ids["turn"]),
        )


def _install_runtime(env: _Env, model: _CrashModel | _AnswerModel) -> None:
    config = Config(
        database=DatabaseConfig(url=env.app_url, system_url=env.system_url),
        blob=BlobConfig(backend="filesystem", root=env.blob_root),
    )
    registry = ModelRegistry(
        providers=(
            ModelProviderSpec(
                name="fake",
                matches=lambda name: True,
                client=lambda name, key: model,
            ),
        ),
        pricing=CORE_PRICING,
        auto_model="claude-opus-4-8",
    )
    loop_queue.init_runtime(
        loop_queue.Runtime(
            config=config,
            blob=FilesystemBlobStore(root=config.blob.root),
            workspace_fs=None,
            hub=InProcessHub(),
            carrier=_EchoCarrier(),
            cdp_provider=None,
            search_provider=None,
            connectors=ConnectorRegistry(entries={}),
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            dbos=DBOSClient(system_database_url=env.system_url),
            subagents=SubagentRegistry(()),
            subagent_grants={},
            manifests=(),
            registry=registry,
            skills=skill_registry(()),
            credentials=None,
            index=DefaultIndex(transaction=workspace_tx),
            embed=_StubEmbed(),
            artifact_token_secret="",
        )
    )


def _in_daemon_thread(factory: Callable[[], Coroutine[object, object, None]]) -> None:
    threading.Thread(target=lambda: asyncio.run(factory()), daemon=True).start()


def _launch_dbos(env: _Env) -> None:
    """Boot the way serve boots: a unique instance id doubling as the DBOS executor id, a seat row
    written before launch, then the heartbeat and executor-recovery loops at their shipped pace —
    the same pace that bounds production's crash-to-redispatch latency."""
    instance_id = uuid4()

    async def seat() -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.runtime_instance).values(
                    id=instance_id,
                    workspace_id=env.workspace_id,
                    started_at=sa.func.now(),
                    heartbeat_at=sa.func.now(),
                    fingerprint="test",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

    asyncio.run(seat())
    _in_daemon_thread(Heartbeat(instance_id=instance_id).run)
    DBOS(
        config={
            "name": DBOS_APP_NAME,
            "application_version": DBOS_APP_VERSION,
            "system_database_url": env.system_url,
            "executor_id": str(instance_id),
            "run_admin_server": False,
        }
    )
    DBOS.launch()
    _in_daemon_thread(ExecutorRecovery().run)


async def _seed(env: _Env) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=env.workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=env.member_id,
                workspace_id=env.workspace_id,
                email="a@b.c",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=env.agent_id,
                workspace_id=env.workspace_id,
                name="assistant",
                prompt="be brief",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=env.conversation_id,
                workspace_id=env.workspace_id,
                surface="cli",
                queue_key=str(env.turn_id),
                member_id=env.member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=env.turn_id,
                workspace_id=env.workspace_id,
                conversation_id=env.conversation_id,
                agent_id=env.agent_id,
                seq=1,
                status="queued",
                inbound="recover me",
                terminal=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


def _enqueue_like_admission(env: _Env) -> None:
    options: EnqueueOptions = {
        "queue_name": TURN_QUEUE_NAME,
        "workflow_name": TURN_WORKFLOW_NAME,
        "workflow_id": str(env.turn_id),
        "queue_partition_key": str(env.conversation_id),
        "app_version": DBOS_APP_VERSION,
    }
    client = DBOSClient(system_database_url=env.system_url)
    try:
        asyncio.run(client.enqueue_async(options, str(env.workspace_id), str(env.turn_id)))
    finally:
        client.destroy()


async def _read_terminal(env: _Env) -> dict[str, object] | None:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.terminal).where(tables.turn.c.id == env.turn_id)
            )
        ).one()
    return row.terminal


def _dump_background_loop_tasks() -> None:
    from dbos._dbos import _get_dbos_instance

    loop = _get_dbos_instance()._background_event_loop._loop
    if loop is None:
        print("BACKGROUND LOOP: never started", flush=True)
        return
    dumped = threading.Event()

    def dump() -> None:
        tasks = asyncio.all_tasks(loop)
        print(f"BACKGROUND LOOP TASKS: {len(tasks)}", flush=True)
        for task in tasks:
            print(f"--- task {task.get_name()}: {task!r}", flush=True)
            task.print_stack()
        dumped.set()

    loop.call_soon_threadsafe(dump)
    if not dumped.wait(5):
        print("BACKGROUND LOOP: wedged — call_soon_threadsafe callback never ran", flush=True)


def run_turn_until_killed(env: _Env, model: _CrashModel | _PacedModel, wait_seconds: float) -> int:
    """Seed, enqueue, and execute the turn, then hold the process open: the `crash` phase's model
    ends it with os._exit mid-round-two, the `incumbent` phase runs mid-turn until the test kills
    it. Falling out of the wait means neither happened — the phase failed."""
    init_db(env.app_url)
    asyncio.run(_seed(env))
    _install_runtime(env, model)
    _launch_dbos(env)
    _enqueue_like_admission(env)
    print("turn enqueued; running until killed", flush=True)
    threading.Event().wait(wait_seconds)
    print("phase timed out: the process outlived its expected death", flush=True)
    return 3


def thief(env: _Env) -> int:
    """Process two: boots exactly the way a rolling deploy's fresh pod boots while its peer is
    still mid-turn. Under the executor-id collision, its startup recovery steals the live turn;
    it must instead leave the peer alone, and finish the turn only once the peer is truly dead."""
    init_db(env.app_url)
    _install_runtime(env, _PacedModel(round_two_seconds=THIEF_ROUND_TWO_SECONDS, endless=False))
    _launch_dbos(env)
    print("thief launched", flush=True)
    waited = 0.0
    while waited < THIEF_WAIT_SECONDS:
        terminal = asyncio.run(_read_terminal(env))
        if terminal is not None:
            print(f"terminal: {json.dumps(terminal)}", flush=True)
            return RECOVERED_EXIT_CODE
        threading.Event().wait(0.5)
        waited += 0.5
    return TIMEOUT_EXIT_CODE


def recover(env: _Env) -> int:
    init_db(env.app_url)
    _install_runtime(env, _AnswerModel())
    _launch_dbos(env)
    waited = 0.0
    while waited < RECOVERY_WAIT_SECONDS:
        terminal = asyncio.run(_read_terminal(env))
        if terminal is not None:
            print(f"terminal: {json.dumps(terminal)}", flush=True)
            return RECOVERED_EXIT_CODE
        threading.Event().wait(0.2)
        waited += 0.2
    print("recovery timed out — dumping DBOS background loop", flush=True)
    _dump_background_loop_tasks()
    return TIMEOUT_EXIT_CODE


def main() -> int:
    faulthandler.register(signal.SIGUSR1)
    env = _Env.load()
    match sys.argv[1]:
        case "crash":
            return run_turn_until_killed(env, _CrashModel(), CRASH_WAIT_SECONDS)
        case "incumbent":
            paced = _PacedModel(round_two_seconds=INCUMBENT_ROUND_TWO_SECONDS, endless=True)
            return run_turn_until_killed(env, paced, INCUMBENT_WAIT_SECONDS)
        case "recover":
            return recover(env)
        case "thief":
            return thief(env)
        case phase:
            raise SystemExit(f"unknown phase: {phase}")


if __name__ == "__main__":
    sys.exit(main())
