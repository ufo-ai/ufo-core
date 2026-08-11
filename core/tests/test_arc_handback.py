"""The wake smoke's graders, driven directly over constructed observations.

The graders are pure functions of what the arc observed, so they are tested as pure logic. What
matters is that they *separate* the ways a wake can be absent — a turn held open on the worker, a
worker that never ran, a child that finished into silence, a wake that produced the wrong state —
because a smoke that reports one undifferentiated failure cannot tell a missing primitive from a
broken harness."""

from pathlib import Path
from uuid import uuid4

import pytest

from evals.handback import (
    ANSWER_FILE,
    ASSIGNMENT_NONCE,
    CASES,
    HEARTBEAT_NONCE,
    _grade_completion_handback,
    _grade_heartbeat_wake,
)
from evals.harness.arc import ArcCase, ArcObservation, ArcTurn
from evals.harness.registry import arc_task


def turn(
    seq: int,
    admission_source: str = "internal",
    reply: str = "",
    status: str = "done",
    subagent_profile: str | None = None,
) -> ArcTurn:
    return ArcTurn(
        turn_id=uuid4(),
        seq=seq,
        admission_source=admission_source,
        status=status,
        inbound="",
        reply=reply,
        parent_turn_id=None,
        subagent_profile=subagent_profile,
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


async def test_heartbeat_wake_needs_a_scheduled_turn(tmp_path: Path) -> None:
    verdict = await _grade_heartbeat_wake(observation(tmp_path, (turn(1),)))
    assert not verdict.passed
    assert "no later turn ran" in verdict.reason


async def test_heartbeat_wake_names_the_wrong_admission_source(tmp_path: Path) -> None:
    turns = (turn(1), turn(2, admission_source="member", reply=HEARTBEAT_NONCE))
    verdict = await _grade_heartbeat_wake(observation(tmp_path, turns))
    assert not verdict.passed
    assert "member" in verdict.reason


async def test_heartbeat_wake_rejects_a_wake_that_read_nothing(tmp_path: Path) -> None:
    turns = (turn(1), turn(2, admission_source="scheduled", reply="nothing there yet"))
    verdict = await _grade_heartbeat_wake(observation(tmp_path, turns))
    assert not verdict.passed
    assert HEARTBEAT_NONCE in verdict.reason


async def test_heartbeat_wake_passes_on_a_scheduled_turn_carrying_the_value(
    tmp_path: Path,
) -> None:
    turns = (
        turn(1),
        turn(2, admission_source="scheduled", reply="still empty"),
        turn(3, admission_source="scheduled", reply=f"the file says {HEARTBEAT_NONCE}"),
    )
    verdict = await _grade_heartbeat_wake(observation(tmp_path, turns))
    assert verdict.passed


async def test_completion_handback_rejects_a_turn_held_open(tmp_path: Path) -> None:
    verdict = await _grade_completion_handback(
        observation(
            tmp_path,
            (turn(1),),
            children=(turn(1, subagent_profile="general_purpose"),),
            opening_calls=("spawn_subagent", "wait_for_subagents", "write"),
        )
    )
    assert not verdict.passed
    assert "wait_for_subagents" in verdict.reason


async def test_completion_handback_rejects_a_missing_worker(tmp_path: Path) -> None:
    verdict = await _grade_completion_handback(
        observation(tmp_path, (turn(1),), opening_calls=("read", "write"))
    )
    assert not verdict.passed
    assert "spawned no subagent" in verdict.reason


async def test_completion_handback_names_the_silent_terminal(tmp_path: Path) -> None:
    """The verdict today: the child finished and nothing woke — the primitive that is missing."""
    verdict = await _grade_completion_handback(
        observation(
            tmp_path,
            (turn(1),),
            children=(turn(1, subagent_profile="general_purpose", reply=ASSIGNMENT_NONCE),),
            opening_calls=("spawn_subagent",),
        )
    )
    assert not verdict.passed
    assert "wakes nothing" in verdict.reason


async def test_completion_handback_rejects_a_wake_that_wrote_the_wrong_value(
    tmp_path: Path,
) -> None:
    (tmp_path / ANSWER_FILE).write_text("code: NONCE-deadbeef\n")
    verdict = await _grade_completion_handback(
        observation(
            tmp_path,
            (turn(1), turn(2)),
            children=(turn(1, subagent_profile="general_purpose"),),
            opening_calls=("spawn_subagent",),
        )
    )
    assert not verdict.passed
    assert ASSIGNMENT_NONCE in verdict.reason


async def test_completion_handback_passes_when_a_woken_turn_wrote_the_value(
    tmp_path: Path,
) -> None:
    (tmp_path / ANSWER_FILE).write_text(f"code: {ASSIGNMENT_NONCE}\n")
    verdict = await _grade_completion_handback(
        observation(
            tmp_path,
            (turn(1), turn(2)),
            children=(turn(1, subagent_profile="general_purpose"),),
            opening_calls=("spawn_subagent",),
        )
    )
    assert verdict.passed


def test_the_arc_task_asks_for_no_judge() -> None:
    """An arc's verdict is a fact about turn rows and workspace bytes; a judge model on this suite
    would mean a grader had started reading prose."""
    task = arc_task("handback", CASES)
    assert task.judge_model is None
    assert task.suite == "arc"
    assert task.cases == ("heartbeat_wake", "completion_handback")


def test_every_case_declares_its_perturbation_after_the_opening_turn() -> None:
    for case in CASES:
        if case.perturbation is not None:
            assert case.perturbation.after_seconds > 0
            assert case.perturbation.after_seconds < case.deadline_seconds


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_case_payloads_are_digestible(case: ArcCase) -> None:
    payload = case.payload()
    assert isinstance(payload, dict)
    assert payload["name"] == case.name
    assert payload["grader"]
