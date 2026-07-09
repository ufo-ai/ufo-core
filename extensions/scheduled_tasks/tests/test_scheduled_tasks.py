"""End-to-end proof of the scheduled-tasks seam: the chat tool writes a durable schedule row, and
the batch-at-interval runner fires a due row into a real admitted turn through the invoke seam.

The runner drives the real `Admission` (real spend preflight, real turn row, real seq allocation);
only the DBOS enqueue stands in, recording the workflow id a live queue would receive — the same
seam `test_admission` stubs. So the chain proven is realistic input (a due schedule row) → durable
state (a queued turn) → an agent can run it, with nothing but the external queue faked."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_scheduled_tasks.manifest import NAME, RUNNER_JOB, manifest
from ufo_ext_scheduled_tasks.runner import ScheduledTaskRunner
from ufo_ext_scheduled_tasks.tasks import (
    CancelScheduledTaskInput,
    ListScheduledTasksInput,
    ScheduleTaskInput,
    cancel_scheduled_task,
    list_scheduled_tasks,
    schedule_task,
)

from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, context_for
from ufo.ext.loader import skill_registry
from ufo.jobs import JobRunner, bindings_from
from ufo.scheduling import ScheduleStore
from ufo.schema import tables
from ufo.schema.records import WRITEBACK_PENDING, Agent, Turn
from ufo.surfaces.admission import Admission, AdmissionInvoker
from ufo.tools.context import SpawnResult, ToolContext
from ufo.workspace import ws

DAILY_9AM = "0 9 * * *"


@dataclass
class StubDbos:
    """Stands in for the DBOS client at the admission seam: enqueue records the workflow id so a
    test reads back which turns were placed on the queue, never asserting DBOS itself."""

    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)


async def _seed(surface: str = "cli") -> tuple[UUID, UUID, UUID]:
    workspace_id, member_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4(), uuid4()
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
                email="who@example.com",
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
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface=surface,
                queue_key="session",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id, conversation_id


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in the scheduled-task tests")


def _tool_ctx(workspace_id: UUID, conversation_id: UUID, agent_id: UUID) -> ToolContext:
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="please schedule this",
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        member_id=None,
        artifact_token_secret="",
        ext=context_for(NAME, frozenset()),
    )


def _runner_ctx(invoker: AdmissionInvoker) -> ExtensionContext:
    return context_for(NAME, frozenset(), invoker=invoker)


async def _turns(conversation_id: UUID) -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.turn).where(tables.turn.c.conversation_id == conversation_id)
                )
            )
            .mappings()
            .all()
        )


async def test_schedule_task_tool_writes_durable_row(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    ctx = _tool_ctx(workspace_id, conversation_id, agent_id)
    with ws(workspace_id):
        result = await schedule_task(
            ctx,
            ScheduleTaskInput(schedule=DAILY_9AM, prompt="check the inbox for investor replies"),
        )
        assert result.is_error is False
        tasks = await ScheduleStore().list()
    assert len(tasks) == 1
    assert tasks[0].schedule == DAILY_9AM
    assert tasks[0].prompt == "check the inbox for investor replies"
    assert tasks[0].conversation_id == conversation_id
    assert tasks[0].agent_id == agent_id
    assert tasks[0].name.startswith("scheduled-")


async def test_reschedule_same_name_updates_in_place(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    ctx = _tool_ctx(workspace_id, conversation_id, agent_id)
    with ws(workspace_id):
        await schedule_task(
            ctx, ScheduleTaskInput(schedule=DAILY_9AM, prompt="daily", name="report")
        )
        await schedule_task(
            ctx, ScheduleTaskInput(schedule="0 17 * * 1", prompt="weekly", name="report")
        )
        tasks = await ScheduleStore().list()
    assert len(tasks) == 1
    assert tasks[0].schedule == "0 17 * * 1"
    assert tasks[0].prompt == "weekly"


async def test_runner_fires_due_task_into_a_turn(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    store = ScheduleStore()
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    with ws(workspace_id):
        await store.create(
            conversation_id,
            agent_id,
            "scheduled-daily",
            DAILY_9AM,
            "check inbox",
            "check inbox",
            due_at,
        )
        await ScheduledTaskRunner(ctx=_runner_ctx(invoker)).run()

        turns = await _turns(conversation_id)
        assert len(turns) == 1
        assert turns[0]["inbound"] == "check inbox"
        assert turns[0]["status"] == "queued"
        assert dbos.enqueued == [str(turns[0]["id"])]
        advanced = (await store.list())[0]
        assert advanced.last_run_at is not None
        assert await store.claim_due(datetime.now(UTC), 300) == ()


async def test_fire_into_a_durable_surface_conversation_registers_delivery(db: None) -> None:
    """The gap this guards: a scheduled fire admits through the same boundary as a surface ingest,
    so a task scheduled in a Slack thread delivers its reply there — the writeback row rides the
    turn insert, never a surface handler."""
    workspace_id, agent_id, conversation_id = await _seed(surface="slack")
    store = ScheduleStore()
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    invoker = AdmissionInvoker(
        admission=Admission(dbos=StubDbos(), durable_surfaces=frozenset({"slack"})),
        workspace_id=workspace_id,
    )
    with ws(workspace_id):
        await store.create(
            conversation_id,
            agent_id,
            "scheduled-daily",
            DAILY_9AM,
            "check inbox",
            "check inbox",
            due_at,
        )
        await ScheduledTaskRunner(ctx=_runner_ctx(invoker)).run()
        turns = await _turns(conversation_id)
        assert len(turns) == 1
        async with workspace_tx() as connection:
            status = (
                await connection.execute(
                    sa.select(tables.writeback.c.status).where(
                        tables.writeback.c.turn_id == turns[0]["id"]
                    )
                )
            ).scalar_one()
        assert status == WRITEBACK_PENDING


async def test_second_poll_does_not_refire_an_advanced_task(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    store = ScheduleStore()
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    dbos = StubDbos()
    ctx = _runner_ctx(
        AdmissionInvoker(
            admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
        ),
    )
    with ws(workspace_id):
        await store.create(
            conversation_id,
            agent_id,
            "scheduled-daily",
            DAILY_9AM,
            "check inbox",
            "check inbox",
            due_at,
        )
        await ScheduledTaskRunner(ctx=ctx).run()
        await ScheduledTaskRunner(ctx=ctx).run()
        assert len(await _turns(conversation_id)) == 1


async def test_cancel_scheduled_task_removes_it(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    ctx = _tool_ctx(workspace_id, conversation_id, agent_id)
    with ws(workspace_id):
        await schedule_task(ctx, ScheduleTaskInput(schedule=DAILY_9AM, prompt="x", name="watcher"))
        cancelled = await cancel_scheduled_task(
            ctx, CancelScheduledTaskInput(name="scheduled-watcher")
        )
        assert "Cancelled" in cancelled.content[0].text
        assert await ScheduleStore().list() == ()
        listed = await list_scheduled_tasks(ctx, ListScheduledTasksInput())
        assert listed.content[0].text == "No scheduled tasks."


async def test_schedule_task_rejects_non_five_field_cron(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    ctx = _tool_ctx(workspace_id, conversation_id, agent_id)
    with ws(workspace_id), pytest.raises(ValueError, match="5-field"):
        await schedule_task(
            ctx, ScheduleTaskInput(schedule="0 9 * * * *", prompt="too many fields")
        )


def test_task_scheduling_skill_parses_and_indexes() -> None:
    index = dict(skill_registry((manifest(),)).index())
    assert "task-scheduling" in index


async def test_manifest_job_fires_through_job_runner(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    with ws(workspace_id):
        await ScheduleStore().create(
            conversation_id,
            agent_id,
            "scheduled-daily",
            DAILY_9AM,
            "check inbox",
            "check inbox",
            due_at,
        )
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    runner = JobRunner(
        bindings=bindings_from((manifest(),), ()),
        invoker_factory=lambda wid: AdmissionInvoker(admission=admission, workspace_id=wid),
    )
    with ws(workspace_id):
        await runner.fire(f"{NAME}:{RUNNER_JOB}")
    assert len(await _turns(conversation_id)) == 1


async def test_invoke_without_invoker_fails_loud(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    store = ScheduleStore()
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    ctx = context_for(NAME, frozenset())
    with ws(workspace_id):
        await store.create(
            conversation_id, agent_id, "scheduled-x", DAILY_9AM, "do it", "do it", due_at
        )
        with pytest.raises(RuntimeError, match="scheduled task fires failed"):
            await ScheduledTaskRunner(ctx=ctx).run()
