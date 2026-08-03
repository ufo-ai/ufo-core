"""Handoff compliance over a recorded run.

Whether a delegated child finished terse is a property of the run, not of the answer the case
asked for — so it is measured here rather than inside the case's verdict. Keeping it out of the
verdict is what lets a run on a prompt that fails this constraint still produce its quality scores:
a case whose child wrote its report twice is still a case whose deliverable can be judged, and
suppressing the judgment would spend the turn and learn nothing.

Every attempt records its children's handoffs whatever the verdict was, so this reads the archive
and needs no re-run. `coding_repo_handoff` is the share of delegated children that were neither
verbose, nor duplicating, nor rerouted to a file — the number that should move when the finish
contract changes."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from evals.coding_repo.cases import CASES, PATCH_SUFFIX, CodingCase
from evals.coding_repo.runner import ANSWERS_TASK, DELIVERABLES_TASK
from evals.harness.handoff import HandoffDocument, SubagentHandoff
from evals.harness.harness import EvalCaseResult, Json
from evals.harness.viewer import EvalRun

MAX_CLOSING_CHARS = 400
MAX_DUPLICATION = 0.3
RUNS_DIR = Path("eval-reports/runs")
METRIC_NAME = "coding_repo_handoff"
SUITE_TASKS = frozenset({ANSWERS_TASK, DELIVERABLES_TASK})


@dataclass(frozen=True)
class ChildHandoff:
    """One recorded handoff read against the case that asked for it. `record` is the producer's own
    row, validated rather than re-declared, so a field the recorder renames fails loud here instead
    of silently defaulting the checks off."""

    case: CodingCase
    record: SubagentHandoff

    @property
    def verbose(self) -> bool:
        return self.record.closing_chars > MAX_CLOSING_CHARS

    @property
    def duplicated(self) -> bool:
        return self.record.duplication > MAX_DUPLICATION

    @property
    def rerouted_documents(self) -> tuple[HandoffDocument, ...]:
        """The report written to a file instead of a message: every document the case never asked
        for, read back, and substantially restated in the payload. Forbidding the final prose
        message does not remove the second copy — it moves it — and a handoff that reads terse while
        this is true is not terse at all. Every document is read, because a patch case's own note is
        asked-for prose that would otherwise stand in front of the scaffolding beside it."""
        return tuple(
            document
            for document in self.record.documents
            if not self._asked_for(document.path)
            and document.chars > MAX_CLOSING_CHARS
            and document.reads > 0
            and document.duplication > MAX_DUPLICATION
        )

    @property
    def rerouted(self) -> bool:
        return bool(self.rerouted_documents)

    @property
    def compliant(self) -> bool:
        return not self.verbose and not self.duplicated and not self.rerouted

    @property
    def handoff_chars(self) -> int:
        """Every character the child produced to hand its work over: the message it left standing,
        the payload it returned, and each copy of the report it routed through a file. This is the
        number a terse-handoff contract has to move — a contract that only forbids the message
        shifts bytes into a document and reads as an improvement while costing the same or more.
        The deliverable the case asked for is the work, not the cost of handing it over."""
        return (
            self.record.closing_chars
            + self.record.result_chars
            + sum(document.chars for document in self.rerouted_documents)
        )

    @property
    def wasted_chars(self) -> int:
        """The prose the child wrote and no parent read: what a compliant handoff would not have
        paid for."""
        return max(self.record.closing_chars - MAX_CLOSING_CHARS, 0)

    def _asked_for(self, document: str) -> bool:
        name = PurePosixPath(document).name
        match self.case.deliverable:
            case "patch":
                return name in {f"{self.case.name}{PATCH_SUFFIX}", self.case.notes_name}
            case "document":
                return name == PurePosixPath(self.case.document_path).name
            case _:
                return False


def handoffs_in(run: Path) -> tuple[ChildHandoff, ...]:
    """Every delegated child's handoff recorded in one run archive, read through the archive's own
    type so a renamed field stops this reader rather than reporting no handoff at all."""
    try:
        recorded = EvalRun.model_validate_json(run.read_text())
    except ValidationError as error:
        raise ValueError(f"{run} is not an eval run archive: {error}") from error
    found: list[ChildHandoff] = []
    for report in recorded.reports:
        if report.name not in SUITE_TASKS:
            continue
        for case_report in report.cases:
            case = next((item for item in CASES if item.name == case_report.name), None)
            if case is None:
                raise ValueError(f"{case_report.name!r} is not a coding_repo case")
            for handoff in _recorded_handoffs(case_report):
                try:
                    record = SubagentHandoff.model_validate(handoff)
                except ValidationError as error:
                    raise ValueError(
                        f"{case_report.name}: a recorded handoff does not match the recorder's own "
                        f"shape: {error}"
                    ) from error
                found.append(ChildHandoff(case=case, record=record))
    return tuple(found)


def _recorded_handoffs(case_report: EvalCaseResult) -> tuple[Json, ...]:
    """The handoff rows one case's attempts recorded. Every capability case records `attempts`, and
    an attempt that delegated nothing records `handoffs` as null, so an attempt list of any other
    shape is a recorder this reader no longer matches."""
    attempts = case_report.evidence.get("attempts")
    if not isinstance(attempts, list):
        raise ValueError(f"{case_report.name}: the recorded evidence has no attempt list")
    found: list[Json] = []
    for attempt in attempts:
        if not isinstance(attempt, dict):
            raise ValueError(f"{case_report.name}: a recorded attempt is not a record")
        handoffs = attempt.get("handoffs")
        if handoffs is None:
            continue
        if not isinstance(handoffs, list):
            raise ValueError(f"{case_report.name}: the recorded handoffs are not a list")
        found.extend(handoffs)
    return tuple(found)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.coding_repo.handoff")
    parser.add_argument("--runs", type=Path, default=RUNS_DIR, help="the run archive directory")
    parser.add_argument("--run", default="", metavar="RUN_ID", help="one run, else the newest")
    parser.add_argument("--metric-stdout", action="store_true")
    args = parser.parse_args(argv)
    if args.run:
        run = args.runs / f"{args.run}.json"
        if not run.is_file():
            parser.error(f"no run archive at {run}")
    else:
        archives = sorted(args.runs.glob("*.json"), key=lambda path: path.stat().st_mtime)
        if not archives:
            parser.error(f"no run archive under {args.runs}")
        run = archives[-1]
    handoffs = handoffs_in(run)
    print(f"run {run.stem}")
    if not handoffs:
        print(
            "no delegated handoff was recorded — every case failed before a child finished, or "
            "none delegated. No metric: absent data is not compliance."
        )
        raise SystemExit(3)
    print(
        f"bounds: closing <= {MAX_CLOSING_CHARS} chars, duplication <= {MAX_DUPLICATION:.0%}, and "
        f"no unasked-for document over {MAX_CLOSING_CHARS} chars read back and restated"
    )
    for handoff in handoffs:
        marks = ", ".join(
            mark
            for mark, failed in (
                ("verbose", handoff.verbose),
                ("duplicated", handoff.duplicated),
                ("rerouted-to-file", handoff.rerouted),
            )
            if failed
        )
        verdict = marks or "compliant"
        verdict += "".join(
            f" [{PurePosixPath(document.path).name} {document.chars}c "
            f"reads={document.reads} errs={document.errors} "
            f"dup={document.duplication:.0%}]"
            for document in handoff.rerouted_documents
        )
        print(
            f"  {handoff.case.name:36} {str(handoff.record.conversation_id)[:8]} "
            f"closing={handoff.record.closing_chars:6} result={handoff.record.result_chars:6} "
            f"dup={handoff.record.duplication:5.0%} handoff={handoff.handoff_chars:6} {verdict}"
        )
    compliant = sum(handoff.compliant for handoff in handoffs)
    wasted = sum(handoff.wasted_chars for handoff in handoffs)
    produced = sum(handoff.handoff_chars for handoff in handoffs)
    print(
        f"{compliant}/{len(handoffs)} children handed off compliantly; "
        f"{wasted} chars written that no parent read; "
        f"{produced} chars produced to hand the work over"
    )
    if args.metric_stdout:
        print(f"{METRIC_NAME}: {compliant / len(handoffs):.4f}")


if __name__ == "__main__":
    main()
