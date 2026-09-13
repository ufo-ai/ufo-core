"""Crash recovery does not redo completed work.

The proof for RFC 0008 Level 1: a turn killed mid-run recovers under the same DBOS `workflow_id`,
replaying its recorded model-round and tool-dispatch steps from `operation_outputs` — so a completed
tool call is NOT re-executed and its tokens are NOT re-spent — and resumes at the first unrecorded
step to a correct terminal. The crash is a `BaseException` raised mid-round: DBOS records no outcome
for the in-flight step and never finalizes the workflow, leaving it PENDING for
`recover_pending_workflows` to re-dispatch — exactly a killed worker recovering on a peer."""

import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
import sqlalchemy as sa
from dbos import DBOS, SetWorkflowID
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from sqlalchemy.engine import make_url
from ufo_ext_context_rollover.manifest import manifest as rollover_manifest
from ufo_ext_index_default import DefaultIndex
from ufo_testsupport.invoker import invoker_factory

from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.harness import o11y
from ufo.harness.durability import replay_safe_client
from ufo.harness.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.harness.models.interface import (
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
)
from ufo.harness.models.registry import ModelRegistry
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ExecResult, ProxyEndpoint, RunTokenCodec
from ufo.host.assemble import HostEnvironment
from ufo.host.ext.loader import skill_registry
from ufo.runtime import queue as loop_queue
from ufo.runtime.access.connectors import ConnectorRegistry
from ufo.runtime.hub import InProcessHub
from ufo.runtime.subagents import SubagentRegistry
from ufo.schema import tables
from ufo.schema.records import TerminalFrame, Usage

RECOVERY_TIMEOUT_SECONDS = 30


class _WorkerCrash(BaseException):
    """A hard mid-turn crash. A BaseException, not Exception, so DBOS records no step outcome and
    never finalizes the workflow — it stays PENDING and recoverable, like a killed worker."""


HITS_LOG = "hits.log"


@dataclass(frozen=True)
class _CrashOnceModel:
    """Round one calls bash to append a line into the workspace — the real side effect the test
    counts to prove the recorded step is replayed, never re-run; round two crashes once, then
    answers on recovery. The crash flag is a shared one-cell list so the recovered run (a fresh
    client instance) sees the crash already happened and answers instead of crashing again."""

    crashed: list[bool]

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        answered = any(
            isinstance(message.content, tuple)
            and any(isinstance(block, ToolResultBlock) for block in message.content)
            for message in request.messages
        )
        if answered:
            if not self.crashed[0]:
                self.crashed[0] = True
                raise _WorkerCrash("killed mid round two")
            yield TextDelta(text="recovered")
            yield Usage(input_tokens=1, output_tokens=1)
            return
        yield ToolCallStart(id="c1", name="bash")
        yield ToolCallDelta(
            id="c1",
            partial_json=json.dumps({"command": f"echo hi >> {HITS_LOG}"}),
        )
        yield Usage(input_tokens=2, output_tokens=2)


@dataclass(frozen=True)
class _CrashAfterBindFailureModel:
    crashed: list[bool]

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        results = tuple(
            block
            for message in request.messages
            if isinstance(message.content, tuple)
            for block in message.content
            if isinstance(block, ToolResultBlock)
        )
        if results:
            if not self.crashed[0]:
                self.crashed[0] = True
                raise _WorkerCrash("killed after a transient bind failure")
            aligned = (
                len(results) == 2
                and results[0].tool_use_id == "r1"
                and results[1].tool_use_id == "r2"
                and (
                    (
                        results[0].is_error
                        and isinstance(results[0].content, str)
                        and "transient bind" in results[0].content
                        and not results[1].is_error
                        and isinstance(results[1].content, str)
                        and "beta" in results[1].content
                    )
                    or (
                        not results[0].is_error
                        and isinstance(results[0].content, str)
                        and "alpha" in results[0].content
                        and results[1].is_error
                        and isinstance(results[1].content, str)
                        and "transient bind" in results[1].content
                    )
                )
            )
            yield TextDelta(text="recovered" if aligned else "crossed")
            yield Usage(input_tokens=1, output_tokens=1)
            return
        yield ToolCallStart(id="r1", name="read")
        yield ToolCallDelta(
            id="r1",
            partial_json=json.dumps(
                {
                    "file_path": "/workspace/alpha.txt",
                }
            ),
        )
        yield ToolCallStart(id="r2", name="read")
        yield ToolCallDelta(
            id="r2",
            partial_json=json.dumps(
                {
                    "file_path": "/workspace/beta.txt",
                }
            ),
        )
        yield Usage(input_tokens=2, output_tokens=2)


async def _seed_turn(model: str = "claude-opus-4-8") -> tuple[UUID, UUID, UUID]:
    workspace_id, member_id, agent_id, conversation_id, turn_id = (uuid4() for _ in range(5))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="a@b.c",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="be brief",
                model=model,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=str(turn_id),
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="queued",
                inbound="recover me",
                terminal=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, conversation_id, turn_id


def _install_runtime(config: Config, registry: ModelRegistry, workspace_root: Path) -> None:
    recovery_dbos = replay_safe_client(config.database.system_url)
    loop_queue.init_runtime(
        loop_queue.Runtime(
            config=config,
            blob=FilesystemBlobStore(root=config.blob.root),
            sandboxes=ConversationSandbox(
                carrier=LocalCarrier(),
                backend="local",
                off_cluster=False,
                image_ref=SANDBOX_IMAGE_REF,
                proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
                workspace_root=workspace_root,
            ),
            hub=InProcessHub(),
            cdp_provider=None,
            search_provider=None,
            connectors=ConnectorRegistry(entries={}),
            run_tokens=RunTokenCodec(b"turn-recovery-test-secret"),
            dbos=recovery_dbos,
            invoker_for=invoker_factory(recovery_dbos),
            subagents=SubagentRegistry(()),
            subagent_grants={},
            manifests=(rollover_manifest(),),
            environment=HostEnvironment(
                manifests=(rollover_manifest(),),
                credentials=None,
                index=DefaultIndex(transaction=workspace_tx),
                embed=_StubEmbed(),
            ),
            registry=registry,
            skills=skill_registry(()),
            credentials=None,
            index=DefaultIndex(transaction=workspace_tx),
            embed=_StubEmbed(),
            artifact_token_secret="",
        )
    )


@dataclass(frozen=True)
class _StubEmbed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(() for _ in texts)


async def _await_terminal(turn_id: UUID) -> TerminalFrame:
    async with asyncio.timeout(RECOVERY_TIMEOUT_SECONDS):
        while True:
            async with workspace_tx() as connection:
                row = (
                    await connection.execute(
                        sa.select(tables.turn.c.terminal).where(tables.turn.c.id == turn_id)
                    )
                ).one()
            if row.terminal is not None:
                return TerminalFrame.model_validate(row.terminal)
            await asyncio.sleep(0.05)


@pytest.mark.serial
async def test_crash_mid_turn_recovers_without_re_executing_completed_work(
    db: None, dbos_launched: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bash side effect lands exactly once across the crash and the recovery: the recorded
    round-one step replays from `operation_outputs`, never re-runs. The direct in-loop workflow
    call makes DBOS install its process-shared thread pool as this test loop's default executor;
    pytest-asyncio shuts the loop's default executor down at test end, which would kill that
    shared pool and leave every later queued workflow in this process PENDING forever — so the
    finally hands the loop a sacrificial executor to shut down instead.

    What the turn meters across the same crash: two executions start, the one that dies reaches no
    exit and records nothing, and the one that finishes records its own wall clock — the replayed
    round costs it milliseconds, so that number is this execution's work, never the crashed one's.
    Its round count is the two rounds its loop walked, the replayed one included, and the terminal
    is counted once, by the execution that wrote it."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(o11y.metrics, "get_meter", provider.get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    monkeypatch.setattr(o11y, "_histograms", {})
    monkeypatch.setattr(o11y, "_up_down_counters", {})
    crashed = [False]
    registry = ModelRegistry(
        specs={
            spec.id: replace(
                spec,
                client=lambda spec, key: _CrashOnceModel(crashed=crashed),
                key_slot="",
                key_env="",
            )
            for spec in CORE_MODEL_SPECS
        },
        pricing=CORE_PRICING,
        auto_model="claude-opus-4-8",
    )
    workspace_id, conversation_id, turn_id = await _seed_turn()
    hits = tmp_path / "workspaces" / str(conversation_id) / HITS_LOG

    saved = loop_queue._runtime
    loop_queue.reset_runtime()
    _install_runtime(dbos_launched, registry, tmp_path / "workspaces")
    try:
        with SetWorkflowID(str(turn_id)):
            with pytest.raises(_WorkerCrash):
                await loop_queue.turn_workflow(str(workspace_id), str(turn_id))

        assert (await asyncio.to_thread(hits.read_text)) == "hi\n"
        assert crashed[0] is True

        DBOS._recover_pending_workflows(["local"])
        terminal = await _await_terminal(turn_id)

        assert terminal.status == "done"
        assert terminal.text == "recovered"
        assert (await asyncio.to_thread(hits.read_text)) == "hi\n"
        assert terminal.tokens == 6
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.ledger.c.amount).where(tables.ledger.c.turn_id == turn_id)
                )
            ).all()
        assert [int(row.amount) for row in rows] == [6]
        points = {
            metric.name: metric.data.data_points
            for resource in reader.get_metrics_data().resource_metrics
            for scope in resource.scope_metrics
            for metric in scope.metrics
        }
        assert sum(point.value for point in points["ufo.turn_started_total"]) == 2
        (wall,) = points["ufo.turn_ms"]
        assert (wall.count, wall.attributes["status"]) == (1, "done")
        (rounds,) = points["ufo.turn_rounds_total"]
        assert (rounds.value, rounds.attributes["status"]) == (2, "done")
        (terminal,) = points["ufo.turn_terminal_total"]
        assert (terminal.value, terminal.attributes["status"]) == (1, "done")
    finally:
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        loop_queue._runtime.dbos.destroy()
        loop_queue.reset_runtime()
        if saved is not None:
            loop_queue.init_runtime(saved)


@pytest.mark.serial
async def test_bind_failure_keeps_dispatch_step_count_stable_on_recovery(
    db: None, dbos_launched: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    crashed = [False]
    registry = ModelRegistry(
        specs={
            spec.id: replace(
                spec,
                client=lambda spec, key: _CrashAfterBindFailureModel(crashed=crashed),
                key_slot="",
                key_env="",
            )
            for spec in CORE_MODEL_SPECS
        },
        pricing=CORE_PRICING,
        auto_model="claude-opus-4-8",
    )
    workspace_id, conversation_id, turn_id = await _seed_turn()
    workspace = tmp_path / "workspaces" / str(conversation_id)
    await asyncio.to_thread(workspace.mkdir, parents=True)
    await asyncio.to_thread((workspace / "alpha.txt").write_text, "alpha\n")
    await asyncio.to_thread((workspace / "beta.txt").write_text, "beta\n")
    original_authorize = loop_queue.SandboxAuthorizer.authorize
    second_bind_started = asyncio.Event()
    binds = 0

    async def authorize(
        authorizer: loop_queue.SandboxAuthorizer, connections: tuple[UUID, ...], call: str
    ):
        nonlocal binds
        binds += 1
        if binds == 1:
            async with asyncio.timeout(5):
                await second_bind_started.wait()
            raise RuntimeError("transient bind")
        second_bind_started.set()
        return await original_authorize(authorizer, connections, call)

    monkeypatch.setattr(loop_queue.SandboxAuthorizer, "authorize", authorize)
    saved = loop_queue._runtime
    loop_queue.reset_runtime()
    _install_runtime(dbos_launched, registry, tmp_path / "workspaces")
    try:
        with SetWorkflowID(str(turn_id)):
            with pytest.raises(_WorkerCrash):
                await loop_queue.turn_workflow(str(workspace_id), str(turn_id))

        DBOS._recover_pending_workflows(["local"])
        terminal = await _await_terminal(turn_id)

        assert terminal.status == "done"
        assert terminal.text == "recovered"
        assert binds == 2
    finally:
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        loop_queue._runtime.dbos.destroy()
        loop_queue.reset_runtime()
        if saved is not None:
            loop_queue.init_runtime(saved)


GUIDANCE_PROBE = "guidance-probe"


@dataclass(frozen=True)
class _GuidanceProbeModel:
    """Round one launches the probe command, whose dispatch kills the worker mid-command. The
    closing round reports what the window shows: `redirected` when the redone call came back clean
    and the member's follow-up is present beside it, `recovered` when the call was redone clean
    with no follow-up queued, `wrong` otherwise."""

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        results = tuple(
            block
            for message in request.messages
            if isinstance(message.content, tuple)
            for block in message.content
            if isinstance(block, ToolResultBlock)
        )
        if not results:
            yield ToolCallStart(id="c1", name="bash")
            yield ToolCallDelta(
                id="c1",
                partial_json=json.dumps({"command": f"echo {GUIDANCE_PROBE}"}),
            )
            yield Usage(input_tokens=2, output_tokens=2)
            return
        guidance = any(
            isinstance(message.content, str) and "do X instead" in message.content
            for message in request.messages
        )
        (result,) = results
        if guidance:
            yield TextDelta(text="redirected" if not result.is_error else "wrong")
        else:
            yield TextDelta(text="recovered" if not result.is_error else "wrong")
        yield Usage(input_tokens=1, output_tokens=1)


def _guidance_probe_registry() -> ModelRegistry:
    return ModelRegistry(
        specs={
            spec.id: replace(
                spec,
                client=lambda spec, key: _GuidanceProbeModel(),
                key_slot="",
                key_env="",
            )
            for spec in CORE_MODEL_SPECS
        },
        pricing=CORE_PRICING,
        auto_model="claude-opus-4-8",
    )


def _crash_once_exec(probe_execs: list[tuple[str, ...]]):
    original = LocalCarrier.exec

    async def exec_(
        self: LocalCarrier,
        handle: object,
        argv: tuple[str, ...],
        timeout_s: int,
        model_command: str | None = None,
    ) -> ExecResult:
        if not any(GUIDANCE_PROBE in arg for arg in argv):
            return await original(self, handle, argv, timeout_s, model_command)
        probe_execs.append(argv)
        if len(probe_execs) == 1:
            raise _WorkerCrash("killed mid command")
        return ExecResult(stdout=GUIDANCE_PROBE, stderr="", exit_code=0)

    return exec_


async def _admit_member_arrival(
    workspace_id: UUID, conversation_id: UUID, turn_id: UUID, body: str
) -> UUID:
    message_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.inbound_message).values(
                id=message_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                seq=1,
                body=body,
                admission_source="member",
                admitted_turn_id=turn_id,
                created_at=sa.func.now(),
            )
        )
    return message_id


@pytest.mark.serial
async def test_guidance_queued_during_the_crash_window_is_answered_after_the_redo(
    db: None, dbos_launched: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A member message durable before recovery does not preempt an adopted bash dispatch: bash
    keys its task on the call's idempotency key and reattaches, so preempting would strand running
    work a re-issued call could only duplicate. The re-executed dispatch lands its result, the
    next round folds the message beside it, and the model answers the member — the guidance waits
    one dispatch, never the work discarded."""
    workspace_id, conversation_id, turn_id = await _seed_turn()
    probe_execs: list[tuple[str, ...]] = []
    monkeypatch.setattr(LocalCarrier, "exec", _crash_once_exec(probe_execs))
    saved = loop_queue._runtime
    loop_queue.reset_runtime()
    _install_runtime(dbos_launched, _guidance_probe_registry(), tmp_path / "workspaces")
    try:
        with SetWorkflowID(str(turn_id)):
            with pytest.raises(_WorkerCrash):
                await loop_queue.turn_workflow(str(workspace_id), str(turn_id))
        assert len(probe_execs) == 1

        guidance_id = await _admit_member_arrival(
            workspace_id, conversation_id, turn_id, "stop, do X instead"
        )
        DBOS._recover_pending_workflows(["local"])
        terminal = await _await_terminal(turn_id)

        assert terminal.status == "done"
        assert terminal.text == "redirected"
        assert len(probe_execs) == 2
        async with workspace_tx() as connection:
            consumed = (
                await connection.execute(
                    sa.select(tables.inbound_message.c.consumed_turn_id).where(
                        tables.inbound_message.c.id == guidance_id
                    )
                )
            ).scalar_one()
        assert consumed == turn_id
    finally:
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        loop_queue._runtime.dbos.destroy()
        loop_queue.reset_runtime()
        if saved is not None:
            loop_queue.init_runtime(saved)


@pytest.mark.serial
async def test_adoption_with_nothing_queued_redoes_the_inflight_step(
    db: None, dbos_launched: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no member message pending, adoption keeps durable execution's contract: the in-flight
    step re-executes in full and the turn completes on its result."""
    workspace_id, _, turn_id = await _seed_turn()
    probe_execs: list[tuple[str, ...]] = []
    monkeypatch.setattr(LocalCarrier, "exec", _crash_once_exec(probe_execs))
    saved = loop_queue._runtime
    loop_queue.reset_runtime()
    _install_runtime(dbos_launched, _guidance_probe_registry(), tmp_path / "workspaces")
    try:
        with SetWorkflowID(str(turn_id)):
            with pytest.raises(_WorkerCrash):
                await loop_queue.turn_workflow(str(workspace_id), str(turn_id))

        DBOS._recover_pending_workflows(["local"])
        terminal = await _await_terminal(turn_id)

        assert terminal.status == "done"
        assert terminal.text == "recovered"
        assert len(probe_execs) == 2
    finally:
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
        loop_queue._runtime.dbos.destroy()
        loop_queue.reset_runtime()
        if saved is not None:
            loop_queue.init_runtime(saved)


WORKER = Path(__file__).parent / "recovery_worker.py"
CRASH_EXIT_CODE = 42
WORKER_TIMEOUT_SECONDS = 120


def _worker_env(app_url: str, system_url: str, blob_root: Path) -> dict[str, str]:
    ids = {name: str(uuid4()) for name in ("workspace", "member", "agent", "conversation", "turn")}
    return {
        **os.environ,
        "RECOVERY_TEST_APP_URL": app_url,
        "RECOVERY_TEST_SYSTEM_URL": system_url,
        "RECOVERY_TEST_BLOB_ROOT": str(blob_root),
        "RECOVERY_TEST_IDS": json.dumps(ids),
    }


def _run_worker(phase: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(WORKER), phase],
        env=env,
        capture_output=True,
        text=True,
        timeout=WORKER_TIMEOUT_SECONDS,
    )


@pytest.mark.serial
def test_queued_turn_killed_mid_run_recovers_on_fresh_boot(
    database_url: str, tmp_path: Path
) -> None:
    """The deploy-rollout scenario, with real process death: process one runs the turn through the
    partitioned turns queue and dies via os._exit mid-round-two, leaving the workflow PENDING with
    its recorded round-one steps; process two boots the way `ufoctl serve` boots (sync-context
    DBOS.launch, recovery firing during it, asyncio.run boot work continuing on the main thread)
    and must drive the turn to a done terminal. A hang here is the prod wedge: an eternally-running
    turn silently blocking its conversation partition."""
    if not database_url.startswith("postgresql"):
        pytest.skip("prod-topology recovery proof runs on postgres")
    system_url = _reset_private_system_db(database_url)
    env = _worker_env(database_url, system_url, tmp_path / "blobs")

    crashed = _run_worker("crash", env)
    assert crashed.returncode == CRASH_EXIT_CODE, (
        f"crash phase died wrong: rc={crashed.returncode}\n{crashed.stdout}\n{crashed.stderr}"
    )

    recovered = _run_worker("recover", env)
    assert recovered.returncode == 0, (
        f"recovered process never finished the turn: rc={recovered.returncode}\n"
        f"{recovered.stdout}\n{recovered.stderr}"
    )
    assert '"status": "done"' in recovered.stdout
    assert '"text": "recovered"' in recovered.stdout


def _reset_private_system_db(database_url: str) -> str:
    """A per-test DBOS system database, so these subprocess fleets never share queues with the
    session's own DBOS launch. Returns the psycopg-driver url the workers take."""
    base = make_url(database_url)
    name = f"{base.database}_recovery_sys"
    from ufo_testsupport.plugin import reset_postgres_database

    asyncio.run(reset_postgres_database(name))
    return (base.set(database=name, drivername="postgresql+psycopg")).render_as_string(
        hide_password=False
    )


def _workflow_attempts_and_steps(system_url: str, turn_id: str) -> tuple[int | None, list[int]]:
    dsn = system_url.replace("postgresql+psycopg", "postgresql")
    try:
        with psycopg.connect(dsn) as db, db.cursor() as cur:
            cur.execute(
                "select recovery_attempts from dbos.workflow_status where workflow_uuid = %s",
                (turn_id,),
            )
            row = cur.fetchone()
            cur.execute(
                "select function_id from dbos.operation_outputs"
                " where workflow_uuid = %s order by function_id",
                (turn_id,),
            )
            steps = [step for (step,) in cur.fetchall()]
    except psycopg.errors.UndefinedTable:
        return None, []
    return (None if row is None else row[0]), steps


LIVE_PEER_WINDOW_SECONDS = 15
ROUND_ONE_DISPATCH_STEP = 3


@pytest.mark.serial
def test_booting_peer_leaves_live_turn_alone_then_recovers_it_after_death(
    database_url: str, tmp_path: Path
) -> None:
    """The rolling-deploy scenario: a peer process boots while the incumbent is mid-turn and ALIVE.
    The booting peer must not treat the live turn as crashed work — stealing it starts a second
    concurrent execution whose loser parks forever in DBOS's duplicate-execution wait, wedging the
    turn and its whole conversation partition. Once the incumbent actually dies, the peer's
    executor-recovery sweep must reclaim the turn and finish it."""
    if not database_url.startswith("postgresql"):
        pytest.skip("prod-topology recovery proof runs on postgres")
    system_url = _reset_private_system_db(database_url)
    env = _worker_env(database_url, system_url, tmp_path / "blobs")
    turn_id = json.loads(env["RECOVERY_TEST_IDS"])["turn"]

    incumbent = subprocess.Popen(
        [sys.executable, str(WORKER), "incumbent"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    thief = None
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            attempts, steps = _workflow_attempts_and_steps(system_url, turn_id)
            if ROUND_ONE_DISPATCH_STEP in steps:
                break
            assert incumbent.poll() is None, "incumbent died before reaching round two"
            time.sleep(0.5)
        else:
            pytest.fail("incumbent never recorded its round-one steps")

        thief = subprocess.Popen(
            [sys.executable, str(WORKER), "thief"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        window_ends = time.monotonic() + LIVE_PEER_WINDOW_SECONDS
        while time.monotonic() < window_ends:
            attempts, _ = _workflow_attempts_and_steps(system_url, turn_id)
            assert attempts == 1, (
                f"the booting peer stole the live turn (recovery_attempts={attempts})"
            )
            time.sleep(1)

        incumbent.kill()
        out, _ = thief.communicate(timeout=90)
        assert thief.returncode == 0, f"peer never finished the dead incumbent's turn:\n{out}"
        assert '"text": "recovered"' in out
    finally:
        for proc in (incumbent, thief):
            if proc is not None and proc.poll() is None:
                proc.kill()
