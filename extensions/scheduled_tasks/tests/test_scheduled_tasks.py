"""End-to-end proof of the scheduled-tasks seam: the chat tool writes a durable schedule row, and
the batch-at-interval runner fires a due row into a real admitted turn through the invoke seam.

The runner drives the real `Admission` (real spend preflight, real turn row, real seq allocation);
only the DBOS enqueue stands in, recording the workflow id a live queue would receive — the same
seam `test_admission` stubs. So the chain proven is realistic input (a due schedule row) → durable
state (a queued turn) → an agent can run it, with nothing but the external queue faked."""

import asyncio
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_scheduled_tasks.manifest import NAME, RUNNER_JOB, manifest
from ufo_ext_scheduled_tasks.runner import ScheduledTaskRunner
from ufo_ext_scheduled_tasks.tools import (
    CancelScheduledTaskInput,
    ListScheduledTasksInput,
    PauseAndWaitInput,
    ScheduleTaskInput,
    cancel_scheduled_task,
    list_scheduled_tasks,
    pause_and_wait,
    schedule_task,
)

from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, context_for
from ufo.ext.loader import skill_registry
from ufo.jobs import JobRunner, bindings_from
from ufo.loop.engine import _claim_turn
from ufo.scheduling import ONE_TIME_SCHEDULE, ScheduleStore
from ufo.schema import tables
from ufo.schema.records import WRITEBACK_PENDING, Agent, TerminalFrame, Turn
from ufo.surfaces.admission import Admission, AdmissionInvoker, MemberAdmission
from ufo.tools.context import SpawnResult, ToolContext
from ufo.workspace import ws

DAILY_9AM = "0 9 * * *"


@dataclass
class StubDbos:
    """Stands in for the DBOS client at the admission seam: enqueue records the workflow id so a
    test reads back which turns were placed on the queue, never asserting DBOS itself."""

    enqueued: list[str] = field(default_factory=list)
    failures_remaining: int = 0

    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("enqueue failed")
        self.enqueued.append(turn_id)


@dataclass
class _BlockingDbos(StubDbos):
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        self.entered.set()
        await self.release.wait()
        await super().enqueue_async(options, workspace_id, turn_id)


@dataclass
class _FirstBlockingDbos(StubDbos):
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    fail_call: int | None = None
    calls: int = 0

    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        self.calls += 1
        call = self.calls
        if call == 1:
            self.entered.set()
            await self.release.wait()
        if call == self.fail_call:
            raise RuntimeError("enqueue failed")
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
            seq=0,
            status="running",
            inbound="please schedule this",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=None,
        audience_member_id=None,
        artifact_token_secret="",
        ext=context_for(NAME, frozenset()),
    )


def _runner_ctx(invoker: AdmissionInvoker | None) -> ExtensionContext:
    return context_for(
        NAME,
        frozenset(),
        invoker=invoker,
        schedule_invoker=invoker,
    )


async def _turns(conversation_id: UUID) -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.turn)
                    .where(tables.turn.c.conversation_id == conversation_id)
                    .order_by(tables.turn.c.seq)
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


async def test_pause_and_wait_runs_tool_to_timer_to_resumed_turn(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    ctx = _tool_ctx(workspace_id, conversation_id, agent_id)
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    with ws(workspace_id):
        result = await pause_and_wait(
            ctx,
            PauseAndWaitInput(
                ai_response="I'll wait for the verification email.",
                wait_minutes=10,
                next_steps="Read the code and continue onboarding.",
                reason="verification email",
                metadata={"account": "member@example.com"},
            ),
        )
        payload = json.loads(result.content[0].text.split("\n", 1)[1])
        assert payload["awaiting"] == "timer"
        assert payload["metadata"] == {"account": "member@example.com"}
        async with workspace_tx() as connection:
            waiting = (
                (
                    await connection.execute(
                        sa.select(tables.scheduled_task).where(
                            tables.scheduled_task.c.conversation_id == conversation_id
                        )
                    )
                )
                .mappings()
                .one()
            )
            await connection.execute(
                sa.update(tables.scheduled_task)
                .where(tables.scheduled_task.c.id == waiting["id"])
                .values(next_run_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        assert waiting["schedule"] == ONE_TIME_SCHEDULE
        assert waiting["origin_seq"] == 0
        assert waiting["resume_turn_id"] is None
        assert "Read the code and continue onboarding." in waiting["prompt"]
        assert "verification email" in waiting["prompt"]
        assert await ScheduleStore().list() == ()

        await ScheduledTaskRunner(ctx=_runner_ctx(invoker)).run()
        turns = await _turns(conversation_id)
        assert len(turns) == 1
        assert turns[0]["agent_id"] == agent_id
        assert "Resume the paused workflow" in turns[0]["inbound"]
        assert "member@example.com" in turns[0]["inbound"]
        async with workspace_tx() as connection:
            resume_turn_id = (
                await connection.execute(sa.select(tables.scheduled_task.c.resume_turn_id))
            ).scalar_one()
        assert resume_turn_id == turns[0]["id"]
        assert await _claim_turn(turns[0]["id"], "timer-resume") is True
        async with workspace_tx() as connection:
            remaining = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.scheduled_task)
                )
            ).scalar_one()
        assert remaining == 0
        assert dbos.enqueued == [str(turns[0]["id"])]


async def test_rejected_timer_resume_removes_its_one_time_pause(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    spent_turn_id = uuid4()
    async with workspace_tx() as connection:
        member_id = (
            await connection.execute(
                sa.select(tables.conversation.c.member_id).where(
                    tables.conversation.c.id == conversation_id
                )
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(tables.turn).values(
                id=spent_turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="earlier work",
                admission_source="internal",
                terminal=TerminalFrame(status="done").model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.ledger).values(
                id=uuid4(),
                workspace_id=workspace_id,
                turn_id=spent_turn_id,
                dimension="tokens",
                amount=10,
                priced_micro_usd=100,
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.spend_cap).values(
                id=uuid4(),
                workspace_id=workspace_id,
                scope="member",
                subject_id=member_id,
                window_seconds=3600,
                limit_micro_usd=50,
                on_breach="reject",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    with ws(workspace_id):
        await ScheduleStore().pause(
            conversation_id,
            agent_id,
            "resume",
            "resume",
            datetime.now(UTC) - timedelta(minutes=1),
            1,
        )
        await ScheduledTaskRunner(ctx=_runner_ctx(invoker)).run()
        turns = await _turns(conversation_id)
        async with workspace_tx() as connection:
            pauses = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.scheduled_task)
                )
            ).scalar_one()
    assert [turn["status"] for turn in turns] == ["done", "cancelled"]
    assert pauses == 0
    assert dbos.enqueued == []


async def test_new_message_resumes_pause_and_cancels_timer(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    ctx = _tool_ctx(workspace_id, conversation_id, agent_id)
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    member_admission = MemberAdmission(admission=admission, workspace_id=workspace_id)
    with ws(workspace_id):
        await pause_and_wait(
            ctx,
            PauseAndWaitInput(
                ai_response="Waiting.",
                wait_minutes=10,
                next_steps="Continue.",
                reason="approval",
            ),
        )
        turn_id = await member_admission.admit(
            conversation_id,
            agent_id,
            "The approval arrived.",
            "approval-1",
            speaker_member_id=None,
        )
        async with workspace_tx() as connection:
            resume_turn_id = (
                await connection.execute(sa.select(tables.scheduled_task.c.resume_turn_id))
            ).scalar_one()
            source = (
                await connection.execute(
                    sa.select(tables.turn.c.admission_source).where(tables.turn.c.id == turn_id)
                )
            ).scalar_one()
        assert resume_turn_id == turn_id
        assert await _claim_turn(turn_id, "member-resume") is True
        async with workspace_tx() as connection:
            remaining = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.scheduled_task)
                )
            ).scalar_one()
        assert remaining == 0
        assert source == "member"
        assert dbos.enqueued == [str(turn_id)]


async def test_pause_does_not_arm_after_a_newer_member_was_admitted(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    base = _tool_ctx(workspace_id, conversation_id, agent_id)
    origin = base.turn.model_copy(update={"id": uuid4(), "seq": 1})
    ctx = replace(base, turn=origin)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=origin.id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=origin.seq,
                status="running",
                inbound=origin.inbound,
                admission_source="internal",
                terminal=None,
                created_at=origin.created_at,
                updated_at=sa.func.now(),
            )
        )
    member_admission = MemberAdmission(
        admission=Admission(dbos=StubDbos(), durable_surfaces=frozenset()),
        workspace_id=workspace_id,
    )
    with ws(workspace_id):
        member_turn_id = await member_admission.admit(
            conversation_id,
            agent_id,
            "new message",
            "newer-member",
            speaker_member_id=None,
        )
        result = await pause_and_wait(
            ctx,
            PauseAndWaitInput(
                ai_response="Waiting.",
                wait_minutes=10,
                next_steps="Continue.",
                reason="approval",
            ),
        )
        payload = json.loads(result.content[0].text.split("\n", 1)[1])
        async with workspace_tx() as connection:
            pauses = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.scheduled_task)
                )
            ).scalar_one()
            queued_bodies = (
                (await connection.execute(sa.select(tables.inbound_message.c.body))).scalars().all()
            )
    assert member_turn_id == origin.id
    assert queued_bodies == ["new message"]
    assert payload["awaiting"] == "member"
    assert pauses == 0


async def test_internal_arrivals_do_not_block_the_pause_timer(db: None) -> None:
    """A pending internal invocation is not a member reply: the pause still arms its timer."""
    workspace_id, agent_id, conversation_id = await _seed()
    base = _tool_ctx(workspace_id, conversation_id, agent_id)
    origin = base.turn.model_copy(update={"id": uuid4(), "seq": 1})
    ctx = replace(base, turn=origin)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=origin.id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=origin.seq,
                status="running",
                inbound=origin.inbound,
                admission_source="internal",
                terminal=None,
                created_at=origin.created_at,
                updated_at=sa.func.now(),
            )
        )
    invoker = AdmissionInvoker(
        admission=Admission(dbos=StubDbos(), durable_surfaces=frozenset()),
        workspace_id=workspace_id,
    )
    with ws(workspace_id):
        background_turn_id = await invoker.invoke(conversation_id, agent_id, "background note")
        result = await pause_and_wait(
            ctx,
            PauseAndWaitInput(
                ai_response="Waiting.",
                wait_minutes=10,
                next_steps="Continue.",
                reason="approval",
            ),
        )
        payload = json.loads(result.content[0].text.split("\n", 1)[1])
        async with workspace_tx() as connection:
            pauses = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.scheduled_task)
                )
            ).scalar_one()
    assert background_turn_id == origin.id
    assert payload["awaiting"] == "timer"
    assert pauses == 1


async def test_redelivered_terminal_message_does_not_cancel_a_later_pause(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    base = _tool_ctx(workspace_id, conversation_id, agent_id)
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    member_admission = MemberAdmission(admission=admission, workspace_id=workspace_id)
    with ws(workspace_id):
        turn_id = await member_admission.admit(
            conversation_id,
            agent_id,
            "The approval arrived.",
            "approval-redelivery",
            speaker_member_id=None,
        )
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .where(tables.turn.c.id == turn_id)
                .values(
                    status="done",
                    terminal=TerminalFrame(status="done", text="handled").model_dump(mode="json"),
                    updated_at=sa.func.now(),
                )
            )
            origin = base.turn.model_copy(
                update={"id": uuid4(), "seq": 2, "created_at": datetime.now(UTC)}
            )
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=origin.id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    seq=origin.seq,
                    status="running",
                    inbound=origin.inbound,
                    admission_source="internal",
                    terminal=None,
                    created_at=origin.created_at,
                    updated_at=sa.func.now(),
                )
            )
        await pause_and_wait(
            replace(base, turn=origin),
            PauseAndWaitInput(
                ai_response="Waiting again.",
                wait_minutes=10,
                next_steps="Continue later.",
                reason="later approval",
            ),
        )
        redelivered = await member_admission.admit(
            conversation_id,
            agent_id,
            "The approval arrived.",
            "approval-redelivery",
            speaker_member_id=None,
        )
        async with workspace_tx() as connection:
            pauses = (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(tables.scheduled_task)
                    .where(tables.scheduled_task.c.schedule == ONE_TIME_SCHEDULE)
                )
            ).scalar_one()
    assert redelivered == turn_id
    assert pauses == 1
    assert dbos.enqueued == [str(turn_id)]


async def test_failed_member_enqueue_preserves_pause_for_the_same_turn_retry(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    ctx = _tool_ctx(workspace_id, conversation_id, agent_id)
    dbos = StubDbos(failures_remaining=1)
    member_admission = MemberAdmission(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    with ws(workspace_id):
        await pause_and_wait(
            ctx,
            PauseAndWaitInput(
                ai_response="Waiting.",
                wait_minutes=10,
                next_steps="Continue.",
                reason="approval",
            ),
        )
        accepted = await member_admission.admit(
            conversation_id,
            agent_id,
            "The approval arrived.",
            "approval-retry",
            speaker_member_id=None,
        )
        async with workspace_tx() as connection:
            pause = (
                await connection.execute(
                    sa.select(
                        tables.scheduled_task.c.claimed_by,
                        tables.scheduled_task.c.claim_expires_at,
                        tables.scheduled_task.c.origin_seq,
                        tables.scheduled_task.c.resume_turn_id,
                    ).where(tables.scheduled_task.c.schedule == ONE_TIME_SCHEDULE)
                )
            ).one()
        [failed] = await _turns(conversation_id)
        retried = await member_admission.admit(
            conversation_id,
            agent_id,
            "The approval arrived.",
            "approval-retry",
            speaker_member_id=None,
        )
        turns = await _turns(conversation_id)
        async with workspace_tx() as connection:
            resume_turn_id = (
                await connection.execute(sa.select(tables.scheduled_task.c.resume_turn_id))
            ).scalar_one()
        assert resume_turn_id == retried
        assert await _claim_turn(retried, "member-retry") is True
        async with workspace_tx() as connection:
            pauses = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.scheduled_task)
                )
            ).scalar_one()
    assert tuple(pause) == (None, None, 0, failed["id"])
    assert accepted == failed["id"]
    assert failed["status"] == "queued"
    assert failed["terminal"] is None
    assert [turn["id"] for turn in turns] == [retried]
    assert dbos.enqueued == [str(retried)]
    assert pauses == 0


async def test_redundant_enqueue_survives_an_ambiguous_failure_until_claim(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    dbos = _FirstBlockingDbos(fail_call=1)
    member_admission = MemberAdmission(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    with ws(workspace_id):
        await ScheduleStore().pause(
            conversation_id,
            agent_id,
            "resume",
            "approval",
            datetime.now(UTC) + timedelta(minutes=10),
            0,
        )
        first = asyncio.create_task(
            member_admission.admit(
                conversation_id,
                agent_id,
                "The approval arrived.",
                "approval-ambiguous",
                speaker_member_id=None,
            )
        )
        await dbos.entered.wait()
        redundant = await member_admission.admit(
            conversation_id,
            agent_id,
            "The approval arrived.",
            "approval-ambiguous",
            speaker_member_id=None,
        )
        dbos.release.set()
        accepted = await first
        [turn] = await _turns(conversation_id)
        async with workspace_tx() as connection:
            pause = (
                await connection.execute(
                    sa.select(
                        tables.scheduled_task.c.resume_turn_id,
                        tables.scheduled_task.c.next_run_at,
                        tables.scheduled_task.c.claimed_by,
                    )
                )
            ).one()
        assert pause.next_run_at.replace(tzinfo=UTC) <= datetime.now(UTC)
        assert pause.claimed_by is None
        assert turn["id"] == redundant
        assert accepted == redundant
        assert turn["status"] == "queued"
        assert turn["terminal"] is None
        assert turn["dispatch_enqueued_at"] is None
        assert pause.resume_turn_id == redundant
        assert dbos.enqueued == [str(redundant)]
        assert await _claim_turn(redundant, "ambiguous-enqueue") is True
        async with workspace_tx() as connection:
            pauses = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.scheduled_task)
                )
            ).scalar_one()
    assert pauses == 0


async def test_background_turn_does_not_cancel_pause(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    ctx = _tool_ctx(workspace_id, conversation_id, agent_id)
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    with ws(workspace_id):
        await pause_and_wait(
            ctx,
            PauseAndWaitInput(
                ai_response="Waiting.",
                wait_minutes=10,
                next_steps="Continue.",
                reason="approval",
            ),
        )
        await ScheduleStore().create(
            conversation_id,
            agent_id,
            "scheduled-check",
            DAILY_9AM,
            "check something else",
            "check something else",
            datetime.now(UTC) - timedelta(minutes=1),
        )
        await ScheduledTaskRunner(ctx=_runner_ctx(invoker)).run()
        async with workspace_tx() as connection:
            pauses = (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(tables.scheduled_task)
                    .where(tables.scheduled_task.c.schedule == ONE_TIME_SCHEDULE)
                )
            ).scalar_one()
    assert pauses == 1
    assert len(await _turns(conversation_id)) == 1


async def test_internal_invoke_does_not_cancel_pause(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    ctx = _tool_ctx(workspace_id, conversation_id, agent_id)
    invoker = AdmissionInvoker(
        admission=Admission(dbos=StubDbos(), durable_surfaces=frozenset()),
        workspace_id=workspace_id,
    )
    with ws(workspace_id):
        await pause_and_wait(
            ctx,
            PauseAndWaitInput(
                ai_response="Waiting.",
                wait_minutes=10,
                next_steps="Continue.",
                reason="approval",
            ),
        )
        turn_id = await invoker.invoke(conversation_id, agent_id, "internal work", "internal-1")
        async with workspace_tx() as connection:
            pauses = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.scheduled_task)
                )
            ).scalar_one()
            source = (
                await connection.execute(
                    sa.select(tables.turn.c.admission_source).where(tables.turn.c.id == turn_id)
                )
            ).scalar_one()
    assert pauses == 1
    assert source == "internal"


async def test_member_admission_wins_against_an_already_claimed_pause(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    dbos = _BlockingDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    member_admission = MemberAdmission(admission=admission, workspace_id=workspace_id)
    invoker = AdmissionInvoker(admission=admission, workspace_id=workspace_id)
    store = ScheduleStore(invoker)
    runner = ScheduledTaskRunner(ctx=_runner_ctx(invoker))
    with ws(workspace_id):
        await store.pause(
            conversation_id,
            agent_id,
            "resume",
            "resume",
            due_at,
            0,
        )
        [claimed] = await store.claim_due(datetime.now(UTC), 300)
        member_turn = asyncio.create_task(
            member_admission.admit(
                conversation_id,
                agent_id,
                "The approval arrived.",
                "approval-race",
                speaker_member_id=None,
            )
        )
        await dbos.entered.wait()
        try:
            assert await runner._fire(store, claimed, datetime.now(UTC)) is None
        finally:
            dbos.release.set()
        turn_id = await member_turn
        turns = await _turns(conversation_id)
        async with workspace_tx() as connection:
            resume_turn_id = (
                await connection.execute(sa.select(tables.scheduled_task.c.resume_turn_id))
            ).scalar_one()
        assert resume_turn_id == turn_id
        assert await _claim_turn(turn_id, "member-wins") is True
        async with workspace_tx() as connection:
            pauses = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.scheduled_task)
                )
            ).scalar_one()
    assert [turn["id"] for turn in turns] == [turn_id]
    assert dbos.enqueued == [str(turn_id)]
    assert pauses == 0


async def test_rearmed_pause_rejects_the_old_claim(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    store = ScheduleStore()
    now = datetime.now(UTC)
    invoker = AdmissionInvoker(
        admission=Admission(dbos=StubDbos(), durable_surfaces=frozenset()),
        workspace_id=workspace_id,
    )
    with ws(workspace_id):
        original = await store.pause(
            conversation_id,
            agent_id,
            "resume old",
            "old",
            now - timedelta(minutes=1),
            1,
        )
        [claimed] = await store.claim_due(now, 300)
        rearmed = await store.pause(
            conversation_id,
            agent_id,
            "resume new",
            "new",
            now + timedelta(minutes=10),
            2,
        )
        assert rearmed.id == original.id
        assert await invoker.invoke_scheduled(claimed) is None
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.select(
                        tables.scheduled_task.c.prompt,
                        tables.scheduled_task.c.next_run_at,
                        tables.scheduled_task.c.claimed_by,
                    ).where(tables.scheduled_task.c.id == rearmed.id)
                )
            ).one()
    assert row.prompt == "resume new"
    assert row.next_run_at.replace(tzinfo=UTC) == rearmed.next_run_at
    assert row.claimed_by is None
    assert await _turns(conversation_id) == []


async def test_member_message_takes_over_a_timer_waiting_to_enqueue(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    now = datetime.now(UTC)
    dbos = _FirstBlockingDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    invoker = AdmissionInvoker(admission=admission, workspace_id=workspace_id)
    member_admission = MemberAdmission(admission=admission, workspace_id=workspace_id)
    store = ScheduleStore(invoker)
    runner = ScheduledTaskRunner(ctx=_runner_ctx(invoker))
    with ws(workspace_id):
        await store.pause(
            conversation_id,
            agent_id,
            "timer resume",
            "timer",
            now - timedelta(minutes=1),
            0,
        )
        [claimed] = await store.claim_due(now, 300)
        timer_fire = asyncio.create_task(runner._fire(store, claimed, now))
        await dbos.entered.wait()
        turn_id = await member_admission.admit(
            conversation_id,
            agent_id,
            "The approval arrived.",
            "approval-after-timer",
            speaker_member_id=None,
        )
        dbos.release.set()
        assert await timer_fire is None
        turns = await _turns(conversation_id)
        async with workspace_tx() as connection:
            resume_turn_id = (
                await connection.execute(sa.select(tables.scheduled_task.c.resume_turn_id))
            ).scalar_one()
        assert resume_turn_id == turn_id
        assert await _claim_turn(turn_id, "timer-takeover") is True
        async with workspace_tx() as connection:
            pauses = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.scheduled_task)
                )
            ).scalar_one()
    assert [(turn["id"], turn["inbound"]) for turn in turns] == [(turn_id, "The approval arrived.")]
    assert dbos.enqueued == [str(turn_id), str(turn_id)]
    assert pauses == 0


async def test_member_takes_over_the_timer_while_internal_work_queues(db: None) -> None:
    """One live turn absorbs everything that arrives around a firing timer: the internal message
    lands on its inbound queue, and the member's reply still takes the queued timer turn over."""
    workspace_id, agent_id, conversation_id = await _seed()
    now = datetime.now(UTC)
    dbos = _FirstBlockingDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    invoker = AdmissionInvoker(admission=admission, workspace_id=workspace_id)
    member_admission = MemberAdmission(admission=admission, workspace_id=workspace_id)
    store = ScheduleStore(invoker)
    runner = ScheduledTaskRunner(ctx=_runner_ctx(invoker))
    with ws(workspace_id):
        await store.pause(
            conversation_id,
            agent_id,
            "timer resume",
            "timer",
            now - timedelta(minutes=1),
            0,
        )
        [claimed] = await store.claim_due(now, 300)
        timer_fire = asyncio.create_task(runner._fire(store, claimed, now))
        await dbos.entered.wait()
        internal_turn = await invoker.invoke(
            conversation_id, agent_id, "internal work", "internal-between"
        )
        member_turn = await member_admission.admit(
            conversation_id,
            agent_id,
            "member reply",
            "member-after-internal",
            speaker_member_id=None,
        )
        dbos.release.set()
        assert await timer_fire is None
        turns = await _turns(conversation_id)
        async with workspace_tx() as connection:
            resume_turn_id = (
                await connection.execute(sa.select(tables.scheduled_task.c.resume_turn_id))
            ).scalar_one()
            queued_bodies = (
                (
                    await connection.execute(
                        sa.select(tables.inbound_message.c.body).order_by(
                            tables.inbound_message.c.seq
                        )
                    )
                )
                .scalars()
                .all()
            )
        [turn] = turns
        assert turn["inbound"] == "member reply"
        assert turn["status"] == "queued"
        assert turn["admission_source"] == "member"
        assert internal_turn == turn["id"]
        assert member_turn == turn["id"]
        assert queued_bodies == ["internal work"]
        assert resume_turn_id == turn["id"]
        assert dbos.enqueued == [str(turn["id"]), str(turn["id"])]
        assert await _claim_turn(turn["id"], "ordered-timer") is True
        async with workspace_tx() as connection:
            pauses = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.scheduled_task)
                )
            ).scalar_one()
    assert pauses == 0


async def test_later_member_joins_the_first_queued_turn(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    dbos = _FirstBlockingDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    member_admission = MemberAdmission(admission=admission, workspace_id=workspace_id)
    with ws(workspace_id):
        await ScheduleStore().pause(
            conversation_id,
            agent_id,
            "timer resume",
            "timer",
            datetime.now(UTC) + timedelta(minutes=10),
            0,
        )
        first = asyncio.create_task(
            member_admission.admit(
                conversation_id,
                agent_id,
                "first",
                "member-first",
                speaker_member_id=None,
            )
        )
        await dbos.entered.wait()
        second_turn = await member_admission.admit(
            conversation_id,
            agent_id,
            "second",
            "member-second",
            speaker_member_id=None,
        )
        dbos.release.set()
        first_turn = await first
        turns = await _turns(conversation_id)
        async with workspace_tx() as connection:
            resume_turn_id = (
                await connection.execute(sa.select(tables.scheduled_task.c.resume_turn_id))
            ).scalar_one()
        assert resume_turn_id == first_turn
        assert await _claim_turn(first_turn, "concurrent-member") is True
        async with workspace_tx() as connection:
            pauses = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.scheduled_task)
                )
            ).scalar_one()
    assert second_turn == first_turn
    [turn] = turns
    assert turn["status"] == "queued"
    assert turn["inbound"] == "first"
    async with workspace_tx() as connection:
        queued_bodies = (
            (await connection.execute(sa.select(tables.inbound_message.c.body))).scalars().all()
        )
    assert queued_bodies == ["second"]
    assert dbos.enqueued == [str(first_turn)]
    assert pauses == 0


async def test_pause_recovers_the_member_turn_after_process_death(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    blocked = _BlockingDbos()
    admission = Admission(dbos=blocked, durable_surfaces=frozenset())
    member_admission = MemberAdmission(admission=admission, workspace_id=workspace_id)
    with ws(workspace_id):
        await ScheduleStore().pause(
            conversation_id,
            agent_id,
            "timer resume",
            "timer",
            datetime.now(UTC) + timedelta(minutes=10),
            0,
        )
        member = asyncio.create_task(
            member_admission.admit(
                conversation_id,
                agent_id,
                "approved",
                "member-crash",
                speaker_member_id=None,
            )
        )
        await blocked.entered.wait()
        member.cancel()
        with pytest.raises(asyncio.CancelledError):
            await member
        [turn] = await _turns(conversation_id)
        async with workspace_tx() as connection:
            pause = (
                await connection.execute(
                    sa.select(
                        tables.scheduled_task.c.resume_turn_id,
                        tables.scheduled_task.c.next_run_at,
                        tables.scheduled_task.c.claimed_by,
                    )
                )
            ).one()
        assert pause.resume_turn_id == turn["id"]
        assert pause.next_run_at.replace(tzinfo=UTC) <= datetime.now(UTC)
        assert pause.claimed_by is None
        recovered_dbos = StubDbos()
        recovered_invoker = AdmissionInvoker(
            admission=Admission(dbos=recovered_dbos, durable_surfaces=frozenset()),
            workspace_id=workspace_id,
        )
        await ScheduledTaskRunner(ctx=_runner_ctx(recovered_invoker)).run()
        async with workspace_tx() as connection:
            recovered_pause = (
                await connection.execute(
                    sa.select(
                        tables.scheduled_task.c.resume_turn_id,
                        tables.scheduled_task.c.claimed_by,
                        tables.scheduled_task.c.claim_expires_at,
                    )
                )
            ).one()
        assert recovered_pause.resume_turn_id == turn["id"]
        assert recovered_pause.claimed_by is not None
        assert await _claim_turn(turn["id"], "recovered-member") is True
        async with workspace_tx() as connection:
            pauses = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.scheduled_task)
                )
            ).scalar_one()
    assert recovered_dbos.enqueued == [str(turn["id"])]
    assert len(await _turns(conversation_id)) == 1
    assert pauses == 0


@pytest.mark.parametrize("wait_minutes", (0, 10_081))
def test_pause_and_wait_bounds_the_timer(wait_minutes: int) -> None:
    with pytest.raises(ValueError):
        PauseAndWaitInput(
            ai_response="Waiting.",
            wait_minutes=wait_minutes,
            next_steps="Continue.",
            reason="approval",
        )


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


async def test_concurrent_first_create_converges_by_workspace_name(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    second_conversation = uuid4()
    async with workspace_tx() as connection:
        member_id = (
            await connection.execute(
                sa.select(tables.member.c.id).where(tables.member.c.workspace_id == workspace_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=second_conversation,
                workspace_id=workspace_id,
                surface="cli",
                queue_key="second-session",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    store = ScheduleStore()
    due_at = datetime.now(UTC) + timedelta(minutes=10)
    with ws(workspace_id):
        first, second = await asyncio.gather(
            store.create(
                conversation_id,
                agent_id,
                "scheduled-shared-name",
                DAILY_9AM,
                "first",
                "first",
                due_at,
            ),
            store.create(
                second_conversation,
                agent_id,
                "scheduled-shared-name",
                DAILY_9AM,
                "second",
                "second",
                due_at,
            ),
        )
        tasks = await store.list()
    assert first.id == second.id
    assert len(tasks) == 1


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
        assert turns[0]["admission_source"] == "scheduled"
        assert turns[0]["status"] == "queued"
        assert dbos.enqueued == [str(turns[0]["id"])]
        advanced = (await store.list())[0]
        assert advanced.last_run_at is not None
        assert await store.claim_due(datetime.now(UTC), 300) == ()


async def test_claim_due_caps_a_sweep_at_its_batch_limit(db: None) -> None:
    """One sweep leases at most `limit` tasks, oldest due first; the remainder stays due and the
    next sweep claims it — a workspace with a task pileup makes bounded progress per tick instead
    of claiming more than one lease can cover."""
    workspace_id, agent_id, conversation_id = await _seed()
    store = ScheduleStore()
    older = datetime.now(UTC) - timedelta(minutes=10)
    newer = datetime.now(UTC) - timedelta(minutes=5)
    with ws(workspace_id):
        await store.create(conversation_id, agent_id, "first", DAILY_9AM, "a", "a", older)
        await store.create(conversation_id, agent_id, "second", DAILY_9AM, "b", "b", newer)
        first_sweep = await store.claim_due(datetime.now(UTC), 300, limit=1)
        assert [task.name for task in first_sweep] == ["first"]
        second_sweep = await store.claim_due(datetime.now(UTC), 300, limit=1)
        assert [task.name for task in second_sweep] == ["second"]


async def test_fire_into_a_durable_surface_conversation_registers_delivery(db: None) -> None:
    """A scheduled fire and member ingress use distinct capabilities over the same admission
    workflow, so a task in a Slack thread still registers its writeback with the turn."""
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


async def test_next_recurring_fire_admits_a_distinct_turn(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()),
        workspace_id=workspace_id,
    )
    runner = ScheduledTaskRunner(ctx=_runner_ctx(invoker))
    with ws(workspace_id):
        task = await ScheduleStore().create(
            conversation_id,
            agent_id,
            "scheduled-daily",
            DAILY_9AM,
            "check inbox",
            "check inbox",
            datetime.now(UTC) - timedelta(minutes=1),
        )
        await runner.run()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.scheduled_task)
                .values(next_run_at=datetime.now(UTC) - timedelta(seconds=1))
                .where(tables.scheduled_task.c.id == task.id)
            )
        await runner.run()
        turns = await _turns(conversation_id)
    assert len(turns) == 2
    assert turns[0]["id"] != turns[1]["id"]
    assert turns[0]["dispatch_enqueued_at"] is not None
    assert turns[1]["dispatch_enqueued_at"] is None
    assert dbos.enqueued == [str(turns[0]["id"])]


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
    assert manifest().requires == ("memory_search",)


def test_manifest_exposes_pause_as_a_side_effecting_tool() -> None:
    tool = next(tool for tool in manifest().tools if tool.name == "pause_and_wait")
    assert tool.handler is pause_and_wait
    assert tool.side_effecting is True


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
    turns = await _turns(conversation_id)
    assert len(turns) == 1
    assert turns[0]["inbound"] == "check inbox"


async def test_invoke_without_invoker_fails_loud(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    store = ScheduleStore()
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    ctx = _runner_ctx(None)
    with ws(workspace_id):
        await store.create(
            conversation_id, agent_id, "scheduled-x", DAILY_9AM, "do it", "do it", due_at
        )
        with pytest.raises(RuntimeError, match="scheduled task fires failed"):
            await ScheduledTaskRunner(ctx=ctx).run()


async def test_failed_pause_resume_keeps_its_one_time_timer(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    with ws(workspace_id):
        task = await ScheduleStore().pause(
            conversation_id,
            agent_id,
            "resume",
            "resume",
            due_at,
            1,
        )
        with pytest.raises(RuntimeError, match="scheduled task fires failed"):
            await ScheduledTaskRunner(ctx=context_for(NAME, frozenset())).run()
        async with workspace_tx() as connection:
            remaining = (
                await connection.execute(
                    sa.select(tables.scheduled_task.c.id).where(
                        tables.scheduled_task.c.id == task.id
                    )
                )
            ).scalar_one()
    assert remaining == task.id


async def test_pause_resume_retries_the_same_turn_after_enqueue_failure(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed(surface="slack")
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    dbos = StubDbos(failures_remaining=1)
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset({"slack"})),
        workspace_id=workspace_id,
    )
    runner = ScheduledTaskRunner(ctx=_runner_ctx(invoker))
    with ws(workspace_id):
        task = await ScheduleStore().pause(
            conversation_id,
            agent_id,
            "resume",
            "resume",
            due_at,
            1,
        )
        await runner.run()
        failed = (await _turns(conversation_id))[0]
        assert failed["status"] == "queued"
        assert failed["terminal"] is None
        assert failed["dispatch_enqueued_at"] is None
        async with workspace_tx() as connection:
            resume_turn_id = (
                await connection.execute(
                    sa.select(tables.scheduled_task.c.resume_turn_id).where(
                        tables.scheduled_task.c.id == task.id
                    )
                )
            ).scalar_one()
            assert resume_turn_id == failed["id"]
            await connection.execute(
                sa.update(tables.scheduled_task)
                .where(tables.scheduled_task.c.id == task.id)
                .values(claim_expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )

        await runner.run()
        resumed = await _turns(conversation_id)
        assert len(resumed) == 1
        assert resumed[0]["id"] == failed["id"]
        assert resumed[0]["status"] == "queued"
        assert resumed[0]["terminal"] is None
        assert resumed[0]["dispatch_enqueued_at"] is not None
        async with workspace_tx() as connection:
            writeback = (
                await connection.execute(
                    sa.select(
                        tables.writeback.c.status,
                        tables.writeback.c.reply_ref,
                        tables.writeback.c.claimed_by,
                        tables.writeback.c.claim_expires_at,
                        tables.writeback.c.last_error,
                    ).where(tables.writeback.c.turn_id == failed["id"])
                )
            ).one()
            persisted_resume_turn_id = (
                await connection.execute(sa.select(tables.scheduled_task.c.resume_turn_id))
            ).scalar_one()
        assert persisted_resume_turn_id == failed["id"]
        assert await _claim_turn(failed["id"], "scheduled-retry") is True
        async with workspace_tx() as connection:
            remaining = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.scheduled_task)
                )
            ).scalar_one()
    assert remaining == 0
    assert tuple(writeback) == (WRITEBACK_PENDING, None, None, None, None)
    assert dbos.enqueued == [str(failed["id"])]
