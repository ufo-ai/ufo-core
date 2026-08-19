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

from nightly_eval_matrix import plan

from evals.harness.viewer import load_runs, write_viewer

REASON_LIMIT = 240


def render(root: Path, smoke: bool) -> str:
    planned = tuple(suite for shard in plan(smoke) for suite in shard.suites)
    runs = load_runs(root)
    reports = tuple((run.label, report) for run in runs for report in run.reports)
    scored = sum(len(report.scored) for _, report in reports)
    passed = sum(1 for _, report in reports for case in report.scored if case.passed)
    excluded = sum(report.excluded_count for _, report in reports)
    recorded = {report.name for _, report in reports}
    missing = tuple(dict.fromkeys(suite for suite in planned if suite not in recorded))
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
            f"{'suite' if len(missing) == 1 else 'suites'} produced no report:** "
            f"{', '.join(missing)}",
            "",
        ]
    lines += ["| Suite | Shard | Passed | Rate |", "| --- | --- | --- | --- |"]
    for label, report in sorted(reports, key=lambda pair: (pair[1].pass_rate, pair[1].name)):
        mark = "" if report.passed else " ⚠️"
        lines.append(
            f"| {report.name}{mark} | {label} | {sum(1 for case in report.scored if case.passed)}"
            f"/{len(report.scored)} | {report.pass_rate:.0%} |"
        )
    failures = tuple(
        (report.name, case) for _, report in reports for case in report.scored if not case.passed
    )
    if failures:
        lines += ["", f"## {len(failures)} failed cases", ""]
        lines += [
            f"- `{suite}` / `{case.name}` — {' '.join(case.reason.split())[:REASON_LIMIT]}"
            for suite, case in failures
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="eval_sweep_summary.py")
    parser.add_argument("root", type=Path, help="the eval-reports archive the sweep wrote")
    parser.add_argument(
        "--smoke",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="the sweep ran the proving subset",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    sys.stdout.write(render(args.root, args.smoke))
    write_viewer(args.root, load_runs(args.root))


if __name__ == "__main__":
    main()
