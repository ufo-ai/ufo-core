"""The wake smoke: does work survive the end of the turn that started it.

Two mechanisms can admit a turn onto a conversation after its opening turn is terminal — a due
scheduled task, and a background subagent finishing. The suite splits on them. `heartbeat_wake`
rides the scheduler, so it passes wherever the arc harness is itself sound, which is what makes it
the control: a run where it fails is measuring the harness, not the system. `completion_handback`
rides the subagent's return leg and names which way that leg was absent. A smoke where everything
passes proves nothing.

Both graders read turn rows and workspace bytes. Neither asks a judge anything: the claim is about
which turns exist and what caused them."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from ufo_ext_scheduled_tasks.manifest import NAME as SCHEDULED_TASKS_NAME
from ufo_ext_scheduled_tasks.schedules import ScheduleStore

from evals.harness.arc import (
    ArcCase,
    ArcObservation,
    ArcPerturbation,
    ArcVerdict,
)
from evals.harness.capability import WorkspaceFile
from ufo.ext.context import context_for

HEARTBEAT_NONCE = "NONCE-8f2ac41d"
ASSIGNMENT_NONCE = "NONCE-5b73e0c9"
LATE_FILE = "late.txt"
ANSWER_FILE = "answer.txt"
ASSIGNMENT_FILE = "assignment.txt"
STACK_OWNER_EMAIL = "evals@localhost"
EVERY_MINUTE = "* * * * *"
FIRST_FIRE_SECONDS = 60.0
WATCH_EXPIRY_SECONDS = 180.0
PERTURBATION_DELAY_SECONDS = 45.0


def _schedule_store() -> ScheduleStore:
    """The relocated store, which now reads its workspace and object agent off the context the
    extension's own callers hand it."""
    return ScheduleStore(context_for(SCHEDULED_TASKS_NAME, frozenset()))


async def _seed_late_file_watch(
    workspace_id: UUID, conversation_id: UUID, member_id: UUID | None
) -> None:
    """Plant the recurring check the arc measures. The arc's opening turn is admitted `internal`
    and so carries no speaker, while creating a scheduled task requires a member requester — asking
    the agent for one would measure that refusal instead of the wake."""
    await _schedule_store().create(
        conversation_id=conversation_id,
        name="late-file-watch",
        schedule=EVERY_MINUTE,
        prompt=(
            f"Check whether the file {LATE_FILE} exists in your workspace. If it does, reply with "
            "its exact contents. If it does not, reply that it is still missing."
        ),
        description="the wake smoke's recurring check",
        next_run_at=datetime.now(UTC) + timedelta(seconds=FIRST_FIRE_SECONDS),
        created_by_member_id=member_id,
        expires_at=datetime.now(UTC) + timedelta(seconds=WATCH_EXPIRY_SECONDS),
    )


async def _land_late_file(workspace_dir: Path) -> None:
    await asyncio.to_thread(workspace_dir.mkdir, parents=True, exist_ok=True)
    await asyncio.to_thread((workspace_dir / LATE_FILE).write_text, f"{HEARTBEAT_NONCE}\n")


async def _grade_heartbeat_wake(observation: ArcObservation) -> ArcVerdict:
    scheduled = observation.woken_by("scheduled")
    if not scheduled:
        sources = sorted({turn.admission_source for turn in observation.woken})
        return ArcVerdict(
            False,
            "no scheduled turn was admitted after the opening turn"
            + (f"; later turns came from {sources}" if sources else " and no later turn ran"),
        )
    carrying = tuple(turn for turn in scheduled if HEARTBEAT_NONCE in turn.reply)
    if not carrying:
        return ArcVerdict(
            False,
            f"{len(scheduled)} scheduled turn(s) ran but none reported {HEARTBEAT_NONCE}, "
            "so the wake did not read the state that landed after the opening turn",
        )
    return ArcVerdict(
        True,
        f"scheduled turn {carrying[0].seq} reported the value written "
        f"{PERTURBATION_DELAY_SECONDS:.0f}s after the opening turn ended",
    )


async def _grade_completion_handback(observation: ArcObservation) -> ArcVerdict:
    if "wait_for_subagents" in observation.opening_calls:
        return ArcVerdict(
            False,
            "the opening turn blocked on wait_for_subagents instead of ending — a turn held open "
            "for the worker is the shape a long-running objective cannot use",
        )
    if not observation.children:
        return ArcVerdict(False, "the opening turn spawned no subagent, so nothing could hand back")
    child = observation.children[0]
    if not observation.woken:
        return ArcVerdict(
            False,
            f"the opening turn ended and the child finished {child.status}, but no turn was "
            "admitted on the conversation — a child's terminal wakes nothing",
        )
    answer = observation.read_workspace(ANSWER_FILE)
    if ASSIGNMENT_NONCE not in answer:
        return ArcVerdict(
            False,
            f"{len(observation.woken)} turn(s) were admitted after the opening turn but "
            f"{ANSWER_FILE} does not carry {ASSIGNMENT_NONCE}",
        )
    woken = observation.woken[0]
    return ArcVerdict(
        True,
        f"turn {woken.seq} ({woken.admission_source}) followed the child's terminal and wrote "
        f"the handed-back value to {ANSWER_FILE}",
    )


CASES: tuple[ArcCase, ...] = (
    ArcCase(
        name="heartbeat_wake",
        message=(
            f"You are watching for a file called {LATE_FILE} in your workspace. It is not there "
            "yet, and a recurring check is already scheduled on this conversation. Acknowledge "
            "and end your turn — the check will wake you."
        ),
        grader=_grade_heartbeat_wake,
        grading=(
            f"A turn with admission_source 'scheduled' runs after the opening turn and its reply "
            f"carries {HEARTBEAT_NONCE}, which only exists in {LATE_FILE} written "
            f"{PERTURBATION_DELAY_SECONDS:.0f}s into the arc."
        ),
        min_seconds=WATCH_EXPIRY_SECONDS + 30.0,
        quiet_seconds=45.0,
        deadline_seconds=480.0,
        member_key=STACK_OWNER_EMAIL,
        seed=_seed_late_file_watch,
        perturbation=ArcPerturbation(
            after_seconds=PERTURBATION_DELAY_SECONDS, apply=_land_late_file
        ),
    ),
    ArcCase(
        name="completion_handback",
        message=(
            f"Delegate this to a background subagent: read {ASSIGNMENT_FILE} in the workspace and "
            "return the code it contains. Start the worker so that it hands its result back to "
            "you, then end your turn — do not sit and wait for it, and do not read the file "
            f"yourself. When the worker's result reaches you, write that code to {ANSWER_FILE}."
        ),
        grader=_grade_completion_handback,
        grading=(
            f"The opening turn spawns a background child without calling wait_for_subagents, ends, "
            f"and a later turn on the same conversation writes {ASSIGNMENT_NONCE} to {ANSWER_FILE}."
        ),
        min_seconds=60.0,
        quiet_seconds=45.0,
        deadline_seconds=420.0,
        workspace_files=(
            WorkspaceFile(path=ASSIGNMENT_FILE, content=f"code: {ASSIGNMENT_NONCE}\n".encode()),
        ),
    ),
)
