import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.runtime import runtime_instance
from ufo.runtime.runtime_instance import (
    STALE_AFTER_SECONDS,
    STRANDED_TURN_GRACE_SECONDS,
    CancelReconciler,
    ExecutorRecovery,
    Heartbeat,
    StrandedTurnReconciler,
    record_fleet_seat,
)
from ufo.schema import tables
from ufo.schema.records import (
    INTENT_ADMISSION,
    MEMBER_ADMISSION,
    TerminalFrame,
    TurnAdmissionSource,
)


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


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_fleet_seat_has_no_workspace_and_counts_as_a_live_executor(db: None) -> None:
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
    assert str(instance_id) in await ExecutorRecovery()._live_executors()
    heartbeat = Heartbeat(instance_id=instance_id)
    await heartbeat.beat()
    assert await _row_live(instance_id)
    await heartbeat.retire()
    assert not await _row_present(instance_id)
    assert str(instance_id) not in await ExecutorRecovery()._live_executors()


@dataclass
class _RecordingClient:
    """Stands in for the DBOS store: `carries` maps a workflow id to the status DBOS holds for it,
    and an id absent from it is absent from the store."""

    cancelled: list[str] = field(default_factory=list)
    carries: dict[str, str] = field(default_factory=dict)
    asked: list[list[str]] = field(default_factory=list)

    async def cancel_workflow_async(self, workflow_id: str) -> None:
        self.cancelled.append(workflow_id)

    async def list_workflows_async(
        self,
        *,
        workflow_ids: list[str],
        status: list[str],
        load_input: bool,
        load_output: bool,
    ) -> list[SimpleNamespace]:
        self.asked.append(workflow_ids)
        return [
            SimpleNamespace(workflow_id=workflow_id)
            for workflow_id in workflow_ids
            if self.carries.get(workflow_id) in status
        ]


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


async def _turn(
    workspace_id: UUID,
    agent_id: UUID,
    status: str,
    parent_id: UUID | None,
    profile: str | None = "general_purpose",
    attempt: str | None = None,
    idle_seconds: float = 0,
    admission_source: TurnAdmissionSource = MEMBER_ADMISSION,
) -> UUID:
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
                subagent_profile=None if parent_id is None else profile,
                inbound="x",
                admission_source=admission_source,
                terminal=None if terminal is None else terminal.model_dump(mode="json"),
                parent_turn_id=parent_id,
                running_attempt=attempt,
                created_at=sa.func.now(),
                updated_at=datetime.now(UTC) - timedelta(seconds=idle_seconds),
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


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_cancel_reconciler_cancels_turns_left_live_under_a_cancelled_parent(db: None) -> None:
    workspace_id = await _workspace()
    agent_id = await _agent(workspace_id)
    cancelled_parent = await _turn(workspace_id, agent_id, "cancelled", None)
    orphan = await _turn(workspace_id, agent_id, "running", cancelled_parent)
    grandchild = await _turn(workspace_id, agent_id, "running", orphan)
    live_parent = await _turn(workspace_id, agent_id, "running", None)
    kept = await _turn(workspace_id, agent_id, "running", live_parent)
    client = _RecordingClient()
    await CancelReconciler(client=client, sessions=None).sweep()
    assert await _turn_status(orphan) == "cancelled"
    assert await _turn_status(grandchild) == "cancelled"
    assert await _turn_status(kept) == "running"
    assert set(client.cancelled) == {str(orphan), str(grandchild)}
    idle = _RecordingClient()
    await CancelReconciler(client=idle, sessions=None).sweep()
    assert idle.cancelled == []


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_cancel_reconciler_reaches_a_live_turn_under_a_done_intermediate(db: None) -> None:
    """The deep-orphan case: a cancelled root R has a child C that finished `done` on its own
    while a grandchild G it spawned is still running."""
    workspace_id = await _workspace()
    agent_id = await _agent(workspace_id)
    root = await _turn(workspace_id, agent_id, "cancelled", None)
    done_child = await _turn(workspace_id, agent_id, "done", root)
    grandchild = await _turn(workspace_id, agent_id, "running", done_child)
    great_grandchild = await _turn(workspace_id, agent_id, "running", grandchild)
    client = _RecordingClient()
    await CancelReconciler(client=client, sessions=None).sweep()
    assert await _turn_status(grandchild) == "cancelled"
    assert await _turn_status(great_grandchild) == "cancelled"
    assert await _turn_status(done_child) == "done"
    assert set(client.cancelled) == {str(grandchild), str(great_grandchild)}


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_cancel_never_crosses_an_agent_child_boundary(db: None) -> None:
    """A spawned agent is an independent peer: cancelling its spawner leaves it and its own
    subtree running, while cancelling the agent child itself still reaps what it spawned."""
    workspace_id = await _workspace()
    agent_id = await _agent(workspace_id)
    cancelled_spawner = await _turn(workspace_id, agent_id, "cancelled", None)
    peer = await _turn(workspace_id, agent_id, "running", cancelled_spawner, profile=None)
    peer_child = await _turn(workspace_id, agent_id, "running", peer)
    client = _RecordingClient()
    await CancelReconciler(client=client, sessions=None).sweep()
    assert await _turn_status(peer) == "running"
    assert await _turn_status(peer_child) == "running"
    assert client.cancelled == []

    cancelled_peer = await _turn(
        workspace_id, agent_id, "cancelled", cancelled_spawner, profile=None
    )
    tied = await _turn(workspace_id, agent_id, "running", cancelled_peer)
    await CancelReconciler(client=client, sessions=None).sweep()
    assert await _turn_status(tied) == "cancelled"
    assert await _turn_status(peer) == "running"
    assert set(client.cancelled) == {str(tied)}


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_cancel_reconciler_cancels_an_intent_child_of_a_plain_agent_turn(db: None) -> None:
    workspace_id = await _workspace()
    agent_id = await _agent(workspace_id)
    cancelled_parent = await _turn(workspace_id, agent_id, "cancelled", None)
    bridge_child = await _turn(
        workspace_id,
        agent_id,
        "running",
        cancelled_parent,
        profile=None,
        admission_source=INTENT_ADMISSION,
    )
    client = _RecordingClient()
    await CancelReconciler(client=client, sessions=None).sweep()
    assert await _turn_status(bridge_child) == "cancelled"
    assert client.cancelled == [str(bridge_child)]


_AGED = STRANDED_TURN_GRACE_SECONDS + 60


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_stranded_reconciler_cancels_a_turn_whose_workflow_cannot_reach_it(db: None) -> None:
    """The strand: a claimed turn whose attempt DBOS has ended or lost."""
    workspace_id = await _workspace()
    agent_id = await _agent(workspace_id)
    ended = await _turn(
        workspace_id, agent_id, "running", None, attempt="wf-ended", idle_seconds=_AGED
    )
    absent = await _turn(
        workspace_id, agent_id, "running", None, attempt="wf-absent", idle_seconds=_AGED
    )
    client = _RecordingClient(carries={"wf-ended": "CANCELLED"})
    await StrandedTurnReconciler(client=client, sessions=None).sweep()
    assert await _turn_status(ended) == "cancelled"
    assert await _turn_status(absent) == "cancelled"
    assert set(client.cancelled) == {"wf-ended", "wf-absent"}


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_stranded_reconciler_spares_a_live_turn_under_a_terminal_parent(db: None) -> None:
    """A spawned agent outlives its spawner, so a live child under a `done` parent is ordinary
    work."""
    workspace_id = await _workspace()
    agent_id = await _agent(workspace_id)
    done_parent = await _turn(workspace_id, agent_id, "done", None)
    live = await _turn(
        workspace_id, agent_id, "running", done_parent, attempt="wf-live", idle_seconds=_AGED
    )
    sibling = await _turn(
        workspace_id, agent_id, "running", done_parent, attempt="wf-dead", idle_seconds=_AGED
    )
    client = _RecordingClient(carries={"wf-live": "PENDING", "wf-dead": "SUCCESS"})
    await StrandedTurnReconciler(client=client, sessions=None).sweep()
    assert await _turn_status(live) == "running"
    assert await _turn_status(sibling) == "cancelled"
    assert client.cancelled == ["wf-dead"]


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_stranded_reconciler_spares_a_turn_that_is_merely_idle(db: None) -> None:
    """Row freshness is not liveness: a turn stepping normally goes minutes between writes. An
    ENQUEUED or DELAYED attempt still carries its turn, however long the row has sat."""
    workspace_id = await _workspace()
    agent_id = await _agent(workspace_id)
    queued_behind = await _turn(
        workspace_id, agent_id, "running", None, attempt="wf-enqueued", idle_seconds=_AGED * 100
    )
    waiting = await _turn(
        workspace_id, agent_id, "running", None, attempt="wf-delayed", idle_seconds=_AGED * 100
    )
    client = _RecordingClient(carries={"wf-enqueued": "ENQUEUED", "wf-delayed": "DELAYED"})
    await StrandedTurnReconciler(client=client, sessions=None).sweep()
    assert await _turn_status(queued_behind) == "running"
    assert await _turn_status(waiting) == "running"
    assert client.cancelled == []


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_stranded_reconciler_holds_off_inside_the_grace_window(db: None) -> None:
    """A claim landing beside the sweep's own reads is not a strand. The row is left for a later
    tick, and once the window passes the same row is taken."""
    workspace_id = await _workspace()
    agent_id = await _agent(workspace_id)
    fresh = await _turn(workspace_id, agent_id, "running", None, attempt="wf-fresh", idle_seconds=0)
    client = _RecordingClient()
    await StrandedTurnReconciler(client=client, sessions=None).sweep()
    assert await _turn_status(fresh) == "running"
    assert client.asked == []
    await StrandedTurnReconciler(client=client, grace_seconds=0, sessions=None).sweep()
    assert await _turn_status(fresh) == "cancelled"


@pytest.mark.parametrize("database_url", ["postgres"], indirect=True)
async def test_stranded_reconciler_leaves_undispatched_turns_to_the_dispatch_sweep(
    db: None,
) -> None:
    """A QUEUED or PARKED turn is re-offered under a fresh workflow id, so its current id going
    missing is that sweep's ordinary path."""
    workspace_id = await _workspace()
    agent_id = await _agent(workspace_id)
    queued = await _turn(
        workspace_id, agent_id, "queued", None, attempt="wf-queued", idle_seconds=_AGED
    )
    parked = await _turn(
        workspace_id, agent_id, "parked", None, attempt="wf-parked", idle_seconds=_AGED
    )
    unclaimed = await _turn(
        workspace_id, agent_id, "running", None, attempt=None, idle_seconds=_AGED
    )
    client = _RecordingClient()
    await StrandedTurnReconciler(client=client, sessions=None).sweep()
    assert await _turn_status(queued) == "queued"
    assert await _turn_status(parked) == "parked"
    assert await _turn_status(unclaimed) == "running"
    assert client.asked == []
