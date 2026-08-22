"""The fan-out arc's grader, driven over constructed observations.

The grader's whole job is telling apart runs that leave an identical workspace: three files copied
by the agent itself, three copied by children spawned one per turn, and three copied by children
dispatched together all end with the same bytes on disk. So each way the shape can be wrong gets its
own case — a smoke that collapses them into one failure cannot say which happened.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from evals.harness.arc import ArcCase, ArcObservation, ArcTurn
from evals.suites.fanout import (
    CASES,
    DISPATCH_TOOL,
    OUTPUTS,
    PLAN_TOOL,
    _grade,
    _grade_plain_delegation,
    _grade_workers_overlap,
)


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
    there. A case that names no workspace file asks for none."""
    for case in CASES:
        seeded = {file.path for file in case.workspace_files}
        if "inputs/" in case.message:
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


def worker(
    parent: UUID,
    started_at: datetime,
    ended_at: datetime | None,
) -> ArcTurn:
    return ArcTurn(
        turn_id=uuid4(),
        seq=1,
        admission_source="internal",
        status="done",
        inbound="",
        reply="",
        parent_turn_id=parent,
        subagent_profile="research",
        started_at=started_at,
        ended_at=ended_at,
    )


BASE = datetime(2026, 8, 15, 8, 53, tzinfo=UTC)


async def test_round_by_round_spawns_are_the_serial_shape_the_case_catches(
    tmp_path: Path,
) -> None:
    """The live run this case replays: three foreground spawns in successive rounds, each child
    created about 100ms after the previous one's terminal. Start spread alone cannot condemn it —
    unequal workers stretch any window — so the verdict reads the handoff: a next start at or past
    the previous end is serial."""
    parent = uuid4()
    handoffs = (
        (BASE, BASE + timedelta(seconds=31)),
        (BASE + timedelta(seconds=31.1), BASE + timedelta(seconds=55)),
        (BASE + timedelta(seconds=55.1), BASE + timedelta(seconds=92)),
    )
    serial = tuple(worker(parent, start, end) for start, end in handoffs)
    verdict = await _grade_workers_overlap(observation(tmp_path, (turn(1),), children=serial))
    assert not verdict.passed
    assert "serial" in verdict.reason


async def test_workers_whose_windows_overlap_pass(tmp_path: Path) -> None:
    parent = uuid4()
    overlapping = tuple(
        worker(
            parent,
            BASE + timedelta(seconds=index),
            BASE + timedelta(seconds=30 + index * 5),
        )
        for index in range(3)
    )
    verdict = await _grade_workers_overlap(observation(tmp_path, (turn(1),), children=overlapping))
    assert verdict.passed
    assert "overlapped" in verdict.reason


async def test_one_serial_handoff_among_overlaps_still_fails(tmp_path: Path) -> None:
    """Two workers together and a third only after both ended is a fan-out that collapsed
    half-way; the case reads every handoff, not just one."""
    parent = uuid4()
    mixed = (
        worker(parent, BASE, BASE + timedelta(seconds=30)),
        worker(parent, BASE + timedelta(seconds=1), BASE + timedelta(seconds=28)),
        worker(parent, BASE + timedelta(seconds=40), BASE + timedelta(seconds=70)),
    )
    verdict = await _grade_workers_overlap(observation(tmp_path, (turn(1),), children=mixed))
    assert not verdict.passed
    assert "serial" in verdict.reason


async def test_workers_split_across_parent_turns_fail_before_timing(tmp_path: Path) -> None:
    spread = tuple(
        worker(uuid4(), BASE + timedelta(seconds=index), BASE + timedelta(seconds=30))
        for index in range(3)
    )
    verdict = await _grade_workers_overlap(observation(tmp_path, (turn(1),), children=spread))
    assert not verdict.passed
    assert "parent turns" in verdict.reason


async def test_a_still_live_worker_condemns_no_handoff(tmp_path: Path) -> None:
    """A worker the observation caught mid-run has no end; a follower that started after it did
    cannot be called serial on a window that is still open."""
    parent = uuid4()
    open_ended = (
        worker(parent, BASE, None),
        worker(parent, BASE + timedelta(seconds=5), BASE + timedelta(seconds=40)),
        worker(parent, BASE + timedelta(seconds=6), BASE + timedelta(seconds=41)),
    )
    verdict = await _grade_workers_overlap(observation(tmp_path, (turn(1),), children=open_ended))
    assert verdict.passed
