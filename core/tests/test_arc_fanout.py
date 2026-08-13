"""The fan-out arc's grader, driven over constructed observations.

The grader's whole job is telling apart runs that leave an identical workspace: three files copied
by the agent itself, three copied by children spawned one per turn, and three copied by children
dispatched together all end with the same bytes on disk. So each way the shape can be wrong gets its
own case — a smoke that collapses them into one failure cannot say which happened.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from evals.fanout import (
    CASES,
    DISPATCH_TOOL,
    OUTPUTS,
    PLAN_TOOL,
    _grade,
    _grade_plain_delegation,
)
from evals.harness.arc import ArcCase, ArcObservation, ArcTurn


def turn(seq: int, admission_source: str = "internal") -> ArcTurn:
    return ArcTurn(
        turn_id=uuid4(),
        seq=seq,
        admission_source=admission_source,
        status="done",
        inbound="",
        reply="",
        parent_turn_id=None,
        subagent_profile=None,
    )


def observation(
    tmp_path: Path,
    turns: tuple[ArcTurn, ...],
    children: tuple[ArcTurn, ...] = (),
    opening_calls: tuple[str, ...] = (),
) -> ArcObservation:
    return ArcObservation(
        conversation_id=uuid4(),
        turns=turns,
        children=children,
        opening_calls=opening_calls,
        workspace_dir=tmp_path,
    )


def _outputs_written(tmp_path: Path) -> None:
    for rel, nonce in OUTPUTS.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"code: {nonce}\n")


async def test_a_turn_that_recorded_no_objective_fails_before_reading_the_plan(
    tmp_path: Path,
) -> None:
    """The plan is what the fan-out is read off, so its absence is a distinct failure from a plan
    that exists and marked nothing independent."""
    verdict = await _grade(observation(tmp_path, (turn(1),), opening_calls=("bash",)))
    assert not verdict.passed
    assert "recorded no objective" in verdict.reason
    assert "bash" in verdict.reason


async def test_the_dispatch_tool_alone_is_not_enough_without_children(
    db: None, tmp_path: Path
) -> None:
    """`run_independent_steps` returning without spawning is the failure the tool's own empty-set
    branch produces, and it must not read as a pass."""
    verdict = await _grade(
        observation(tmp_path, (turn(1), turn(2)), opening_calls=(PLAN_TOOL, DISPATCH_TOOL))
    )
    assert not verdict.passed
    assert "no objective row landed" in verdict.reason or "independent" in verdict.reason


async def test_a_woken_turn_is_not_required(db: None, tmp_path: Path) -> None:
    """A child that finishes before its parent's round ends folds into the live turn, so a healthy
    fast run has no second turn at all. Requiring one scored the best case as a failure — the first
    live run dispatched three children, delivered all three, wrote every output, and failed on this.
    What replaced it is whether the parent recorded the steps, which it cannot do unread."""
    _outputs_written(tmp_path)
    verdict = await _grade(
        observation(tmp_path, (turn(1),), children=(turn(1), turn(1)), opening_calls=(PLAN_TOOL,))
    )
    assert not verdict.passed
    assert "woken" not in verdict.reason


async def test_outputs_alone_do_not_pass_the_case(tmp_path: Path) -> None:
    """The point of the arc: an agent that did all three jobs by hand leaves exactly the workspace a
    fanned-out run leaves. Grading the bytes would score both the same."""
    _outputs_written(tmp_path)
    verdict = await _grade(observation(tmp_path, (turn(1),), opening_calls=("bash", "bash")))
    assert not verdict.passed


def test_the_case_seeds_every_source_it_asks_the_agent_to_read() -> None:
    """A case whose inputs are missing measures the agent failing to read a file that was never
    there."""
    for case in CASES:
        seeded = {file.path for file in case.workspace_files}
        assert seeded, case.name
        for path in seeded:
            assert path.startswith("inputs/"), case.name


def test_the_case_never_names_the_dispatch_tool_in_its_prompt() -> None:
    """Naming the tool measures instruction-following. The question is whether the agent reaches for
    the fan-out when the work is independent and nothing told it to."""
    for case in CASES:
        assert DISPATCH_TOOL not in case.message, case.name
        assert "independent" not in case.message, case.name


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_the_case_waits_past_a_dispatch_before_it_reads(case: ArcCase) -> None:
    """A conversation waiting on its children is quiet, so quiescence alone would end the arc before
    the wake it exists to observe."""
    assert case.min_seconds > 0
    assert case.deadline_seconds > case.min_seconds


async def test_serial_workers_under_one_turn_are_not_concurrency(tmp_path: Path) -> None:
    """Sharing a parent turn is not overlap. An agent can spawn three workers in three successive
    rounds of the same turn, which is serial work wearing the shape of a batch — so the control case
    reads when each child started, not merely whose child it is."""
    parent = uuid4()
    base = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    serial = tuple(
        ArcTurn(
            turn_id=uuid4(),
            seq=1,
            admission_source="internal",
            status="done",
            inbound="",
            reply="",
            parent_turn_id=parent,
            subagent_profile="general_purpose",
            started_at=base + timedelta(minutes=index * 2),
        )
        for index in range(3)
    )
    verdict = await _grade_plain_delegation(observation(tmp_path, (turn(1),), children=serial))
    assert not verdict.passed
    assert "one after another" in verdict.reason


async def test_overlapping_workers_under_one_turn_are_concurrency(tmp_path: Path) -> None:
    parent = uuid4()
    base = datetime(2026, 8, 13, 12, 0, tzinfo=UTC)
    together = tuple(
        ArcTurn(
            turn_id=uuid4(),
            seq=1,
            admission_source="internal",
            status="done",
            inbound="",
            reply="",
            parent_turn_id=parent,
            subagent_profile="general_purpose",
            started_at=base + timedelta(seconds=index),
        )
        for index in range(3)
    )
    verdict = await _grade_plain_delegation(observation(tmp_path, (turn(1),), children=together))
    assert verdict.passed
    assert "already overlaps" in verdict.reason
