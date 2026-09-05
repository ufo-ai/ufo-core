"""Render one sweep's run records into what a reader opens first.

The records carry every case's evidence and are megabytes each, so a red job says nothing until
someone downloads them. This prints the three things a reader needs first — which suites the sweep
covered, how each scored, and the name and reason of every case that failed — and rebuilds the
archive viewer over every shard's records, which each shard could only build over its own.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nightly_eval_matrix import NIGHTLY_MODELS, sweep_jobs
from nightly_memory_ingestion import FULL_REPORT_CASES, SMOKE_REPORT_CASES, target_run_label

from evals.harness.viewer import load_runs, write_viewer
from evals.memory_ingestion.materialize import IngestionReadiness
from evals.registry import TASKS

REASON_LIMIT = 240
NOT_RECORDED = "Not recorded"
MEMORY_STATE_ROOT = Path("state")
MEMORY_PRODUCER_STATE_ROOT = Path("producer-state")
# A fully excluded case the cohort requires, on every arm of a full sweep.
EXPECTED_FULL_EXCLUSIONS = frozenset(
    {
        ("skill_loading_member", "bias-ach-explainer-block-off"),
        ("skill_loading_member", "bias-deploy-pdf-merge-block-off"),
    }
)
# A fully excluded case the cohort accepts without requiring it. A document-read page excludes
# only when the sweep's own shape refused the render — the stack boots the egress proxy with no
# `UFO_EGRESS_PREVIEW_DAEMON`, so the preview host is never admitted — and the target model asked
# for that page. A model that answers the question from another tool leaves the case scored, which
# is a finding the suite records, not a night without a trend point.
TOLERATED_FULL_EXCLUSIONS = frozenset(
    {
        ("document_read", "docx-second-page"),
        ("document_read", "xlsx-second-printed-page"),
    }
)
type PlannedRun = tuple[str, str, str | None]
type RecordedRun = tuple[str, str, str]


def _planned_runs(smoke: bool, memory_ingestion: bool) -> tuple[PlannedRun, ...]:
    planned = tuple(
        (job.shard.label, suite, job.expected_model)
        for job in sweep_jobs(smoke)
        for suite in job.shard.suites
    )
    if memory_ingestion:
        cases = SMOKE_REPORT_CASES if smoke else FULL_REPORT_CASES
        planned += tuple(
            (target_run_label(model), name, model.id) for model in NIGHTLY_MODELS for name in cases
        )
    return planned


def _matches(planned: PlannedRun, recorded: RecordedRun) -> bool:
    return planned[:2] == recorded[:2] and (planned[2] is None or planned[2] == recorded[2])


def _sweep_exclusions(
    planned_runs: tuple[PlannedRun, ...], cases: frozenset[tuple[str, str]], smoke: bool
) -> tuple[PlannedRun, ...]:
    """Each planned run's copy of the fixed-case exclusions, and none for the proving subset."""
    if smoke:
        return ()
    return tuple(
        (suite, case, model)
        for _, suite, model in planned_runs
        for listed_suite, case in cases
        if suite == listed_suite
    )


def _format_run(run: PlannedRun | RecordedRun) -> str:
    label, suite, model = run
    return f"{label}/{suite} ({model or 'fixed agent model'})"


def _memory_readiness_errors(root: Path, smoke: bool) -> tuple[str, ...]:
    reports = SMOKE_REPORT_CASES if smoke else FULL_REPORT_CASES
    missing = []
    mismatched = []
    for report in reports:
        producer_path = root / MEMORY_PRODUCER_STATE_ROOT / report / "readiness.json"
        if not producer_path.is_file():
            missing.append(f"producer/{report}")
            continue
        producer = IngestionReadiness.model_validate_json(producer_path.read_bytes())
        for model in NIGHTLY_MODELS:
            path = root / MEMORY_STATE_ROOT / model.label / report / "readiness.json"
            if not path.is_file():
                missing.append(f"{report} ({model.id})")
                continue
            readiness = IngestionReadiness.model_validate_json(path.read_bytes())
            if (
                readiness.snapshot_digest,
                readiness.derived_corpus_digest,
            ) != (
                producer.snapshot_digest,
                producer.derived_corpus_digest,
            ):
                mismatched.append(
                    f"{report} ({model.id}) {readiness.snapshot_digest}/"
                    f"{readiness.derived_corpus_digest} != producer {producer.snapshot_digest}/"
                    f"{producer.derived_corpus_digest}"
                )
    errors = []
    if missing:
        errors.append("missing memory ingestion readiness: " + ", ".join(missing))
    if mismatched:
        errors.append("memory ingestion producer/target mismatch: " + "; ".join(mismatched))
    return tuple(errors)


def render(root: Path, smoke: bool, memory_ingestion: bool = False) -> str:
    planned = _planned_runs(smoke, memory_ingestion)
    runs = load_runs(root)
    reports = tuple((run.label, report) for run in runs for report in run.reports)
    scored = sum(len(report.scored) for _, report in reports)
    passed = sum(1 for _, report in reports for case in report.scored if case.passed)
    excluded = sum(report.excluded_count for _, report in reports)
    recorded = tuple(
        (label, report.name, report.target_model or NOT_RECORDED) for label, report in reports
    )
    missing = tuple(item for item in planned if not any(_matches(item, row) for row in recorded))
    lines = [
        f"# Nightly evals — {passed}/{scored} cases passed",
        "",
        f"{len(reports)} of {len(planned)} planned suite runs recorded across "
        f"{len(runs)} {'shard' if len(runs) == 1 else 'shards'}; {excluded} cases excluded.",
        "",
    ]
    if missing:
        lines += [
            f"**{len(missing)} planned "
            f"{'suite run' if len(missing) == 1 else 'suite runs'} produced no report:** "
            f"{', '.join(_format_run(item) for item in missing)}",
            "",
        ]
    lines += [
        "| Suite | Shard | Target Model | Passed | Rate |",
        "| --- | --- | --- | --- | --- |",
    ]
    for label, report in sorted(reports, key=lambda pair: (pair[1].pass_rate, pair[1].name)):
        mark = "" if report.passed else " ⚠️"
        lines.append(
            f"| {report.name}{mark} | {label} | {report.target_model or NOT_RECORDED} | "
            f"{sum(1 for case in report.scored if case.passed)}/{len(report.scored)} | "
            f"{report.pass_rate:.0%} |"
        )
    failures = tuple(
        (report.name, report.target_model, case)
        for _, report in reports
        for case in report.scored
        if not case.passed
    )
    if failures:
        lines += ["", f"## {len(failures)} failed cases", ""]
        lines += [
            f"- `{suite}` / `{model or NOT_RECORDED}` / `{case.name}` — "
            f"{' '.join(case.reason.split())[:REASON_LIMIT]}"
            for suite, model, case in failures
        ]
    provider_faults = tuple(
        (report.name, report.target_model, case)
        for _, report in reports
        for case in report.cases
        if case.excluded and case.provider_fault
    )
    if provider_faults:
        lines += ["", f"## {len(provider_faults)} cases excluded on provider faults", ""]
        lines += [
            f"- `{suite}` / `{model or NOT_RECORDED}` / `{case.name}` — "
            f"{' '.join(case.reason.split())[:REASON_LIMIT]}"
            for suite, model, case in provider_faults
        ]
    return "\n".join(lines) + "\n"


def require_comparable(root: Path, smoke: bool, memory_ingestion: bool = False) -> None:
    """Require one complete fixed case cohort before the sweep becomes a trend point.

    An exclusion the record attributes to the provider — a timeout, an overload, a 429 — is the
    night's weather, not cohort drift: it lands on whichever case the provider dropped, so listing
    it ahead of time is impossible and refusing it costs the sweep its trend point over a fault the
    harness already decided not to score. `render` names those cases in the summary instead. Every
    other exclusion still has to be one this file lists.
    """
    planned_runs = _planned_runs(smoke, memory_ingestion)
    planned = tuple(suite for _, suite, _ in planned_runs)
    expected_counts = {task.name: len(task.cases) for task in TASKS if task.name in set(planned)}
    if memory_ingestion:
        memory_counts = SMOKE_REPORT_CASES if smoke else FULL_REPORT_CASES
        expected_counts.update(memory_counts)
    labelled_reports = tuple(
        (run.label, report) for run in load_runs(root) for report in run.reports
    )
    reports = tuple(report for _, report in labelled_reports)
    recorded_runs = [
        (label, report.name, report.target_model or NOT_RECORDED)
        for label, report in labelled_reports
    ]
    missing = tuple(
        item for item in planned_runs if not any(_matches(item, row) for row in recorded_runs)
    )
    unexpected = sorted(
        {row for row in recorded_runs if not any(_matches(item, row) for item in planned_runs)}
    )
    repeated = tuple(
        item for item in planned_runs if sum(_matches(item, row) for row in recorded_runs) > 1
    )
    wrong_counts = sorted(
        f"{report.name} ({report.target_model or NOT_RECORDED}) "
        f"{len(report.cases)}/{expected_counts[report.name]}"
        for report in reports
        if report.name in expected_counts and len(report.cases) != expected_counts[report.name]
    )
    excluded = tuple(
        (report.name, case.name, report.target_model or NOT_RECORDED)
        for report in reports
        for case in report.cases
        if case.excluded
    )
    provider_faults = frozenset(
        (report.name, case.name, report.target_model or NOT_RECORDED)
        for report in reports
        for case in report.cases
        if case.excluded and case.provider_fault
    )
    expected_exclusions = _sweep_exclusions(planned_runs, EXPECTED_FULL_EXCLUSIONS, smoke)
    accepted_exclusions = (
        *expected_exclusions,
        *_sweep_exclusions(planned_runs, TOLERATED_FULL_EXCLUSIONS, smoke),
    )
    unexpected_exclusions = sorted(
        {
            row
            for row in excluded
            if row not in provider_faults
            and not any(_matches(item, row) for item in accepted_exclusions)
        }
    )
    absent_exclusions = tuple(
        item for item in expected_exclusions if not any(_matches(item, row) for row in excluded)
    )
    errors = []
    if missing:
        errors.append("missing reports: " + ", ".join(_format_run(item) for item in missing))
    if unexpected:
        errors.append("unexpected reports: " + ", ".join(_format_run(item) for item in unexpected))
    if repeated:
        errors.append("repeated reports: " + ", ".join(_format_run(item) for item in repeated))
    if wrong_counts:
        errors.append(f"wrong case counts: {', '.join(wrong_counts)}")
    if unexpected_exclusions:
        errors.append(
            "unexpected exclusions: "
            + ", ".join(f"{suite}/{case} ({model})" for suite, case, model in unexpected_exclusions)
        )
    if absent_exclusions:
        errors.append(
            "expected exclusions absent: "
            + ", ".join(f"{suite}/{case} ({model})" for suite, case, model in absent_exclusions)
        )
    if memory_ingestion:
        errors.extend(_memory_readiness_errors(root, smoke))
    if errors:
        raise RuntimeError("incomplete fixed-case cohort — " + "; ".join(errors))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="eval_sweep_summary.py")
    parser.add_argument("root", type=Path, help="the eval-reports archive the sweep wrote")
    parser.add_argument(
        "--smoke",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="the sweep ran the proving subset",
    )
    parser.add_argument("--memory-ingestion", action="store_true")
    parser.add_argument("--require-comparable", action="store_true")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.require_comparable:
        require_comparable(args.root, args.smoke, args.memory_ingestion)
        return
    sys.stdout.write(render(args.root, args.smoke, args.memory_ingestion))
    write_viewer(args.root, load_runs(args.root))


if __name__ == "__main__":
    main()
