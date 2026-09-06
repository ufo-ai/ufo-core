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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from sqlalchemy.exc import IntegrityError
from ufo_ext_memory.store import memory_item
from ufo_ext_scheduled_tasks import tools as scheduled_tools
from ufo_ext_scheduled_tasks.conversation_slot import AUTOMATIONS_SLOT
from ufo_ext_scheduled_tasks.cron import next_fire
from ufo_ext_scheduled_tasks.manifest import NAME, manifest
from ufo_ext_scheduled_tasks.runner import (
    ScheduledTaskRunner,
)
from ufo_ext_scheduled_tasks.schedules import (
    ScheduledTask,
    ScheduleStore,
    due_task_workspaces,
)
from ufo_ext_scheduled_tasks.schedules import scheduled_task as schedule_table
from ufo_ext_scheduled_tasks.tools import (
    PROMPT_EXCERPT_MAX,
    SCHEDULE_MAX,
    SCHEDULED_TASK_KIND,
    SCHEDULED_TASK_OBJECT,
    ScheduledTaskObjects,
    ScheduledTaskSpec,
)
from ufo_ext_scheduled_tasks.visibility import task_content_visible

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.suites.object_tools import (
    BOUNDED_INFORMATIONAL_FIRES,
    FEW_FIRES_MAX,
    REMEMBERED_CADENCE,
    _graded_a_few_runs,
    _graded_bounded_daily,
    _graded_no_emulated_run_once,
    _graded_remembered_cadence,
    _remember_cadence,
)
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.host.ext.loader import turn_tools
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.ext.conversation_slots import ConversationSlotContext, ConversationSlotItem
from ufo.runtime.objects import AdminRequired, ObjectListQuery, UnknownObject, VerbNotSupported
from ufo.runtime.surfaces.admission import Admission, AdmissionInvoker
from ufo.runtime.tools.context import SpawnResult, SpeakerRequired, ToolContext
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.turns.subjects import SHARED_SUBJECT
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, TerminalFrame, Turn
from ufo.sdk.audience import (
    conversation_audience,
)

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

TOOL_NARRATION = "setting up the reminder"

DAILY_9AM = "0 9 * * *"
APPLY_NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


def _object_tool(name: str) -> ToolDef:
    tools, _, _ = turn_tools((manifest(),), None, audience=conversation_audience(None))
    return next(tool for tool in tools if tool.name == name)


def _task_manifest(
    name: str,
    schedule: str,
    prompt: str,
    description: str = "",
    expires_at: datetime | None = None,
    run_now: bool | None = None,
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
                **({} if run_now is None else {"run_now": run_now}),
            },
        }
    )


async def _dispatch(tool: ToolDef, ctx: ToolContext, **args: object) -> str:
    result = await tool.handler(ctx, tool.input_model.model_validate({**args}))
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


async def _speaker(workspace_id: UUID) -> UUID:
    """The workspace's own member. Every member surface resolves its speaker before it admits, so a
    member message — a pause resume included — always carries one; one that resolved to nobody is
    refused at admission rather than answered."""
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.member.c.id).where(tables.member.c.workspace_id == workspace_id)
            )
        ).scalar_one()


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


async def test_list_reported_carries_audience_and_surface_label(db: None) -> None:
    workspace_id, agent_id, private_conversation = await _seed()
    async with workspace_tx() as connection:
        creator = (
            await connection.execute(
                sa.select(tables.member.c.id).where(
                    tables.member.c.workspace_id == workspace_id,
                    tables.member.c.email == "who@example.com",
                )
            )
        ).scalar_one()
    shared_conversation = uuid4()
    other_member = await _member(workspace_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=shared_conversation,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="slack",
                surface_label="#general",
                queue_key="shared",
                audience="shared",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id), agent(agent_id):
        await _store().create(
            private_conversation,
            "private",
            DAILY_9AM,
            "private prompt",
            "private task",
            datetime.now(UTC),
            created_by_member_id=creator,
        )
        await _store().create(
            shared_conversation,
            "shared",
            DAILY_9AM,
            "shared prompt",
            "shared task",
            datetime.now(UTC),
            created_by_member_id=creator,
        )
        listed = await _store().list_reported()

    by_name = {row.task.name: row for row in listed}
    assert by_name["shared"].audience == "shared"
    assert by_name["shared"].surface_label == "#general"
    assert by_name["private"].surface_label is None
    assert task_content_visible(by_name["shared"], other_member)
    assert not task_content_visible(by_name["private"], other_member)


async def test_a_creatorless_task_follows_the_conversation_it_reports_into(db: None) -> None:
    """A task nobody is recorded as creating is not everybody's to read: its content follows the
    audience of the conversation it reports into, so a private conversation keeps it to that
    conversation's own member and only a workspace-shared one answers every member."""
    workspace_id, agent_id, private_conversation = await _seed()
    async with workspace_tx() as connection:
        conversation_member = (
            await connection.execute(
                sa.select(tables.conversation.c.member_id).where(
                    tables.conversation.c.id == private_conversation
                )
            )
        ).scalar_one()
    shared_conversation = uuid4()
    other_member = await _member(workspace_id)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=shared_conversation,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="slack",
                surface_label="#general",
                queue_key="shared",
                audience="shared",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id), agent(agent_id):
        await _store().create(
            private_conversation,
            "orphan-private",
            DAILY_9AM,
            "private prompt",
            "private task",
            datetime.now(UTC),
        )
        await _store().create(
            shared_conversation,
            "orphan-shared",
            DAILY_9AM,
            "shared prompt",
            "shared task",
            datetime.now(UTC),
        )
        listed = await _store().list_reported()

    by_name = {row.task.name: row for row in listed}
    assert by_name["orphan-private"].task.created_by_member_id is None
    assert not task_content_visible(by_name["orphan-private"], other_member)
    assert not task_content_visible(by_name["orphan-private"], None)
    assert task_content_visible(by_name["orphan-private"], conversation_member)
    assert task_content_visible(by_name["orphan-shared"], other_member)


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
                visibility="workspace",
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
    return context_for(NAME, frozenset(), invoker=invoker)


def _store() -> ScheduleStore:
    """The extension's own schedule store over the ambient workspace — the store is the
    extension's now, so a caller hands it a context rather than finding one on core."""
    return ScheduleStore(context_for(NAME, frozenset()))


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


async def test_a_turn_reads_a_prompt_excerpt_the_member_reads_whole(db: None) -> None:
    """`object_list` answers a whole page — 50 rows — into the model's context, and the spec bounds
    no prompt, so the rows a turn reads carry an excerpt. The member's own read carries the prompt
    whole: the record's own page states it, and a cut made in the projection reaches the reader
    indistinguishable from a prompt that ended. No index reads the prompt down a column — every row
    cut it mid-word — so the portal states it where it has the room."""
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=creator)
    sprawling = "Summarize what merged yesterday and post the list to the team. " * 8
    assert len(sprawling) > PROMPT_EXCERPT_MAX
    with ws(workspace_id), agent(agent_id):
        await _store().create(
            conversation_id,
            "digest",
            DAILY_9AM,
            sprawling,
            "daily digest",
            datetime(2026, 8, 8, 9, tzinfo=UTC),
            created_by_member_id=creator,
        )
        listed = json.loads(
            await _dispatch(_object_tool("object_list"), ctx, kind=SCHEDULED_TASK_KIND)
        )
        page = await ScheduledTaskObjects().member_page(
            context_for(NAME, frozenset()),
            member_id=creator,
            admin=False,
            query=ObjectListQuery(supported_fields=SCHEDULED_TASK_OBJECT.list_fields),
        )

    [turn_row] = listed["objects"]
    assert turn_row["prompt"] == sprawling[:PROMPT_EXCERPT_MAX]
    [member_row] = page.rows
    assert member_row.fields["prompt"] == sprawling


async def test_automations_slot_rejects_a_recreated_task_generation(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    with ws(workspace_id), agent(agent_id):
        store = _store()
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
        tasks = await _store().list()
        fetched = yaml.safe_load(
            await _dispatch(
                _object_tool("object_get"),
                ctx,
                ref=f"{SCHEDULED_TASK_KIND}/investor-replies",
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
            sa.update(schedule_table)
            .values(last_run_at=fired_at)
            .where(schedule_table.c.id == tasks[0].id)
        )
    with ws(workspace_id), agent(agent_id):
        refetched = yaml.safe_load(
            await _dispatch(
                _object_tool("object_get"),
                ctx,
                ref=f"{SCHEDULED_TASK_KIND}/investor-replies",
            )
        )
    assert refetched["status"]["last_run_at"] == fired_at.isoformat()


async def test_applied_task_requires_a_member_requester(db: None) -> None:
    """The refusal is about who is asking, not about what they may do, so it is `SpeakerRequired`:
    a `requested_by` ref binds a requester and the same call then stands. Raised as
    `AdminRequired` the model reads an authority it cannot obtain and never retries."""
    workspace_id, agent_id, conversation_id = await _seed()
    with ws(workspace_id), agent(agent_id):
        with pytest.raises(SpeakerRequired, match="member requester"):
            await _dispatch(
                _object_tool("object_apply"),
                _tool_ctx(workspace_id, conversation_id, agent_id),
                manifest=_task_manifest("digest", DAILY_9AM, "check the inbox"),
            )
        assert await _store().list() == ()


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
        [task] = await _store().list()
        assert task.paused is True
        assert task.prompt == "assemble the digest"
        fetched = yaml.safe_load(
            await _dispatch(_object_tool("object_get"), ctx, ref=f"{SCHEDULED_TASK_KIND}/digest")
        )
        assert fetched["spec"]["paused"] is True
        assert fetched["status"]["paused"] is True
        due_at = task.next_run_at + timedelta(seconds=1)
        assert await _store().claim_due(due_at, 300) == ()
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
        [still_paused] = await _store().list()
        assert still_paused.paused is True
        overdue = datetime.now(UTC) - timedelta(minutes=5)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(schedule_table)
                .values(next_run_at=overdue)
                .where(schedule_table.c.id == still_paused.id)
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
        [resumed] = await _store().list()
        assert resumed.paused is False
        [claimed] = await _store().claim_due(resumed.next_run_at + timedelta(seconds=1), 300)
        assert claimed.name == "digest"
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(schedule_table)
                .values(next_run_at=overdue, claimed_by=None, claim_expires_at=None)
                .where(schedule_table.c.id == resumed.id)
            )
    assert workspace_id in await due_task_workspaces()()


async def test_re_applying_a_manifest_keeps_the_recorded_run(db: None) -> None:
    """Re-applying an existing task's manifest changes what the task says, never what it did: the
    last fire's time, its turn, and that turn's response all read back after the update."""
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=creator)
    store = _store()
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    with ws(workspace_id), agent(agent_id):
        await store.create(
            conversation_id,
            "competitive-intel-daily",
            DAILY_9AM,
            "summarize what competitors shipped",
            "competitive intel",
            due_at,
            created_by_member_id=creator,
        )
        await ScheduledTaskRunner(ctx=_runner_ctx(invoker)).run()
        [turn] = await _turns(conversation_id)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .values(
                    status="done",
                    terminal={"status": "done", "text": "three competitors moved"},
                )
                .where(tables.turn.c.id == turn["id"])
            )
        fired = (await store.list())[0]
        applied = json.loads(
            await _dispatch(
                _object_tool("object_apply"),
                ctx,
                manifest=_task_manifest(
                    "competitive-intel-daily",
                    DAILY_9AM,
                    "summarize what competitors shipped and price",
                    "competitive intel",
                ),
            )
        )
        fetched = yaml.safe_load(
            await _dispatch(
                _object_tool("object_get"),
                ctx,
                ref=f"{SCHEDULED_TASK_KIND}/competitive-intel-daily",
            )
        )
        edited = (await store.list())[0]

    assert applied["result"] == "updated"
    assert fired.last_run_at is not None
    assert edited.prompt == "summarize what competitors shipped and price"
    assert edited.last_run_at == fired.last_run_at
    assert fetched["status"]["last_run_at"] == fired.last_run_at.isoformat()
    assert fetched["status"]["last_run"] == {
        "turn_id": str(turn["id"]),
        "turn_status": "done",
        "response": "three competitors moved",
    }


async def test_expired_task_is_cancelled_without_invoking(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    with ws(workspace_id), agent(agent_id):
        await _store().create(
            conversation_id,
            "expired-digest",
            DAILY_9AM,
            "send the digest",
            "send the digest",
            datetime.now(UTC) + timedelta(days=1),
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        await ScheduledTaskRunner(ctx=_runner_ctx(invoker)).run()
        tasks = await _store().list()
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
    store = _store()
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
    store = _store()
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


async def test_batch_backlog_does_not_skip_next_cron_occurrence(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    tick_at = datetime.now(UTC).replace(second=30, microsecond=0)
    expiry_checked_at = tick_at + timedelta(seconds=31)
    invoker = AdmissionInvoker(
        admission=Admission(dbos=StubDbos(), durable_surfaces=frozenset()),
        workspace_id=workspace_id,
    )
    store = _store()
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
    store = _store()
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
        await _store().create(
            conversation_id,
            "expired-before-next-fire",
            DAILY_9AM,
            "send the digest",
            "send the digest",
            now + timedelta(days=1),
            expires_at=now - timedelta(seconds=1),
        )
        assert await due_task_workspaces()() == (workspace_id,)
        assert await _store().claim_due(now, 300) == ()
        tasks = await _store().list()
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
        rows = await _store().list()
    assert updated["result"] == "updated"
    assert len(rows) == 1
    assert rows[0].schedule == "0 17 * * 1"
    assert rows[0].conversation_id == conversation_id


async def test_claim_due_caps_a_sweep_at_its_batch_limit(db: None) -> None:
    """One sweep leases at most `limit` tasks, oldest due first; the remainder stays due and the
    next sweep claims it — a workspace with a task pileup makes bounded progress per tick instead
    of claiming more than one lease can cover."""
    workspace_id, agent_id, conversation_id = await _seed()
    store = _store()
    older = datetime.now(UTC) - timedelta(minutes=10)
    newer = datetime.now(UTC) - timedelta(minutes=5)
    with ws(workspace_id), agent(agent_id):
        await store.create(conversation_id, "first", DAILY_9AM, "a", "a", older)
        await store.create(conversation_id, "second", DAILY_9AM, "b", "b", newer)
        first_sweep = await store.claim_due(datetime.now(UTC), 300, limit=1)
        assert [task.name for task in first_sweep] == ["first"]
        second_sweep = await store.claim_due(datetime.now(UTC), 300, limit=1)
        assert [task.name for task in second_sweep] == ["second"]


async def test_next_recurring_fire_admits_a_distinct_turn(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()),
        workspace_id=workspace_id,
    )
    runner = ScheduledTaskRunner(ctx=_runner_ctx(invoker))
    with ws(workspace_id), agent(agent_id):
        task = await _store().create(
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
                sa.update(schedule_table)
                .values(next_run_at=datetime.now(UTC) - timedelta(seconds=1))
                .where(schedule_table.c.id == task.id)
            )
        await runner.run()
        turns = await _turns(conversation_id)
    assert len(turns) == 2
    assert turns[0]["id"] != turns[1]["id"]
    assert turns[0]["dispatch_enqueued_at"] is not None
    assert turns[1]["dispatch_enqueued_at"] is None
    assert dbos.enqueued == [str(turns[0]["id"])]


async def test_run_now_fires_at_once_and_keeps_the_schedule_after_it(db: None) -> None:
    """`run_now: true` puts the first fire in the present, so the next tick claims it and the
    member reads a first report without waiting for the cron. The fire after it is the schedule's
    own, and an apply that omits `run_now` still waits for the schedule."""
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=creator)
    invoker = AdmissionInvoker(
        admission=Admission(dbos=StubDbos(), durable_surfaces=frozenset()),
        workspace_id=workspace_id,
    )
    applied_at = datetime.now(UTC)
    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            _object_tool("object_apply"),
            ctx,
            manifest=_task_manifest(
                "competitive-intel-daily", DAILY_9AM, "watch the competitors", run_now=True
            ),
        )
        await _dispatch(
            _object_tool("object_apply"),
            ctx,
            manifest=_task_manifest("quiet-digest", DAILY_9AM, "watch the warehouse"),
        )
        applied = {task.name: task for task in await _store().list()}
        await ScheduledTaskRunner(ctx=_runner_ctx(invoker)).run()
        after = {task.name: task for task in await _store().list()}
        turns = await _turns(conversation_id)
    immediate = applied["competitive-intel-daily"]
    waiting = applied["quiet-digest"]
    assert applied_at <= immediate.next_run_at <= datetime.now(UTC)
    assert waiting.next_run_at == next_fire(DAILY_9AM, applied_at)
    assert ["watch the competitors" in turn["inbound"] for turn in turns] == [True]
    assert after["competitive-intel-daily"].next_run_at == next_fire(DAILY_9AM, datetime.now(UTC))
    assert after["competitive-intel-daily"].last_run_at is not None
    assert after["quiet-digest"].last_run_at is None


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
        assert await _store().list() == ()
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
            "manifest": _task_manifest("too-many", "0 9 * * * *", "too many fields"),
        }
    )
    with ws(workspace_id), agent(agent_id), pytest.raises(ValueError, match="5-field"):
        await apply.handler(ctx, args)


async def test_applied_task_rejects_expiry_at_its_first_fire(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scheduled_tools, "datetime", SimpleNamespace(now=lambda _tz: APPLY_NOW))
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=creator)
    first_fire = next_fire(DAILY_9AM, APPLY_NOW)
    apply = _object_tool("object_apply")
    args = apply.input_model.model_validate(
        {"manifest": _task_manifest("zero-fire", DAILY_9AM, "report once", expires_at=first_fire)}
    )

    with ws(workspace_id), agent(agent_id), pytest.raises(ValueError, match="next scheduled fire"):
        await apply.handler(ctx, args)


async def test_applied_task_update_rejects_expiry_at_its_next_fire(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scheduled_tools, "datetime", SimpleNamespace(now=lambda _tz: APPLY_NOW))
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=creator)
    apply = _object_tool("object_apply")
    first_fire = next_fire(DAILY_9AM, APPLY_NOW)

    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            apply,
            ctx,
            manifest=_task_manifest("zero-fire-update", DAILY_9AM, "report once"),
        )
        args = apply.input_model.model_validate(
            {
                "manifest": _task_manifest(
                    "zero-fire-update", DAILY_9AM, "report once", expires_at=first_fire
                )
            }
        )
        with pytest.raises(ValueError, match="next scheduled fire"):
            await apply.handler(ctx, args)
        [task] = await _store().list()

    assert task.expires_at is None


async def test_bounded_daily_eval_rejects_open_ended_and_accepts_ten_fires(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    first_fire = datetime(2026, 8, 1, 9, tzinfo=UTC)
    final_fire = first_fire
    for _ in range(BOUNDED_INFORMATIONAL_FIRES - 1):
        final_fire = next_fire(DAILY_9AM, final_fire)
    expires_at = next_fire(DAILY_9AM, final_fire)
    store = _store()
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


def _fire_after(schedule: str, first_fire: datetime, occurrences: int) -> datetime:
    fire = first_fire
    for _ in range(occurrences):
        fire = next_fire(schedule, fire)
    return fire


async def test_run_once_eval_rejects_a_cron_schedule_bounded_by_an_expiry(db: None) -> None:
    workspace_id, agent_id, _ = await _seed()
    applied = CapabilityOutput(
        "",
        (
            ToolInvocation(
                name="object_apply",
                input={
                    "manifest": _task_manifest(
                        "board-deck-link",
                        "0 9 * * *",
                        "Post the Q3 board deck link.",
                        expires_at=datetime(2026, 8, 2, 9, tzinfo=UTC),
                    )
                },
                result="created",
                has_result=True,
            ),
        ),
    )
    recurring = CapabilityOutput(
        "",
        (
            ToolInvocation(
                name="object_apply",
                input={
                    "manifest": _task_manifest(
                        "board-deck-link", "0 9 * * *", "Post the Q3 board deck link."
                    )
                },
                result="created",
                has_result=True,
            ),
        ),
    )
    with ws(workspace_id), agent(agent_id):
        emulated = await _graded_no_emulated_run_once(applied)
        assert not emulated.passed
        assert "expiry" in emulated.reason
        assert not (await _graded_no_emulated_run_once(recurring)).passed
        assert (
            await _graded_no_emulated_run_once(
                CapabilityOutput("One-time runs are not supported, so nothing is scheduled.", ())
            )
        ).passed


async def test_a_few_runs_eval_accepts_a_small_bound_and_rejects_a_long_one(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    schedule = "0 5 * * *"
    first_fire = datetime(2026, 8, 1, 5, tzinfo=UTC)
    prompt = "Report warehouse temperature readings out of range."
    store = _store()
    with ws(workspace_id), agent(agent_id):
        task = await store.create(
            conversation_id,
            "warehouse-temperature",
            schedule,
            prompt,
            "Warehouse temperature log",
            first_fire,
            expires_at=_fire_after(schedule, first_fire, FEW_FIRES_MAX + 3),
        )
        too_many = await _graded_a_few_runs(CapabilityOutput("", ()))
        assert not too_many.passed
        assert "that 'a few' names" in too_many.reason
        await store.update(
            task,
            schedule,
            prompt,
            "Warehouse temperature log",
            first_fire,
            expires_at=_fire_after(schedule, first_fire, 3),
            paused=False,
        )
        assert (await _graded_a_few_runs(CapabilityOutput("", ()))).passed


async def test_remembered_cadence_eval_seeds_the_preference_and_requires_its_cadence(
    db: None, tmp_path: Path
) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    remembered = "0 8 * * 1-5"
    guessed = "0 9 * * 1-5"
    prompt = "Report anything red on the staging deploy dashboard."
    store = _store()
    with ws(workspace_id), agent(agent_id):
        await _remember_cadence(workspace_id, agent_id, FilesystemBlobStore(root=tmp_path))
        await _remember_cadence(workspace_id, agent_id, FilesystemBlobStore(root=tmp_path))
        async with workspace_tx() as connection:
            remembered_rows = list(
                (
                    await connection.execute(
                        sa.select(memory_item.c.body, memory_item.c.subject).where(
                            memory_item.c.workspace_id == workspace_id
                        )
                    )
                ).all()
            )
        assert [(row.body, row.subject) for row in remembered_rows] == [
            (REMEMBERED_CADENCE, SHARED_SUBJECT)
        ]
        task = await store.create(
            conversation_id,
            "staging-deploy-watch",
            guessed,
            prompt,
            "Staging deploy dashboard",
            next_fire(guessed, datetime(2026, 8, 1, tzinfo=UTC)),
        )
        guessed_cadence = await _graded_remembered_cadence(CapabilityOutput("", ()))
        assert not guessed_cadence.passed
        assert "remembered weekday 08:00 UTC" in guessed_cadence.reason
        await store.update(
            task,
            remembered,
            prompt,
            "Staging deploy dashboard",
            next_fire(remembered, datetime(2026, 8, 1, tzinfo=UTC)),
            paused=False,
        )
        searched = await _graded_remembered_cadence(
            CapabilityOutput(
                "",
                (
                    ToolInvocation(
                        name="memory_search",
                        input={"query": "regularly"},
                        result=REMEMBERED_CADENCE,
                        has_result=True,
                    ),
                ),
            )
        )
        assert searched.passed
        assert searched.evidence["memory_search"] is True


async def test_invoke_without_invoker_fails_loud(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    store = _store()
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    ctx = _runner_ctx(None)
    with ws(workspace_id), agent(agent_id):
        await store.create(conversation_id, "scheduled-x", DAILY_9AM, "do it", "do it", due_at)
        with pytest.raises(RuntimeError, match="scheduled task fires failed"):
            await ScheduledTaskRunner(ctx=ctx).run()


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
        tasks = await _store().list()
    assert len(tasks) == 1
    assert tasks[0].created_by_member_id == creator


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
                get.input_model.model_validate({"ref": f"{SCHEDULED_TASK_KIND}/digest"}),
            )
        with pytest.raises(UnknownObject):
            await apply.handler(
                stranger_ctx,
                apply.input_model.model_validate(
                    {
                        "manifest": _task_manifest("digest", "0 17 * * 1", "hijacked"),
                    }
                ),
            )
        with pytest.raises(UnknownObject):
            await delete.handler(
                stranger_ctx,
                delete.input_model.model_validate(
                    {
                        "kind": SCHEDULED_TASK_KIND,
                        "name": "digest",
                    }
                ),
            )
        creators_view = json.loads(await _dispatch(listing, creator_ctx, kind=SCHEDULED_TASK_KIND))
        tasks = await _store().list()
    assert [row["name"] for row in creators_view["objects"]] == ["digest"]
    assert len(tasks) == 1
    assert tasks[0].prompt == "Check my oncology portal and summarize the biopsy result."
    assert tasks[0].created_by_member_id == creator


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
            await _store().create(
                first_conversation,
                "digest",
                DAILY_9AM,
                "first agent digest",
                "first",
                due_at,
            )
        with agent(second_agent):
            await _store().create(
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
    store = _store()
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


async def test_update_preserves_the_marks_of_the_last_fire(db: None) -> None:
    """An edit states the definition, so `update` takes no run marks at all: the fire time and the
    turn a fire recorded survive it, and only the claim is released."""
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    store = _store()
    ran_at = datetime(2026, 8, 15, 9, tzinfo=UTC)
    fired_turn = uuid4()
    with ws(workspace_id), agent(agent_id):
        await store.create(
            conversation_id,
            "digest",
            DAILY_9AM,
            "v1",
            "v1",
            datetime.now(UTC) - timedelta(minutes=1),
            created_by_member_id=creator,
        )
        [claimed] = await store.claim_due(datetime.now(UTC), 300)
        assert await store.reschedule(claimed, next_fire(DAILY_9AM, ran_at), ran_at, fired_turn)
        ran = (await store.list())[0]
        edited = await store.update(
            ran,
            "0 17 * * 1",
            "v2",
            "v2",
            next_fire("0 17 * * 1", ran_at),
            paused=False,
        )
        inspection = await store.inspect(edited)

    assert ran.last_run_at == ran_at
    assert edited.prompt == "v2"
    assert edited.last_run_at == ran_at
    assert edited.claim_id is None
    assert inspection is not None
    assert inspection.last_run_at == ran_at
    assert inspection.last_turn_id == fired_turn


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
        [original] = await _store().list()
        await _store().cancel(original)
        replacement = await _store().create(
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
        [remaining] = await _store().list()

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
    scheduler = _store()
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


async def test_object_delete_refuses_a_replacement_after_its_detail_snapshot(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    alice = await _member(workspace_id)
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=alice)
    scheduler = _store()
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
    scheduler = _store()
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
    scheduler = _store()
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
    scheduler = _store()
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
        [original] = await _store().list()
        deleting = asyncio.create_task(
            _dispatch(
                delete,
                requester_ctx,
                kind=SCHEDULED_TASK_KIND,
                name="digest",
            )
        )
        await entered.wait()
        await real_cancel(_store(), original)
        replacement = await _store().create(
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
        [remaining] = await _store().list()

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
        await _store().create(
            conversation_id,
            "intel",
            DAILY_9AM,
            "v1",
            "v1",
            datetime.now(UTC),
            created_by_member_id=None,
        )
        await _dispatch(apply, admin_ctx, manifest=_task_manifest("intel", "0 17 * * 1", "v2"))
        tasks = await _store().list()
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
        [task] = await _store().list()
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
                sa.update(schedule_table)
                .where(schedule_table.c.id == task.id)
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
                ref=f"{SCHEDULED_TASK_KIND}/digest",
            )
        )
        with pytest.raises(AdminRequired, match="creator"):
            await apply.handler(
                admin_ctx,
                apply.input_model.model_validate(
                    {
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
        after_edit = await _store().list()
        admin_delete = json.loads(
            await _dispatch(delete, admin_ctx, kind=SCHEDULED_TASK_KIND, name="digest")
        )
        after_delete = await _store().list()
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


async def test_admin_cannot_force_another_members_task_to_run_now(db: None) -> None:
    """`run_now` fires the creator's prompt at once under the creator's authority, so it is content
    the creator owns rather than cadence an admin manages: an admin who created nothing is refused
    and the next fire stays where the schedule put it, and the creator's own ask fires."""
    workspace_id, agent_id, conversation_id = await _seed()
    admin = await _member(workspace_id, created_at=datetime(2020, 1, 1, tzinfo=UTC), is_admin=True)
    creator = await _member(workspace_id, created_at=datetime(2027, 1, 1, tzinfo=UTC))
    creator_ctx = replace(
        _tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=creator
    )
    admin_ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=admin)
    apply = _object_tool("object_apply")
    forced = yaml.safe_dump(
        {"kind": SCHEDULED_TASK_KIND, "name": "digest", "spec": {"run_now": True}}
    )
    with ws(workspace_id), agent(agent_id):
        await _dispatch(
            apply,
            creator_ctx,
            manifest=_task_manifest("digest", DAILY_9AM, "watch the warehouse"),
        )
        [applied] = await _store().list()
        with pytest.raises(AdminRequired, match="creator"):
            await _dispatch(apply, admin_ctx, manifest=forced)
        [refused] = await _store().list()
        await _dispatch(apply, creator_ctx, manifest=forced)
        [creator_forced] = await _store().list()
    assert refused.next_run_at == applied.next_run_at
    assert creator_forced.next_run_at <= datetime.now(UTC)


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
            [task] = await _store().list()
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
                    sa.update(schedule_table)
                    .where(schedule_table.c.id == task.id)
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
                    ref=f"{SCHEDULED_TASK_KIND}/digest",
                    agent=child_name,
                )
            )
            reports_to = next(link for link in fetched["links"] if link["relation"] == "reports_to")
            linked_conversation = yaml.safe_load(
                await _dispatch(
                    get,
                    main_ctx,
                    ref=reports_to["target"],
                    agent=reports_to.get("agent", ""),
                )
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
                    ref=f"{SCHEDULED_TASK_KIND}/digest",
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
                    ref=f"{SCHEDULED_TASK_KIND}/digest",
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
            remaining = await _store().list()

    assert listed["agent"] == child_name
    assert [row["name"] for row in listed["objects"]] == ["digest"]
    assert fetched["agent"] == child_name
    assert fetched["links"] == [
        {
            "relation": "reports_to",
            "target": f"conversation/{child_conversation}",
            "agent": child_name,
        },
    ]
    assert linked_conversation["agent"] == child_name
    assert linked_conversation["ref"] == f"conversation/{child_conversation}"
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


async def test_cross_agent_target_hides_a_private_app_from_other_members(db: None) -> None:
    workspace_id, main_agent, main_conversation = await _seed()
    private_agent, _ = await _second_agent(workspace_id)
    owner = await _member(workspace_id)
    stranger = await _member(workspace_id)
    admin = await _member(workspace_id, is_admin=True)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent).where(tables.agent.c.id == main_agent).values(is_main=True)
        )
        await connection.execute(
            sa.update(tables.agent)
            .where(tables.agent.c.id == private_agent)
            .values(visibility="private", owner_member_id=owner)
        )
        private_name = (
            await connection.execute(
                sa.select(tables.agent.c.name).where(tables.agent.c.id == private_agent)
            )
        ).scalar_one()
    ctx = _tool_ctx(workspace_id, main_conversation, main_agent)
    listing = _object_tool("object_list")

    with ws(workspace_id), agent(main_agent):
        owned = json.loads(
            await _dispatch(
                listing,
                replace(ctx, speaker_member_id=owner),
                kind=SCHEDULED_TASK_KIND,
                agent=private_name,
            )
        )
        managed = json.loads(
            await _dispatch(
                listing,
                replace(ctx, speaker_member_id=admin),
                kind=SCHEDULED_TASK_KIND,
                agent=private_name,
            )
        )
        with pytest.raises(ValueError, match="no agent named"):
            await _dispatch(
                listing,
                replace(ctx, speaker_member_id=stranger),
                kind=SCHEDULED_TASK_KIND,
                agent=private_name,
            )

    assert owned["agent"] == private_name
    assert managed["agent"] == private_name


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
            assert await _store().list() == ()


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


def test_a_bound_field_refuses_a_value_past_its_bound() -> None:
    with pytest.raises(ValueError):
        ScheduledTaskSpec(schedule="* " * SCHEDULE_MAX)


async def test_a_cancel_inside_the_lease_window_does_not_fire(db: None) -> None:
    """A lease is seconds wide and a member can cancel inside it. The fire revalidates its claim
    against the row's whole identity first, so the cancelled task does not get one last fire —
    idempotency would not have caught this, because the key only collapses repeat deliveries of a
    fire and never asks whether the task still exists."""
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    store = _store()
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    runner = ScheduledTaskRunner(ctx=_runner_ctx(invoker))
    with ws(workspace_id), agent(agent_id):
        created = await store.create(
            conversation_id,
            "digest",
            DAILY_9AM,
            "check inbox",
            "digest",
            due_at,
            created_by_member_id=creator,
        )
        [claimed] = await store.claim_due(datetime.now(UTC), 300)
        assert await store.claim_holds(claimed) is True
        await store.cancel(created)
        assert await store.claim_holds(claimed) is False
        assert await runner._fire(store, claimed, due_at, due_at) is None
        turns = await _turns(conversation_id)
        remaining = await store.list()

    assert turns == []
    assert remaining == ()
    assert dbos.enqueued == []


async def test_a_task_pointing_at_another_workspaces_conversation_is_not_listed(db: None) -> None:
    """The reachable shape of a task whose conversation cannot be read.

    A dangling conversation id is impossible — `scheduled_task_conversation_id_fkey` enforces that
    the row exists — but existing is not the same as being *this* workspace's, and the facts read is
    workspace-scoped in its predicate. So a task pointing at another tenant's conversation resolves
    to no audience at all, and the page must omit it rather than default it: standing in a shared
    audience for a fact nobody vouched for would publish one workspace's task to another's members.
    """
    workspace_id, agent_id, conversation_id = await _seed()
    _, _, other_conversation = await _seed()
    store = _store()
    with ws(workspace_id), agent(agent_id):
        await store.create(
            conversation_id,
            "digest",
            DAILY_9AM,
            "check inbox",
            "digest",
            datetime.now(UTC) + timedelta(hours=1),
        )
        assert len(await store.list_reported()) == 1
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(schedule_table)
                .where(schedule_table.c.conversation_id == conversation_id)
                .values(conversation_id=other_conversation)
            )
        borrowed = await store.list_reported()
        still_a_row = await store.list()

    assert borrowed == ()
    assert len(still_a_row) == 1
    assert still_a_row[0].conversation_id == other_conversation


async def test_a_conversation_holding_a_task_cannot_be_deleted(db: None) -> None:
    """Why `list_reported` can never meet a task whose conversation row is simply gone.

    `scheduled_task_conversation_id_fkey` has no cascade, so the conversation cannot be deleted
    out from under a live task and the task's conversation id cannot be pointed at a row that does
    not exist. The database enforces that half. What the store must still handle is a conversation
    that exists and is not *readable here* — a borrowed cross-workspace id — which is the case
    `test_a_task_pointing_at_another_workspaces_conversation_is_not_listed` covers."""
    workspace_id, agent_id, conversation_id = await _seed()
    store = _store()
    with ws(workspace_id), agent(agent_id):
        await store.create(
            conversation_id,
            "digest",
            DAILY_9AM,
            "check inbox",
            "digest",
            datetime.now(UTC) + timedelta(hours=1),
        )
        with pytest.raises(IntegrityError):
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.delete(tables.conversation).where(
                        tables.conversation.c.id == conversation_id
                    )
                )
        with pytest.raises(IntegrityError):
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(schedule_table)
                    .where(schedule_table.c.conversation_id == conversation_id)
                    .values(conversation_id=uuid4())
                )
        assert len(await store.list_reported()) == 1


async def test_a_task_on_an_archived_app_keeps_its_occurrence_and_fails_no_tick(db: None) -> None:
    workspace_id, agent_id, conversation_id = await _seed()
    creator = await _member(workspace_id)
    store = _store()
    due_at = datetime.now(UTC) - timedelta(minutes=1)
    dbos = StubDbos()
    invoker = AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )
    runner_ctx = _runner_ctx(invoker)
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
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .values(
                    name=f"~archived-{agent_id}",
                    archived_name=tables.agent.c.name,
                    archived_at=sa.func.now(),
                )
                .where(tables.agent.c.id == agent_id)
            )

        await ScheduledTaskRunner(ctx=runner_ctx).run()

        assert await _turns(conversation_id) == []
        assert dbos.enqueued == []
        [held] = await store.list()
        assert held.last_run_at is None
        assert held.next_run_at == due_at

        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .values(name=tables.agent.c.archived_name, archived_name=None, archived_at=None)
                .where(tables.agent.c.id == agent_id)
            )
            # The refused fire left the claim it took, which the lease clears in its own time.
            await connection.execute(
                sa.update(schedule_table).values(claimed_by=None, claim_expires_at=None)
            )
        assert workspace_id in await due_task_workspaces()()
        await ScheduledTaskRunner(ctx=runner_ctx).run()
        [restored] = await store.list()
        restored_turns = await _turns(conversation_id)

    assert len(restored_turns) == 1
    assert restored.last_run_at is not None
    assert restored.last_run_at > due_at
    assert restored.next_run_at > restored.last_run_at
