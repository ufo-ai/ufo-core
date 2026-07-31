"""The task registry helpers: bundle a suite of capability cases into a digest-pinned EvalTask and
run selected tasks against a target. The concrete suites live with the cases in the `evals` package;
these are the reusable machinery that turns them into runnable, comparable reports."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from functools import partial
from typing import cast

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


def capability_task(
    name: str,
    cases: tuple[CapabilityCase, ...],
    judge_model: str | None = None,
    judge_max_tokens: int = JUDGE_MAX_TOKENS,
    judge_reasoning: ReasoningEffort = "low",
    serial: bool = False,
) -> EvalTask:
    needs_judge = any(case.rubric or case.visual_rubric for case in cases)
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
    )


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
