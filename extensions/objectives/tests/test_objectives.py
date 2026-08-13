"""The closure gate, which is the only part of this design that is not bookkeeping.

RFC 0025 measured both closure forms a person or an agent performs and both scored below doing
nothing. Its worst regression is the shape asserted here: the agent declared *"W9_...pdf exists"*,
wrote the file to the workspace root, checked "does the file exist?", found it, and marked the
condition verified — while the rubric required it inside `1099/2025/`. The check ran, passed, and
was wrong.

So the test that matters is not that a step can close. It is that a step whose conditions name
state that is false does **not** close, however confidently it was recorded, and that a condition
fixed at plan time survives a later revision that would have weakened it."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection
from ufo_ext_objectives.store import (
    ATTEMPTED_STATE,
    BLOCKED,
    BLOCKED_STATE,
    DID,
    DONE_STATE,
    PENDING_STATE,
    UNMET_STATE,
    CommandSucceeds,
    ConditionVerdict,
    FileExists,
    Objectives,
    ObjectiveView,
    StepEvent,
    StepPlan,
    StepView,
    condition_summary,
)
from ufo_ext_objectives.tools import PlanObjectiveInput, plan_objective

from ufo.agent_scope import agent
from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.sdk.context import ExtensionContext
from ufo.sdk.tools import ToolContext
from ufo.workspace import ws


async def _seeded_conversation() -> tuple[UUID, UUID]:
    workspace_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
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
                surface="cli",
                queue_key="k",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, conversation_id


NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)
REQUIRED_PATH = "/workspace/1099/2025/W9_Whitfield_Consulting_Group.pdf"
WRONG_PATH = "/workspace/W9_Whitfield_Consulting_Group.pdf"


def step(
    title: str,
    accepts: tuple[FileExists, ...] = (),
    events: tuple[object, ...] = (),
    verdicts: tuple[object, ...] = (),
) -> StepView:
    return StepView(
        id=uuid4(),
        title=title,
        accepts=accepts,  # type: ignore[arg-type]
        events=events,  # type: ignore[arg-type]
        verdicts=verdicts,  # type: ignore[arg-type]
    )


class Event:
    def __init__(self, kind: str, turn: UUID | None = None) -> None:
        self.kind = kind
        self.actor_turn_id = turn or uuid4()
        self.evidence = "did the thing"
        self.created_at = None


class Verdict:
    def __init__(self, holds: bool) -> None:
        self.condition = FileExists(path=REQUIRED_PATH)
        self.holds = holds
        self.detail = "detail"


def test_a_step_nobody_attempted_is_pending() -> None:
    assert step("file the W9").state == PENDING_STATE


def test_an_attempt_alone_does_not_close_a_step_with_conditions() -> None:
    """The doer's word is recorded, never load-bearing."""
    unmet = step(
        "file the W9",
        accepts=(FileExists(path=REQUIRED_PATH),),
        events=(Event(DID),),
        verdicts=(Verdict(holds=False),),
    )
    assert unmet.state == UNMET_STATE


def test_a_step_whose_conditions_nobody_ran_is_not_closed() -> None:
    """The frontier a woken turn is handed is built without a sandbox, so it carries no verdicts.
    Reading `all(())` as True closed every attempted step there on the claim alone — the exact
    surface the whole design exists to make trustworthy."""
    unchecked = step(
        "file the W9",
        accepts=(FileExists(path=REQUIRED_PATH),),
        events=(Event(DID),),
    )
    assert unchecked.state == ATTEMPTED_STATE
    assert unchecked.state != DONE_STATE


def test_an_unchecked_step_stays_in_the_frontier(tmp_path: object) -> None:
    """It must not fall out of the injected view: a step that has not been verified is work the
    next turn still owns."""
    from ufo_ext_objectives.store import ObjectiveView

    view = ObjectiveView(
        id=uuid4(),
        name="filing",
        directive="d",
        steps=(
            step("file the W9", accepts=(FileExists(path=REQUIRED_PATH),), events=(Event(DID),)),
        ),
    )
    assert len(view.frontier) == 1
    assert view.confirmed == 0


def test_a_step_closes_only_when_every_condition_holds() -> None:
    closed = step(
        "file the W9",
        accepts=(FileExists(path=REQUIRED_PATH),),
        events=(Event(DID),),
        verdicts=(Verdict(holds=True),),
    )
    assert closed.state == DONE_STATE


def test_a_step_with_no_conditions_closes_on_the_workers_word() -> None:
    """Stated plainly rather than prevented: such a closure carries no guarantee, and is
    admissible only because nothing downstream reads it as one."""
    assert step("say hello", events=(Event(DID),)).state == DONE_STATE


def test_blocked_wins_over_an_earlier_attempt() -> None:
    held = step("file the W9", events=(Event(DID), Event(BLOCKED)))
    assert held.state == BLOCKED_STATE


def test_a_standing_block_is_the_question_already_asked() -> None:
    held = step("file the W9", events=(Event(DID), Event(BLOCKED)))
    standing = held.open_block
    assert standing is not None
    assert standing.kind == BLOCKED
    assert step("file the W9", events=(Event(DID),)).open_block is None


async def test_the_same_block_is_not_raised_twice(db: None) -> None:
    """A heartbeat objective wakes every minute. Re-recording the block it is already waiting on
    restates an open question as a new one, which is how a thread fills up overnight."""
    workspace_id, conversation_id = await _seeded_conversation()
    async with workspace_tx() as connection:
        objectives = Objectives(connection, workspace_id)
        planned = await objectives.plan(
            conversation_id,
            "filing",
            "file the vendor forms",
            (StepPlan(title="file the W9", accepts=(FileExists(path=REQUIRED_PATH),)),),
        )
        question = "which folder should this go in?"
        assert await objectives.record(planned.steps[0], BLOCKED, uuid4(), question) is True
        again = await objectives.named(conversation_id, "filing")
        assert again is not None
        assert await objectives.record(again.steps[0], BLOCKED, uuid4(), question) is False
        moved = await objectives.named(conversation_id, "filing")
        assert moved is not None
        assert len(moved.steps[0].events) == 1
        assert (
            await objectives.record(moved.steps[0], BLOCKED, uuid4(), "the folder does not exist")
            is True
        )
        final = await objectives.named(conversation_id, "filing")
        assert final is not None
        assert len(final.steps[0].events) == 2


def test_the_condition_carries_the_folder_the_rubric_required() -> None:
    """0025's root cause, as a property of the data: the path IS the check, so the folder cannot be
    dropped by an author summarizing their own intent."""
    assert "1099/2025" in condition_summary(FileExists(path=REQUIRED_PATH))
    assert "1099/2025" not in condition_summary(FileExists(path=WRONG_PATH))


@pytest.mark.parametrize("kind", [DID, BLOCKED])
def test_every_event_kind_the_schema_allows_is_readable(kind: str) -> None:
    assert step("s", events=(Event(kind),)).state in {DONE_STATE, BLOCKED_STATE}


async def test_a_revision_cannot_weaken_a_condition_already_attempted(db: None) -> None:
    """Time separates the interests: an `accepts` fixed before the work was known to be hard is
    kept as written when the plan is revised after it turned out to be."""
    workspace_id, conversation_id = await _seeded_conversation()
    async with workspace_tx() as connection:
        objectives = Objectives(connection, workspace_id)
        planned = await objectives.plan(
            conversation_id,
            "filing",
            "file the vendor forms",
            (StepPlan(title="file the W9", accepts=(FileExists(path=REQUIRED_PATH),)),),
        )
        await objectives.record(planned.steps[0], DID, uuid4(), "wrote the file")
        revised = await objectives.plan(
            conversation_id,
            "filing",
            "file the vendor forms",
            (StepPlan(title="file the W9", accepts=(FileExists(path=WRONG_PATH),)),),
        )
    assert revised.steps[0].accepts == (FileExists(path=REQUIRED_PATH),)
    assert revised.attempts == 1


def test_every_metric_this_extension_emits_is_declared_in_core() -> None:
    """An extension emits a name core declares or fails loud, so an undeclared counter is a runtime
    raise on the first real use rather than a missing dashboard."""
    from ufo.o11y import METRICS

    for name in (
        "objective_step_recorded_total",
        "objective_condition_total",
        "objective_frontier_injected_total",
    ):
        assert name in METRICS


async def test_a_checked_step_leaves_the_frontier_on_a_later_read(db: None) -> None:
    """The bug this design cannot survive: a check runs, passes, and is thrown away, so the next
    turn's frontier lists the step as unfinished again. The frontier is assembled by the hook that
    opens every turn, which holds no sandbox and so evaluates nothing — it can only read what was
    written down. A step carrying conditions would therefore never leave the list, and steps
    carrying conditions are exactly the ones this extension exists for."""
    workspace_id, conversation_id = await _seeded_conversation()
    async with workspace_tx() as connection:
        objectives = Objectives(connection, workspace_id)
        planned = await objectives.plan(
            conversation_id,
            "filing",
            "file the vendor forms",
            (StepPlan(title="file the W9", accepts=(FileExists(path=REQUIRED_PATH),)),),
        )
        step = planned.steps[0]
        await objectives.record(step, DID, uuid4(), "filed it")
        await objectives.checked(
            step,
            (
                ConditionVerdict(
                    condition=FileExists(path=REQUIRED_PATH), holds=True, detail="found"
                ),
            ),
            uuid4(),
        )
        read = await objectives.named(conversation_id, "filing")
        assert read is not None
        assert read.steps[0].state == DONE_STATE
        assert read.steps[0].checked_at is not None
        assert read.frontier == ()
        assert read.confirmed == 1


async def test_a_later_check_that_fails_reopens_a_closed_step(db: None) -> None:
    """Checks append rather than overwrite, so a step that held yesterday and fails today reads as
    unmet. A stored closure that could not reopen would be the self-asserted status column this
    design exists to refuse, just written by the extension instead of the worker."""
    workspace_id, conversation_id = await _seeded_conversation()
    async with workspace_tx() as connection:
        objectives = Objectives(connection, workspace_id)
        planned = await objectives.plan(
            conversation_id,
            "filing",
            "file the vendor forms",
            (StepPlan(title="file the W9", accepts=(FileExists(path=REQUIRED_PATH),)),),
        )
        step = planned.steps[0]
        await objectives.record(step, DID, uuid4(), "filed it")
        held = ConditionVerdict(
            condition=FileExists(path=REQUIRED_PATH), holds=True, detail="found"
        )
        await objectives.checked(step, (held,), uuid4())
        closed = await objectives.named(conversation_id, "filing")
        assert closed is not None and closed.steps[0].state == DONE_STATE
        gone = ConditionVerdict(
            condition=FileExists(path=REQUIRED_PATH), holds=False, detail="missing"
        )
        await objectives.checked(closed.steps[0], (gone,), uuid4())
        reopened = await objectives.named(conversation_id, "filing")
        assert reopened is not None
        assert reopened.steps[0].state == UNMET_STATE
        assert reopened.frontier != ()


async def test_a_check_against_fewer_conditions_than_the_step_carries_does_not_close_it(
    db: None,
) -> None:
    """A stored check is only an answer about the conditions it actually ran. A plan revision that
    adds a condition must not inherit the old check's closure — `all()` over a short list is the
    same false close as `all(())`."""
    workspace_id, conversation_id = await _seeded_conversation()
    async with workspace_tx() as connection:
        objectives = Objectives(connection, workspace_id)
        planned = await objectives.plan(
            conversation_id,
            "filing",
            "file the vendor forms",
            (
                StepPlan(
                    title="file the W9",
                    accepts=(FileExists(path=REQUIRED_PATH), FileExists(path=WRONG_PATH)),
                ),
            ),
        )
        step = planned.steps[0]
        await objectives.record(step, DID, uuid4(), "filed it")
        await objectives.checked(
            step,
            (ConditionVerdict(condition=FileExists(path=REQUIRED_PATH), holds=True, detail="ok"),),
            uuid4(),
        )
        read = await objectives.named(conversation_id, "filing")
        assert read is not None
        assert read.steps[0].state == ATTEMPTED_STATE


async def test_a_subagent_reusing_a_name_does_not_adopt_its_parents_objective(db: None) -> None:
    """A subagent runs on its own conversation and is handed the same tools. Scoped to the
    workspace, a worker reusing its parent's handle would adopt the parent's objective and delete
    every step nobody had started yet — the plan erased by the worker it was written for."""
    workspace_id, parent_conversation = await _seeded_conversation()
    child_conversation = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=child_conversation,
                workspace_id=workspace_id,
                agent_id=(
                    await connection.execute(
                        sa.select(tables.conversation.c.agent_id).where(
                            tables.conversation.c.id == parent_conversation
                        )
                    )
                ).scalar_one(),
                surface="subagent",
                queue_key="child",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        objectives = Objectives(connection, workspace_id)
        await objectives.plan(
            parent_conversation,
            "rollout",
            "ship the thing",
            (StepPlan(title="build it"), StepPlan(title="verify it")),
        )
        await objectives.plan(
            child_conversation, "rollout", "just my bit", (StepPlan(title="build it"),)
        )
        parent = await objectives.named(parent_conversation, "rollout")
        child = await objectives.named(child_conversation, "rollout")
        assert parent is not None and child is not None
        assert parent.id != child.id
        assert parent.directive == "ship the thing"
        assert tuple(step.title for step in parent.steps) == ("build it", "verify it")
        assert tuple(step.title for step in child.steps) == ("build it",)


def _step(title: str, *, independent: bool, events: tuple[str, ...] = ()) -> StepView:
    return StepView(
        id=uuid4(),
        title=title,
        accepts=(),
        events=tuple(
            StepEvent(kind=kind, actor_turn_id=uuid4(), evidence="", created_at=NOW)
            for kind in events
        ),
        verdicts=(),
        independent=independent,
    )


def _objective(*steps: StepView) -> ObjectiveView:
    return ObjectiveView(id=uuid4(), name="rollout", directive="ship it", steps=steps)


def test_only_independent_untaken_steps_are_runnable() -> None:
    """The fan-out shape is read off the plan, not decided again by whichever turn is awake. A step
    the plan did not mark independent needs something its siblings produce, so dispatching it
    alongside them would run it against state that does not exist yet."""
    view = _objective(
        _step("read the spec", independent=True),
        _step("write the migration", independent=False),
        _step("read the dashboard", independent=True),
    )
    assert tuple(step.title for step in view.runnable) == ("read the spec", "read the dashboard")


def test_a_step_already_attempted_is_not_dispatched_again() -> None:
    view = _objective(
        _step("read the spec", independent=True, events=(DID,)),
        _step("read the dashboard", independent=True),
    )
    assert tuple(step.title for step in view.runnable) == ("read the dashboard",)


def test_a_blocked_step_is_not_dispatched() -> None:
    """A step standing on a question already put to the member is waiting on a person, not on a
    worker. Dispatching it spends a subagent on work that cannot proceed."""
    view = _objective(
        _step("pick the folder", independent=True, events=(BLOCKED,)),
        _step("read the dashboard", independent=True),
    )
    assert tuple(step.title for step in view.runnable) == ("read the dashboard",)


async def test_independence_survives_a_plan_revision(db: None) -> None:
    """A revision restates the plan, so it must restate independence with it — a step that comes
    back declared dependent must stop being dispatched, and one that comes back independent must
    start."""
    workspace_id, conversation_id = await _seeded_conversation()
    async with workspace_tx() as connection:
        objectives = Objectives(connection, workspace_id)
        await objectives.plan(
            conversation_id,
            "rollout",
            "ship it",
            (
                StepPlan(title="read the spec", independent=True),
                StepPlan(title="write the migration"),
            ),
        )
        first = await objectives.named(conversation_id, "rollout")
        assert first is not None
        assert tuple(step.title for step in first.runnable) == ("read the spec",)

        await objectives.plan(
            conversation_id,
            "rollout",
            "ship it",
            (
                StepPlan(title="read the spec"),
                StepPlan(title="write the migration", independent=True),
            ),
        )
        second = await objectives.named(conversation_id, "rollout")
        assert second is not None
        assert tuple(step.title for step in second.runnable) == ("write the migration",)


async def _agent_of(conversation_id: UUID) -> UUID:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.conversation.c.agent_id).where(
                    tables.conversation.c.id == conversation_id
                )
            )
        ).scalar_one()


class _Ext:
    """The extension context as these tools use it: one transaction over the real database."""

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        async with workspace_tx() as connection:
            yield connection


class _Sandbox:
    """Stands in for the sandbox only. A command exits 0 when it mentions state named in `holds`, so
    a test drives the one thing the refusal turns on — whether the condition was already true —
    without asserting anything about the stub itself."""

    def __init__(self, *holds: str) -> None:
        self.holds = holds
        self.commands: list[str] = []

    async def bash(self, command: str, timeout_s: int = 0) -> SimpleNamespace:
        self.commands.append(command)
        held = any(fragment in command for fragment in self.holds)
        return SimpleNamespace(exit_code=0 if held else 1, stdout="", stderr="")


def _tool_context(sandbox: _Sandbox, conversation_id: UUID, ext: object) -> ToolContext:
    return ToolContext(
        sandbox=cast("Any", sandbox),
        blob=None,
        turn=cast(
            "Any",
            SimpleNamespace(id=uuid4(), conversation_id=conversation_id, subagent_profile=None),
        ),
        agent=cast("Any", SimpleNamespace()),
        spawn=None,
        speaker_member_id=None,
        audience=None,
        artifact_token_secret="",
        ext=cast("ExtensionContext", ext),
    )


async def test_a_condition_that_already_holds_is_refused_at_plan_time(db: None) -> None:
    """Red before green, enforced. A `file_exists` condition true before the work starts cannot tell
    before from after, so it closes its step on nothing. Production measured `file_exists` and
    `file_contains` refusing 0 of 45 claims — not working, manufacturing confidence, which this
    design holds to be worse than no check at all."""
    workspace_id, conversation_id = await _seeded_conversation()
    sandbox = _Sandbox(REQUIRED_PATH)
    agent_id = await _agent_of(conversation_id)
    with ws(workspace_id), agent(agent_id):
        result = await plan_objective(
            _tool_context(sandbox, conversation_id, _Ext()),
            PlanObjectiveInput(
                name="filing",
                directive="file the vendor forms",
                steps=(StepPlan(title="file the W9", accepts=(FileExists(path=REQUIRED_PATH),)),),
                user_description="planning",
            ),
        )
    assert result.is_error
    text = result.content[0].text
    assert "already there" in text
    assert REQUIRED_PATH in text
    async with workspace_tx() as connection:
        assert await Objectives(connection, workspace_id).named(conversation_id, "filing") is None


async def test_a_condition_that_does_not_hold_yet_is_accepted(db: None) -> None:
    workspace_id, conversation_id = await _seeded_conversation()
    sandbox = _Sandbox()
    agent_id = await _agent_of(conversation_id)
    with ws(workspace_id), agent(agent_id):
        result = await plan_objective(
            _tool_context(sandbox, conversation_id, _Ext()),
            PlanObjectiveInput(
                name="filing",
                directive="file the vendor forms",
                steps=(StepPlan(title="file the W9", accepts=(FileExists(path=REQUIRED_PATH),)),),
                user_description="planning",
            ),
        )
    assert not result.is_error
    async with workspace_tx() as connection:
        stored = await Objectives(connection, workspace_id).named(conversation_id, "filing")
    assert stored is not None
    assert tuple(step.title for step in stored.steps) == ("file the W9",)


async def test_a_suite_already_green_is_the_condition_worth_having(db: None) -> None:
    """The shape the `red_after_green` arc turns on, and the reason `command_succeeds` is not gated.
    The member asks for a change that must leave `pytest -q` passing when the work is finished, not
    only when the edit lands — so the suite is green before the work starts. Refusing it for being
    green would leave the worker only `file_contains: MAX_ITEMS = 100`, which still holds once the
    edit lands and cannot go red when another team's commit breaks the invariant. That is the exact
    silent close this extension exists to prevent, so a green suite plans."""
    workspace_id, conversation_id = await _seeded_conversation()
    suite = "pytest -q"
    sandbox = _Sandbox(suite)
    agent_id = await _agent_of(conversation_id)
    with ws(workspace_id), agent(agent_id):
        result = await plan_objective(
            _tool_context(sandbox, conversation_id, _Ext()),
            PlanObjectiveInput(
                name="batch-size",
                directive="raise the batch size to 100 and leave the suite passing",
                steps=(
                    StepPlan(title="raise MAX_ITEMS", accepts=(CommandSucceeds(command=suite),)),
                ),
                user_description="planning",
            ),
        )
    assert not result.is_error
    async with workspace_tx() as connection:
        stored = await Objectives(connection, workspace_id).named(conversation_id, "batch-size")
    assert stored is not None
    assert stored.steps[0].accepts == (CommandSucceeds(command=suite),)


async def test_a_revision_keeps_a_condition_the_finished_work_made_true(db: None) -> None:
    """A step is gated when its condition is written, never again. A subagent hands back the work
    for a step the parent has not recorded yet, so that step's condition now holds — re-running the
    gate over it would refuse the revision for having made progress, and the plan is refused whole,
    losing the steps the revision came to add."""
    workspace_id, conversation_id = await _seeded_conversation()
    agent_id = await _agent_of(conversation_id)
    plan = PlanObjectiveInput(
        name="filing",
        directive="file the vendor forms",
        steps=(StepPlan(title="file the W9", accepts=(FileExists(path=REQUIRED_PATH),)),),
        user_description="planning",
    )
    with ws(workspace_id), agent(agent_id):
        assert not (
            await plan_objective(_tool_context(_Sandbox(), conversation_id, _Ext()), plan)
        ).is_error
        landed = _Sandbox(REQUIRED_PATH)
        revised = await plan_objective(
            _tool_context(landed, conversation_id, _Ext()),
            PlanObjectiveInput(
                name="filing",
                directive="file the vendor forms, then the 1099",
                steps=(
                    *plan.steps,
                    StepPlan(title="file the 1099", accepts=(FileExists(path=WRONG_PATH),)),
                ),
                user_description="revising",
            ),
        )
    assert not revised.is_error
    assert landed.commands == [f"test -e {WRONG_PATH}"]
    async with workspace_tx() as connection:
        stored = await Objectives(connection, workspace_id).named(conversation_id, "filing")
    assert stored is not None
    assert tuple(step.title for step in stored.steps) == ("file the W9", "file the 1099")
    assert stored.directive == "file the vendor forms, then the 1099"


async def test_a_revision_of_an_attempted_step_is_not_gated_on_accepts_it_cannot_write(
    db: None,
) -> None:
    """An attempted step's `accepts` are frozen, so a revision restating that step is not writing a
    condition at all — the one already on the record is kept. Gating the submitted text would refuse
    the plan over a condition that is then discarded, which is a refusal the worker cannot act on:
    editing the offending line changes nothing that gets stored."""
    workspace_id, conversation_id = await _seeded_conversation()
    agent_id = await _agent_of(conversation_id)
    with ws(workspace_id), agent(agent_id):
        assert not (
            await plan_objective(
                _tool_context(_Sandbox(), conversation_id, _Ext()),
                PlanObjectiveInput(
                    name="filing",
                    directive="file the vendor forms",
                    steps=(
                        StepPlan(title="file the W9", accepts=(FileExists(path=REQUIRED_PATH),)),
                    ),
                    user_description="planning",
                ),
            )
        ).is_error
        async with workspace_tx() as connection:
            objectives = Objectives(connection, workspace_id)
            attempted = await objectives.named(conversation_id, "filing")
            assert attempted is not None
            await objectives.record(attempted.steps[0], DID, uuid4(), "wrote the file")
        revised = await plan_objective(
            _tool_context(_Sandbox(WRONG_PATH), conversation_id, _Ext()),
            PlanObjectiveInput(
                name="filing",
                directive="file the vendor forms",
                steps=(StepPlan(title="file the W9", accepts=(FileExists(path=WRONG_PATH),)),),
                user_description="revising",
            ),
        )
    assert not revised.is_error
    async with workspace_tx() as connection:
        stored = await Objectives(connection, workspace_id).named(conversation_id, "filing")
    assert stored is not None
    assert stored.steps[0].accepts == (FileExists(path=REQUIRED_PATH),)


async def test_a_revision_that_swaps_in_a_condition_already_true_is_refused(db: None) -> None:
    """Not re-checking the record is not a way in. A revision that rewrites an unattempted step's
    condition to one that already holds is writing a new condition, so it is gated exactly as it
    would have been at first planning."""
    workspace_id, conversation_id = await _seeded_conversation()
    agent_id = await _agent_of(conversation_id)
    with ws(workspace_id), agent(agent_id):
        assert not (
            await plan_objective(
                _tool_context(_Sandbox(), conversation_id, _Ext()),
                PlanObjectiveInput(
                    name="filing",
                    directive="file the vendor forms",
                    steps=(
                        StepPlan(title="file the W9", accepts=(FileExists(path=REQUIRED_PATH),)),
                    ),
                    user_description="planning",
                ),
            )
        ).is_error
        weakened = await plan_objective(
            _tool_context(_Sandbox(WRONG_PATH), conversation_id, _Ext()),
            PlanObjectiveInput(
                name="filing",
                directive="file the vendor forms",
                steps=(StepPlan(title="file the W9", accepts=(FileExists(path=WRONG_PATH),)),),
                user_description="revising",
            ),
        )
    assert weakened.is_error
    assert WRONG_PATH in weakened.content[0].text
    async with workspace_tx() as connection:
        stored = await Objectives(connection, workspace_id).named(conversation_id, "filing")
    assert stored is not None
    assert stored.steps[0].accepts == (FileExists(path=REQUIRED_PATH),)
