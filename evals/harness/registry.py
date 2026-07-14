"""The task registry helpers: bundle a suite of capability cases into a digest-pinned EvalTask and
run selected tasks against a target. The concrete suites live with the cases in the `evals` package;
these are the reusable machinery that turns them into runnable, comparable reports."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from evals.harness.capability import CapabilityCase, run_capability_case
from evals.harness.harness import EvalReport, digest_payload
from evals.harness.target import CapabilityTarget

type EvalRunner = Callable[[CapabilityTarget], Awaitable[EvalReport]]


@dataclass(frozen=True)
class EvalTask:
    name: str
    suite: str
    digest: str
    cases: tuple[str, ...]
    run: EvalRunner


def capability_task(name: str, cases: tuple[CapabilityCase, ...]) -> EvalTask:
    digest = digest_payload(
        {"runner": "capability-case", "task": name, "cases": [case.payload() for case in cases]}
    )

    async def run(target: CapabilityTarget) -> EvalReport:
        results = tuple([await run_capability_case(case, target) for case in cases])
        return EvalReport(name=name, suite="capability", digest=digest, cases=results)

    return EvalTask(name, "capability", digest, tuple(case.name for case in cases), run)


def selected_tasks(
    tasks: tuple[EvalTask, ...], names: tuple[str, ...] = ()
) -> tuple[EvalTask, ...]:
    if not names:
        return tasks
    by_name = {task.name: task for task in tasks}
    missing = tuple(name for name in names if name not in by_name)
    if missing:
        raise ValueError(f"unknown eval task: {', '.join(missing)}")
    return tuple(by_name[name] for name in names)
