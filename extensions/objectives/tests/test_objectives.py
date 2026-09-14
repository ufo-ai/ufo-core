"""The closure gate, which is the only part of this design that is not bookkeeping.

RFC 0025 measured both closure forms a person or an agent performs and both scored below doing
nothing. Its worst regression is the shape asserted here: the agent declared *"W9_...pdf exists"*,
wrote the file to the workspace root, checked "does the file exist?", found it, and marked the
condition verified — while the rubric required it inside `1099/2025/`. The check ran, passed, and
was wrong.

So the test that matters is not that a step can close. It is that a step whose conditions name
state that is false does **not** close, however confidently it was recorded, and that a condition
fixed at plan time survives a later revision that would have weakened it."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection
from ufo_ext_objectives.store import (
    ATTEMPTED_STATE,
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
)
from ufo_ext_objectives.tools import (
    CONDITION_OUTPUT_MAX_CHARS,
    PlanObjectiveInput,
    RunIndependentStepsInput,
    evaluate,
    plan_objective,
    run_independent_steps,
)

from ufo.db import workspace_tx
from ufo.runtime.agent_scope import agent, agent_current
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.sdk.context import ExtensionContext
from ufo.sdk.sandbox import ExecResult
from ufo.sdk.tools import ToolContext

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]


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
    without asserting anything about the stub itself. A failing command answers whatever `said`
    and `expired_after_s` script, since that is what a verdict has to carry back."""

    def __init__(
        self,
        *holds: str,
        said: tuple[str, str] = ("", ""),
        exit_code: int = 1,
        expired_after_s: int | None = None,
    ) -> None:
        self.holds = holds
        self.said = said
        self.exit_code = exit_code
        self.expired_after_s = expired_after_s
        self.commands: list[str] = []

    async def bash(self, command: str, timeout_s: int = 0) -> ExecResult:
        self.commands.append(command)
        if any(fragment in command for fragment in self.holds):
            return ExecResult(stdout="", stderr="", exit_code=0)
        stdout, stderr = self.said
        return ExecResult(
            stdout=stdout,
            stderr=stderr,
            exit_code=self.exit_code,
            timed_out_after_s=self.expired_after_s,
        )


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
            ),
        )
    assert not result.is_error
    async with workspace_tx() as connection:
        stored = await Objectives(connection, workspace_id).named(conversation_id, "filing")
    assert stored is not None
    assert tuple(step.title for step in stored.steps) == ("file the W9",)


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
                ),
            )
        ).is_error
        weakened = await plan_objective(
            _tool_context(_Sandbox(WRONG_PATH), conversation_id, _Ext()),
            PlanObjectiveInput(
                name="filing",
                directive="file the vendor forms",
                steps=(StepPlan(title="file the W9", accepts=(FileExists(path=WRONG_PATH),)),),
            ),
        )
    assert weakened.is_error
    assert WRONG_PATH in weakened.content[0].text
    async with workspace_tx() as connection:
        stored = await Objectives(connection, workspace_id).named(conversation_id, "filing")
    assert stored is not None
    assert stored.steps[0].accepts == (FileExists(path=REQUIRED_PATH),)


async def _planned_steps(conversation_id: UUID, *titles: str) -> None:
    async with workspace_tx() as connection:
        await Objectives(connection, agent_current().workspace_id).plan(
            conversation_id,
            "launch",
            "ship the landing page",
            tuple(StepPlan(title=title, accepts=(), independent=True) for title in titles),
        )


async def test_a_failed_check_reports_the_exit_code_and_what_the_command_said(db: None) -> None:
    """ "false" is the whole of what a step is told today, and a `command_succeeds` gate is a test
    suite behind that word — a failing assertion, a missing binary and an expired budget all read
    identically, and each wants a different next move."""
    workspace_id, conversation_id = await _seeded_conversation()
    agent_id = await _agent_of(conversation_id)
    sandbox = _Sandbox(said=("", "E   assert 3 == 4"), exit_code=1)
    suite = step("green suite", accepts=(CommandSucceeds(command="pytest -q"),))
    with ws(workspace_id), agent(agent_id):
        (verdict,) = await evaluate(_tool_context(sandbox, conversation_id, _Ext()), suite)
    assert verdict.holds is False
    assert "exit 1" in verdict.detail
    assert "assert 3 == 4" in verdict.detail


async def test_a_check_the_sandbox_stopped_says_so_rather_than_naming_its_exit_code(
    db: None,
) -> None:
    """A command running `timeout` exits 124 exactly as a carrier-stopped one does, so the code
    cannot separate broken work from a budget that ran out."""
    workspace_id, conversation_id = await _seeded_conversation()
    agent_id = await _agent_of(conversation_id)
    sandbox = _Sandbox(exit_code=124, expired_after_s=120)
    suite = step("green suite", accepts=(CommandSucceeds(command="pytest -q"),))
    with ws(workspace_id), agent(agent_id):
        (verdict,) = await evaluate(_tool_context(sandbox, conversation_id, _Ext()), suite)
    assert "stopped after 120s" in verdict.detail
    assert "exit 124" not in verdict.detail
    assert "said nothing" in verdict.detail


async def test_a_failed_check_clips_what_the_command_said(db: None) -> None:
    workspace_id, conversation_id = await _seeded_conversation()
    agent_id = await _agent_of(conversation_id)
    sandbox = _Sandbox(said=("", "x" * (CONDITION_OUTPUT_MAX_CHARS + 500)))
    suite = step("green suite", accepts=(CommandSucceeds(command="pytest -q"),))
    with ws(workspace_id), agent(agent_id):
        (verdict,) = await evaluate(_tool_context(sandbox, conversation_id, _Ext()), suite)
    tail = "x" * CONDITION_OUTPUT_MAX_CHARS
    assert verdict.detail.endswith(tail)
    assert not verdict.detail.removesuffix(tail).endswith("x")


async def test_a_fan_out_that_stops_part_way_names_the_children_already_running(
    db: None,
) -> None:
    """Each dispatched child is a real background turn that delivers its result into this
    conversation whatever this call returns. A failure naming only the step that would not start
    reads as though none of them exist, and the turn ends without the record to match arriving
    results to steps."""
    workspace_id, conversation_id = await _seeded_conversation()
    agent_id = await _agent_of(conversation_id)
    started: list[UUID] = []

    async def spawn(
        profile: str,
        payload: dict[str, object],
        background: bool = False,
        dedup_key: str | None = None,
        delivers_result: bool = False,
        *,
        requesting_message_ref: UUID | None = None,
    ) -> SimpleNamespace:
        if len(started) == 2:
            raise RuntimeError("no capacity for another coding child")
        turn_id = uuid4()
        started.append(turn_id)
        return SimpleNamespace(turn_id=turn_id)

    with ws(workspace_id), agent(agent_id):
        await _planned_steps(conversation_id, "copy", "pricing", "footer")
        context = _tool_context(_Sandbox(), conversation_id, _Ext())
        result = await run_independent_steps(
            replace(context, spawn=cast("Any", spawn)),
            RunIndependentStepsInput(name="launch", profile="coding"),
        )
    assert result.is_error
    failure = json.loads(result.content[0].text)
    assert failure["operation"] == "run_independent_steps"
    assert [effect["identity"] for effect in failure["applied"]] == [str(id) for id in started]
    assert [effect["state"] for effect in failure["applied"]] == [
        "running: copy",
        "running: pricing",
    ]
    assert "no capacity for another coding child" in failure["summary"]
    assert "footer" in failure["summary"]
