"""Does the agent let the engine widen the fan-out, or keep deciding the shape itself.

Three sub-tasks that share nothing: each reads one source file and writes one output. Independence
is a property of the work here, not a hint in the prompt — nothing in step two needs anything step
one produced, and the request never says the word.

What is graded is the *shape of the dispatch*, not whether the outputs appeared. An agent that reads
all three files itself, or spawns three children one at a time across three turns, arrives at the
same workspace as one that planned three independent steps and dispatched them together. Only the
record and the child turns tell those apart, so the grader reads both.

A returning result reaches its parent two ways — folded into the turn that is still live, or
admitted as the next one — so a woken turn is evidence and never a requirement. Children that finish
before their parent's round ends are the *fast* case, and an arc that demanded a second turn would
score it a failure. What must be true either way is that the parent recorded the steps, which it
cannot do without having read what came back.
"""

from __future__ import annotations

from datetime import datetime
from itertools import pairwise

from evals.harness.arc import ArcCase, ArcObservation, ArcVerdict
from evals.harness.capability import WorkspaceFile
from evals.suites.objective_record import recorded_objective

SOURCES = {
    "inputs/alpha.txt": "NONCE-ALPHA-7c31",
    "inputs/beta.txt": "NONCE-BETA-b48e",
    "inputs/gamma.txt": "NONCE-GAMMA-1f90",
}
OUTPUTS = {
    "out/alpha.txt": "NONCE-ALPHA-7c31",
    "out/beta.txt": "NONCE-BETA-b48e",
    "out/gamma.txt": "NONCE-GAMMA-1f90",
}
DISPATCH_TOOL = "run_independent_steps"
SPAWN_TOOL = "spawn"
CONCURRENT_WITHIN_SECONDS = 20.0
MIN_WORKERS = 3
PLAN_TOOL = "plan_objective"
MIN_INDEPENDENT = 2
MIN_SECONDS = 150.0


async def _grade(observation: ArcObservation) -> ArcVerdict:
    calls = sorted(set(observation.opening_calls))
    if PLAN_TOOL not in observation.opening_calls:
        return ArcVerdict(
            False,
            f"the opening turn recorded no objective, so there was no plan to fan out; it called "
            f"{calls}",
        )
    plan = await recorded_objective(observation.conversation_id)
    if plan is None:
        return ArcVerdict(False, f"{PLAN_TOOL} was called but no objective row landed")
    independent = tuple(step.title for step in plan.steps if step.independent)
    if len(independent) < MIN_INDEPENDENT:
        return ArcVerdict(
            False,
            f"the plan marked {len(independent)} of {len(plan.steps)} steps independent, so the "
            f"engine had nothing to widen: {[step.title for step in plan.steps]}",
        )
    if DISPATCH_TOOL not in observation.opening_calls:
        return ArcVerdict(
            False,
            f"the plan marked {len(independent)} steps independent but the turn never called "
            f"{DISPATCH_TOOL}, so it decided the shape itself; it called {calls}",
        )
    if len(observation.children) < MIN_INDEPENDENT:
        return ArcVerdict(
            False,
            f"{DISPATCH_TOOL} was called but only {len(observation.children)} child turn(s) "
            "exist, so the dispatch did not fan out",
        )
    landed = tuple(step.title for step in plan.steps if step.independent and step.attempted)
    if len(landed) < MIN_INDEPENDENT:
        return ArcVerdict(
            False,
            f"{len(observation.children)} children were dispatched but only {len(landed)} "
            "independent step(s) were ever recorded, so their results did not reach the parent",
        )
    missing = [
        rel for rel, nonce in OUTPUTS.items() if nonce not in observation.read_workspace(rel)
    ]
    if missing:
        return ArcVerdict(
            False,
            f"dispatched {len(observation.children)} children and woke {len(observation.woken)} "
            f"time(s), but these outputs never carried their nonce: {missing}",
        )
    return ArcVerdict(
        True,
        f"planned {len(independent)} independent steps, dispatched {len(observation.children)} "
        f"children in one turn, and recorded {len(landed)} of them once their results came back "
        f"({len(observation.woken)} woken turn(s))",
    )


_FAN_OUT = ArcCase(
    name="independent_steps_fan_out",
    message=(
        "Three jobs, and none of them needs anything from the others. Read "
        "inputs/alpha.txt and copy the code in it to out/alpha.txt. Read inputs/beta.txt and "
        "copy its code to out/beta.txt. Read inputs/gamma.txt and copy its code to "
        "out/gamma.txt. Do not read the input files yourself — delegate each job to a worker. "
        "Record an objective first so the work survives this turn, then end your turn without "
        "waiting for the workers."
    ),
    grader=_grade,
    grading=(
        "The opening turn records an objective whose steps are marked independent, dispatches "
        "them together rather than one at a time or by hand, ends, and the turns woken by the "
        "workers' results leave every nonce at its named output path."
    ),
    min_seconds=MIN_SECONDS,
    deadline_seconds=600.0,
    workspace_files=tuple(
        WorkspaceFile(path=path, content=f"code: {nonce}\n".encode())
        for path, nonce in SOURCES.items()
    ),
)


async def _grade_plain_delegation(observation: ArcObservation) -> ArcVerdict:
    """Whether ordinary delegation already overlaps, with no objective in play.

    This is the control for `independent_steps_fan_out`. If three plain spawns in one turn already
    run at the same time, concurrency was never the missing capability and `run_independent_steps`
    buys only a shape that is declared and durable — worth having for a turn that wakes with no
    memory, worth nothing for work that finishes in one. If they instead run one after another, the
    fan-out is reachable today only through an objective, which agents correctly decline to record
    for short work, and that is the gap to close.

    Overlap is read from when each child started. Sharing one parent turn is not enough on its own:
    an agent can spawn three children in three successive rounds of the same turn, which is serial
    work wearing the shape of a batch.
    """
    children = observation.children
    if len(children) < MIN_WORKERS:
        return ArcVerdict(
            False,
            f"only {len(children)} worker(s) were spawned for three independent jobs; the turn "
            f"called {sorted(set(observation.opening_calls))}",
        )
    parents = {child.parent_turn_id for child in children}
    if len(parents) != 1:
        return ArcVerdict(
            False,
            f"{len(children)} workers were spread across {len(parents)} parent turns, so the work "
            "was delegated a turn at a time rather than together",
        )
    starts = sorted(child.started_at for child in children if child.started_at is not None)
    if len(starts) < MIN_WORKERS:
        return ArcVerdict(False, "the observation carried no start time for every worker")
    spread = (starts[-1] - starts[0]).total_seconds()
    if spread > CONCURRENT_WITHIN_SECONDS:
        return ArcVerdict(
            False,
            f"{len(children)} workers under one turn but their starts spanned {spread:.0f}s, so "
            "they ran one after another rather than at once",
        )
    return ArcVerdict(
        True,
        f"{len(children)} workers started within {spread:.0f}s under one turn, so ordinary "
        "delegation already overlaps without an objective",
    )


async def _grade_workers_overlap(observation: ArcObservation) -> ArcVerdict:
    """Whether a member's own fan-out phrasing gets overlapping workers, graded on run windows.

    The conversation this case replays (metalcraft testing, 2026-08-15) asked for exactly this
    message, was told the lookups would run in parallel, and got three foreground spawns in three
    successive rounds — each child created about 100ms after the previous one's terminal. The
    workspace cannot tell that run from a parallel one, and start spread alone cannot either once
    children take unequal time, so the grader reads each worker's window: a fan-out is real when
    every worker after the first started before the one before it ended."""
    children = observation.children
    if len(children) < MIN_WORKERS:
        return ArcVerdict(
            False,
            f"only {len(children)} worker(s) were spawned for three independent lookups; the "
            f"turn called {sorted(set(observation.opening_calls))}",
        )
    parents = {child.parent_turn_id for child in children}
    if len(parents) != 1:
        return ArcVerdict(
            False,
            f"{len(children)} workers were spread across {len(parents)} parent turns, so the "
            "work was delegated a turn at a time rather than together",
        )
    spans: list[tuple[datetime, datetime | None]] = []
    for child in children:
        if child.started_at is None:
            return ArcVerdict(False, "the observation carried no start time for every worker")
        spans.append((child.started_at, child.ended_at))
    windows = sorted(spans, key=lambda span: (span[0], span[1] or span[0]))
    serial = [
        (start - previous_end).total_seconds()
        for (_, previous_end), (start, _) in pairwise(windows)
        if previous_end is not None and start >= previous_end
    ]
    if serial:
        gaps = ", ".join(f"{gap:.1f}s" for gap in serial)
        return ArcVerdict(
            False,
            f"{len(serial)} of {len(windows) - 1} worker handoff(s) were serial — the next "
            f"started only after the previous ended (gaps of {gaps})",
        )
    return ArcVerdict(
        True,
        f"{len(windows)} workers overlapped: each started before the one before it ended",
    )


_OVERLAP = ArcCase(
    name="spawned_workers_overlap",
    message="check the weather in 3 cities using subagents",
    grader=_grade_workers_overlap,
    grading=(
        "Three workers are spawned under one parent turn and their run windows overlap — each "
        "starts before the one before it ends. A next worker created only after the previous "
        "one's terminal is the serial shape this case exists to catch, whatever the replies say "
        "about parallelism."
    ),
    min_seconds=MIN_SECONDS,
    deadline_seconds=600.0,
)


_PLAIN = ArcCase(
    name="plain_delegation_overlaps",
    message=(
        "Three jobs, and none of them needs anything from the others. Read "
        "inputs/alpha.txt and copy the code in it to out/alpha.txt. Read inputs/beta.txt and "
        "copy its code to out/beta.txt. Read inputs/gamma.txt and copy its code to "
        "out/gamma.txt. Hand each job to its own worker rather than reading the files "
        "yourself."
    ),
    grader=_grade_plain_delegation,
    grading=(
        "Three workers are spawned from one turn and start within seconds of each other, which "
        "says ordinary delegation already runs them at once. Starts spread over a longer window "
        "say the turn delegated serially and only an objective can widen the fan-out."
    ),
    min_seconds=MIN_SECONDS,
    deadline_seconds=600.0,
    workspace_files=tuple(
        WorkspaceFile(path=path, content=f"code: {nonce}\n".encode())
        for path, nonce in SOURCES.items()
    ),
)


CASES = (_FAN_OUT, _PLAIN, _OVERLAP)
