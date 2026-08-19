"""Render one sweep's scores as the Datadog series payload that carries the trend.

A suite's score is comparable only while its `digest` holds — the digest covers the cases, the
judge model, and its reasoning settings, so a rate that moves after the cases change compares two
different tests. The digest is therefore not a tag, which would leak cardinality every time a suite
changes; it rides the sweep's own event, where a reader sees where a line must break.

Counts, not rates: averaging 40 per-suite rates weights a one-case suite like a sixty-nine-case one,
so the sweep submits what it counted and the query does the arithmetic.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from evals.harness.viewer import load_runs

GAUGE = 3
PASSED_METRIC = "ufo.evals.cases_passed"
SCORED_METRIC = "ufo.evals.cases_scored"


def series(root: Path, mode: str, timestamp: int) -> dict:
    runs = load_runs(root)
    points = []
    for run in runs:
        for report in run.reports:
            tags = [f"suite:{report.name}", f"shard:{run.label}", f"mode:{mode}"]
            passed = sum(1 for case in report.scored if case.passed)
            for metric, value in ((PASSED_METRIC, passed), (SCORED_METRIC, len(report.scored))):
                points.append(
                    {
                        "metric": metric,
                        "type": GAUGE,
                        "tags": tags,
                        "points": [{"timestamp": timestamp, "value": value}],
                    }
                )
    if not points:
        raise SystemExit(f"no run record under {root} — the sweep recorded no score to submit")
    return {"series": points}


def digest_event(root: Path, mode: str, run_url: str) -> dict:
    """The marker a trend graph breaks its line on: every suite's digest as the sweep saw it."""
    digests = sorted(
        {(report.name, report.digest) for run in load_runs(root) for report in run.reports}
    )
    return {
        "title": f"ufo evals {mode} suite digests",
        "text": "\n".join(f"{name} {digest}" for name, digest in digests) + f"\n{run_url}",
        "tags": [f"mode:{mode}"],
        "source_type_name": "github",
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="eval_sweep_metrics.py")
    parser.add_argument("root", type=Path, help="the eval-reports archive the sweep wrote")
    parser.add_argument("--mode", required=True, choices=("smoke", "sweep"))
    parser.add_argument("--timestamp", type=int, required=True, help="unix seconds for the point")
    parser.add_argument("--run-url", default="", help="the workflow run the event points at")
    parser.add_argument("--event", action="store_true", help="print the digest event instead")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.event:
        print(json.dumps(digest_event(args.root, args.mode, args.run_url)))
        return
    print(json.dumps(series(args.root, args.mode, args.timestamp)))


if __name__ == "__main__":
    main()
