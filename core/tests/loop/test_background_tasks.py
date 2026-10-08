import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from starlette.applications import Starlette

from core.tests.access.proxy_fake import proxy_app
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import (
    RUNTIME_DIRNAME,
    ExecResult,
    SandboxSession,
    SandboxSpec,
)
from ufo.host.tools.builtins import BashInput, bash_handler
from ufo.runtime.access.egress_rules import SessionPolicy
from ufo.runtime.access.proxy_sessions import ProxySessions, SessionCreated
from ufo.runtime.background_tasks import (
    DETACHED_FOLLOW,
    BackgroundTaskSweep,
)
from ufo.runtime.billing.accounting import TURN_LABEL
from ufo.runtime.surfaces.admission import Admission, AdmissionInvoker
from ufo.runtime.tools.context import SpawnResult, ToolContext
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import BACKGROUND_TASK_KEY_PREFIX, Agent, Turn

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]
BEARER = "ufo_background-tasks-system-token"


@dataclass(frozen=True)
class _Bearer:
    async def bearer(self) -> str:
        return BEARER


@pytest.fixture
def fake() -> Starlette:
    return proxy_app(BEARER)


@pytest.fixture
async def proxy(fake: Starlette) -> AsyncIterator[ProxySessions]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=fake), base_url="https://proxy.test"
    ) as http:
        yield ProxySessions("https://proxy.test", _Bearer(), http)


async def _turn_session(proxy: ProxySessions, workspace_id: UUID, turn_id: UUID) -> SessionCreated:
    return await proxy.open(
        workspace_id,
        key=f"turn:{turn_id}:-:0123456789abcdef",
        labels={TURN_LABEL: str(turn_id)},
        policy=SessionPolicy(),
    )


@dataclass
class _RecordingClient:
    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)


@dataclass(frozen=True)
class _SessionProbes:
    session: SandboxSession

    async def run(self, conversation_id: UUID, command: str, timeout_s: int) -> ExecResult:
        return await self.session.bash(command, timeout_s=timeout_s)


@dataclass
class _RecordingProbes:
    session: SandboxSession
    conversations: list[UUID] = field(default_factory=list)

    async def run(self, conversation_id: UUID, command: str, timeout_s: int) -> ExecResult:
        self.conversations.append(conversation_id)
        return await self.session.bash(command, timeout_s=timeout_s)


@dataclass
class _LaunchingProbes:
    session: SandboxSession
    launch: Callable[[], Awaitable[None]]
    launched: bool = False

    async def run(self, conversation_id: UUID, command: str, timeout_s: int) -> ExecResult:
        if not self.launched:
            self.launched = True
            await self.launch()
        return await self.session.bash(command, timeout_s=timeout_s)


@dataclass(frozen=True)
class _Rig:
    turn: Turn
    session: SandboxSession
    ctx: ToolContext
    sweep: BackgroundTaskSweep
    clock: list[datetime]


async def _no_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn unused")


async def _rig(
    tmp_path: Path,
    runtime_id: str | None = None,
    sandbox_conversation_id: UUID | None = None,
) -> _Rig:
    turn = Turn(
        id=uuid4(),
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=1,
        status="running",
        inbound="hi",
        created_at=datetime(2026, 9, 14, tzinfo=UTC),
        sandbox_conversation_id=sandbox_conversation_id,
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=turn.workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=turn.agent_id,
                workspace_id=turn.workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=turn.conversation_id,
                workspace_id=turn.workspace_id,
                agent_id=turn.agent_id,
                surface="web",
                queue_key=uuid4().hex,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        if sandbox_conversation_id is not None:
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=sandbox_conversation_id,
                    workspace_id=turn.workspace_id,
                    agent_id=turn.agent_id,
                    surface="web",
                    queue_key=uuid4().hex,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn.id,
                workspace_id=turn.workspace_id,
                conversation_id=turn.conversation_id,
                agent_id=turn.agent_id,
                seq=1,
                status="done",
                inbound="hi",
                terminal={"status": "done", "text": "launched"},
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    carrier = LocalCarrier()
    handle = await carrier.create(
        SandboxSpec(
            conversation_id=sandbox_conversation_id or turn.conversation_id,
            image_ref=SANDBOX_IMAGE_REF,
            workspace_host_path=str(tmp_path / "ws"),
        )
    )
    if runtime_id is not None:
        runtime_root = carrier.ufo_home / RUNTIME_DIRNAME / runtime_id
        await asyncio.to_thread(runtime_root.mkdir, parents=True)
        handle = replace(handle, runtime_root=str(runtime_root))
    session = SandboxSession(carrier=carrier, handle=handle)
    ctx = ToolContext(
        sandbox=session,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_no_spawn,
        speaker_member_id=None,
        audience=conversation_audience(None),
        artifact_token_secret="",
    )
    admission = Admission(dbos=_RecordingClient(), durable_surfaces=frozenset())
    clock = [datetime.now(UTC)]
    sweep = BackgroundTaskSweep(
        probes=_SessionProbes(session),
        invoker_for=lambda scoped: AdmissionInvoker(admission=admission, workspace_id=scoped),
        clock=lambda: clock[0],
    )
    return _Rig(turn=turn, session=session, ctx=ctx, sweep=sweep, clock=clock)


async def _launch(rig: _Rig, command: str) -> str:
    result = await bash_handler(rig.ctx, BashInput(command=command, background=True))
    assert not result.is_error
    payload = json.loads(result.content[0].text.splitlines()[-1])
    return payload["task"]


async def _sweep(rig: _Rig, sweep: BackgroundTaskSweep | None = None) -> None:
    with ws(rig.turn.workspace_id):
        await (sweep or rig.sweep).run()


async def _turns(rig: _Rig) -> list[sa.Row]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(
                        tables.turn.c.inbound,
                        tables.turn.c.idempotency_key,
                        tables.turn.c.detached_until,
                    )
                    .where(tables.turn.c.conversation_id == rig.turn.conversation_id)
                    .order_by(tables.turn.c.seq)
                )
            ).all()
        )


async def _tasks(rig: _Rig) -> list[sa.Row]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(
                        tables.detached_task.c.task,
                        tables.detached_task.c.runtime_base,
                        tables.detached_task.c.sandbox_conversation_id,
                        tables.detached_task.c.follow_until,
                    )
                    .where(tables.detached_task.c.turn_id == rig.turn.id)
                    .order_by(tables.detached_task.c.task)
                )
            ).all()
        )


async def _task_base(rig: _Rig, task: str) -> str:
    rows = await _tasks(rig)
    return next(row.runtime_base for row in rows if row.task == task)


async def _wait_for_exit(rig: _Rig, task: str) -> None:
    exit_file = f"{await _task_base(rig, task)}.exit"
    for _ in range(200):
        if (await rig.session.bash(f'cat "{exit_file}" 2>/dev/null')).stdout.strip():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"{task} never wrote its exit file")


async def test_a_running_task_keeps_the_stamp_and_posts_nothing(tmp_path: Path, db: None) -> None:
    rig = await _rig(tmp_path)
    gate = "/workspace/release"
    await _launch(rig, f'while [ ! -f "{gate}" ]; do sleep 0.05; done')

    await _sweep(rig)

    turns = await _turns(rig)
    assert len(turns) == 1
    assert turns[0].detached_until is not None
    assert rig.turn.workspace_id in await rig.sweep.candidate_workspaces()
    await rig.session.bash(f'touch "{gate}"')


async def test_a_finished_task_is_posted_once_and_the_stamp_clears(
    tmp_path: Path, db: None, fake: Starlette, proxy: ProxySessions
) -> None:
    rig = await _rig(tmp_path)
    own = await _turn_session(proxy, rig.turn.workspace_id, rig.turn.id)
    other = await _turn_session(proxy, rig.turn.workspace_id, uuid4())
    task = await _launch(rig, "echo finished-marker; exit 3")
    base = await _task_base(rig, task)
    await _wait_for_exit(rig, task)
    sweep = replace(rig.sweep, sessions=proxy)

    await _sweep(rig, sweep)
    await _sweep(rig, sweep)

    turns = await _turns(rig)
    assert len(turns) == 2
    posted = turns[1]
    assert posted.idempotency_key == f"{BACKGROUND_TASK_KEY_PREFIX}{rig.turn.id.hex}:{task}"
    assert f"Background task {task} ended with exit code 3." in posted.inbound
    assert f"{base}.log" in posted.inbound
    assert "finished-marker" in posted.inbound
    assert turns[0].detached_until is None
    assert await _tasks(rig) == []
    assert rig.turn.workspace_id not in await rig.sweep.candidate_workspaces()
    assert fake.state.sessions[own.id]["revoked_at"] is not None
    assert fake.state.sessions[other.id]["revoked_at"] is None
    revokes = [target for method, target, _, _ in fake.state.calls if target.endswith("/revoke")]
    assert revokes == [f"/v1/sessions/{own.id}/revoke"]


async def test_a_running_turns_last_task_leaves_its_sessions_live(
    tmp_path: Path, db: None, fake: Starlette, proxy: ProxySessions
) -> None:
    rig = await _rig(tmp_path)
    own = await _turn_session(proxy, rig.turn.workspace_id, rig.turn.id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(status="running", terminal=None)
            .where(tables.turn.c.id == rig.turn.id)
        )
    task = await _launch(rig, "exit 0")
    await _wait_for_exit(rig, task)

    await _sweep(rig, replace(rig.sweep, sessions=proxy))

    assert await _tasks(rig) == []
    assert fake.state.sessions[own.id]["revoked_at"] is None
    assert [method for method, _, _, _ in fake.state.calls] == ["POST"]


async def test_a_task_whose_supervisor_died_is_posted_as_lost(tmp_path: Path, db: None) -> None:
    rig = await _rig(tmp_path)
    task = await _launch(rig, "sleep 30")
    base = await _task_base(rig, task)
    await rig.session.bash(f'kill -9 "$(cat "{base}.pid")"; pkill -9 -P "$(cat "{base}.pid")"')
    for _ in range(200):
        gone = await rig.session.bash(f'kill -0 "$(cat "{base}.pid")" 2>/dev/null || echo gone')
        if gone.stdout.strip() == "gone":
            break
        await asyncio.sleep(0.05)

    await _sweep(rig)

    turns = await _turns(rig)
    assert len(turns) == 2
    assert (
        f"Background task {task} is no longer running and wrote no exit code." in turns[1].inbound
    )
    assert turns[0].detached_until is None


async def test_a_task_alive_at_the_deadline_is_posted_as_unfollowed(
    tmp_path: Path, db: None
) -> None:
    rig = await _rig(tmp_path)
    gate = "/workspace/release"
    task = await _launch(rig, f'while [ ! -f "{gate}" ]; do sleep 0.05; done')
    rig.clock[0] = datetime.now(UTC) + DETACHED_FOLLOW + timedelta(seconds=1)

    await _sweep(rig)

    turns = await _turns(rig)
    assert len(turns) == 2
    assert f"Background task {task} has run for 24 hours and is no longer followed." in (
        turns[1].inbound
    )
    assert turns[0].detached_until is None
    await rig.session.bash(f'touch "{gate}"')


async def test_a_foreground_journal_entry_is_not_reported(tmp_path: Path, db: None) -> None:
    rig = await _rig(tmp_path)
    foreground = await bash_handler(rig.ctx, BashInput(command="echo foreground-marker"))
    assert not foreground.is_error
    task = await _launch(rig, "echo detached-marker")
    await _wait_for_exit(rig, task)

    await _sweep(rig)

    turns = await _turns(rig)
    assert len(turns) == 2
    assert "detached-marker" in turns[1].inbound
    assert "foreground-marker" not in turns[1].inbound


async def test_a_terminal_runtime_path_is_probed_verbatim(tmp_path: Path, db: None) -> None:
    rig = await _rig(tmp_path, runtime_id="terminal-runtime")
    task = await _launch(rig, "echo terminal-marker")
    base = await _task_base(rig, task)
    await _wait_for_exit(rig, task)

    await _sweep(rig)

    turns = await _turns(rig)
    assert f"$UFO_HOME/runs/terminal-runtime/tasks/{task}.log" in turns[1].inbound
    assert "terminal-marker" in turns[1].inbound
    assert base == f"$UFO_HOME/runs/terminal-runtime/tasks/{task}"


async def test_a_shared_sandbox_is_probed_through_its_owning_conversation(
    tmp_path: Path, db: None
) -> None:
    sandbox_conversation_id = uuid4()
    rig = await _rig(tmp_path, sandbox_conversation_id=sandbox_conversation_id)
    task = await _launch(rig, "echo shared-marker")
    await _wait_for_exit(rig, task)
    probes = _RecordingProbes(rig.session)

    await _sweep(rig, replace(rig.sweep, probes=probes))

    turns = await _turns(rig)
    assert len(turns) == 2
    assert "shared-marker" in turns[1].inbound
    assert set(probes.conversations) == {sandbox_conversation_id}


async def test_a_launch_during_cleanup_keeps_the_new_task(tmp_path: Path, db: None) -> None:
    rig = await _rig(tmp_path)
    finished = await _launch(rig, "echo first-marker")
    await _wait_for_exit(rig, finished)
    gate = "/workspace/release"
    launched: list[str] = []

    async def launch() -> None:
        launched.append(await _launch(rig, f'while [ ! -f "{gate}" ]; do sleep 0.05; done'))

    probes = _LaunchingProbes(rig.session, launch)
    sweep = replace(rig.sweep, probes=probes)

    await _sweep(rig, sweep)

    tasks = await _tasks(rig)
    turns = await _turns(rig)
    assert [row.task for row in tasks] == launched
    assert turns[0].detached_until == tasks[0].follow_until
    assert rig.turn.workspace_id in await rig.sweep.candidate_workspaces()
    await rig.session.bash(f'touch "{gate}"')
