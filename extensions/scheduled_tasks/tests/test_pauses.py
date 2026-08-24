"""The durable workflow pause on the monitor substrate: an extension-owned row, fired by the
extension's own runner, arbitrated against member ingress by one guarded admission parameter.

Every behavioural contract the `@pause` name-protocol row used to hold is proven here on the new
substrate, and the arbitration moved rather than changed: the arm no longer asks whether a member
has spoken (a message landing between that read and the timer would make any answer stale), and the
fire asks admission under the conversation lock instead. So the two ways one wait can end — the
member's next message, or the timer — still converge on exactly one resume turn.

The fire drives the real `Admission` (real spend preflight, real turn row, real seq allocation);
only the DBOS enqueue stands in, recording the workflow id a live queue would receive."""

import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from ufo_ext_scheduled_tasks.manifest import NAME, PAUSE_RUNNER_JOB, manifest
from ufo_ext_scheduled_tasks.pause_runner import FIRE_KEY_PREFIX, PauseRunner
from ufo_ext_scheduled_tasks.pauses import Pause, PauseStore, due_pause_workspaces
from ufo_ext_scheduled_tasks.pauses import pause as pause_table
from ufo_ext_scheduled_tasks.tools import (
    PAUSE_DIRECTIVE,
    SCHEDULED_TASK_KIND,
    PauseAndWaitInput,
    pause_and_wait,
)

from ufo.agent_scope import agent
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, context_for
from ufo.ext.loader import turn_tools
from ufo.loop.engine import FRESH_CLAIM, _claim_turn
from ufo.schema import tables
from ufo.schema.records import Agent, TerminalFrame, Turn
from ufo.surfaces.admission import Admission, AdmissionInvoker, MemberAdmission
from ufo.tools.context import SpawnResult, ToolContext
from ufo.tools.registry import ToolDef
from ufo.turns.audience import SHARED_AUDIENCE, conversation_audience
from ufo.workspace import ws

TOOL_NARRATION = "waiting on the approval"


@dataclass(frozen=True)
class _RetireCrashes(PauseStore):
    """The process dying between the fire's invoke and its retire — the one window where a pause can
    be re-fired, and the reason the row must survive it still claimed."""

    async def retire(self, row: Pause) -> None:
        raise RuntimeError("the fire crashed before retiring")


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


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in the pause tests")


def _object_tool(name: str) -> ToolDef:
    tools, _ = turn_tools((manifest(),), None, audience=conversation_audience(None))
    return next(tool for tool in tools if tool.name == name)


async def _dispatch(tool: ToolDef, ctx: ToolContext, **args: object) -> str:
    result = await tool.handler(
        ctx, tool.input_model.model_validate({"user_description": TOOL_NARRATION, **args})
    )
    assert result.is_error is False
    return result.content[0].text


async def _seed(surface: str = "cli") -> tuple[UUID, UUID, UUID, UUID]:
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
    return workspace_id, agent_id, conversation_id, member_id


async def _second_agent(workspace_id: UUID) -> UUID:
    agent_id = uuid4()
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
    return agent_id


def _tool_ctx(
    workspace_id: UUID,
    conversation_id: UUID,
    agent_id: UUID,
    *,
    speaker_member_id: UUID | None = None,
    seq: int = 0,
) -> ToolContext:
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=seq,
            status="running",
            inbound="please wait for the approval",
            created_at=datetime(2026, 8, 14, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=speaker_member_id,
        audience=SHARED_AUDIENCE,
        artifact_token_secret="",
        ext=context_for(NAME, frozenset()),
    )


def _runner_ctx(invoker: AdmissionInvoker | None) -> ExtensionContext:
    return context_for(NAME, frozenset(), invoker=invoker)


def _invoker(workspace_id: UUID, dbos: StubDbos) -> AdmissionInvoker:
    return AdmissionInvoker(
        admission=Admission(dbos=dbos, durable_surfaces=frozenset()), workspace_id=workspace_id
    )


def _wait(**overrides: object) -> PauseAndWaitInput:
    return PauseAndWaitInput.model_validate(
        {
            "user_description": TOOL_NARRATION,
            "ai_response": "I'll wait for the verification email.",
            "wait_minutes": 10,
            "next_steps": "Read the code and continue onboarding.",
            "reason": "verification email",
            "metadata": {"account": "member@example.com"},
            **overrides,
        }
    )


async def _rows(workspace_id: UUID) -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(pause_table).where(pause_table.c.workspace_id == workspace_id)
                )
            )
            .mappings()
            .all()
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


async def _arrivals() -> list[str]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.inbound_message.c.body).order_by(tables.inbound_message.c.seq)
                )
            )
            .scalars()
            .all()
        )


async def _due_now(row_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(pause_table)
            .where(pause_table.c.id == row_id)
            .values(resume_at=datetime.now(UTC) - timedelta(seconds=1))
        )


async def test_pause_arms_a_row_and_returns_the_timer_directive(db: None) -> None:
    """The arm is unconditional and records what the fire will need: the resume body, the turn
    sequence to arbitrate from, and who to act as."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    armed_at = datetime.now(UTC)
    with ws(workspace_id), agent(agent_id):
        result = await pause_and_wait(ctx, _wait())
        rows = await _rows(workspace_id)

    directive, encoded = result.content[0].text.split("\n", 1)
    payload = json.loads(encoded)
    assert directive == PAUSE_DIRECTIVE
    assert payload["awaiting"] == "timer"
    assert payload["metadata"] == {"account": "member@example.com"}
    [row] = rows
    assert row["conversation_id"] == conversation_id
    assert row["agent_id"] == agent_id
    assert row["origin_seq"] == 0
    assert row["created_by_member_id"] == member_id
    assert row["user_description"] == TOOL_NARRATION
    assert row["claimed_by"] is None
    assert "Read the code and continue onboarding." in row["prompt"]
    assert "verification email" in row["prompt"]
    resume_at = row["resume_at"].replace(tzinfo=UTC)
    assert timedelta(minutes=9) < resume_at - armed_at < timedelta(minutes=11)


async def test_pause_fires_the_timer_into_a_resumed_turn(db: None) -> None:
    """Tool to timer to resumed turn. The turn carries the stored body verbatim — a pause composes
    its own resume prompt, so nothing wraps it in `<scheduled_task>` — and is stamped a scheduled
    fire acting on behalf of the member who armed it."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    dbos = StubDbos()
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait())
        [row] = await _rows(workspace_id)
        await _due_now(row["id"])
        await PauseRunner(ctx=_runner_ctx(_invoker(workspace_id, dbos))).run()
        turns = await _turns(conversation_id)
        remaining = await _rows(workspace_id)

    [turn] = turns
    assert remaining == []
    assert turn["inbound"] == row["prompt"]
    assert not turn["inbound"].startswith("<scheduled_task>")
    assert turn["admission_source"] == "scheduled"
    assert turn["on_behalf_of_member_id"] == member_id
    assert turn["speaker_member_id"] is None
    assert turn["agent_id"] == agent_id
    assert dbos.enqueued == [str(turn["id"])]


async def test_a_member_message_supersedes_the_due_fire(db: None) -> None:
    """The convergence, member first: their message already resumed the workflow — its transcript
    holds the pause's own `next_steps` — so the timer answers superseded, writes no turn, and the
    wait is over."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait())
        [row] = await _rows(workspace_id)
        member_turn = (
            await MemberAdmission(admission=admission, workspace_id=workspace_id).admit(
                conversation_id, "The approval arrived.", "approval-1", speaker_member_id=member_id
            )
        ).turn_id
        await _due_now(row["id"])
        await PauseRunner(
            ctx=_runner_ctx(AdmissionInvoker(admission=admission, workspace_id=workspace_id))
        ).run()
        turns = await _turns(conversation_id)
        remaining = await _rows(workspace_id)

    assert remaining == []
    assert [turn["id"] for turn in turns] == [member_turn]
    assert turns[0]["admission_source"] == "member"
    assert dbos.enqueued == [str(member_turn)]


async def test_the_pause_arms_even_when_a_member_already_spoke(db: None) -> None:
    """The arm-time detection is gone, deliberately: a pause armed after a newer member message
    still writes its row, and the same fire-time guard refuses it. One check, at the only moment
    its answer cannot go stale."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    with ws(workspace_id), agent(agent_id):
        member_turn = (
            await MemberAdmission(admission=admission, workspace_id=workspace_id).admit(
                conversation_id, "already spoke", "spoke-first", speaker_member_id=member_id
            )
        ).turn_id
        result = await pause_and_wait(
            replace(
                _tool_ctx(workspace_id, conversation_id, agent_id),
                speaker_member_id=member_id,
            ),
            _wait(),
        )
        [row] = await _rows(workspace_id)
        await _due_now(row["id"])
        await PauseRunner(
            ctx=_runner_ctx(AdmissionInvoker(admission=admission, workspace_id=workspace_id))
        ).run()
        turns = await _turns(conversation_id)
        remaining = await _rows(workspace_id)

    assert json.loads(result.content[0].text.split("\n", 1)[1])["awaiting"] == "timer"
    assert remaining == []
    assert [turn["id"] for turn in turns] == [member_turn]


async def test_a_member_message_folds_into_the_fired_turn(db: None) -> None:
    """The convergence, timer first: the fire founds its turn, and the member's message joins that
    queued turn as an arrival rather than starting a second one. One turn, not two — the property
    the old in-place timer-turn rewrite existed to produce."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait())
        [row] = await _rows(workspace_id)
        await _due_now(row["id"])
        await PauseRunner(
            ctx=_runner_ctx(AdmissionInvoker(admission=admission, workspace_id=workspace_id))
        ).run()
        [fired] = await _turns(conversation_id)
        joined = (
            await MemberAdmission(admission=admission, workspace_id=workspace_id).admit(
                conversation_id,
                "The approval arrived.",
                "approval-after",
                speaker_member_id=member_id,
            )
        ).turn_id
        turns = await _turns(conversation_id)
        arrivals = await _arrivals()

    assert joined == fired["id"]
    assert [turn["id"] for turn in turns] == [fired["id"]]
    assert arrivals == ["The approval arrived."]


async def test_an_internal_arrival_does_not_supersede_the_timer(db: None) -> None:
    """A pending internal invocation is not a member reply: the guard is member-only, so the timer
    still fires."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    invoker = AdmissionInvoker(admission=admission, workspace_id=workspace_id)
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait())
        [row] = await _rows(workspace_id)
        await invoker.invoke(conversation_id, agent_id, "background note", "internal-1")
        await _due_now(row["id"])
        await PauseRunner(ctx=_runner_ctx(invoker)).run()
        turns = await _turns(conversation_id)
        remaining = await _rows(workspace_id)

    assert remaining == []
    assert any(turn["admission_source"] == "scheduled" for turn in turns)
    fired = next(turn for turn in turns if turn["admission_source"] == "scheduled")
    assert fired["inbound"] == row["prompt"]


async def test_a_redelivered_older_message_does_not_supersede_a_later_pause(db: None) -> None:
    """A message already admitted and answered is not a newer one. Its redelivery dedupes to the
    finished turn, so no member turn exists past the pause's origin and the timer still fires."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    member_admission = MemberAdmission(admission=admission, workspace_id=workspace_id)
    with ws(workspace_id), agent(agent_id):
        earlier = (
            await member_admission.admit(
                conversation_id, "The approval arrived.", "redelivered", speaker_member_id=member_id
            )
        ).turn_id
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .where(tables.turn.c.id == earlier)
                .values(
                    status="done",
                    terminal=TerminalFrame(status="done", text="handled").model_dump(mode="json"),
                    seq=1,
                    updated_at=sa.func.now(),
                )
            )
        await pause_and_wait(
            replace(
                _tool_ctx(workspace_id, conversation_id, agent_id, seq=1),
                speaker_member_id=member_id,
            ),
            _wait(),
        )
        [row] = await _rows(workspace_id)
        redelivered = (
            await member_admission.admit(
                conversation_id, "The approval arrived.", "redelivered", speaker_member_id=member_id
            )
        ).turn_id
        await _due_now(row["id"])
        await PauseRunner(
            ctx=_runner_ctx(AdmissionInvoker(admission=admission, workspace_id=workspace_id))
        ).run()
        turns = await _turns(conversation_id)
        remaining = await _rows(workspace_id)

    assert redelivered == earlier
    assert remaining == []
    assert [turn["admission_source"] for turn in turns] == ["member", "scheduled"]


async def test_a_crash_between_the_fire_and_the_retire_admits_one_turn(db: None) -> None:
    """Invoke first, retire second: a crash between the two leaves the row armed and still claimed,
    so recovery is the lease expiring and the next tick re-claiming it. That tick re-fires under the
    same key, which admits the turn already there. Retiring first would drop a resume nobody
    received."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    dbos = StubDbos()
    ext = _runner_ctx(_invoker(workspace_id, dbos))
    runner = PauseRunner(ctx=ext)
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait())
        [persisted] = await _rows(workspace_id)
        await _due_now(persisted["id"])
        [claimed] = await PauseStore(ext).claim_due(datetime.now(UTC), 0)
        with pytest.raises(RuntimeError, match="crashed"):
            await runner._fire(_RetireCrashes(ext), claimed)
        assert len(await _rows(workspace_id)) == 1

        [reclaimed] = await PauseStore(ext).claim_due(datetime.now(UTC), 300)
        await runner._fire(PauseStore(ext), reclaimed)
        turns = await _turns(conversation_id)
        assert await _rows(workspace_id) == []

    [turn] = turns
    assert turn["idempotency_key"] == f"{FIRE_KEY_PREFIX}{persisted['id']}"
    assert dbos.enqueued == [str(turn["id"]), str(turn["id"])]


async def test_re_arming_overwrites_the_wait_and_releases_its_claim(db: None) -> None:
    """A workflow waits for one thing at a time, so a second pause replaces the first rather than
    queueing beside it — and the tick that leased the predecessor can no longer retire what replaced
    it, for two independent reasons: the claim it holds was released, and the row it leased no
    longer carries that id."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    ext = _runner_ctx(_invoker(workspace_id, StubDbos()))
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait(reason="first wait", wait_minutes=1))
        [first] = await _rows(workspace_id)
        await _due_now(first["id"])
        [claimed] = await PauseStore(ext).claim_due(datetime.now(UTC), 300)
        await pause_and_wait(ctx, _wait(reason="second wait", wait_minutes=30))
        rows = await _rows(workspace_id)
        await PauseStore(ext).retire(claimed)
        after_stale_retire = await _rows(workspace_id)

    [row] = rows
    assert row["id"] != first["id"]
    assert row["id"] != claimed.id
    assert "second wait" in row["prompt"]
    assert "first wait" not in row["prompt"]
    assert row["claimed_by"] is None
    assert len(after_stale_retire) == 1


async def test_a_re_armed_wait_is_not_fired_by_the_tick_that_leased_its_predecessor(
    db: None,
) -> None:
    """The agent replaced the wait while a tick held the old one — a re-arm inside the lease window,
    which needs no member message and so passes the supersession guard untouched. Firing the leased
    predecessor would resume the workflow against an instruction it has already abandoned, and leave
    the wait that replaced it still armed to fire again."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    dbos = StubDbos()
    ext = _runner_ctx(_invoker(workspace_id, dbos))
    runner = PauseRunner(ctx=ext)
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait(reason="first wait", wait_minutes=1))
        [first] = await _rows(workspace_id)
        await _due_now(first["id"])
        [claimed] = await PauseStore(ext).claim_due(datetime.now(UTC), 300)
        await pause_and_wait(ctx, _wait(reason="second wait", wait_minutes=30))

        await runner._fire(PauseStore(ext), claimed)

        turns = await _turns(conversation_id)
        rows = await _rows(workspace_id)

    assert turns == []
    assert dbos.enqueued == []
    assert len(rows) == 1
    assert "second wait" in rows[0]["prompt"]


async def test_a_failed_enqueue_still_ends_the_wait(db: None) -> None:
    """An enqueue that failed is not a resume that failed: the turn is durably admitted and the
    outbox owns getting it onto the queue, so the wait is over and the row retires. Holding the
    pause open for a turn that already exists would resume the workflow twice."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    dbos = StubDbos(failures_remaining=1)
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait())
        [row] = await _rows(workspace_id)
        await _due_now(row["id"])
        await PauseRunner(ctx=_runner_ctx(_invoker(workspace_id, dbos))).run()
        turns = await _turns(conversation_id)
        remaining = await _rows(workspace_id)

    [turn] = turns
    assert remaining == []
    assert turn["status"] == "queued"
    assert turn["terminal"] is None
    assert turn["dispatch_enqueued_at"] is None
    assert dbos.enqueued == []


async def test_a_fire_without_an_invoker_fails_loud(db: None) -> None:
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait())
        [row] = await _rows(workspace_id)
        await _due_now(row["id"])
        with pytest.raises(RuntimeError, match="pause fires failed"):
            await PauseRunner(ctx=_runner_ctx(None)).run()


async def test_a_fire_refuses_a_conversation_bound_to_another_agent(db: None) -> None:
    """A stored binding can never fire into another agent's conversation: admission asserts the
    conversation is bound to the agent the row names, so a row whose agent drifted fails its tick
    rather than waking the wrong agent. It is also the proof that a genuinely failed fire keeps its
    row — the wait did not end, so the lease expires and the next tick tries again."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    stranger = await _second_agent(workspace_id)
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait())
        [row] = await _rows(workspace_id)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(pause_table)
                .where(pause_table.c.id == row["id"])
                .values(agent_id=stranger, resume_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        with pytest.raises(RuntimeError, match="pause fires failed"):
            await PauseRunner(ctx=_runner_ctx(_invoker(workspace_id, StubDbos()))).run()
        turns = await _turns(conversation_id)
        kept = await _rows(workspace_id)

    assert turns == []
    assert [held["id"] for held in kept] == [row["id"]]
    assert kept[0]["claimed_by"] is not None


async def test_an_unclaimed_pause_cannot_be_retired(db: None) -> None:
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    ext = _runner_ctx(None)
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait())
        [armed] = await PauseStore(ext).armed(conversation_id)
        with pytest.raises(ValueError, match="unclaimed pause"):
            await PauseStore(ext).retire(armed)


async def test_only_workspaces_with_a_due_pause_reach_the_runner(db: None) -> None:
    """A pause whose timer has not come never opens a tick, so a fleet of armed waits costs the
    dispatcher nothing."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait())
        [row] = await _rows(workspace_id)
    assert workspace_id not in await due_pause_workspaces()()
    await _due_now(row["id"])
    assert workspace_id in await due_pause_workspaces()()


async def test_a_spend_breach_cancels_the_resume_and_ends_the_wait(db: None) -> None:
    """A resume the workspace cannot pay for is refused, not retried: the turn commits cancelled and
    the row retires, because a client's wait always ends."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    spent_turn_id = uuid4()
    async with workspace_tx() as connection:
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
                prompt_tokens=10,
                input_tokens=10,
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
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait())
        [row] = await _rows(workspace_id)
        await _due_now(row["id"])
        await PauseRunner(ctx=_runner_ctx(_invoker(workspace_id, dbos))).run()
        turns = await _turns(conversation_id)
        remaining = await _rows(workspace_id)

    assert remaining == []
    assert [turn["status"] for turn in turns] == ["done", "cancelled"]
    assert dbos.enqueued == []


async def test_a_fired_resume_claims_like_any_turn(db: None) -> None:
    """The resumed turn is an ordinary queued turn: the worker claims it, and nothing has to reach
    back into a pause row to release it — the row was already gone at fire time."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait())
        [row] = await _rows(workspace_id)
        await _due_now(row["id"])
        await PauseRunner(ctx=_runner_ctx(_invoker(workspace_id, StubDbos()))).run()
        [turn] = await _turns(conversation_id)
        assert await _claim_turn(turn["id"], "timer-resume") == FRESH_CLAIM
        assert await _rows(workspace_id) == []


async def test_pause_rows_never_surface_as_objects(db: None) -> None:
    """A paused workflow is a thing happening, not a thing a member manages: the pause lives in its
    own table and reaches no object surface."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait())
        tasks = json.loads(
            await _dispatch(_object_tool("object_list"), ctx, kind=SCHEDULED_TASK_KIND)
        )
        assert len(await _rows(workspace_id)) == 1

    assert tasks["objects"] == []


@pytest.mark.parametrize("wait_minutes", (0, 10_081))
def test_pause_and_wait_bounds_the_timer(wait_minutes: int) -> None:
    with pytest.raises(ValueError):
        _wait(wait_minutes=wait_minutes)


def test_the_manifest_exposes_the_pause_tool_and_its_runner() -> None:
    tool = next(tool for tool in manifest().tools if tool.name == "pause_and_wait")
    assert tool.handler is pause_and_wait
    assert tool.side_effecting is True
    assert PAUSE_RUNNER_JOB in {job.name for job in manifest().jobs}


async def test_a_re_arm_after_an_undelivered_retire_still_resumes(db: None) -> None:
    """The crash window a stable row id would swallow.

    A fire is invoked and the process dies before the retire commits, so the row survives with a
    turn already admitted under its key. The resumed turn waits again — and if re-arming kept the
    row's id, the next fire would present the key that turn already used, admission would dedupe to
    it, nothing would be admitted, and the runner would retire the row. The second wait would never
    resume, silently and permanently. Every arm therefore mints a fresh id: the identity the fire
    key names is the logical wait, not the physical row."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    dbos = StubDbos()
    ext = _runner_ctx(_invoker(workspace_id, dbos))
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait(reason="first wait"))
        [first] = await _rows(workspace_id)
        await _due_now(first["id"])
        await PauseRunner(ctx=ext).run()
        [fired] = await _turns(conversation_id)
        assert await _rows(workspace_id) == []

        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(pause_table).values(
                    **{**dict(first), "claimed_by": None, "claim_expires_at": None}
                )
            )

        await pause_and_wait(ctx, _wait(reason="second wait"))
        [rearmed] = await _rows(workspace_id)
        await _due_now(rearmed["id"])
        await PauseRunner(ctx=ext).run()
        turns = await _turns(conversation_id)
        remaining = await _rows(workspace_id)

    assert len(turns) == 2
    assert turns[0]["id"] == fired["id"]
    assert "second wait" in rearmed["prompt"]
    assert rearmed["id"] != first["id"]
    assert remaining == []


async def test_the_ordinary_re_arm_after_a_delivered_fire_resumes_twice(db: None) -> None:
    """The uncrashed path: fire, retire, wait again, fire again — two waits, two resumed turns."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    dbos = StubDbos()
    ext = _runner_ctx(_invoker(workspace_id, dbos))
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait(reason="first wait"))
        [first] = await _rows(workspace_id)
        await _due_now(first["id"])
        await PauseRunner(ctx=ext).run()
        await pause_and_wait(ctx, _wait(reason="second wait"))
        [second] = await _rows(workspace_id)
        await _due_now(second["id"])
        await PauseRunner(ctx=ext).run()
        turns = await _turns(conversation_id)

    assert second["id"] != first["id"]
    assert len(turns) == 2
    assert len({str(turn["idempotency_key"]) for turn in turns}) == 2
    assert dbos.enqueued == [str(turns[0]["id"])]


async def test_a_member_message_the_live_turn_absorbed_supersedes_the_timer(db: None) -> None:
    """The fold window, end to end on the pause substrate.

    The agent arms a wait during a turn that is still running, the member replies, and that reply
    folds into the running turn rather than founding one of its own — the agent answers it there and
    the drain stamps it consumed. Read only as a turn with a speaker, or as a still-queued arrival,
    the member would have left no trace by the time the timer came due and the workflow would resume
    a second time against work the member had already redirected. The trace is the turn the message
    folded into, so the fire is refused and the wait ends where it actually ended.

    Note the asymmetry this depends on: a member's own turn must be strictly past the arming seq,
    but a fold counts at or past it, because the turn a fold lands on is the arming turn itself."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    invoker = AdmissionInvoker(admission=admission, workspace_id=workspace_id)
    with ws(workspace_id), agent(agent_id):
        arming = await invoker.invoke(conversation_id, agent_id, "the arming work", "arming")
        [arming_row] = await _turns(conversation_id)
        base = _tool_ctx(
            workspace_id,
            conversation_id,
            agent_id,
            speaker_member_id=member_id,
            seq=arming_row["seq"],
        )
        await pause_and_wait(
            replace(base, turn=base.turn.model_copy(update={"id": arming})), _wait()
        )
        [row] = await _rows(workspace_id)

        folded = (
            await MemberAdmission(admission=admission, workspace_id=workspace_id).admit(
                conversation_id, "actually, do this instead", "folded", speaker_member_id=member_id
            )
        ).turn_id
        assert folded == arming
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.inbound_message)
                .values(consumed_turn_id=arming)
                .where(tables.inbound_message.c.consumed_turn_id.is_(None))
            )

        await _due_now(row["id"])
        await PauseRunner(ctx=_runner_ctx(invoker)).run()
        turns = await _turns(conversation_id)
        remaining = await _rows(workspace_id)

    assert [turn["id"] for turn in turns] == [arming]
    assert remaining == []


async def test_a_fold_the_agent_had_already_read_does_not_kill_the_timer(db: None) -> None:
    """The member spoke, the agent absorbed it, and *then* decided to wait — so the timer must fire.

    Sibling of `test_a_member_message_the_live_turn_absorbed_supersedes_the_timer`, and the pair is
    the whole argument for a second watermark. Both fold a member message into the turn that arms
    the wait, so both have `folded_into.seq == origin_seq`: in turn-sequence space the two are
    indistinguishable, yet they must end opposite ways. What separates them is *when* the arrival
    landed relative to the arm, which only the arrival counter can answer."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    invoker = AdmissionInvoker(admission=admission, workspace_id=workspace_id)
    with ws(workspace_id), agent(agent_id):
        arming = await invoker.invoke(conversation_id, agent_id, "the arming work", "arming")
        folded = (
            await MemberAdmission(admission=admission, workspace_id=workspace_id).admit(
                conversation_id, "some context first", "folded-early", speaker_member_id=member_id
            )
        ).turn_id
        assert folded == arming
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.inbound_message)
                .values(consumed_turn_id=arming)
                .where(tables.inbound_message.c.consumed_turn_id.is_(None))
            )

        [arming_row] = await _turns(conversation_id)
        base = _tool_ctx(
            workspace_id,
            conversation_id,
            agent_id,
            speaker_member_id=member_id,
            seq=arming_row["seq"],
        )
        await pause_and_wait(
            replace(base, turn=base.turn.model_copy(update={"id": arming})), _wait()
        )
        [row] = await _rows(workspace_id)
        await _due_now(row["id"])
        await PauseRunner(ctx=_runner_ctx(invoker)).run()
        turns = await _turns(conversation_id)
        remaining = await _rows(workspace_id)

    assert len(turns) == 2
    assert turns[1]["admission_source"] == "scheduled"
    assert turns[1]["inbound"] == row["prompt"]
    assert remaining == []


async def test_re_arming_moves_both_watermarks_forward(db: None) -> None:
    """A second wait is armed from where the conversation is now, not where the first one started.

    Both marks advance together: the turn the new wait was armed from, and how far member arrivals
    had got when it was. Leaving the arrival mark behind would make the new wait inherit the first
    one's blind spot — every message folded in between would look like it landed after the arm."""
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    invoker = AdmissionInvoker(admission=admission, workspace_id=workspace_id)
    member_admission = MemberAdmission(admission=admission, workspace_id=workspace_id)
    with ws(workspace_id), agent(agent_id):
        live = await invoker.invoke(conversation_id, agent_id, "the arming work", "arming")
        [live_row] = await _turns(conversation_id)
        base = _tool_ctx(
            workspace_id,
            conversation_id,
            agent_id,
            speaker_member_id=member_id,
            seq=live_row["seq"],
        )
        armed_ctx = replace(base, turn=base.turn.model_copy(update={"id": live}))
        await pause_and_wait(armed_ctx, _wait(reason="first wait"))
        [first] = await _rows(workspace_id)

        assert (
            await member_admission.admit(
                conversation_id, "one more thing", "folded-between", speaker_member_id=member_id
            )
        ).turn_id == live
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.inbound_message)
                .values(consumed_turn_id=live)
                .where(tables.inbound_message.c.consumed_turn_id.is_(None))
            )

        await pause_and_wait(armed_ctx, _wait(reason="second wait"))
        [second] = await _rows(workspace_id)
        await _due_now(second["id"])
        await PauseRunner(ctx=_runner_ctx(invoker)).run()
        turns = await _turns(conversation_id)

    assert first["origin_arrival_seq"] < second["origin_arrival_seq"]
    assert second["origin_seq"] == first["origin_seq"]
    assert "second wait" in second["prompt"]
    assert len(turns) == 2
    assert turns[1]["admission_source"] == "scheduled"


async def test_a_pause_on_an_archived_app_keeps_its_row_for_the_restore(db: None) -> None:
    workspace_id, agent_id, conversation_id, member_id = await _seed()
    ctx = replace(_tool_ctx(workspace_id, conversation_id, agent_id), speaker_member_id=member_id)
    dbos = StubDbos()
    runner_ctx = _runner_ctx(_invoker(workspace_id, dbos))
    with ws(workspace_id), agent(agent_id):
        await pause_and_wait(ctx, _wait())
        [row] = await _rows(workspace_id)
        await _due_now(row["id"])
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
        await PauseRunner(ctx=runner_ctx).run()
        turns = await _turns(conversation_id)
        remaining = await _rows(workspace_id)
        assert dbos.enqueued == []

        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .values(name=tables.agent.c.archived_name, archived_name=None, archived_at=None)
                .where(tables.agent.c.id == agent_id)
            )
            # The refused fire left the claim it took, which the lease clears in its own time.
            await connection.execute(
                sa.update(pause_table).values(claimed_by=None, claim_expires_at=None)
            )
        assert workspace_id in await due_pause_workspaces()()
        await PauseRunner(ctx=runner_ctx).run()
        restored = await _rows(workspace_id)
        restored_turns = await _turns(conversation_id)

    assert [held["id"] for held in remaining] == [row["id"]]
    assert turns == []
    assert restored == []
    assert len(restored_turns) == 1
    assert len(dbos.enqueued) == 1
