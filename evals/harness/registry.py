"""The task registry helpers: bundle a suite of capability cases into a digest-pinned EvalTask and
run selected tasks against a target. The concrete suites live with the cases in the `evals` package;
these are the reusable machinery that turns them into runnable, comparable reports."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from functools import partial
from typing import cast

from evals.harness.arc import ArcCase, ArcRun
from evals.harness.capability import CapabilityCase, run_capability_case
from evals.harness.harness import EvalCaseResult, EvalReport, digest_payload
from evals.harness.judge import JUDGE_MAX_TOKENS, JUDGE_REVISION
from evals.harness.scenario import ScenarioCase, run_scenario_case
from evals.harness.target import CapabilityTarget
from ufo.schema.records import ReasoningEffort

type EvalRunner = Callable[[CapabilityTarget, asyncio.Semaphore], Awaitable[EvalReport]]


async def gather_cases[ResultT](
    slots: asyncio.Semaphore,
    runs: Sequence[Callable[[], Awaitable[ResultT]]],
) -> tuple[ResultT, ...]:
    """Run every case bounded by the run-wide semaphore, preserving input order. Siblings always
    settle; only then does any raise surface, so one harness fault never cancels in-flight turns."""

    async def bounded(run: Callable[[], Awaitable[ResultT]]) -> ResultT:
        async with slots:
            return await run()

    outcomes = await asyncio.gather(*(bounded(run) for run in runs), return_exceptions=True)
    errors = tuple(outcome for outcome in outcomes if isinstance(outcome, BaseException))
    if errors:
        raise BaseExceptionGroup("eval cases raised", errors)
    return tuple(cast("ResultT", outcome) for outcome in outcomes)


@dataclass(frozen=True)
class EvalTask:
    name: str
    suite: str
    digest: str
    cases: tuple[str, ...]
    run: EvalRunner
    judge_model: str | None = None
    simulator_model: str | None = None
    judge_revision: str | None = None
    judge_max_tokens: int = JUDGE_MAX_TOKENS
    judge_reasoning: ReasoningEffort = "low"
    simulator_max_tokens: int = JUDGE_MAX_TOKENS
    simulator_reasoning: ReasoningEffort = "off"
    pin_runtime: bool = False
    exclusive: bool = False
    packs: tuple[str, ...] = ()
    agent: str | None = None
    wait_seconds: float | None = None
    """How long this suite's turns may run before the harness stops waiting; `None` takes the run's
    default. The wait belongs to the task because a shard mixes suites: a deck build needs minutes a
    chat case does not, and a wait chosen for the shard as a whole cancelled the deck cases of every
    shard that also carried something else (measured on `document_visual`, 2026-08-21 sweep)."""
    narrow: Callable[[tuple[str, ...]], EvalTask] | None = None


def capability_task(
    name: str,
    cases: tuple[CapabilityCase, ...],
    judge_model: str | None = None,
    judge_max_tokens: int = JUDGE_MAX_TOKENS,
    judge_reasoning: ReasoningEffort = "low",
    serial: bool = False,
    packs: tuple[str, ...] = (),
    agent: str | None = None,
    wait_seconds: float | None = None,
) -> EvalTask:
    needs_judge = any(case.rubric or case.artifact_rubric or case.visual_rubric for case in cases)
    if needs_judge and judge_model is None:
        raise ValueError(f"capability task {name!r} has semantic rubrics but no judge model")
    if judge_model is not None and not needs_judge:
        raise ValueError(
            f"capability task {name!r} declares a judge model without semantic rubrics"
        )
    digest = digest_payload(
        {
            "runner": "capability-case",
            "task": name,
            "cases": [case.payload() for case in cases],
            **({"serial": True} if serial else {}),
            **({"agent": agent} if agent is not None else {}),
            **(
                {
                    "judgeModel": judge_model,
                    "judgeMaxTokens": judge_max_tokens,
                    "judgeReasoning": judge_reasoning,
                }
                if judge_model is not None
                else {}
            ),
        }
    )

    async def run(target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        if serial:
            results: tuple[EvalCaseResult, ...] = ()
            for case in cases:
                async with slots:
                    results += (await run_capability_case(case, target),)
        else:
            results = await gather_cases(
                slots, tuple(partial(run_capability_case, case, target) for case in cases)
            )
        return EvalReport(name=name, suite="capability", digest=digest, cases=results)

    def narrow(names: tuple[str, ...]) -> EvalTask:
        kept = tuple(case for case in cases if case.name in names)
        rubricked = any(case.rubric or case.artifact_rubric or case.visual_rubric for case in kept)
        return capability_task(
            name,
            kept,
            judge_model=judge_model if rubricked else None,
            judge_max_tokens=judge_max_tokens,
            judge_reasoning=judge_reasoning,
            serial=serial,
            packs=packs,
            agent=agent,
            wait_seconds=wait_seconds,
        )

    return EvalTask(
        name,
        "capability",
        digest,
        tuple(case.name for case in cases),
        run,
        judge_model=judge_model,
        judge_revision=JUDGE_REVISION if judge_model is not None else None,
        judge_max_tokens=judge_max_tokens,
        judge_reasoning=judge_reasoning,
        exclusive=serial,
        packs=packs,
        agent=agent,
        wait_seconds=wait_seconds,
        narrow=narrow,
    )


def scenario_task(
    name: str,
    cases: tuple[ScenarioCase, ...],
    simulator_model: str,
    simulator_max_tokens: int = JUDGE_MAX_TOKENS,
    simulator_reasoning: ReasoningEffort = "off",
    judge_model: str | None = None,
    judge_max_tokens: int = JUDGE_MAX_TOKENS,
    judge_reasoning: ReasoningEffort = "low",
    wait_seconds: float | None = None,
) -> EvalTask:
    needs_judge = any(case.rubric for case in cases)
    if needs_judge and judge_model is None:
        raise ValueError(f"scenario task {name!r} has semantic rubrics but no judge model")
    if judge_model is not None and not needs_judge:
        raise ValueError(f"scenario task {name!r} declares a judge model without semantic rubrics")
    digest = digest_payload(
        {
            "runner": "scenario-case",
            "task": name,
            "cases": [case.payload() for case in cases],
            "simulatorModel": simulator_model,
            "simulatorMaxTokens": simulator_max_tokens,
            "simulatorReasoning": simulator_reasoning,
            **(
                {
                    "judgeModel": judge_model,
                    "judgeMaxTokens": judge_max_tokens,
                    "judgeReasoning": judge_reasoning,
                }
                if judge_model is not None
                else {}
            ),
        }
    )

    async def run(target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        results: list[EvalCaseResult] = []
        for case in cases:
            async with slots:
                results.append(await run_scenario_case(case, target))
        return EvalReport(name=name, suite="scenario", digest=digest, cases=tuple(results))

    def narrow(names: tuple[str, ...]) -> EvalTask:
        kept = tuple(case for case in cases if case.name in names)
        return scenario_task(
            name,
            kept,
            simulator_model=simulator_model,
            simulator_max_tokens=simulator_max_tokens,
            simulator_reasoning=simulator_reasoning,
            judge_model=judge_model if any(case.rubric for case in kept) else None,
            judge_max_tokens=judge_max_tokens,
            judge_reasoning=judge_reasoning,
            wait_seconds=wait_seconds,
        )

    return EvalTask(
        name,
        "scenario",
        digest,
        tuple(case.name for case in cases),
        run,
        simulator_model=simulator_model,
        simulator_max_tokens=simulator_max_tokens,
        simulator_reasoning=simulator_reasoning,
        judge_model=judge_model,
        judge_revision=JUDGE_REVISION if judge_model is not None else None,
        judge_max_tokens=judge_max_tokens,
        judge_reasoning=judge_reasoning,
        exclusive=True,
        wait_seconds=wait_seconds,
        narrow=narrow,
    )


def arc_task(name: str, cases: tuple[ArcCase, ...]) -> EvalTask:
    """An arc suite. Cases run one at a time and the task is exclusive: an arc waits on the real
    clock for the deploy's scheduler to fire, so a co-running suite's turns would contend for the
    same worker and stretch the very wait the case measures."""
    digest = digest_payload(
        {"runner": "arc-case", "task": name, "cases": [case.payload() for case in cases]}
    )

    async def run(target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        results: list[EvalCaseResult] = []
        for case in cases:
            async with slots:
                results.append(await ArcRun(case, target).result())
        return EvalReport(name=name, suite="arc", digest=digest, cases=tuple(results))

    return EvalTask(name, "arc", digest, tuple(case.name for case in cases), run, exclusive=True)


def rewrapped(task: EvalTask, wrap: Callable[[EvalTask], EvalTask]) -> EvalTask:
    """The task as `wrap` builds it, with `wrap` applied again to every narrowing of it. A suite
    that wraps its runner or sets a task flag here keeps both under `--case`, where the narrowed
    task is otherwise rebuilt from the kept cases alone."""
    narrow = task.narrow
    wrapped = wrap(task)
    if narrow is None:
        return wrapped
    return replace(wrapped, narrow=lambda names: rewrapped(narrow(names), wrap))


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


def narrowed_tasks(
    tasks: tuple[EvalTask, ...], case_names: tuple[str, ...]
) -> tuple[EvalTask, ...]:
    """Narrow the selected tasks to the named cases: each task keeps only the cases named, a task
    naming none is dropped, and a case no selected task carries is an error. A narrowed task's
    digest covers the kept subset, so its report compares only to runs of the same subset."""
    known = {case for task in tasks for case in task.cases}
    unknown = tuple(name for name in case_names if name not in known)
    if unknown:
        raise ValueError(f"unknown eval case: {', '.join(unknown)}")
    narrowed = []
    for task in tasks:
        kept = tuple(name for name in task.cases if name in case_names)
        if not kept:
            continue
        if task.narrow is None:
            raise ValueError(f"suite {task.name!r} cannot narrow to individual cases")
        narrowed.append(task.narrow(kept))
    return tuple(narrowed)
