import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from dbos import DBOS, DBOSClient, EnqueueOptions, WorkflowStatus
from dbos._dbos import _get_dbos_instance

from ufo import runtime_instance
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.loop.queue import TURN_QUEUE
from ufo.runtime_instance import (
    STALE_AFTER_SECONDS,
    CancelReconciler,
    ExecutorRecovery,
    Heartbeat,
    record_fleet_seat,
)
from ufo.schema import tables
from ufo.schema.records import TURN_QUEUE_NAME, TURN_WORKFLOW_NAME, TerminalFrame


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _insert_instance(workspace_id: UUID, heartbeat_age_seconds: float) -> UUID:
    instance_id = uuid4()
    when = datetime.now(UTC) - timedelta(seconds=heartbeat_age_seconds)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.runtime_instance).values(
                id=instance_id,
                workspace_id=workspace_id,
                heartbeat_at=when,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return instance_id


async def _row_present(instance_id: UUID) -> bool:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.runtime_instance.c.id).where(
                    tables.runtime_instance.c.id == instance_id
                )
            )
        ).one_or_none()
    return row is not None


async def _row_live(instance_id: UUID) -> bool:
    cutoff = datetime.now(UTC) - timedelta(seconds=STALE_AFTER_SECONDS)
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.runtime_instance.c.id).where(
                    tables.runtime_instance.c.id == instance_id,
                    tables.runtime_instance.c.heartbeat_at >= cutoff,
                )
            )
        ).one_or_none()
    return row is not None


async def test_a_transient_error_does_not_kill_the_heartbeat_loop(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    instance_id = await _insert_instance(
        workspace_id, heartbeat_age_seconds=STALE_AFTER_SECONDS + 20
    )
    real_tx = runtime_instance.owner_tx
    ticks = {"n": 0}

    def flaky_tx() -> object:
        ticks["n"] += 1
        if ticks["n"] == 1:
            raise sa.exc.SQLAlchemyError("transient connection reset")
        return real_tx()

    monkeypatch.setattr(runtime_instance, "owner_tx", flaky_tx)
    monkeypatch.setattr(runtime_instance, "HEARTBEAT_INTERVAL_SECONDS", 0.02)
    heartbeat = Heartbeat(instance_id=instance_id)
    task = asyncio.create_task(heartbeat.run())
    try:
        async with asyncio.timeout(5):
            while True:
                if await _row_live(instance_id):
                    break
                await asyncio.sleep(0.05)
    finally:
        task.cancel()
    assert ticks["n"] >= 2
    assert await _row_live(instance_id)


async def test_heartbeat_refreshes_a_stale_row_and_retire_removes_it(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = await _workspace()
    instance_id = await _insert_instance(
        workspace_id, heartbeat_age_seconds=STALE_AFTER_SECONDS + 20
    )
    assert not await _row_live(instance_id)
    monkeypatch.setattr(runtime_instance, "HEARTBEAT_INTERVAL_SECONDS", 0.02)
    heartbeat = Heartbeat(instance_id=instance_id)
    task = asyncio.create_task(heartbeat.run())
    try:
        async with asyncio.timeout(5):
            while True:
                if await _row_live(instance_id):
                    break
                await asyncio.sleep(0.05)
    finally:
        task.cancel()
    assert await _row_live(instance_id)
    await heartbeat.retire()
    assert not await _row_present(instance_id)


async def test_fleet_seat_has_no_workspace_and_counts_as_a_live_executor(
    db: None, dbos_launched: Config
) -> None:
    """The shared fleet's seat: recorded with no workspace (it serves them all), refreshed by the
    same heartbeat, and read as live by the executor-recovery sweep — so a booting fleet process is
    never swept as stranded and its retirement frees the seat like any instance's."""
    instance_id = uuid4()
    await record_fleet_seat(instance_id)
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.runtime_instance.c.workspace_id).where(
                    tables.runtime_instance.c.id == instance_id
                )
            )
        ).one()
    assert row.workspace_id is None
    sweep = ExecutorRecovery(dbos=_get_dbos_instance())
    assert str(instance_id) in await sweep._live_executors()
    heartbeat = Heartbeat(instance_id=instance_id)
    await heartbeat.beat()
    assert await _row_live(instance_id)
    await heartbeat.retire()
    assert not await _row_present(instance_id)
    assert str(instance_id) not in await sweep._live_executors()


UNDEQUEUEABLE_APP_VERSION = "claim-liveness-probe"


async def _workflow_status(workflow_id: str) -> str:
    (status,) = await asyncio.to_thread(
        DBOS.list_workflows, workflow_ids=[workflow_id], load_input=False, load_output=False
    )
    return status.status


async def _claim_a_workflow(system_url: str) -> str:
    """A real claim nothing executes: DBOS's own dequeue takes an enqueued workflow under this
    executor id, on an app version this deploy's queue workers never dequeue, so the row is PENDING
    and no task ever runs it."""
    workflow_id = str(uuid4())
    client = DBOSClient(system_database_url=system_url)
    try:
        options: EnqueueOptions = {
            "queue_name": TURN_QUEUE_NAME,
            "workflow_name": TURN_WORKFLOW_NAME,
            "workflow_id": workflow_id,
            "queue_partition_key": workflow_id,
            "app_version": UNDEQUEUEABLE_APP_VERSION,
        }
        await client.enqueue_async(options, str(uuid4()), workflow_id)
    finally:
        client.destroy()
    await _dequeue(workflow_id)
    return workflow_id


async def _dequeue(workflow_id: str) -> None:
    dequeued = await asyncio.to_thread(
        _get_dbos_instance()._sys_db.start_queued_workflows,
        TURN_QUEUE,
        DBOS.executor_id,
        UNDEQUEUEABLE_APP_VERSION,
        workflow_id,
        0,
    )
    assert dequeued == [workflow_id]
    assert await _workflow_status(workflow_id) == "PENDING"


async def _own_claims(sweep: ExecutorRecovery, *workflow_ids: str) -> list[WorkflowStatus]:
    return [
        status for status in await sweep._pending_workflows() if status.workflow_id in workflow_ids
    ]


@pytest.mark.serial
async def test_a_claim_is_released_only_where_consecutive_sweeps_find_no_execution(
    dbos_launched: Config,
) -> None:
    """One absence from the active-workflow set settles nothing — a workflow dequeued moments before
    a sweep has not reached its first step — so a claim survives a single sighting, and a sweep that
    finds the workflow executing starts the count over rather than leaving a sighting to pair with a
    later one. The release is read back as the ENQUEUED row the queue can dispatch again."""
    dbos = _get_dbos_instance()
    workflow_id = await _claim_a_workflow(dbos_launched.database.system_url)
    sweep = ExecutorRecovery(dbos=dbos)

    await sweep._release_unexecuted_claims(await _own_claims(sweep, workflow_id))
    assert await _workflow_status(workflow_id) == "PENDING"

    dbos._active_workflows_set.acquire(workflow_id, TURN_QUEUE_NAME, workflow_id)
    await sweep._release_unexecuted_claims(await _own_claims(sweep, workflow_id))
    dbos._active_workflows_set.release(workflow_id)
    assert await _workflow_status(workflow_id) == "PENDING"

    await sweep._release_unexecuted_claims(await _own_claims(sweep, workflow_id))
    assert await _workflow_status(workflow_id) == "PENDING"
    await sweep._release_unexecuted_claims(await _own_claims(sweep, workflow_id))
    assert await _workflow_status(workflow_id) == "ENQUEUED"


@pytest.mark.serial
async def test_a_re_dispatched_claim_gets_its_own_two_sweeps(dbos_launched: Config) -> None:
    """A released claim starts its count over: the row the queue dispatches next is a fresh claim in
    the dequeue-to-first-step gap, and carrying the released id would let one sighting release it —
    re-enqueueing a workflow whose execution is starting."""
    dbos = _get_dbos_instance()
    workflow_id = await _claim_a_workflow(dbos_launched.database.system_url)
    sweep = ExecutorRecovery(dbos=dbos)
    for _ in range(2):
        await sweep._release_unexecuted_claims(await _own_claims(sweep, workflow_id))
    assert await _workflow_status(workflow_id) == "ENQUEUED"

    await _dequeue(workflow_id)

    await sweep._release_unexecuted_claims(await _own_claims(sweep, workflow_id))
    assert await _workflow_status(workflow_id) == "PENDING"
    await sweep._release_unexecuted_claims(await _own_claims(sweep, workflow_id))
    assert await _workflow_status(workflow_id) == "ENQUEUED"


@pytest.mark.serial
async def test_a_claim_that_starts_executing_mid_sweep_keeps_it_and_starts_over(
    dbos_launched: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A claim absent when the sweep listed it can be executing by the time the sweep reaches it —
    its dequeue-to-first-step gap closing while an earlier release runs — which is why the set is
    read again at the release. That claim is left alone, and the sighting it spent there is gone, so
    the sweep after it starts a fresh pair rather than releasing on one absence. The interleaving is
    the real one: a second workflow acquires its active-set entry while the first release runs."""
    dbos = _get_dbos_instance()
    first, second = sorted(
        [
            await _claim_a_workflow(dbos_launched.database.system_url),
            await _claim_a_workflow(dbos_launched.database.system_url),
        ]
    )
    sweep = ExecutorRecovery(dbos=dbos)
    await sweep._release_unexecuted_claims(await _own_claims(sweep, first, second))
    assert await _workflow_status(second) == "PENDING"

    release_queue_assignment = dbos._sys_db.clear_queue_assignment

    def start_second(workflow_id: str) -> None:
        if workflow_id == first:
            dbos._active_workflows_set.acquire(second, TURN_QUEUE_NAME, second)
        release_queue_assignment(workflow_id)

    monkeypatch.setattr(dbos._sys_db, "clear_queue_assignment", start_second)
    await sweep._release_unexecuted_claims(await _own_claims(sweep, first, second))
    monkeypatch.undo()
    dbos._active_workflows_set.release(second)

    assert await _workflow_status(first) == "ENQUEUED"
    assert await _workflow_status(second) == "PENDING"
    await sweep._release_unexecuted_claims(await _own_claims(sweep, second))
    assert await _workflow_status(second) == "PENDING"
    await sweep._release_unexecuted_claims(await _own_claims(sweep, second))
    assert await _workflow_status(second) == "ENQUEUED"


@pytest.mark.serial
async def test_a_release_that_fails_spends_its_sighting(
    dbos_launched: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count is written before any release runs, so a release that raises — the tick dies, the
    loop logs it — leaves no sighting to pair with the next absence: a fresh pair releases the
    claim, never the single absence that follows the failure."""
    dbos = _get_dbos_instance()
    workflow_id = await _claim_a_workflow(dbos_launched.database.system_url)
    sweep = ExecutorRecovery(dbos=dbos)
    await sweep._release_unexecuted_claims(await _own_claims(sweep, workflow_id))

    def refuse(workflow_id: str) -> None:
        raise sa.exc.SQLAlchemyError("connection reset releasing the claim")

    monkeypatch.setattr(dbos._sys_db, "clear_queue_assignment", refuse)
    with pytest.raises(sa.exc.SQLAlchemyError):
        await sweep._release_unexecuted_claims(await _own_claims(sweep, workflow_id))
    monkeypatch.undo()
    assert await _workflow_status(workflow_id) == "PENDING"

    await sweep._release_unexecuted_claims(await _own_claims(sweep, workflow_id))
    assert await _workflow_status(workflow_id) == "PENDING"
    await sweep._release_unexecuted_claims(await _own_claims(sweep, workflow_id))
    assert await _workflow_status(workflow_id) == "ENQUEUED"


@pytest.mark.serial
async def test_an_executing_claim_is_never_sighted(dbos_launched: Config) -> None:
    """A claim the sweep finds executing is not sighted at all, so the pair it needs starts once the
    execution is gone. Counting it would pair that sighting with the moment a finishing workflow
    has released its entry and not yet written its outcome — one absence releasing a claim whose
    workflow was executing throughout."""
    dbos = _get_dbos_instance()
    workflow_id = await _claim_a_workflow(dbos_launched.database.system_url)
    sweep = ExecutorRecovery(dbos=dbos)

    dbos._active_workflows_set.acquire(workflow_id, TURN_QUEUE_NAME, workflow_id)
    await sweep._release_unexecuted_claims(await _own_claims(sweep, workflow_id))
    dbos._active_workflows_set.release(workflow_id)

    await sweep._release_unexecuted_claims(await _own_claims(sweep, workflow_id))
    assert await _workflow_status(workflow_id) == "PENDING"
    await sweep._release_unexecuted_claims(await _own_claims(sweep, workflow_id))
    assert await _workflow_status(workflow_id) == "ENQUEUED"


@dataclass
class _RecordingClient:
    cancelled: list[str] = field(default_factory=list)

    async def cancel_workflow_async(self, workflow_id: str) -> None:
        self.cancelled.append(workflow_id)


async def _agent(workspace_id: UUID) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="a",
                prompt="p",
                model="m",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


async def _turn(workspace_id: UUID, agent_id: UUID, status: str, parent_id: UUID | None) -> UUID:
    conversation_id, turn_id = uuid4(), uuid4()
    terminal = None if status in ("queued", "running", "parked") else TerminalFrame(status=status)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="subagent",
                queue_key=str(turn_id),
                member_id=None,
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
                status=status,
                inbound="x",
                terminal=None if terminal is None else terminal.model_dump(mode="json"),
                parent_turn_id=parent_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


async def _turn_status(turn_id: UUID) -> str:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one()


async def test_cancel_reconciler_cancels_turns_left_live_under_a_cancelled_parent(db: None) -> None:
    """The backstop: a turn left live under a cancelled parent — a fault mid-cancel, or an orphan
    DBOS recovery re-dispatched — is cancelled, along with its own descendants; a turn under a
    still-live parent is left untouched."""
    workspace_id = await _workspace()
    agent_id = await _agent(workspace_id)
    cancelled_parent = await _turn(workspace_id, agent_id, "cancelled", None)
    orphan = await _turn(workspace_id, agent_id, "running", cancelled_parent)
    grandchild = await _turn(workspace_id, agent_id, "running", orphan)
    live_parent = await _turn(workspace_id, agent_id, "running", None)
    kept = await _turn(workspace_id, agent_id, "running", live_parent)
    client = _RecordingClient()
    await CancelReconciler(client=client).sweep()
    assert await _turn_status(orphan) == "cancelled"
    assert await _turn_status(grandchild) == "cancelled"
    assert await _turn_status(kept) == "running"
    assert set(client.cancelled) == {str(orphan), str(grandchild)}
    idle = _RecordingClient()
    await CancelReconciler(client=idle).sweep()
    assert idle.cancelled == []


async def test_cancel_reconciler_reaches_a_live_turn_under_a_done_intermediate(db: None) -> None:
    """The deep-orphan case: a cancelled root R has a child C that finished `done` on its own while
    a grandchild G it spawned is still running. G's immediate parent is `done`, not cancelled, but G
    has a cancelled ancestor (R), so the ancestor-climbing sweep still reaches and cancels it — not
    only direct children of the cancelled turn."""
    workspace_id = await _workspace()
    agent_id = await _agent(workspace_id)
    root = await _turn(workspace_id, agent_id, "cancelled", None)
    done_child = await _turn(workspace_id, agent_id, "done", root)
    grandchild = await _turn(workspace_id, agent_id, "running", done_child)
    great_grandchild = await _turn(workspace_id, agent_id, "running", grandchild)
    client = _RecordingClient()
    await CancelReconciler(client=client).sweep()
    assert await _turn_status(grandchild) == "cancelled"
    assert await _turn_status(great_grandchild) == "cancelled"
    assert await _turn_status(done_child) == "done"
    assert set(client.cancelled) == {str(grandchild), str(great_grandchild)}
