"""Prepare and verify the nightly memory-ingestion corpus run."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import tomli_w

from evals.harness.viewer import load_runs
from evals.memory_100.build import SELECTION_FILE, Selection
from evals.memory_ingestion.build import LOCOMO_CATEGORIES, LOCOMO_COUNTS, MemoryIngestionBuilder
from evals.memory_ingestion.materialize import DERIVATION_MODEL, IngestionReadiness

LABEL = "memory-ingestion"
DATABASE_URL = "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo"
SMOKE_CASES = (
    "longmem/118b2229",
    "locomo/conv-26/120",
    "locomo/conv-26/146",
    "longmem/9aaed6a3",
    "locomo/conv-41/006",
    "locomo/conv-41/021",
    "longmem/0bb5a684",
    "locomo/conv-26/062",
    "locomo/conv-30/030",
)
LONGMEM_SELECTION = Selection.model_validate_json(SELECTION_FILE.read_bytes()).longmem.model_dump()
FULL_REPORT_CASES = {
    **{
        f"memory_ingestion.longmem.{category}": len(case_ids)
        for category, case_ids in LONGMEM_SELECTION.items()
    },
    **{
        f"memory_ingestion.locomo.{LOCOMO_CATEGORIES[category]}": count
        for category, count in LOCOMO_COUNTS.items()
    },
}
SMOKE_REPORT_CASES = {
    "memory_ingestion.locomo.information_extraction": 2,
    "memory_ingestion.locomo.multi_hop": 2,
    "memory_ingestion.locomo.temporal_reasoning": 2,
    "memory_ingestion.longmem.information_extraction": 1,
    "memory_ingestion.longmem.multi_session": 1,
    "memory_ingestion.longmem.temporal_reasoning": 1,
}


def write_inputs(root: Path, snapshot: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    config = root / "ufo.toml"
    config.write_text(
        tomli_w.dumps(
            {
                "database": {"url": DATABASE_URL},
                "blob": {"backend": "filesystem", "root": "./blobs"},
                "models": {"background_jobs_model": DERIVATION_MODEL},
                "pack": {"name": "assistant"},
                "research": {"search_provider": "perplexity"},
                "o11y": {"otlp_endpoint": "http://127.0.0.1:4318"},
            }
        )
    )
    (root / "matrix.toml").write_text(
        tomli_w.dumps(
            {
                "run": [
                    {
                        "label": LABEL,
                        "config": str(config.resolve()),
                        "memory_ingestion": str(snapshot.resolve()),
                    }
                ]
            }
        )
    )


def prepare(longmem: Path, locomo: Path, root: Path, smoke: bool) -> None:
    snapshot = root / "snapshot"
    MemoryIngestionBuilder(longmem, locomo, snapshot).run(SMOKE_CASES if smoke else ())
    write_inputs(root, snapshot)


def verify(reports: Path, runs_root: Path, state_output: Path, smoke: bool) -> None:
    runs = load_runs(reports)
    if len(runs) != 1 or runs[0].label != LABEL:
        raise RuntimeError(f"expected one {LABEL!r} run, found {[run.label for run in runs]}")
    expected = SMOKE_REPORT_CASES if smoke else FULL_REPORT_CASES
    found = {report.name: len(report.cases) for report in runs[0].reports}
    if found != expected:
        raise RuntimeError(
            f"memory ingestion report cases differ: expected {expected}, found {found}"
        )
    excluded = [case.name for report in runs[0].reports for case in report.cases if case.excluded]
    if excluded:
        raise RuntimeError(f"memory ingestion excluded cases: {', '.join(excluded)}")
    states = tuple(runs_root.glob(f"*/{LABEL}/state/*/readiness.json"))
    if len(states) != 1:
        raise RuntimeError(f"expected one memory ingestion readiness file, found {len(states)}")
    readiness = IngestionReadiness.model_validate_json(states[0].read_bytes())
    if readiness.derivation_model != DERIVATION_MODEL:
        raise RuntimeError(
            f"memory ingestion used {readiness.derivation_model!r}, expected {DERIVATION_MODEL!r}"
        )
    state_output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(states[0], state_output)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="nightly_memory_ingestion.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--longmem", type=Path, required=True)
    prepare_parser.add_argument("--locomo", type=Path, required=True)
    prepare_parser.add_argument("--dir", type=Path, required=True)
    prepare_parser.add_argument("--smoke", action=argparse.BooleanOptionalAction, default=False)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--reports", type=Path, required=True)
    verify_parser.add_argument("--runs-root", type=Path, required=True)
    verify_parser.add_argument("--state-output", type=Path, required=True)
    verify_parser.add_argument("--smoke", action=argparse.BooleanOptionalAction, default=False)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        prepare(args.longmem, args.locomo, args.dir, args.smoke)
        return
    verify(args.reports, args.runs_root, args.state_output, args.smoke)


if __name__ == "__main__":
    main()
