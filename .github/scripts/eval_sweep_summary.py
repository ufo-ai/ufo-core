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
from nightly_memory_ingestion import FULL_REPORT_CASES, SMOKE_REPORT_CASES

from evals.harness.viewer import load_runs, write_viewer
from evals.memory_ingestion.materialize import IngestionReadiness
from evals.registry import TASKS

REASON_LIMIT = 240
NOT_RECORDED = "Not recorded"
MEMORY_STATE_ROOT = Path("state")
EXPECTED_FULL_EXCLUSIONS = frozenset(
    {
        ("skill_loading_member", "bias-ach-explainer-block-off"),
        ("skill_loading_member", "bias-deploy-pdf-merge-block-off"),
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
            ("memory-ingestion", name, model.id) for model in NIGHTLY_MODELS for name in cases
        )
    return planned


def _matches(planned: PlannedRun, recorded: RecordedRun) -> bool:
    return planned[:2] == recorded[:2] and (planned[2] is None or planned[2] == recorded[2])


def _format_run(run: PlannedRun | RecordedRun) -> str:
    label, suite, model = run
    return f"{label}/{suite} ({model or 'fixed agent model'})"


def _memory_readiness_errors(root: Path, smoke: bool) -> tuple[str, ...]:
    reports = SMOKE_REPORT_CASES if smoke else FULL_REPORT_CASES
    snapshots: dict[str, dict[str, str]] = {report: {} for report in reports}
    missing = []
    for model in NIGHTLY_MODELS:
        for report in reports:
            path = root / MEMORY_STATE_ROOT / model.label / report / "readiness.json"
            if not path.is_file():
                missing.append(f"{report} ({model.id})")
                continue
            readiness = IngestionReadiness.model_validate_json(path.read_bytes())
            snapshots[report][model.id] = readiness.snapshot_digest
    mismatched = [
        f"{report}: " + ", ".join(f"{model} {digest}" for model, digest in sorted(by_model.items()))
        for report, by_model in snapshots.items()
        if len(set(by_model.values())) > 1
    ]
    errors = []
    if missing:
        errors.append("missing memory ingestion readiness: " + ", ".join(missing))
    if mismatched:
        errors.append("memory ingestion snapshot mismatch: " + "; ".join(mismatched))
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
    return "\n".join(lines) + "\n"


def require_comparable(root: Path, smoke: bool, memory_ingestion: bool = False) -> None:
    """Require one complete fixed case cohort before the sweep becomes a trend point."""
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
    expected_exclusions = (
        ()
        if smoke
        else tuple(
            (suite, case, model)
            for _, suite, model in planned_runs
            for expected_suite, case in EXPECTED_FULL_EXCLUSIONS
            if suite == expected_suite
        )
    )
    unexpected_exclusions = sorted(
        {row for row in excluded if not any(_matches(item, row) for item in expected_exclusions)}
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
