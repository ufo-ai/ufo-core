"""End-to-end proof of the scheduled-tasks seam: the `scheduled_task` object kind writes a durable
schedule row through the generic object verbs, and the batch-at-interval runner fires a due row
into a real admitted turn through the invoke seam.

The runner drives the real `Admission` (real spend preflight, real turn row, real seq allocation);
only the DBOS enqueue stands in, recording the workflow id a live queue would receive — the same
seam `test_admission` stubs. So the chain proven is realistic input (a due schedule row) → durable
state (a queued turn) → an agent can run it, with nothing but the external queue faked."""

import asyncio
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from ufo_ext_scheduled_tasks.conversation_slot import AUTOMATIONS_SLOT
from ufo_ext_scheduled_tasks.cron import next_fire
from ufo_ext_scheduled_tasks.manifest import NAME, RUNNER_JOB, manifest
from ufo_ext_scheduled_tasks.runner import FINAL_FIRE_INSTRUCTION, ScheduledTaskRunner
from ufo_ext_scheduled_tasks.tools import (
    SCHEDULE_MAX,
    SCHEDULED_TASK_KIND,
    SUMMARY_MAX,
    PauseAndWaitInput,
    ScheduledTaskObjects,
    ScheduledTaskSpec,
    pause_and_wait,
)

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.object_tools import (
    BOUNDED_INFORMATIONAL_FIRES,
    _graded_bounded_daily,
    _graded_final_fire_result_and_check_in,
    _graded_operational_task_stays_open,
)
from ufo.agent_scope import AgentUnbound, agent
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, context_for
from ufo.ext.conversation_slots import ConversationSlotContext, ConversationSlotItem
from ufo.ext.loader import skill_registry, turn_tools
from ufo.jobs import JobRunner, bindings_from
from ufo.loop.engine import _claim_turn
from ufo.loop.queue import _load_turn
from ufo.objects import AdminRequired, ObjectListQuery, UnknownObject, VerbNotSupported
from ufo.scheduling import ONE_TIME_SCHEDULE, ScheduledTask, ScheduleStore, due_task_workspaces
from ufo.schema import tables
from ufo.schema.records import WRITEBACK_PENDING, Agent, TerminalFrame, Turn
from ufo.sdk.audience import (
    SHARED_AUDIENCE,
    conversation_audience,
    foreign_room_audience,
    room_audience,
)
from ufo.surfaces.admission import Admission, AdmissionInvoker, MemberAdmission
from ufo.tools.context import SpawnResult, ToolContext
from ufo.tools.registry import ToolDef
from ufo.workspace import ws

TOOL_NARRATION = "setting up the reminder"

DAILY_9AM = "0 9 * * *"


def _object_tool(name: str) -> ToolDef:
    tools, _ = turn_tools((manifest(),), None, audience=conversation_audience(None))
    return next(tool for tool in tools if tool.name == name)


def _task_manifest(
    name: str,
    schedule: str,
    prompt: str,
    description: str = "",
    expires_at: datetime | None = None,
) -> str:
    return yaml.safe_dump(
        {
            "kind": SCHEDULED_TASK_KIND,
            "name": name,
            "spec": {
                "schedule": schedule,
                "prompt": prompt,
                "description": description,
                "expires_at": expires_at,
            },
        }
    )


async def _dispatch(tool: ToolDef, ctx: ToolContext, **args: object) -> str:
    result = await tool.handler(
        ctx, tool.input_model.model_validate({"user_description": TOOL_NARRATION, **args})
    )
    assert result.is_error is False
    return result.content[0].text


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
                agent_id=agent_id,
                surface=surface,
                queue_key="session",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id, conversation_id


async def _second_agent(workspace_id: UUID) -> tuple[UUID, UUID]:
    agent_id, conversation_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=f"second-{agent_id.hex[:8]}",
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
                agent_id=agent_id,
                surface="cli",
                queue_key=f"session-{conversation_id.hex[:8]}",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id, conversation_id


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
        audience=conversation_audience(None),
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


async def test_the_task_kind_filters_and_orders_on_its_declared_fields(db: None) -> None:
    """`next_run_at` and `paused` ride the listing rows, so the agent reads what fires next and
    what is stopped without opening every task."""
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=creator)
    listing = _object_tool("object_list")
    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            _object_tool("object_apply"),
            ctx,
            manifest=_task_manifest("daily", DAILY_9AM, "morning"),
        )
        await _dispatch(
            _object_tool("object_apply"),
            ctx,
            manifest=yaml.safe_dump(
                {
                    "kind": SCHEDULED_TASK_KIND,
                    "name": "weekly",
                    "spec": {"schedule": "0 17 * * 1", "prompt": "digest", "paused": True},
                }
            ),
        )
        tasks = {task.name: task for task in await ScheduleStore().list()}
        listed = json.loads(await _dispatch(listing, ctx, kind=SCHEDULED_TASK_KIND))
        stopped = json.loads(
            await _dispatch(listing, ctx, kind=SCHEDULED_TASK_KIND, filters={"paused": True})
        )
        soonest_first = json.loads(
            await _dispatch(listing, ctx, kind=SCHEDULED_TASK_KIND, order_by="next_run_at")
        )

    rows = {row["name"]: row for row in listed["objects"]}
    assert rows["daily"]["paused"] is False
    assert rows["daily"]["next_run_at"] == tasks["daily"].next_run_at.isoformat()
    assert rows["weekly"]["paused"] is True
    assert [row["name"] for row in stopped["objects"]] == ["weekly"]
    assert [row["name"] for row in soonest_first["objects"]] == sorted(
        ("daily", "weekly"), key=lambda name: tasks[name].next_run_at
    )


async def test_malformed_conversation_filter_still_validates_the_list_query() -> None:
    query = ObjectListQuery(
        filters={"conversation": "not-a-uuid", "unexpected": True},
        supported_fields=frozenset({"conversation", "next_run_at", "paused"}),
    )

    with pytest.raises(ValueError, match=r"unknown object list filters.*unexpected"):
        await ScheduledTaskObjects().member_page(
            None,
            member_id=uuid4(),
            admin=False,
            query=query,
        )


async def test_automations_slot_rejects_a_recreated_task_generation(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    with ws(workspace_id), agent(agent_id):
        store = ScheduleStore()
        original = await store.create(
            conversation_id,
            "daily",
            DAILY_9AM,
            "send it",
            "daily send",
            datetime(2026, 8, 8, 9, tzinfo=UTC),
        )
        context = ConversationSlotContext(
            ext=context_for(NAME, frozenset()),
            conversation_id=conversation_id,
            agent_id=agent_id,
            audience=conversation_audience(None),
            messages=(),
            compacted=False,
            visible_items=(ConversationSlotItem(original.name, original.id, True),),
        )
        await store.cancel(original)
        recreated = await store.create(
            conversation_id,
            original.name,
            original.schedule,
            original.prompt,
            original.description,
            original.next_run_at,
        )
        assert recreated.id != original.id
        assert (await AUTOMATIONS_SLOT.read(context)).automations == ()


async def test_applied_task_writes_durable_row_bound_to_the_turn(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=creator)
    with ws(workspace_id), agent(agent_id):
        applied = json.loads(
            await _dispatch(
                _object_tool("object_apply"),
                ctx,
                manifest=_task_manifest(
                    "investor-replies", DAILY_9AM, "check the inbox for investor replies"
                ),
            )
        )
        assert applied == {
            "kind": SCHEDULED_TASK_KIND,
            "name": "investor-replies",
            "result": "created",
        }
        tasks = await ScheduleStore().list()
        fetched = yaml.safe_load(
            await _dispatch(
                _object_tool("object_get"), ctx, kind=SCHEDULED_TASK_KIND, name="investor-replies"
            )
        )
    assert len(tasks) == 1
    assert tasks[0].schedule == DAILY_9AM
    assert tasks[0].prompt == "check the inbox for investor replies"
    assert tasks[0].conversation_id == conversation_id
    assert tasks[0].agent_id == agent_id
    assert fetched["spec"]["schedule"] == DAILY_9AM
    assert fetched["status"]["next_run_at"] == tasks[0].next_run_at.isoformat()
    assert fetched["status"]["next_run_at"].endswith("+00:00")
    assert fetched["status"]["last_run_at"] is None
    assert fetched["created_at"].endswith("+00:00")
    assert fetched["updated_at"].endswith("+00:00")
    fired_at = datetime(2026, 12, 25, 9, 0, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.scheduled_task)
            .values(last_run_at=fired_at)
            .where(tables.scheduled_task.c.id == tasks[0].id)
        )
    with ws(workspace_id), agent(agent_id):
        refetched = yaml.safe_load(
            await _dispatch(
                _object_tool("object_get"), ctx, kind=SCHEDULED_TASK_KIND, name="investor-replies"
            )
        )
    assert refetched["status"]["last_run_at"] == fired_at.isoformat()


async def test_applied_task_requires_a_member_requester(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    with ws(workspace_id), agent(agent_id):
        with pytest.raises(AdminRequired, match="member requester"):
            await _dispatch(
                _object_tool("object_apply"),
                _tool_ctx(workspace_id, conversation_id, agent_id),
                manifest=_task_manifest("digest", DAILY_9AM, "check the inbox"),
            )
        assert await ScheduleStore().list() == ()


async def test_pause_and_wait_runs_tool_to_timer_to_resumed_turn(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    ctx = _tool_ctx(workspace_id, conversation_id, agent_id)
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    with ws(workspace_id), agent(agent_id):
        result = await pause_and_wait(
            ctx,
            PauseAndWaitInput(
                user_description=TOOL_NARRATION,
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
        assert turns[0]["inbound"] == waiting["prompt"]
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
    with ws(workspace_id), agent(agent_id):
        await ScheduleStore().pause(
            conversation_id,
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
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(
            ctx,
            PauseAndWaitInput(
                user_description=TOOL_NARRATION,
                ai_response="Waiting.",
                wait_minutes=10,
                next_steps="Continue.",
                reason="approval",
            ),
        )
        turn_id = (
            await member_admission.admit(
                conversation_id,
                "The approval arrived.",
                "approval-1",
                speaker_member_id=None,
            )
        ).turn_id
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
    with ws(workspace_id), agent(agent_id):
        member_turn_id = (
            await member_admission.admit(
                conversation_id,
                "new message",
                "newer-member",
                speaker_member_id=None,
            )
        ).turn_id
        result = await pause_and_wait(
            ctx,
            PauseAndWaitInput(
                user_description=TOOL_NARRATION,
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
    with ws(workspace_id), agent(agent_id):
        background_turn_id = await invoker.invoke(conversation_id, agent_id, "background note")
        result = await pause_and_wait(
            ctx,
            PauseAndWaitInput(
                user_description=TOOL_NARRATION,
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
    with ws(workspace_id), agent(agent_id):
        turn_id = (
            await member_admission.admit(
                conversation_id,
                "The approval arrived.",
                "approval-redelivery",
                speaker_member_id=None,
            )
        ).turn_id
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
                user_description=TOOL_NARRATION,
                ai_response="Waiting again.",
                wait_minutes=10,
                next_steps="Continue later.",
                reason="later approval",
            ),
        )
        redelivered = (
            await member_admission.admit(
                conversation_id,
                "The approval arrived.",
                "approval-redelivery",
                speaker_member_id=None,
            )
        ).turn_id
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
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(
            ctx,
            PauseAndWaitInput(
                user_description=TOOL_NARRATION,
                ai_response="Waiting.",
                wait_minutes=10,
                next_steps="Continue.",
                reason="approval",
            ),
        )
        accepted = (
            await member_admission.admit(
                conversation_id,
                "The approval arrived.",
                "approval-retry",
                speaker_member_id=None,
            )
        ).turn_id
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
        retried = (
            await member_admission.admit(
                conversation_id,
                "The approval arrived.",
                "approval-retry",
                speaker_member_id=None,
            )
        ).turn_id
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
    with ws(workspace_id), agent(agent_id):
        await ScheduleStore().pause(
            conversation_id,
            "resume",
            "approval",
            datetime.now(UTC) + timedelta(minutes=10),
            0,
        )
        first = asyncio.create_task(
            member_admission.admit(
                conversation_id,
                "The approval arrived.",
                "approval-ambiguous",
                speaker_member_id=None,
            )
        )
        await dbos.entered.wait()
        redundant = (
            await member_admission.admit(
                conversation_id,
                "The approval arrived.",
                "approval-ambiguous",
                speaker_member_id=None,
            )
        ).turn_id
        dbos.release.set()
        accepted = (await first).turn_id
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
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(
            ctx,
            PauseAndWaitInput(
                user_description=TOOL_NARRATION,
                ai_response="Waiting.",
                wait_minutes=10,
                next_steps="Continue.",
                reason="approval",
            ),
        )
        await ScheduleStore().create(
            conversation_id,
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
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(
            ctx,
            PauseAndWaitInput(
                user_description=TOOL_NARRATION,
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
    with ws(workspace_id), agent(agent_id):
        await store.pause(
            conversation_id,
            "resume",
            "resume",
            due_at,
            0,
        )
        [claimed] = await store.claim_due(datetime.now(UTC), 300)
        member_turn = asyncio.create_task(
            member_admission.admit(
                conversation_id,
                "The approval arrived.",
                "approval-race",
                speaker_member_id=None,
            )
        )
        await dbos.entered.wait()
        try:
            fire_at = datetime.now(UTC)
            assert await runner._fire(store, claimed, fire_at, fire_at) is None
        finally:
            dbos.release.set()
        turn_id = (await member_turn).turn_id
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
    with ws(workspace_id), agent(agent_id):
        original = await store.pause(
            conversation_id,
            "resume old",
            "old",
            now - timedelta(minutes=1),
            1,
        )
        [claimed] = await store.claim_due(now, 300)
        rearmed = await store.pause(
            conversation_id,
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


async def test_one_time_pause_rejects_runtime_instruction(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    now = datetime.now(UTC)
    invoker = AdmissionInvoker(
        admission=Admission(dbos=StubDbos(), durable_surfaces=frozenset()),
        workspace_id=workspace_id,
    )
    with ws(workspace_id), agent(agent_id):
        await ScheduleStore().pause(
            conversation_id,
            "resume workflow",
            "waiting",
            now - timedelta(minutes=1),
            1,
        )
        [claimed] = await ScheduleStore().claim_due(now, 300)
        with pytest.raises(ValueError, match="one-time workflow pause"):
            await invoker.invoke_scheduled(claimed, "unexpected")


async def test_member_message_takes_over_a_timer_waiting_to_enqueue(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    member_id = await _member(workspace_id)
    now = datetime.now(UTC)
    dbos = _FirstBlockingDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    invoker = AdmissionInvoker(admission=admission, workspace_id=workspace_id)
    member_admission = MemberAdmission(admission=admission, workspace_id=workspace_id)
    store = ScheduleStore(invoker)
    runner = ScheduledTaskRunner(ctx=_runner_ctx(invoker))
    with ws(workspace_id), agent(agent_id):
        await store.pause(
            conversation_id,
            "timer resume",
            "timer",
            now - timedelta(minutes=1),
            0,
            created_by_member_id=member_id,
        )
        [claimed] = await store.claim_due(now, 300)
        timer_fire = asyncio.create_task(runner._fire(store, claimed, now, now))
        await dbos.entered.wait()
        taken_over = await member_admission.admit(
            conversation_id,
            "The approval arrived.",
            "approval-after-timer",
            speaker_member_id=member_id,
        )
        turn_id = taken_over.turn_id
        dbos.release.set()
        assert await timer_fire is None
        turns = await _turns(conversation_id)
        async with workspace_tx() as connection:
            resume_turn_id = (
                await connection.execute(sa.select(tables.scheduled_task.c.resume_turn_id))
            ).scalar_one()
        assert resume_turn_id == turn_id
        assert taken_over.opened_run
        assert await _claim_turn(turn_id, "timer-takeover") is True
        async with workspace_tx() as connection:
            pauses = (
                await connection.execute(
                    sa.select(sa.func.count()).select_from(tables.scheduled_task)
                )
            ).scalar_one()
    assert [(turn["id"], turn["inbound"]) for turn in turns] == [(turn_id, "The approval arrived.")]
    assert turns[0]["speaker_member_id"] == member_id
    assert turns[0]["on_behalf_of_member_id"] is None
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
    with ws(workspace_id), agent(agent_id):
        await store.pause(
            conversation_id,
            "timer resume",
            "timer",
            now - timedelta(minutes=1),
            0,
        )
        [claimed] = await store.claim_due(now, 300)
        timer_fire = asyncio.create_task(runner._fire(store, claimed, now, now))
        await dbos.entered.wait()
        internal_turn = await invoker.invoke(
            conversation_id, agent_id, "internal work", "internal-between"
        )
        taken_over = await member_admission.admit(
            conversation_id,
            "member reply",
            "member-after-internal",
            speaker_member_id=None,
        )
        member_turn = taken_over.turn_id
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
        assert taken_over.opened_run
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
    with ws(workspace_id), agent(agent_id):
        await ScheduleStore().pause(
            conversation_id,
            "timer resume",
            "timer",
            datetime.now(UTC) + timedelta(minutes=10),
            0,
        )
        first = asyncio.create_task(
            member_admission.admit(
                conversation_id,
                "first",
                "member-first",
                speaker_member_id=None,
            )
        )
        await dbos.entered.wait()
        second_turn = (
            await member_admission.admit(
                conversation_id,
                "second",
                "member-second",
                speaker_member_id=None,
            )
        ).turn_id
        dbos.release.set()
        first_turn = (await first).turn_id
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
    with ws(workspace_id), agent(agent_id):
        await ScheduleStore().pause(
            conversation_id,
            "timer resume",
            "timer",
            datetime.now(UTC) + timedelta(minutes=10),
            0,
        )
        member = asyncio.create_task(
            member_admission.admit(
                conversation_id,
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
            user_description=TOOL_NARRATION,
            ai_response="Waiting.",
            wait_minutes=wait_minutes,
            next_steps="Continue.",
            reason="approval",
        )


async def test_paused_task_neither_fires_nor_reopens_its_workspace(db: None) -> None:
    """Applying `paused: true` stops the schedule without losing the task: the row never claims,
    its workspace never re-enters the runner's candidates on the due arm, and spec and status
    both say so; `paused: false` resumes from the next cron fire. Omitting `paused` on an
    ordinary update preserves the pause."""
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=creator)
    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            _object_tool("object_apply"),
            ctx,
            manifest=_task_manifest("digest", DAILY_9AM, "assemble the digest"),
        )
        await _dispatch(
            _object_tool("object_apply"),
            ctx,
            manifest=yaml.safe_dump(
                {"kind": SCHEDULED_TASK_KIND, "name": "digest", "spec": {"paused": True}}
            ),
        )
        [task] = await ScheduleStore().list()
        assert task.paused is True
        assert task.prompt == "assemble the digest"
        fetched = yaml.safe_load(
            await _dispatch(
                _object_tool("object_get"), ctx, kind=SCHEDULED_TASK_KIND, name="digest"
            )
        )
        assert fetched["spec"]["paused"] is True
        assert fetched["status"]["paused"] is True
        due_at = task.next_run_at + timedelta(seconds=1)
        assert await ScheduleStore().claim_due(due_at, 300) == ()
        await _dispatch(
            _object_tool("object_apply"),
            ctx,
            manifest=yaml.safe_dump(
                {
                    "kind": SCHEDULED_TASK_KIND,
                    "name": "digest",
                    "spec": {"description": "kept while paused"},
                }
            ),
        )
        [still_paused] = await ScheduleStore().list()
        assert still_paused.paused is True
        overdue = datetime.now(UTC) - timedelta(minutes=5)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.scheduled_task)
                .values(next_run_at=overdue)
                .where(tables.scheduled_task.c.id == still_paused.id)
            )
    assert workspace_id not in await due_task_workspaces()()
    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            _object_tool("object_apply"),
            ctx,
            manifest=yaml.safe_dump(
                {"kind": SCHEDULED_TASK_KIND, "name": "digest", "spec": {"paused": False}}
            ),
        )
        [resumed] = await ScheduleStore().list()
        assert resumed.paused is False
        [claimed] = await ScheduleStore().claim_due(resumed.next_run_at + timedelta(seconds=1), 300)
        assert claimed.name == "digest"
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.scheduled_task)
                .values(next_run_at=overdue, claimed_by=None, claim_expires_at=None)
                .where(tables.scheduled_task.c.id == resumed.id)
            )
    assert workspace_id in await due_task_workspaces()()


async def test_reapplied_name_updates_in_place(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=creator)
    apply = _object_tool("object_apply")
    with ws(workspace_id), agent(agent_id):
        await _dispatch(ctx=ctx, tool=apply, manifest=_task_manifest("report", DAILY_9AM, "daily"))
        second = json.loads(
            await _dispatch(
                ctx=ctx, tool=apply, manifest=_task_manifest("report", "0 17 * * 1", "weekly")
            )
        )
        tasks = await ScheduleStore().list()
    assert second["result"] == "updated"
    assert len(tasks) == 1
    assert tasks[0].schedule == "0 17 * * 1"
    assert tasks[0].prompt == "weekly"


async def test_concurrent_first_create_converges_by_agent_name(db: None) -> None:
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
                agent_id=agent_id,
                surface="cli",
                queue_key="second-session",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    store = ScheduleStore()
    due_at = datetime.now(UTC) + timedelta(minutes=10)
    with ws(workspace_id), agent(agent_id):
        outcomes = await asyncio.gather(
            store.create(
                conversation_id,
                "scheduled-shared-name",
                DAILY_9AM,
                "first",
                "first",
                due_at,
            ),
            store.create(
                second_conversation,
                "scheduled-shared-name",
                DAILY_9AM,
                "second",
                "second",
                due_at,
            ),
            return_exceptions=True,
        )
        tasks = await store.list()
    created = [outcome for outcome in outcomes if isinstance(outcome, ScheduledTask)]
    refused = [outcome for outcome in outcomes if isinstance(outcome, ValueError)]
    assert len(created) == 1
    assert len(refused) == 1
    assert "already exists" in str(refused[0])
    assert len(tasks) == 1


async def test_runner_fires_due_task_into_a_turn(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    store = ScheduleStore()
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    with ws(workspace_id), agent(agent_id):
        await store.create(
            conversation_id,
            "scheduled-daily",
            DAILY_9AM,
            "check inbox",
            "check inbox",
            due_at,
            created_by_member_id=creator,
        )
        await ScheduledTaskRunner(ctx=_runner_ctx(invoker)).run()

        turns = await _turns(conversation_id)
        assert len(turns) == 1
        assert turns[0]["inbound"] == (
            "<scheduled_task>\n"
            f"scheduled_fire: {due_at.isoformat().replace('+00:00', 'Z')}\n"
            "</scheduled_task>\n"
            "check inbox"
        )
        assert turns[0]["admission_source"] == "scheduled"
        assert turns[0]["status"] == "queued"
        assert dbos.enqueued == [str(turns[0]["id"])]
        advanced = (await store.list())[0]
        assert advanced.last_run_at is not None
        assert await store.claim_due(datetime.now(UTC), 300) == ()

        inspection = await store.inspect(advanced)
        assert inspection is not None
        assert inspection.last_turn_id == turns[0]["id"]
        assert inspection.last_response is None
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .values(status="done", terminal={"status": "done", "text": "found 3 new replies"})
                .where(tables.turn.c.id == turns[0]["id"])
            )
        fetched = yaml.safe_load(
            await _dispatch(
                _object_tool("object_get"),
                replace(
                    _tool_ctx(workspace_id, conversation_id, agent_id),
                    speaker_member_id=creator,
                ),
                kind=SCHEDULED_TASK_KIND,
                name="scheduled-daily",
            )
        )
        assert fetched["links"] == [
            {
                "relation": "reports_to",
                "target": {"kind": "conversation", "name": str(conversation_id)},
            },
        ]
        assert fetched["updated_at"] is not None
        status = fetched["status"]
        assert status["last_run"]["turn_id"] == str(turns[0]["id"])
        assert status["last_run"]["response"] == "found 3 new replies"


async def test_runner_replaces_final_permitted_fire_with_check_in(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    tick_at = datetime.now(UTC)
    due_at = tick_at - timedelta(minutes=1)
    expires_at = next_fire(DAILY_9AM, tick_at)
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    store = ScheduleStore(invoker)
    with ws(workspace_id), agent(agent_id):
        await store.create(
            conversation_id,
            "bounded-daily",
            DAILY_9AM,
            "check inbox",
            "check inbox",
            due_at,
            expires_at=expires_at,
        )
        [claimed] = await store.claim_due(tick_at, 300)
        assert (
            await ScheduledTaskRunner(ctx=_runner_ctx(invoker))._fire(
                store, claimed, tick_at, tick_at
            )
            is None
        )
        [advanced] = await store.list()
    [turn] = await _turns(conversation_id)
    assert turn["inbound"] == (
        "<scheduled_task>\n"
        f"scheduled_fire: {due_at.isoformat().replace('+00:00', 'Z')}\n"
        "</scheduled_task>\n"
        "check inbox\n"
        "<scheduled_task_instruction>\n"
        f"{FINAL_FIRE_INSTRUCTION}\n"
        "</scheduled_task_instruction>"
    )
    assert advanced.next_run_at.replace(tzinfo=UTC) == expires_at


async def test_task_horizon_round_trips_through_spec_and_status(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    expires_at = datetime.now(UTC) + timedelta(days=7)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=creator)
    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            _object_tool("object_apply"),
            ctx,
            manifest=_task_manifest(
                "seven-day-digest",
                DAILY_9AM,
                "send the digest",
                expires_at=expires_at,
            ),
        )
        fetched = yaml.safe_load(
            await _dispatch(
                _object_tool("object_get"),
                ctx,
                kind=SCHEDULED_TASK_KIND,
                name="seven-day-digest",
            )
        )
    assert datetime.fromisoformat(fetched["spec"]["expires_at"]) == expires_at
    assert datetime.fromisoformat(fetched["status"]["expires_at"]) == expires_at


def test_task_expiry_requires_utc() -> None:
    with pytest.raises(ValueError, match="UTC"):
        ScheduledTaskSpec(
            schedule=DAILY_9AM,
            prompt="send the digest",
            expires_at=datetime(2026, 8, 1, tzinfo=UTC).astimezone(timezone(timedelta(hours=-4))),
        )


async def test_expired_task_is_cancelled_without_invoking(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    with ws(workspace_id), agent(agent_id):
        await ScheduleStore().create(
            conversation_id,
            "expired-digest",
            DAILY_9AM,
            "send the digest",
            "send the digest",
            datetime.now(UTC) + timedelta(days=1),
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        await ScheduledTaskRunner(ctx=_runner_ctx(invoker)).run()
        tasks = await ScheduleStore().list()
    assert tasks == ()
    assert await _turns(conversation_id) == []
    assert dbos.enqueued == []


async def test_expiry_after_claim_cancels_before_invoking(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    now = datetime.now(UTC)
    claim_at = now - timedelta(seconds=1)
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    store = ScheduleStore(invoker)
    runner = ScheduledTaskRunner(ctx=_runner_ctx(invoker))
    with ws(workspace_id), agent(agent_id):
        await store.create(
            conversation_id,
            "claim-race-digest",
            DAILY_9AM,
            "send the digest",
            "send the digest",
            claim_at,
            expires_at=now,
        )
        [claimed] = await store.claim_due(claim_at, 300)
        assert await runner._fire(store, claimed, claim_at, now) is None
        tasks = await store.list()
    assert tasks == ()
    assert await _turns(conversation_id) == []
    assert dbos.enqueued == []


async def test_unclaimed_task_cannot_be_retired(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    now = datetime.now(UTC)
    store = ScheduleStore()
    with ws(workspace_id), agent(agent_id):
        task = await store.create(
            conversation_id,
            "unclaimed-digest",
            DAILY_9AM,
            "send the digest",
            "send the digest",
            now + timedelta(days=1),
            expires_at=now,
        )
        with pytest.raises(ValueError, match="unclaimed"):
            await store.retire_if_expired(task, now)


async def test_future_expiry_allows_claimed_task_to_invoke(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    now = datetime.now(UTC)
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    store = ScheduleStore(invoker)
    runner = ScheduledTaskRunner(ctx=_runner_ctx(invoker))
    with ws(workspace_id), agent(agent_id):
        await store.create(
            conversation_id,
            "unexpired-digest",
            DAILY_9AM,
            "send the digest",
            "send the digest",
            now,
            expires_at=now + timedelta(minutes=1),
        )
        [claimed] = await store.claim_due(now, 300)
        assert await runner._fire(store, claimed, now, now) is None
        [remaining] = await store.list()
    [turn] = await _turns(conversation_id)
    assert remaining.last_run_at is not None
    assert remaining.last_run_at.replace(tzinfo=UTC) == now
    assert dbos.enqueued == [str(turn["id"])]


async def test_batch_backlog_does_not_skip_next_cron_occurrence(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    tick_at = datetime.now(UTC).replace(second=30, microsecond=0)
    expiry_checked_at = tick_at + timedelta(seconds=31)
    invoker = AdmissionInvoker(
        admission=Admission(dbos=StubDbos(), durable_surfaces=frozenset()),
        workspace_id=workspace_id,
    )
    store = ScheduleStore(invoker)
    runner = ScheduledTaskRunner(ctx=_runner_ctx(invoker))
    with ws(workspace_id), agent(agent_id):
        await store.create(
            conversation_id,
            "minutely-digest",
            "* * * * *",
            "send the digest",
            "send the digest",
            tick_at,
        )
        [claimed] = await store.claim_due(tick_at, 300)
        assert await runner._fire(store, claimed, tick_at, expiry_checked_at) is None
        [remaining] = await store.list()
    expected = tick_at.replace(second=0) + timedelta(minutes=1)
    assert remaining.next_run_at.replace(tzinfo=UTC) == expected
    assert remaining.last_run_at is not None
    assert remaining.last_run_at.replace(tzinfo=UTC) == tick_at


async def test_expired_stale_claim_never_invokes(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=1)
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    store = ScheduleStore(invoker)
    runner = ScheduledTaskRunner(ctx=_runner_ctx(invoker))
    with ws(workspace_id), agent(agent_id):
        await store.create(
            conversation_id,
            "reclaimed-digest",
            DAILY_9AM,
            "send the digest",
            "send the digest",
            now,
            expires_at=expires_at,
        )
        [claimed] = await store.claim_due(now, 1)
        [reclaimed] = await store.claim_due(now + timedelta(seconds=2), 300)
        assert await runner._fire(store, claimed, now, expires_at) is None
        [remaining] = await store.list()
    assert remaining.claim_id == reclaimed.claim_id
    assert await _turns(conversation_id) == []
    assert dbos.enqueued == []


async def test_expired_task_with_future_fire_is_a_workspace_candidate(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    now = datetime.now(UTC)
    with ws(workspace_id), agent(agent_id):
        await ScheduleStore().create(
            conversation_id,
            "expired-before-next-fire",
            DAILY_9AM,
            "send the digest",
            "send the digest",
            now + timedelta(days=1),
            expires_at=now - timedelta(seconds=1),
        )
        assert await due_task_workspaces()() == (workspace_id,)
        assert await ScheduleStore().claim_due(now, 300) == ()
        tasks = await ScheduleStore().list()
    assert tasks == ()


async def test_update_from_another_conversation_keeps_reporting_home(db: None) -> None:
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
                agent_id=agent_id,
                surface="cli",
                queue_key="second-session",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    apply = _object_tool("object_apply")
    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            ctx=replace(
                _tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id
            ),
            tool=apply,
            manifest=_task_manifest("report", DAILY_9AM, "daily"),
        )
        updated = json.loads(
            await _dispatch(
                ctx=replace(
                    _tool_ctx(workspace_id, second_conversation, agent_id),
                    speaker_member_id=member_id,
                ),
                tool=apply,
                manifest=_task_manifest("report", "0 17 * * 1", "weekly"),
            )
        )
        rows = await ScheduleStore().list()
    assert updated["result"] == "updated"
    assert len(rows) == 1
    assert rows[0].schedule == "0 17 * * 1"
    assert rows[0].conversation_id == conversation_id


async def test_claim_due_caps_a_sweep_at_its_batch_limit(db: None) -> None:
    """One sweep leases at most `limit` tasks, oldest due first; the remainder stays due and the
    next sweep claims it — a workspace with a task pileup makes bounded progress per tick instead
    of claiming more than one lease can cover."""
    workspace_id, agent_id, conversation_id = await _seed()
    store = ScheduleStore()
    older = datetime.now(UTC) - timedelta(minutes=10)
    newer = datetime.now(UTC) - timedelta(minutes=5)
    with ws(workspace_id), agent(agent_id):
        await store.create(conversation_id, "first", DAILY_9AM, "a", "a", older)
        await store.create(conversation_id, "second", DAILY_9AM, "b", "b", newer)
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
    with ws(workspace_id), agent(agent_id):
        await store.create(
            conversation_id,
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
    with ws(workspace_id), agent(agent_id):
        await store.create(
            conversation_id,
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
    with ws(workspace_id), agent(agent_id):
        task = await ScheduleStore().create(
            conversation_id,
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


async def test_deleted_task_stops_and_leaves_the_listing(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=creator)
    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            _object_tool("object_apply"), ctx, manifest=_task_manifest("watcher", DAILY_9AM, "x")
        )
        deleted = json.loads(
            await _dispatch(
                _object_tool("object_delete"), ctx, kind=SCHEDULED_TASK_KIND, name="watcher"
            )
        )
        assert deleted["deleted"] is True
        assert deleted["spec"]["schedule"] == DAILY_9AM
        assert await ScheduleStore().list() == ()
        listing = json.loads(
            await _dispatch(_object_tool("object_list"), ctx, kind=SCHEDULED_TASK_KIND)
        )
        assert listing["objects"] == []


async def test_applied_task_rejects_non_five_field_cron(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    ctx = _tool_ctx(workspace_id, conversation_id, agent_id)
    apply = _object_tool("object_apply")
    args = apply.input_model.model_validate(
        {
            "user_description": TOOL_NARRATION,
            "manifest": _task_manifest("too-many", "0 9 * * * *", "too many fields"),
        }
    )
    with ws(workspace_id), agent(agent_id), pytest.raises(ValueError, match="5-field"):
        await apply.handler(ctx, args)


async def test_pause_rows_never_surface_as_objects(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    ctx = _tool_ctx(workspace_id, conversation_id, agent_id)
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(
            ctx,
            PauseAndWaitInput(
                user_description=TOOL_NARRATION,
                ai_response="waiting",
                wait_minutes=5,
                next_steps="continue",
                reason="approval",
            ),
        )
        listing = json.loads(
            await _dispatch(_object_tool("object_list"), ctx, kind=SCHEDULED_TASK_KIND)
        )
    assert listing["objects"] == []


def test_task_scheduling_skill_parses_and_indexes() -> None:
    registry = skill_registry((manifest(),))
    index = dict(registry.index())
    assert "task-scheduling" in index
    assert manifest().requires == ("memory_search",)


async def test_bounded_daily_eval_rejects_open_ended_and_accepts_ten_fires(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    first_fire = datetime(2026, 8, 1, 9, tzinfo=UTC)
    final_fire = first_fire
    for _ in range(BOUNDED_INFORMATIONAL_FIRES - 1):
        final_fire = next_fire(DAILY_9AM, final_fire)
    expires_at = next_fire(DAILY_9AM, final_fire)
    store = ScheduleStore()
    with ws(workspace_id), agent(agent_id):
        task = await store.create(
            conversation_id,
            "mccarren-park-events",
            DAILY_9AM,
            "Report McCarren Park events.",
            "McCarren Park events",
            first_fire,
        )
        assert not (await _graded_bounded_daily(CapabilityOutput("", ()))).passed
        task = await store.update(
            task,
            DAILY_9AM,
            "Report McCarren Park events. On the final scheduled fire, also offer Continue same "
            "cadence, Change cadence, or Stop.",
            "McCarren Park events",
            first_fire,
            expires_at=expires_at,
            paused=False,
        )
        assert not (await _graded_bounded_daily(CapabilityOutput("", ()))).passed
        await store.update(
            task,
            DAILY_9AM,
            "Report McCarren Park events.",
            "McCarren Park events",
            first_fire,
            expires_at=expires_at,
            paused=False,
        )
        assert (await _graded_bounded_daily(CapabilityOutput("", ()))).passed


async def test_operational_eval_requires_an_open_ended_task(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    first_fire = datetime(2026, 8, 1, 9, tzinfo=UTC)
    store = ScheduleStore()
    with ws(workspace_id), agent(agent_id):
        task = await store.create(
            conversation_id,
            "credential-refresh",
            "0 * * * *",
            "Refresh integration credentials so synchronization keeps access.",
            "Credential refresh",
            first_fire,
            expires_at=first_fire + timedelta(days=1),
        )
        assert not (await _graded_operational_task_stays_open(CapabilityOutput("", ()))).passed
        await store.update(
            task,
            "0 * * * *",
            "Refresh integration credentials so synchronization keeps access.",
            "Credential refresh",
            first_fire,
            paused=False,
        )
        assert (await _graded_operational_task_stays_open(CapabilityOutput("", ()))).passed


async def test_final_fire_eval_requires_result_then_check_in() -> None:
    no_question = CapabilityOutput("", ())
    assert not (await _graded_final_fire_result_and_check_in(no_question)).passed
    question_without_report = CapabilityOutput(
        "",
        (
            ToolInvocation(
                name="object_apply",
                input={},
                result="created",
                has_result=True,
            ),
            ToolInvocation(
                name="ask_user",
                input={
                    "title": "Keep this task running?",
                    "questions": [
                        {
                            "question": "What should I do next?",
                            "options": [
                                {"label": "Continue daily"},
                                {"label": "Change cadence"},
                                {"label": "Stop"},
                            ],
                        }
                    ],
                },
                result="waiting",
                has_result=True,
            ),
        ),
    )
    assert not (await _graded_final_fire_result_and_check_in(question_without_report)).passed
    searched_without_result = CapabilityOutput(
        "",
        (
            ToolInvocation(
                name="object_apply",
                input={},
                result="created",
                has_result=True,
            ),
            ToolInvocation(
                name="search_web",
                input={"queries": ["McCarren Park events"]},
                result="No listed events.",
                has_result=True,
            ),
            ToolInvocation(
                name="ask_user",
                input={
                    "title": "Keep this task running?",
                    "questions": [
                        {
                            "question": "What should I do next?",
                            "options": [
                                {"label": "Continue daily"},
                                {"label": "Change cadence"},
                                {"label": "Stop"},
                            ],
                        }
                    ],
                },
                result="waiting",
                has_result=True,
            ),
        ),
    )
    assert not (await _graded_final_fire_result_and_check_in(searched_without_result)).passed
    result_and_question = CapabilityOutput(
        "McCarren Park today: Unable to retrieve event listings. Keep this running?",
        (
            ToolInvocation(
                name="object_apply",
                input={},
                result="created",
                has_result=True,
            ),
            ToolInvocation(
                name="search_web",
                input={"queries": ["McCarren Park events"]},
                result="CredentialSlotUnset: exa_api_key",
                has_result=True,
                is_error=True,
            ),
            ToolInvocation(
                name="ask_user",
                input={
                    "title": "Keep this task running?",
                    "questions": [
                        {
                            "question": "What should I do next?",
                            "options": [
                                {"label": "Continue daily"},
                                {"label": "Change cadence"},
                                {"label": "Stop"},
                            ],
                        }
                    ],
                },
                result="waiting",
                has_result=True,
            ),
        ),
    )
    assert (await _graded_final_fire_result_and_check_in(result_and_question)).passed


def test_manifest_exposes_pause_as_a_side_effecting_tool() -> None:
    tool = next(tool for tool in manifest().tools if tool.name == "pause_and_wait")
    assert tool.handler is pause_and_wait
    assert tool.side_effecting is True
    assert manifest().conversation_slots == (AUTOMATIONS_SLOT,)


async def test_manifest_job_fires_through_job_runner(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    with ws(workspace_id), agent(agent_id):
        await ScheduleStore().create(
            conversation_id,
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
    with ws(workspace_id), agent(agent_id):
        for workspace_id in await runner.candidates(f"{NAME}:{RUNNER_JOB}"):
            await runner.fire(f"{NAME}:{RUNNER_JOB}", workspace_id)
    turns = await _turns(conversation_id)
    assert len(turns) == 1
    assert turns[0]["inbound"] == (
        "<scheduled_task>\n"
        f"scheduled_fire: {due_at.isoformat().replace('+00:00', 'Z')}\n"
        "</scheduled_task>\n"
        "check inbox"
    )


async def test_invoke_without_invoker_fails_loud(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    store = ScheduleStore()
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    ctx = _runner_ctx(None)
    with ws(workspace_id), agent(agent_id):
        await store.create(conversation_id, "scheduled-x", DAILY_9AM, "do it", "do it", due_at)
        with pytest.raises(RuntimeError, match="scheduled task fires failed"):
            await ScheduledTaskRunner(ctx=ctx).run()


async def test_failed_pause_resume_keeps_its_one_time_timer(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    with ws(workspace_id), agent(agent_id):
        task = await ScheduleStore().pause(
            conversation_id,
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
    with ws(workspace_id), agent(agent_id):
        task = await ScheduleStore().pause(
            conversation_id,
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


async def _member(
    workspace_id: UUID, created_at: datetime | None = None, *, is_admin: bool = False
) -> UUID:
    member_id = uuid4()
    when = created_at or datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{member_id.hex[:8]}@x.test",
                is_admin=is_admin,
                created_at=when,
                updated_at=when,
            )
        )
    return member_id


async def test_apply_captures_the_creating_member(db: None) -> None:
    """The schedule records who created it, so a later fire can run as that member."""
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=creator)
    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            _object_tool("object_apply"),
            ctx,
            manifest=_task_manifest("digest", DAILY_9AM, "send the digest"),
        )
        tasks = await ScheduleStore().list()
    assert len(tasks) == 1
    assert tasks[0].created_by_member_id == creator


async def test_scheduled_fire_runs_on_behalf_of_the_creator(db: None) -> None:
    """A fired task's turn carries the creator as on_behalf_of (never as speaker), so it keeps the
    creator's private connections without gaining the granting authority a live speaker has."""
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    with ws(workspace_id), agent(agent_id):
        await ScheduleStore().create(
            conversation_id,
            "digest",
            DAILY_9AM,
            "send the digest",
            "send the digest",
            due_at,
            created_by_member_id=creator,
        )
        await ScheduledTaskRunner(ctx=_runner_ctx(invoker)).run()
        turns = await _turns(conversation_id)
        _, _, audience = await _load_turn(turns[0]["id"])
        async with workspace_tx() as connection:
            conversation_member = (
                await connection.execute(
                    sa.select(tables.conversation.c.member_id).where(
                        tables.conversation.c.id == conversation_id
                    )
                )
            ).scalar_one()
    assert len(turns) == 1
    assert turns[0]["admission_source"] == "scheduled"
    assert turns[0]["speaker_member_id"] is None
    assert turns[0]["on_behalf_of_member_id"] == creator
    assert audience == conversation_audience(conversation_member)
    assert audience != conversation_audience(creator)


async def test_a_stranger_cannot_hijack_or_read_another_members_task(db: None) -> None:
    """A task reporting into one member's own conversation is that member's: a stranger cannot read
    it, re-point it (the re-point hijack), or delete it. An admin may inspect or delete, but only
    the creator may edit, so the created_by identity cannot be reassigned."""
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id, created_at=datetime(2027, 1, 1, tzinfo=UTC))
    stranger = await _member(workspace_id, created_at=datetime(2027, 1, 2, tzinfo=UTC))
    creator_ctx = replace(
        _tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=creator
    )
    stranger_ctx = replace(
        _tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=stranger
    )
    apply = _object_tool("object_apply")
    get = _object_tool("object_get")
    listing = _object_tool("object_list")
    delete = _object_tool("object_delete")
    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            apply,
            creator_ctx,
            manifest=_task_manifest(
                "digest",
                DAILY_9AM,
                "Check my oncology portal and summarize the biopsy result.",
            ),
        )
        strangers_view = json.loads(
            await _dispatch(listing, stranger_ctx, kind=SCHEDULED_TASK_KIND)
        )
        assert strangers_view["objects"] == []
        with pytest.raises(UnknownObject):
            await get.handler(
                stranger_ctx,
                get.input_model.model_validate(
                    {
                        "user_description": TOOL_NARRATION,
                        "kind": SCHEDULED_TASK_KIND,
                        "name": "digest",
                    }
                ),
            )
        with pytest.raises(UnknownObject):
            await apply.handler(
                stranger_ctx,
                apply.input_model.model_validate(
                    {
                        "user_description": TOOL_NARRATION,
                        "manifest": _task_manifest("digest", "0 17 * * 1", "hijacked"),
                    }
                ),
            )
        with pytest.raises(UnknownObject):
            await delete.handler(
                stranger_ctx,
                delete.input_model.model_validate(
                    {
                        "user_description": TOOL_NARRATION,
                        "kind": SCHEDULED_TASK_KIND,
                        "name": "digest",
                    }
                ),
            )
        creators_view = json.loads(await _dispatch(listing, creator_ctx, kind=SCHEDULED_TASK_KIND))
        tasks = await ScheduleStore().list()
    assert [row["name"] for row in creators_view["objects"]] == ["digest"]
    assert len(tasks) == 1
    assert tasks[0].prompt == "Check my oncology portal and summarize the biopsy result."
    assert tasks[0].created_by_member_id == creator


async def test_a_task_reporting_into_a_shared_conversation_is_read_but_not_changed(
    db: None,
) -> None:
    """A task reporting into a conversation the whole workspace reads is listed and read whole by
    every member — its fires post there for all of them, so the schedule behind them is no secret.
    Reading is not owning: the same member is refused the edit and the delete, and told who may."""
    workspace_id, agent_id, _private_conversation = await _seed()
    creator = await _member(workspace_id)
    colleague = await _member(workspace_id)
    shared_conversation, private_room, foreign_room = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        for conversation_id, queue_key, audience in (
            (shared_conversation, "channel", str(SHARED_AUDIENCE)),
            (private_room, "private-room", str(room_audience("slack", "C0PRIVATE"))),
            (foreign_room, "foreign-room", str(foreign_room_audience("slack", "C0FOREIGN"))),
        ):
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    surface="slack",
                    queue_key=queue_key,
                    member_id=None,
                    audience=audience,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    creator_ctx = replace(
        _tool_ctx(workspace_id, shared_conversation, agent_id), speaker_member_id=creator
    )
    colleague_ctx = replace(creator_ctx, speaker_member_id=colleague)
    apply = _object_tool("object_apply")
    get = _object_tool("object_get")
    listing = _object_tool("object_list")
    delete = _object_tool("object_delete")

    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            apply,
            creator_ctx,
            manifest=_task_manifest("digest", DAILY_9AM, "post the channel digest", "the digest"),
        )
        seen = json.loads(await _dispatch(listing, colleague_ctx, kind=SCHEDULED_TASK_KIND))
        fetched = yaml.safe_load(
            await _dispatch(get, colleague_ctx, kind=SCHEDULED_TASK_KIND, name="digest")
        )
        with pytest.raises(AdminRequired, match="creator"):
            await _dispatch(
                apply,
                colleague_ctx,
                manifest=_task_manifest("digest", "0 17 * * 1", "hijacked"),
            )
        with pytest.raises(AdminRequired, match="creator or a workspace admin"):
            await _dispatch(delete, colleague_ctx, kind=SCHEDULED_TASK_KIND, name="digest")
        for conversation_id, name in ((private_room, "room-sweep"), (foreign_room, "guest-sweep")):
            await _dispatch(
                apply,
                replace(
                    _tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=creator
                ),
                manifest=_task_manifest(name, DAILY_9AM, f"run {name}", name),
            )
        walled = json.loads(await _dispatch(listing, colleague_ctx, kind=SCHEDULED_TASK_KIND))
        surviving = await ScheduleStore().list()

    assert [row["name"] for row in seen["objects"]] == ["digest"]
    assert [row["summary"] for row in seen["objects"]] == [f"{DAILY_9AM} — the digest"]
    assert fetched["spec"]["prompt"] == "post the channel digest"
    assert [row["name"] for row in walled["objects"]] == ["digest"]
    assert {task.name for task in surviving} == {"digest", "room-sweep", "guest-sweep"}
    assert [
        (task.prompt, task.created_by_member_id) for task in surviving if task.name == "digest"
    ] == [("post the channel digest", creator)]


async def test_task_namespace_and_conversation_are_ambient_agent_scoped(db: None) -> None:
    workspace_id, first_agent, first_conversation = await _seed()
    second_agent, second_conversation = await _second_agent(workspace_id)
    store = ScheduleStore()
    due_at = datetime.now(UTC) + timedelta(hours=1)

    with ws(workspace_id):
        with pytest.raises(AgentUnbound):
            await store.list()
        with agent(first_agent):
            first = await store.create(
                first_conversation,
                "digest",
                DAILY_9AM,
                "first agent digest",
                "first",
                due_at,
            )
            with pytest.raises(ValueError, match="bound to its executing agent"):
                await store.create(
                    second_conversation,
                    "crossed",
                    DAILY_9AM,
                    "crossed",
                    "crossed",
                    due_at,
                )
            with pytest.raises(ValueError, match="bound to its executing agent"):
                await store.pause(
                    second_conversation,
                    "crossed",
                    "crossed",
                    due_at,
                    0,
                )
        with agent(second_agent):
            assert await store.inspect(first) is None
            second_due_at = due_at + timedelta(minutes=1)
            second = await store.create(
                second_conversation,
                "digest",
                DAILY_9AM,
                "second agent digest",
                "second",
                second_due_at,
            )
            second_rows = await store.list()
            second_inspection = await store.inspect(second)
            assert second_inspection is not None
            assert second_inspection.next_run_at.replace(tzinfo=UTC) == second_due_at
        with agent(first_agent):
            first_rows = await store.list()
            first_inspection = await store.inspect(first)
            assert first_inspection is not None
            assert first_inspection.next_run_at.replace(tzinfo=UTC) == due_at
            await store.cancel(first)
            assert await store.inspect(first) is None
        with agent(second_agent):
            surviving_rows = await store.list()
            surviving_inspection = await store.inspect(second)
            assert surviving_inspection is not None
            assert surviving_inspection.next_run_at.replace(tzinfo=UTC) == second_due_at

    assert first.id != second.id
    assert [(task.agent_id, task.prompt) for task in first_rows] == [
        (first_agent, "first agent digest")
    ]
    assert [(task.agent_id, task.prompt) for task in second_rows] == [
        (second_agent, "second agent digest")
    ]
    assert surviving_rows == second_rows


async def test_workspace_clock_fires_exact_records_across_agents(db: None) -> None:
    workspace_id, first_agent, first_conversation = await _seed()
    second_agent, second_conversation = await _second_agent(workspace_id)
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    due_at = datetime.now(UTC) - timedelta(minutes=1)

    with ws(workspace_id):
        with agent(first_agent):
            await ScheduleStore().create(
                first_conversation,
                "digest",
                DAILY_9AM,
                "first agent digest",
                "first",
                due_at,
            )
        with agent(second_agent):
            await ScheduleStore().create(
                second_conversation,
                "digest",
                DAILY_9AM,
                "second agent digest",
                "second",
                due_at,
            )
        await ScheduledTaskRunner(ctx=_runner_ctx(invoker)).run()
        first_turns = await _turns(first_conversation)
        second_turns = await _turns(second_conversation)

    assert len(first_turns) == 1
    assert first_turns[0]["agent_id"] == first_agent
    assert "first agent digest" in first_turns[0]["inbound"]
    assert len(second_turns) == 1
    assert second_turns[0]["agent_id"] == second_agent
    assert "second agent digest" in second_turns[0]["inbound"]
    assert set(dbos.enqueued) == {str(first_turns[0]["id"]), str(second_turns[0]["id"])}


async def test_update_preserves_the_original_creator(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    store = ScheduleStore()
    when = datetime.now(UTC)
    with ws(workspace_id), agent(agent_id):
        first = await store.create(
            conversation_id,
            "digest",
            DAILY_9AM,
            "v1",
            "v1",
            when,
            created_by_member_id=creator,
        )
        second = await store.update(
            first,
            "0 17 * * 1",
            "v2",
            "v2",
            when,
            paused=False,
        )
        tasks = await store.list()
    assert first.id == second.id
    assert second.created_by_member_id == creator
    assert tasks[0].created_by_member_id == creator
    assert tasks[0].schedule == "0 17 * * 1"


async def test_update_cannot_overwrite_a_task_recreated_after_authorization(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    alice = await _member(workspace_id)
    bob = await _member(workspace_id)
    alice_ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=alice)
    apply = _object_tool("object_apply")
    entered = asyncio.Event()
    release = asyncio.Event()
    real_update = ScheduleStore.update

    async def blocked_update(
        store: ScheduleStore,
        expected: ScheduledTask,
        schedule: str,
        prompt: str,
        description: str,
        next_run_at: datetime,
        expires_at: datetime | None = None,
        *,
        paused: bool,
    ) -> ScheduledTask:
        entered.set()
        await release.wait()
        return await real_update(
            store,
            expected,
            schedule,
            prompt,
            description,
            next_run_at,
            expires_at,
            paused=paused,
        )

    monkeypatch.setattr(ScheduleStore, "update", blocked_update)
    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            apply,
            alice_ctx,
            manifest=_task_manifest("digest", DAILY_9AM, "Alice's digest"),
        )
        editing = asyncio.create_task(
            _dispatch(
                apply,
                alice_ctx,
                manifest=_task_manifest("digest", "0 17 * * 1", "Alice's edit"),
            )
        )
        await entered.wait()
        [original] = await ScheduleStore().list()
        await ScheduleStore().cancel(original)
        replacement = await ScheduleStore().create(
            conversation_id,
            "digest",
            DAILY_9AM,
            "Bob's digest",
            "Bob's digest",
            datetime.now(UTC),
            created_by_member_id=bob,
        )
        release.set()
        with pytest.raises(ValueError, match="changed while editing"):
            await editing
        [remaining] = await ScheduleStore().list()

    assert remaining.id == replacement.id
    assert remaining.created_by_member_id == bob
    assert remaining.prompt == "Bob's digest"


@pytest.mark.parametrize(
    "existing",
    [False, True],
    ids=("absent-to-created", "existing-to-deleted"),
)
async def test_object_apply_refuses_a_generation_change_after_its_snapshot(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
    existing: bool,
) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    alice = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=alice)
    scheduler = ScheduleStore()
    real_get = ScheduledTaskObjects.get
    replacement: ScheduledTask | None = None

    with ws(workspace_id), agent(agent_id):
        original = (
            await scheduler.create(
                conversation_id,
                "digest",
                DAILY_9AM,
                "original",
                "original",
                datetime.now(UTC),
                created_by_member_id=alice,
            )
            if existing
            else None
        )

        async def change_after_get(
            store: ScheduledTaskObjects,
            tool_ctx: ToolContext,
            name: str,
        ):
            nonlocal replacement
            detail = await real_get(store, tool_ctx, name)
            if original is not None:
                await scheduler.cancel(original)
            else:
                replacement = await scheduler.create(
                    conversation_id,
                    name,
                    DAILY_9AM,
                    "replacement",
                    "replacement",
                    datetime.now(UTC),
                    created_by_member_id=alice,
                )
            return detail

        monkeypatch.setattr(ScheduledTaskObjects, "get", change_after_get)
        with pytest.raises(ValueError, match="changed while editing"):
            await _dispatch(
                _object_tool("object_apply"),
                ctx,
                manifest=_task_manifest("digest", "0 17 * * 1", "stale create"),
            )
        remaining = await scheduler.list()

    if existing:
        assert remaining == ()
    else:
        assert replacement is not None
        assert remaining == (replacement,)


@pytest.mark.parametrize("change", ["none", "delete", "replace"])
async def test_object_get_rechecks_generation_after_status(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    alice = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=alice)
    scheduler = ScheduleStore()

    with ws(workspace_id), agent(agent_id):
        original = await scheduler.create(
            conversation_id,
            "digest",
            DAILY_9AM,
            "original",
            "original",
            datetime.now(UTC),
            created_by_member_id=alice,
        )

        async def finish_status(store: ScheduleStore, expected: ScheduledTask):
            if change != "none":
                await scheduler.cancel(original)
            if change == "replace":
                await scheduler.create(
                    conversation_id,
                    expected.name,
                    DAILY_9AM,
                    "replacement",
                    "replacement",
                    datetime.now(UTC),
                    created_by_member_id=alice,
                )
            return None

        monkeypatch.setattr(ScheduleStore, "inspect", finish_status)
        if change == "none":
            fetched = yaml.safe_load(
                await _dispatch(
                    _object_tool("object_get"),
                    ctx,
                    kind=SCHEDULED_TASK_KIND,
                    name="digest",
                )
            )
            assert fetched["status"] is None
        else:
            with pytest.raises(ValueError, match="changed while reading"):
                await _dispatch(
                    _object_tool("object_get"),
                    ctx,
                    kind=SCHEDULED_TASK_KIND,
                    name="digest",
                )
        remaining = await scheduler.list()

    if change == "none":
        assert remaining == (original,)
    elif change == "delete":
        assert remaining == ()
    else:
        assert len(remaining) == 1
        assert remaining[0].prompt == "replacement"


async def test_object_delete_refuses_a_replacement_after_its_detail_snapshot(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    alice = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=alice)
    scheduler = ScheduleStore()
    real_delete = ScheduledTaskObjects.delete
    replacement: ScheduledTask | None = None

    with ws(workspace_id), agent(agent_id):
        original = await scheduler.create(
            conversation_id,
            "digest",
            DAILY_9AM,
            "original",
            "original",
            datetime.now(UTC),
            created_by_member_id=alice,
        )

        async def replace_before_delete(
            store: ScheduledTaskObjects,
            tool_ctx: ToolContext,
            name: str,
            *,
            expected_generation: UUID | None,
        ) -> None:
            nonlocal replacement
            await scheduler.cancel(original)
            replacement = await scheduler.create(
                conversation_id,
                name,
                DAILY_9AM,
                "replacement",
                "replacement",
                datetime.now(UTC),
                created_by_member_id=alice,
            )
            await real_delete(store, tool_ctx, name, expected_generation=expected_generation)

        monkeypatch.setattr(ScheduledTaskObjects, "delete", replace_before_delete)
        with pytest.raises(ValueError, match="changed while deleting"):
            await _dispatch(
                _object_tool("object_delete"),
                ctx,
                kind=SCHEDULED_TASK_KIND,
                name="digest",
            )
        [remaining] = await scheduler.list()

    assert replacement is not None
    assert remaining.id == replacement.id
    assert remaining.prompt == "replacement"


async def test_task_read_refuses_a_same_named_replacement(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    alice = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=alice)
    scheduler = ScheduleStore()
    objects = ScheduledTaskObjects()
    real_owner = ScheduledTaskObjects._owner

    with ws(workspace_id), agent(agent_id):
        original = await scheduler.create(
            conversation_id,
            "digest",
            DAILY_9AM,
            "Alice's digest",
            "Alice's digest",
            datetime.now(UTC),
            created_by_member_id=alice,
        )

        async def replace_after_authorization(
            store: ScheduledTaskObjects,
            tool_ctx: ToolContext,
            name: str,
        ):
            owner = await real_owner(store, tool_ctx, name)
            await scheduler.cancel(original)
            await scheduler.create(
                conversation_id,
                name,
                DAILY_9AM,
                "admin-only secret",
                "admin-only secret",
                datetime.now(UTC),
                created_by_member_id=None,
            )
            return owner

        monkeypatch.setattr(ScheduledTaskObjects, "_owner", replace_after_authorization)
        result = await objects.get(ctx, "digest")
        [remaining] = await scheduler.list()

    assert result is None
    assert remaining.prompt == "admin-only secret"


async def test_task_status_refuses_replacement_during_inspection(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    alice = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=alice)
    scheduler = ScheduleStore()
    real_inspect = ScheduleStore.inspect

    with ws(workspace_id), agent(agent_id):
        original = await scheduler.create(
            conversation_id,
            "digest",
            DAILY_9AM,
            "Alice's digest",
            "Alice's digest",
            datetime.now(UTC),
            created_by_member_id=alice,
        )

        async def replace_before_inspection(
            store: ScheduleStore,
            expected: ScheduledTask,
        ):
            await store.cancel(original)
            await store.create(
                conversation_id,
                expected.name,
                DAILY_9AM,
                "admin-only secret",
                "admin-only secret",
                datetime.now(UTC),
                created_by_member_id=None,
            )
            return await real_inspect(store, expected)

        monkeypatch.setattr(ScheduleStore, "inspect", replace_before_inspection)
        with pytest.raises(ValueError, match="changed while reading"):
            await ScheduledTaskObjects().status(ctx, "digest", expected_generation=original.id)
        [remaining] = await scheduler.list()

    assert remaining.prompt == "admin-only secret"


async def test_task_create_refuses_a_row_created_after_absence_check(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    alice = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=alice)
    scheduler = ScheduleStore()
    real_owner = ScheduledTaskObjects._owner

    async def create_after_absence(
        store: ScheduledTaskObjects,
        tool_ctx: ToolContext,
        name: str,
    ):
        assert await real_owner(store, tool_ctx, name) is None
        await scheduler.create(
            conversation_id,
            name,
            DAILY_9AM,
            "replacement",
            "replacement",
            datetime.now(UTC),
            created_by_member_id=alice,
        )
        return None

    monkeypatch.setattr(ScheduledTaskObjects, "_owner", create_after_absence)
    with ws(workspace_id), agent(agent_id):
        with pytest.raises(ValueError, match="changed while editing"):
            await ScheduledTaskObjects().apply(
                ctx,
                "digest",
                ScheduledTaskSpec(schedule=DAILY_9AM, prompt="new task"),
                None,
                expected_generation=None,
            )
        [remaining] = await scheduler.list()

    assert remaining.prompt == "replacement"


@pytest.mark.parametrize("requester_is_admin", [False, True])
async def test_cancel_cannot_delete_a_task_recreated_after_authorization(
    db: None,
    monkeypatch: pytest.MonkeyPatch,
    requester_is_admin: bool,
) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    alice = await _member(workspace_id)
    bob = await _member(workspace_id)
    requester = await _member(workspace_id, is_admin=True) if requester_is_admin else alice
    requester_ctx = replace(
        _tool_ctx(workspace_id, conversation_id, agent_id),
        speaker_member_id=requester,
    )
    apply = _object_tool("object_apply")
    delete = _object_tool("object_delete")
    entered = asyncio.Event()
    release = asyncio.Event()
    real_cancel = ScheduleStore.cancel

    async def blocked_cancel(store: ScheduleStore, expected: ScheduledTask) -> None:
        entered.set()
        await release.wait()
        await real_cancel(store, expected)

    monkeypatch.setattr(ScheduleStore, "cancel", blocked_cancel)
    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            apply,
            replace(requester_ctx, speaker_member_id=alice),
            manifest=_task_manifest("digest", DAILY_9AM, "Alice's digest"),
        )
        [original] = await ScheduleStore().list()
        deleting = asyncio.create_task(
            _dispatch(
                delete,
                requester_ctx,
                kind=SCHEDULED_TASK_KIND,
                name="digest",
            )
        )
        await entered.wait()
        await real_cancel(ScheduleStore(), original)
        replacement = await ScheduleStore().create(
            conversation_id,
            "digest",
            DAILY_9AM,
            "Bob's digest",
            "Bob's digest",
            datetime.now(UTC),
            created_by_member_id=bob,
        )
        release.set()
        with pytest.raises(ValueError, match="changed while cancelling"):
            await deleting
        [remaining] = await ScheduleStore().list()

    assert remaining.id == replacement.id
    assert remaining.created_by_member_id == bob
    assert remaining.prompt == "Bob's digest"


async def test_admin_edits_a_task_no_member_created(db: None) -> None:
    """A task applied on a turn with no acting member is stored creatorless, and the workspace
    admin administers it: there is no creator to fire the prompt as, so the creator gate that
    protects another member's task must not deny the admin a creatorless one."""
    workspace_id, agent_id, conversation_id = await _seed()
    admin = await _member(workspace_id, created_at=datetime(2020, 1, 1, tzinfo=UTC), is_admin=True)
    admin_ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=admin)
    apply = _object_tool("object_apply")
    with ws(workspace_id), agent(agent_id):
        await ScheduleStore().create(
            conversation_id,
            "intel",
            DAILY_9AM,
            "v1",
            "v1",
            datetime.now(UTC),
            created_by_member_id=None,
        )
        await _dispatch(apply, admin_ctx, manifest=_task_manifest("intel", "0 17 * * 1", "v2"))
        tasks = await ScheduleStore().list()
    assert tasks[0].prompt == "v2"
    assert tasks[0].schedule == "0 17 * * 1"
    assert tasks[0].created_by_member_id is None


async def test_admin_may_delete_but_not_edit_another_members_task(db: None) -> None:
    """An admin can manage cadence and cancellation without reading or changing task content."""
    workspace_id, agent_id, conversation_id = await _seed()
    admin = await _member(workspace_id, created_at=datetime(2020, 1, 1, tzinfo=UTC), is_admin=True)
    creator = await _member(workspace_id, created_at=datetime(2027, 1, 1, tzinfo=UTC))
    creator_ctx = replace(
        _tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=creator
    )
    admin_ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=admin)
    apply = _object_tool("object_apply")
    get = _object_tool("object_get")
    listing = _object_tool("object_list")
    delete = _object_tool("object_delete")
    private_prompt = "creator's private medical prompt"
    private_description = "creator's private medical description"
    private_response = "creator's private medical result"
    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            apply,
            creator_ctx,
            manifest=_task_manifest(
                "digest",
                DAILY_9AM,
                private_prompt,
                private_description,
            ),
        )
        [task] = await ScheduleStore().list()
        completed_turn_id = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=completed_turn_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    seq=1,
                    status="done",
                    inbound=private_prompt,
                    admission_source="internal",
                    terminal=TerminalFrame(
                        status="done",
                        text=private_response,
                    ).model_dump(mode="json"),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.update(tables.scheduled_task)
                .where(tables.scheduled_task.c.id == task.id)
                .values(
                    last_run_at=sa.func.now(),
                    last_turn_id=completed_turn_id,
                )
            )
        admin_listing = json.loads(await _dispatch(listing, admin_ctx, kind=SCHEDULED_TASK_KIND))
        admin_get = yaml.safe_load(
            await _dispatch(
                get,
                admin_ctx,
                kind=SCHEDULED_TASK_KIND,
                name="digest",
            )
        )
        with pytest.raises(AdminRequired, match="creator"):
            await apply.handler(
                admin_ctx,
                apply.input_model.model_validate(
                    {
                        "user_description": TOOL_NARRATION,
                        "manifest": _task_manifest(
                            "digest", "0 17 * * 1", "admin's injected prompt"
                        ),
                    }
                ),
            )
        with pytest.raises(AdminRequired, match="creator"):
            await _dispatch(
                apply,
                admin_ctx,
                manifest=_task_manifest(
                    "digest",
                    "0 17 * * 1",
                    private_prompt,
                    private_description,
                ),
            )
        cadence = "0 17 * * 1"
        expiry = datetime.now(UTC) + timedelta(days=30)
        await _dispatch(
            apply,
            admin_ctx,
            manifest=yaml.safe_dump(
                {
                    "kind": SCHEDULED_TASK_KIND,
                    "name": "digest",
                    "spec": {"schedule": cadence, "expires_at": expiry},
                }
            ),
        )
        after_edit = await ScheduleStore().list()
        admin_delete = json.loads(
            await _dispatch(delete, admin_ctx, kind=SCHEDULED_TASK_KIND, name="digest")
        )
        after_delete = await ScheduleStore().list()
    admin_rendered = json.dumps((admin_listing, admin_get, admin_delete))
    assert private_prompt not in admin_rendered
    assert private_description not in admin_rendered
    assert private_response not in admin_rendered
    [admin_row] = admin_listing["objects"]
    assert admin_row["name"] == "digest"
    assert admin_row["summary"] == f"{DAILY_9AM} — private member task"
    assert admin_row["paused"] is False
    assert admin_get["spec"] is None
    assert admin_get["status"]["last_run"] == {
        "turn_id": str(completed_turn_id),
        "turn_status": "done",
    }
    assert admin_delete["spec"] is None
    assert after_edit[0].prompt == private_prompt
    assert after_edit[0].description == private_description
    assert after_edit[0].schedule == cadence
    assert after_edit[0].expires_at == expiry
    assert after_edit[0].created_by_member_id == creator
    assert after_delete == ()


async def test_main_controls_a_members_child_agent_task_without_moving_it(
    db: None, tmp_path: Path
) -> None:
    workspace_id, main_agent, main_conversation = await _seed()
    child_agent, child_conversation = await _second_agent(workspace_id)
    alice = await _member(workspace_id)
    bob = await _member(workspace_id)
    admin = await _member(workspace_id, is_admin=True)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent).where(tables.agent.c.id == main_agent).values(is_main=True)
        )
        await connection.execute(
            sa.update(tables.conversation)
            .where(tables.conversation.c.id == child_conversation)
            .values(member_id=alice, audience=str(conversation_audience(alice)))
        )
        child_name = (
            await connection.execute(
                sa.select(tables.agent.c.name).where(tables.agent.c.id == child_agent)
            )
        ).scalar_one()

    child_ctx = replace(
        _tool_ctx(workspace_id, child_conversation, child_agent),
        speaker_member_id=alice,
    )
    main_ctx = replace(
        _tool_ctx(workspace_id, main_conversation, main_agent),
        speaker_member_id=alice,
        blob=FilesystemBlobStore(root=tmp_path),
    )
    bob_ctx = replace(main_ctx, speaker_member_id=bob)
    admin_ctx = replace(main_ctx, speaker_member_id=admin)
    apply = _object_tool("object_apply")
    get = _object_tool("object_get")
    listing = _object_tool("object_list")
    delete = _object_tool("object_delete")
    private_prompt = "Alice's daily digest"
    private_description = "Alice's private research cadence"
    private_response = "Alice's private research result"

    with ws(workspace_id):
        with agent(child_agent):
            await _dispatch(
                apply,
                child_ctx,
                manifest=_task_manifest(
                    "digest",
                    DAILY_9AM,
                    private_prompt,
                    private_description,
                ),
            )
            [task] = await ScheduleStore().list()
            completed_turn_id = uuid4()
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.turn).values(
                        id=completed_turn_id,
                        workspace_id=workspace_id,
                        conversation_id=child_conversation,
                        agent_id=child_agent,
                        seq=1,
                        status="done",
                        inbound=private_prompt,
                        admission_source="scheduled",
                        on_behalf_of_member_id=alice,
                        terminal=TerminalFrame(
                            status="done",
                            text=private_response,
                        ).model_dump(mode="json"),
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
                await connection.execute(
                    sa.update(tables.scheduled_task)
                    .where(tables.scheduled_task.c.id == task.id)
                    .values(last_run_at=sa.func.now(), last_turn_id=completed_turn_id)
                )
        with agent(main_agent):
            listed = json.loads(
                await _dispatch(
                    listing,
                    main_ctx,
                    kind=SCHEDULED_TASK_KIND,
                    agent=child_name,
                )
            )
            fetched = yaml.safe_load(
                await _dispatch(
                    get,
                    main_ctx,
                    kind=SCHEDULED_TASK_KIND,
                    name="digest",
                    agent=child_name,
                )
            )
            reports_to = next(link for link in fetched["links"] if link["relation"] == "reports_to")
            linked_conversation = yaml.safe_load(
                await _dispatch(get, main_ctx, **reports_to["target"])
            )
            bob_listing = json.loads(
                await _dispatch(
                    listing,
                    bob_ctx,
                    kind=SCHEDULED_TASK_KIND,
                    agent=child_name,
                )
            )
            with pytest.raises(UnknownObject):
                await _dispatch(
                    get,
                    bob_ctx,
                    kind=SCHEDULED_TASK_KIND,
                    name="digest",
                    agent=child_name,
                )
            with pytest.raises(UnknownObject):
                await _dispatch(
                    delete,
                    bob_ctx,
                    kind=SCHEDULED_TASK_KIND,
                    name="digest",
                    agent=child_name,
                )
            admin_listing = json.loads(
                await _dispatch(
                    listing,
                    admin_ctx,
                    kind=SCHEDULED_TASK_KIND,
                    agent=child_name,
                )
            )
            admin_get = yaml.safe_load(
                await _dispatch(
                    get,
                    admin_ctx,
                    kind=SCHEDULED_TASK_KIND,
                    name="digest",
                    agent=child_name,
                )
            )
            updated = json.loads(
                await _dispatch(
                    apply,
                    main_ctx,
                    manifest=_task_manifest("digest", "0 17 * * 1", "Alice's weekly digest"),
                    agent=child_name,
                )
            )
            with pytest.raises(AdminRequired, match="creator"):
                await _dispatch(
                    apply,
                    admin_ctx,
                    manifest=_task_manifest("digest", "0 8 * * *", "admin rewrite"),
                    agent=child_name,
                )
            admin_updated = json.loads(
                await _dispatch(
                    apply,
                    admin_ctx,
                    manifest=yaml.safe_dump(
                        {
                            "kind": SCHEDULED_TASK_KIND,
                            "name": "digest",
                            "spec": {"schedule": "0 8 * * *"},
                        }
                    ),
                    agent=child_name,
                )
            )
            admin_after = json.loads(
                await _dispatch(
                    listing,
                    admin_ctx,
                    kind=SCHEDULED_TASK_KIND,
                    agent=child_name,
                )
            )
            await _dispatch(
                delete,
                admin_ctx,
                kind=SCHEDULED_TASK_KIND,
                name="digest",
                agent=child_name,
            )
        with agent(child_agent):
            remaining = await ScheduleStore().list()

    assert listed["agent"] == child_name
    assert [row["name"] for row in listed["objects"]] == ["digest"]
    assert fetched["agent"] == child_name
    assert fetched["links"] == [
        {
            "relation": "reports_to",
            "target": {
                "kind": "conversation",
                "name": str(child_conversation),
                "agent": child_name,
            },
        },
    ]
    assert linked_conversation["agent"] == child_name
    assert linked_conversation["name"] == str(child_conversation)
    assert updated == {
        "kind": SCHEDULED_TASK_KIND,
        "name": "digest",
        "result": "updated",
        "agent": child_name,
    }
    assert bob_listing["objects"] == []
    assert [row["name"] for row in admin_listing["objects"]] == ["digest"]
    admin_rendered = json.dumps((admin_listing, admin_get))
    assert private_prompt not in admin_rendered
    assert private_description not in admin_rendered
    assert private_response not in admin_rendered
    assert admin_get["spec"] is None
    assert admin_get["status"]["last_run"] == {
        "turn_id": str(completed_turn_id),
        "turn_status": "done",
    }
    assert admin_updated["result"] == "updated"
    [after_row] = admin_after["objects"]
    assert after_row["name"] == "digest"
    assert after_row["summary"] == "0 8 * * * — private member task"
    assert after_row["paused"] is False
    assert remaining == ()


async def test_cross_agent_object_target_requires_main_live_member_authority(db: None) -> None:
    workspace_id, main_agent, main_conversation = await _seed()
    child_agent, child_conversation = await _second_agent(workspace_id)
    alice = await _member(workspace_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent).where(tables.agent.c.id == main_agent).values(is_main=True)
        )
        child_name = (
            await connection.execute(
                sa.select(tables.agent.c.name).where(tables.agent.c.id == child_agent)
            )
        ).scalar_one()
        main_name = (
            await connection.execute(
                sa.select(tables.agent.c.name).where(tables.agent.c.id == main_agent)
            )
        ).scalar_one()
    listing = _object_tool("object_list")
    main_ctx = replace(
        _tool_ctx(workspace_id, main_conversation, main_agent),
        speaker_member_id=alice,
    )
    child_ctx = replace(
        _tool_ctx(workspace_id, child_conversation, child_agent),
        speaker_member_id=alice,
    )

    with ws(workspace_id):
        with agent(child_agent), pytest.raises(ValueError, match="only the workspace main agent"):
            await _dispatch(
                listing,
                child_ctx,
                kind=SCHEDULED_TASK_KIND,
                agent=main_name,
            )
        with agent(main_agent):
            self_listing = json.loads(
                await _dispatch(
                    listing,
                    main_ctx,
                    kind=SCHEDULED_TASK_KIND,
                    agent=main_name,
                )
            )
            with pytest.raises(ValueError, match="exact live member-requested"):
                await _dispatch(
                    listing,
                    replace(main_ctx, speaker_member_id=None),
                    kind=SCHEDULED_TASK_KIND,
                    agent=child_name,
                )
            with pytest.raises(ValueError, match="typed subagent"):
                await _dispatch(
                    listing,
                    replace(
                        main_ctx,
                        turn=main_ctx.turn.model_copy(
                            update={"subagent_profile": "research"},
                        ),
                    ),
                    kind=SCHEDULED_TASK_KIND,
                    agent=child_name,
                )
            with pytest.raises(ValueError, match="no agent named"):
                await _dispatch(
                    listing,
                    main_ctx,
                    kind=SCHEDULED_TASK_KIND,
                    agent="unknown-agent",
                )
            with pytest.raises(ValueError, match="rejects an agent target"):
                await _dispatch(listing, main_ctx, kind="agent", agent=child_name)
    assert "agent" not in self_listing


async def test_main_cross_agent_task_create_is_not_supported(
    db: None,
) -> None:
    workspace_id, main_agent, main_conversation = await _seed()
    child_agent, _ = await _second_agent(workspace_id)
    alice = await _member(workspace_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent).where(tables.agent.c.id == main_agent).values(is_main=True)
        )
        child_name = (
            await connection.execute(
                sa.select(tables.agent.c.name).where(tables.agent.c.id == child_agent)
            )
        ).scalar_one()
    ctx = replace(
        _tool_ctx(workspace_id, main_conversation, main_agent),
        speaker_member_id=alice,
    )

    with ws(workspace_id):
        with (
            agent(main_agent),
            pytest.raises(
                VerbNotSupported,
                match="do not support cross-agent create",
            ),
        ):
            await _dispatch(
                _object_tool("object_apply"),
                ctx,
                manifest=_task_manifest("digest", DAILY_9AM, "run as the child"),
                agent=child_name,
            )
        with agent(child_agent):
            assert await ScheduleStore().list() == ()


async def test_parallel_main_targets_keep_their_agent_namespaces_isolated(db: None) -> None:
    workspace_id, main_agent, main_conversation = await _seed()
    first_agent, first_conversation = await _second_agent(workspace_id)
    second_agent, second_conversation = await _second_agent(workspace_id)
    alice = await _member(workspace_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent).where(tables.agent.c.id == main_agent).values(is_main=True)
        )
        names = {
            row.id: row.name
            for row in (
                await connection.execute(
                    sa.select(tables.agent.c.id, tables.agent.c.name).where(
                        tables.agent.c.id.in_((first_agent, second_agent))
                    )
                )
            ).all()
        }
    apply = _object_tool("object_apply")
    listing = _object_tool("object_list")
    with ws(workspace_id):
        with agent(first_agent):
            await _dispatch(
                apply,
                replace(
                    _tool_ctx(workspace_id, first_conversation, first_agent),
                    speaker_member_id=alice,
                ),
                manifest=_task_manifest("digest", DAILY_9AM, "first child"),
            )
        with agent(second_agent):
            await _dispatch(
                apply,
                replace(
                    _tool_ctx(workspace_id, second_conversation, second_agent),
                    speaker_member_id=alice,
                ),
                manifest=_task_manifest("digest", DAILY_9AM, "second child"),
            )
        with agent(main_agent):
            main_ctx = replace(
                _tool_ctx(workspace_id, main_conversation, main_agent),
                speaker_member_id=alice,
            )
            first, second = await asyncio.gather(
                _dispatch(
                    listing,
                    main_ctx,
                    kind=SCHEDULED_TASK_KIND,
                    agent=names[first_agent],
                ),
                _dispatch(
                    listing,
                    main_ctx,
                    kind=SCHEDULED_TASK_KIND,
                    agent=names[second_agent],
                ),
            )

    first_payload, second_payload = json.loads(first), json.loads(second)
    assert first_payload["agent"] == names[first_agent]
    assert "first child" in first_payload["objects"][0]["summary"]
    assert second_payload["agent"] == names[second_agent]
    assert "second child" in second_payload["objects"][0]["summary"]


def _facet(prop: dict, key: str) -> object:
    if key in prop:
        return prop[key]
    return next((entry[key] for entry in prop.get("anyOf", ()) if key in entry), None)


def test_spec_schema_states_the_shape_a_form_needs() -> None:
    properties = ScheduledTaskSpec.model_json_schema()["properties"]

    assert properties["schedule"]["examples"] == ["0 9 * * 1-5"]
    assert _facet(properties["schedule"], "maxLength") == SCHEDULE_MAX
    assert _facet(properties["description"], "maxLength") == SUMMARY_MAX
    assert _facet(properties["prompt"], "maxLength") is None
    assert properties["expires_at"]["title"] == "Expires At"
    assert _facet(properties["expires_at"], "format") == "date-time"


def test_a_bound_field_refuses_a_value_past_its_bound() -> None:
    with pytest.raises(ValueError):
        ScheduledTaskSpec(schedule="* " * SCHEDULE_MAX)
