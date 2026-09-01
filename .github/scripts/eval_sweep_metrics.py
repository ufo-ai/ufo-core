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
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nightly_eval_matrix import NIGHTLY_MODELS

from evals.harness.viewer import load_runs
from evals.memory_ingestion.materialize import IngestionReadiness

GAUGE = 3
PASSED_METRIC = "ufo.evals.cases_passed"
SCORED_METRIC = "ufo.evals.cases_scored"
MEAN_EVIDENCE_METRIC = "ufo.evals.mapped_evidence_coverage_mean"
MIN_EVIDENCE_METRIC = "ufo.evals.mapped_evidence_coverage_min"
PAGE_EVIDENCE_METRIC = "ufo.evals.page_evidence_coverage"
DEGRADED_RECALL_METRIC = "ufo.evals.degraded_recall"
UNMAPPED_EVIDENCE_METRIC = "ufo.evals.unmapped_evidence"
MEMORY_PAGE_METRIC = "ufo.evals.memory_ingestion.pages"
MEMORY_FACT_METRIC = "ufo.evals.memory_ingestion.facts"
MEMORY_CHUNK_METRIC = "ufo.evals.memory_ingestion.chunks"
MEMORY_EVIDENCE_METRIC = "ufo.evals.memory_ingestion.evidence_refs"
MEMORY_EMPTY_EVIDENCE_METRIC = "ufo.evals.memory_ingestion.empty_evidence_refs"
MEMORY_STATE_ROOT = Path("state")


def series(root: Path, mode: str, timestamp: int) -> dict:
    runs = load_runs(root)
    points = []
    for run in runs:
        for report in run.reports:
            tags = [f"suite:{report.name}", f"shard:{run.label}", f"mode:{mode}"]
            if report.target_model is not None:
                tags.append(f"target_model:{report.target_model}")
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
            for metric, value in (
                (MEAN_EVIDENCE_METRIC, report.mean_mapped_evidence_coverage),
                (MIN_EVIDENCE_METRIC, report.min_mapped_evidence_coverage),
                (PAGE_EVIDENCE_METRIC, report.page_evidence_coverage),
                (DEGRADED_RECALL_METRIC, report.degraded_recall_count),
                (UNMAPPED_EVIDENCE_METRIC, report.unmapped_evidence_count),
            ):
                if value is not None:
                    points.append(
                        {
                            "metric": metric,
                            "type": GAUGE,
                            "tags": tags,
                            "points": [{"timestamp": timestamp, "value": value}],
                        }
                    )
    for model in NIGHTLY_MODELS:
        state_root = root / MEMORY_STATE_ROOT / model.label
        for state_path in sorted(state_root.glob("*/readiness.json")):
            readiness = IngestionReadiness.model_validate_json(state_path.read_bytes())
            tags = [
                "suite:memory_ingestion",
                "shard:memory-ingestion",
                f"memory_report:{state_path.parent.name}",
                f"mode:{mode}",
                f"target_model:{model.id}",
            ]
            for metric, value in (
                (MEMORY_PAGE_METRIC, readiness.page_count),
                (MEMORY_FACT_METRIC, readiness.memory_count),
                (MEMORY_CHUNK_METRIC, readiness.chunk_count),
                (MEMORY_EVIDENCE_METRIC, len(readiness.evidence)),
                (
                    MEMORY_EMPTY_EVIDENCE_METRIC,
                    sum(not evidence.memory_ids for evidence in readiness.evidence),
                ),
            ):
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
        {
            (report.name, report.target_model or "unknown", report.digest)
            for run in load_runs(root)
            for report in run.reports
        }
    )
    return {
        "title": f"ufo evals {mode} suite digests",
        "text": "\n".join(f"{name} {model} {digest}" for name, model, digest in digests)
        + f"\n{run_url}",
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
