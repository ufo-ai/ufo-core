"""Prepare and verify the nightly memory-ingestion corpus run."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import tomli_w

from evals.harness.viewer import load_runs
from evals.memory_100.build import SELECTION_FILE, Selection
from evals.memory_ingestion.build import LOCOMO_CATEGORIES, LOCOMO_COUNTS, MemoryIngestionBuilder
from evals.memory_ingestion.materialize import DERIVATION_MODEL, IngestionReadiness
from evals.memory_ingestion.models import Corpus
from ufo.config import DEFAULT_AUTO_MODEL
from ufo.schema.records import DEFAULT_REASONING_EFFORT, ReasoningEffort

LABEL = "memory-ingestion"
DATABASE_URL = "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo"
SMOKE_SAMPLES = 3
SMOKE_CASES = (
    "longmem/118b2229",
    "locomo/conv-26/120",
    "longmem/a96c20ee",
    "locomo/conv-41/021",
    "longmem/0bb5a684",
    "locomo/conv-26/062",
    "locomo/conv-42/037",
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
    "memory_ingestion.locomo.information_extraction": 1,
    "memory_ingestion.locomo.multi_hop": 1,
    "memory_ingestion.locomo.temporal_reasoning": 2,
    "memory_ingestion.longmem.information_extraction": 1,
    "memory_ingestion.longmem.multi_session": 1,
    "memory_ingestion.longmem.temporal_reasoning": 1,
}


@dataclass(frozen=True)
class IngestionShard:
    report: str
    artifact: str
    corpus: Corpus
    category: str
    cases: int


def ingestion_shards(smoke: bool) -> tuple[IngestionShard, ...]:
    """Return the independently materialized report shards for one memory-ingestion run."""
    reports = SMOKE_REPORT_CASES if smoke else FULL_REPORT_CASES
    shards = []
    for report, cases in sorted(reports.items()):
        prefix, corpus_name, category = report.split(".", maxsplit=2)
        if prefix != "memory_ingestion" or corpus_name not in ("longmem", "locomo"):
            raise ValueError(f"invalid memory ingestion report {report!r}")
        shards.append(
            IngestionShard(
                report=report,
                artifact=f"{corpus_name}-{category.replace('_', '-')}",
                corpus=corpus_name,
                category=category,
                cases=cases,
            )
        )
    return tuple(shards)


def ingestion_shard(report: str, smoke: bool) -> IngestionShard:
    """Resolve one report from the selected nightly cohort."""
    found = tuple(shard for shard in ingestion_shards(smoke) if shard.report == report)
    if len(found) != 1:
        raise ValueError(f"memory ingestion report {report!r} is not in this cohort")
    return found[0]


def write_inputs(
    root: Path,
    snapshot: Path,
    model: str,
    reasoning: ReasoningEffort,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    config = root / "ufo.toml"
    config.write_text(
        tomli_w.dumps(
            {
                "database": {"url": DATABASE_URL},
                "blob": {"backend": "filesystem", "root": "./blobs"},
                "models": {
                    "auto_model": model,
                    "background_jobs_model": DERIVATION_MODEL,
                },
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
                        **(
                            {}
                            if reasoning == DEFAULT_REASONING_EFFORT
                            else {"reasoning": reasoning}
                        ),
                    }
                ]
            }
        )
    )


def prepare(
    longmem: Path,
    locomo: Path,
    root: Path,
    smoke: bool,
    model: str,
    reasoning: ReasoningEffort,
    report: str,
) -> None:
    shard = ingestion_shard(report, smoke)
    snapshot = root / "snapshot"
    MemoryIngestionBuilder(longmem, locomo, snapshot).run(
        SMOKE_CASES if smoke else (),
        samples=SMOKE_SAMPLES if smoke else 1,
        report=(shard.corpus, shard.category),
    )
    write_inputs(root, snapshot, model, reasoning)


def verify(
    reports: Path,
    runs_root: Path,
    state_output: Path,
    smoke: bool,
    model: str,
    report: str,
) -> None:
    shard = ingestion_shard(report, smoke)
    runs = load_runs(reports)
    if len(runs) != 1 or runs[0].label != LABEL:
        raise RuntimeError(f"expected one {LABEL!r} run, found {[run.label for run in runs]}")
    target_models = {report.target_model for report in runs[0].reports}
    if target_models != {model}:
        raise RuntimeError(
            f"memory ingestion recall used {sorted(str(item) for item in target_models)}, "
            f"expected {model!r}"
        )
    expected = {report: shard.cases}
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
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--smoke", action=argparse.BooleanOptionalAction, default=False)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--longmem", type=Path, required=True)
    prepare_parser.add_argument("--locomo", type=Path, required=True)
    prepare_parser.add_argument("--dir", type=Path, required=True)
    prepare_parser.add_argument("--smoke", action=argparse.BooleanOptionalAction, default=False)
    prepare_parser.add_argument("--model", default=DEFAULT_AUTO_MODEL)
    prepare_parser.add_argument("--report", required=True)
    prepare_parser.add_argument(
        "--reasoning",
        choices=("auto", "off", "low", "medium", "high"),
        default=DEFAULT_REASONING_EFFORT,
    )
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--reports", type=Path, required=True)
    verify_parser.add_argument("--runs-root", type=Path, required=True)
    verify_parser.add_argument("--state-output", type=Path, required=True)
    verify_parser.add_argument("--smoke", action=argparse.BooleanOptionalAction, default=False)
    verify_parser.add_argument("--model", default=DEFAULT_AUTO_MODEL)
    verify_parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    if args.command == "plan":
        print(
            json.dumps(
                [
                    {"report": shard.report, "artifact": shard.artifact}
                    for shard in ingestion_shards(args.smoke)
                ]
            )
        )
        return
    if args.command == "prepare":
        prepare(
            args.longmem,
            args.locomo,
            args.dir,
            args.smoke,
            args.model,
            args.reasoning,
            args.report,
        )
        return
    verify(args.reports, args.runs_root, args.state_output, args.smoke, args.model, args.report)


if __name__ == "__main__":
    main()
