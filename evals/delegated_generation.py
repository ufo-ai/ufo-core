"""What a delegated child generated over recorded runs.

A spawn returns one payload. Everything a child produced before the round that carried it — the
reasoning, the narration between tool calls, the conclusion it wrote as prose and then handed over
again as the payload — reached nobody through that payload. That output is largely the price of the
delegation rather than the work, and it is the number a change to the subagent contract has to move.

Largely, not wholly: a child asked for an artifact writes it in one of those rounds, and those
tokens are the deliverable. So the delivered side is reported beside the token count — the payload
and the bytes the child wrote to files — and a change that cuts tokens by writing a thinner report
shows up as both numbers falling together rather than as a reduction. The prose the child wrote
between tool calls and left standing is reported too, exactly, because that is the part a contract
can remove without touching the work.

It is read over the archive rather than gated inside a case verdict, for the reason
`coding_repo.handoff` is: a case whose child generated twice what it delivered still answers the
question it was asked, and failing it would spend the turn and learn nothing. Every attempt records
its turn timing whatever the verdict was, so a run needs no re-run to be read, and an arm whose
cases mostly failed still reports what its children generated.

Every archive under `--runs` is read, so one arm's repeats sum into one figure. The headline is the
mean per child turn: an arm that delegated fewer times must not read as a reduction because it
generated less in total. The child count is printed beside it so an uneven comparison is visible."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from evals.harness.handoff import SubagentHandoff
from evals.harness.harness import EvalCaseResult, Json
from evals.harness.timing import CaseTiming, TurnTiming
from evals.harness.viewer import EvalRun
from evals.suites.response_register import DELEGATED_TASK

RUNS_DIR = Path("eval-reports/runs")
METRIC_NAME = "delegated_intermediate_tokens"
DELIVERED_METRIC_NAME = "delegated_delivered_chars"
CHILD_ROLE = "child"


@dataclass(frozen=True)
class CaseGeneration:
    """One case attempt's delegated children: what its child turns cost, and what those children
    handed over. Both sides are read through the recorder's own types, so a field either of them
    renames stops this reader rather than reporting a reduction that never happened. An attempt is
    the unit because a case that spawns twice pays for both and delivers through both."""

    run: str
    case: str
    turns: tuple[TurnTiming, ...]
    handoffs: tuple[SubagentHandoff, ...]

    @property
    def measured(self) -> bool:
        return all(
            turn.output_tokens is not None and turn.intermediate_output_tokens is not None
            for turn in self.turns
        )

    @property
    def rounds(self) -> int:
        return sum(turn.rounds for turn in self.turns)

    @property
    def output_tokens(self) -> int:
        return sum(turn.output_tokens or 0 for turn in self.turns)

    @property
    def intermediate_output_tokens(self) -> int:
        return sum(turn.intermediate_output_tokens or 0 for turn in self.turns)

    @property
    def prose_chars(self) -> int:
        """The lines the child wrote between its tool calls and left standing at the end — output
        that reached nobody at all, and the part a finish contract can remove outright."""
        return sum(handoff.interim_chars + handoff.closing_chars for handoff in self.handoffs)

    @property
    def delivered_chars(self) -> int:
        """The payload the parent read plus the bytes the child left in files it can read. A
        candidate whose token count falls because this fell wrote a thinner deliverable."""
        return sum(
            handoff.result_chars + sum(document.chars for document in handoff.documents)
            for handoff in self.handoffs
        )


def generation_in(run: Path) -> tuple[CaseGeneration, ...]:
    """Every delegated child turn one run archive recorded for the suite, one entry per attempt."""
    try:
        recorded = EvalRun.model_validate_json(run.read_text())
    except ValidationError as error:
        raise ValueError(f"{run} is not an eval run archive: {error}") from error
    found: list[CaseGeneration] = []
    for report in recorded.reports:
        if report.name != DELEGATED_TASK:
            continue
        for case_report in report.cases:
            for attempt in _recorded_attempts(case_report):
                timing, handoffs = _attempt_record(case_report, attempt)
                if timing is None:
                    continue
                children = tuple(turn for turn in timing.turns if turn.role == CHILD_ROLE)
                if children:
                    found.append(CaseGeneration(run.stem, case_report.name, children, handoffs))
    return tuple(found)


def _recorded_attempts(case_report: EvalCaseResult) -> tuple[dict[str, Json], ...]:
    """Every capability case records `attempts`, so an evidence shape without that list is a
    recorder this reader no longer matches."""
    attempts = case_report.evidence.get("attempts")
    if not isinstance(attempts, list):
        raise ValueError(f"{case_report.name}: the recorded evidence has no attempt list")
    found: list[dict[str, Json]] = []
    for attempt in attempts:
        if not isinstance(attempt, dict):
            raise ValueError(f"{case_report.name}: a recorded attempt is not a record")
        found.append(attempt)
    return tuple(found)


def _attempt_record(
    case_report: EvalCaseResult, attempt: dict[str, Json]
) -> tuple[CaseTiming | None, tuple[SubagentHandoff, ...]]:
    """One attempt's timing and handoffs. An attempt the harness could not time records `timing`
    as null, and one that delegated nothing records `handoffs` as null."""
    timing: CaseTiming | None = None
    recorded = attempt.get("timing")
    if recorded is not None:
        try:
            timing = CaseTiming.model_validate(recorded)
        except ValidationError as error:
            raise ValueError(
                f"{case_report.name}: a recorded timing does not match the recorder's own shape: "
                f"{error}"
            ) from error
    handoffs = attempt.get("handoffs")
    if handoffs is None:
        return timing, ()
    if not isinstance(handoffs, list):
        raise ValueError(f"{case_report.name}: the recorded handoffs are not a list")
    try:
        return timing, tuple(SubagentHandoff.model_validate(row) for row in handoffs)
    except ValidationError as error:
        raise ValueError(
            f"{case_report.name}: a recorded handoff does not match the recorder's own shape: "
            f"{error}"
        ) from error


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.delegated_generation")
    parser.add_argument("--runs", type=Path, default=RUNS_DIR, help="the run archive directory")
    parser.add_argument("--run", default="", metavar="RUN_ID", help="one run, else every archive")
    parser.add_argument("--metric-stdout", action="store_true")
    parser.add_argument("--json", type=Path, default=None, help="write the figures to this path")
    args = parser.parse_args(argv)
    if args.run:
        archives = [args.runs / f"{args.run}.json"]
        if not archives[0].is_file():
            parser.error(f"no run archive at {archives[0]}")
    else:
        archives = sorted(args.runs.glob("*.json"))
        if not archives:
            parser.error(f"no run archive under {args.runs}")
    cases = tuple(case for archive in archives for case in generation_in(archive))
    print(f"{len(archives)} archive(s) under {args.runs}")
    if not cases:
        print(
            f"no delegated child turn was recorded for {DELEGATED_TASK} — no metric: absent data "
            "is not a reduction."
        )
        raise SystemExit(3)
    for case in cases:
        mark = "" if case.measured else "  UNMEASURED"
        print(
            f"  {case.case:48} {case.run[:8]} children={len(case.turns)} "
            f"rounds={case.rounds:3} output={case.output_tokens:7} "
            f"intermediate={case.intermediate_output_tokens:7} "
            f"prose={case.prose_chars:6} delivered={case.delivered_chars:7}{mark}"
        )
    measured = tuple(case for case in cases if case.measured)
    unmeasured = len(cases) - len(measured)
    if not measured:
        print(
            f"{unmeasured} attempt(s) recorded child turns with no output usage — no metric: a "
            "partial count is not a reduction."
        )
        raise SystemExit(3)
    children = sum(len(case.turns) for case in measured)
    output = sum(case.output_tokens for case in measured)
    intermediate = sum(case.intermediate_output_tokens for case in measured)
    prose = sum(case.prose_chars for case in measured)
    delivered = sum(case.delivered_chars for case in measured)
    share = intermediate / output if output else 0.0
    print(
        f"{children} child turn(s) over {len(measured)} attempt(s) generated {output} output "
        f"tokens; {intermediate} of them ({share:.0%}) did not carry the payload"
    )
    print(
        f"{prose} chars of prose reached nobody; {delivered} chars were delivered as the payload "
        "or as files"
    )
    if unmeasured:
        print(f"{unmeasured} attempt(s) excluded for missing output usage")
    per_child = intermediate / children
    delivered_per_child = delivered / children
    if args.json is not None:
        args.json.write_text(
            json.dumps(
                {
                    "task": DELEGATED_TASK,
                    "attempts": len(measured),
                    "unmeasuredAttempts": unmeasured,
                    "childTurns": children,
                    "outputTokens": output,
                    "intermediateOutputTokens": intermediate,
                    "proseChars": prose,
                    "deliveredChars": delivered,
                    "intermediatePerChildTurn": per_child,
                    "deliveredPerChildTurn": delivered_per_child,
                },
                indent=2,
            )
            + "\n"
        )
    if args.metric_stdout:
        print(f"{METRIC_NAME}: {per_child:.1f}")
        print(f"{DELIVERED_METRIC_NAME}: {delivered_per_child:.1f}")


if __name__ == "__main__":
    main()
