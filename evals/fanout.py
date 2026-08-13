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

from evals.harness.arc import ArcCase, ArcObservation, ArcVerdict
from evals.harness.capability import WorkspaceFile
from evals.objective_record import recorded_objective

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


CASES = (
    ArcCase(
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
    ),
)
