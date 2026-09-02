"""Prepare and verify the nightly memory-ingestion corpus run."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import tomli_w
from nightly_eval_matrix import NIGHTLY_MODELS, NightlyModel

from evals.harness.viewer import load_runs
from evals.memory_100.build import SELECTION_FILE, Selection
from evals.memory_ingestion.build import LOCOMO_CATEGORIES, LOCOMO_COUNTS, MemoryIngestionBuilder
from evals.memory_ingestion.materialize import (
    DERIVATION_MODEL,
    DerivedCorpus,
    IngestionReadiness,
)
from evals.memory_ingestion.models import Corpus, load_snapshot
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


def target_run_label(model: NightlyModel) -> str:
    """Name one paired target stack within the stack label bound."""
    token = model.id.rsplit("/", maxsplit=1)[-1].replace(".", "-")
    return f"{LABEL}-{token}"


def write_config(root: Path, name: str, model: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    config = root / f"{name}.toml"
    config.write_text(
        tomli_w.dumps(
            {
                "database": {"url": DATABASE_URL},
                "blob": {"backend": "filesystem", "root": str((root / "blobs").resolve())},
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
    return config


def write_inputs(
    root: Path,
    snapshot: Path,
    corpus: Path,
    reasoning: ReasoningEffort,
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    runs = []
    for model in NIGHTLY_MODELS:
        config = write_config(root, f"ufo-{model.label}", model.id)
        runs.append(
            {
                "label": target_run_label(model),
                "config": str(config.resolve()),
                "memory_ingestion": str(snapshot.resolve()),
                "memory_ingestion_corpus": str(corpus.resolve()),
                **({} if reasoning == DEFAULT_REASONING_EFFORT else {"reasoning": reasoning}),
            }
        )
    (root / "matrix.toml").write_text(tomli_w.dumps({"run": runs}))


def build(
    longmem: Path,
    locomo: Path,
    root: Path,
    smoke: bool,
    report: str,
) -> None:
    shard = ingestion_shard(report, smoke)
    snapshot = root / "snapshot"
    MemoryIngestionBuilder(longmem, locomo, snapshot).run(
        SMOKE_CASES if smoke else (),
        samples=SMOKE_SAMPLES if smoke else 1,
        report=(shard.corpus, shard.category),
    )
    write_config(root, "producer", DERIVATION_MODEL)


def verify_producer(
    snapshot_root: Path,
    corpus_path: Path,
    readiness_path: Path,
    state_output: Path,
) -> None:
    snapshot = load_snapshot(snapshot_root)
    corpus = DerivedCorpus.model_validate_json(corpus_path.read_bytes())
    readiness = IngestionReadiness.model_validate_json(readiness_path.read_bytes())
    if corpus.snapshot_digest != snapshot.manifest.digest:
        raise RuntimeError("memory ingestion derived corpus belongs to a different snapshot")
    if readiness.snapshot_digest != snapshot.manifest.digest:
        raise RuntimeError("memory ingestion producer readiness belongs to a different snapshot")
    if readiness.derived_corpus_digest != corpus.digest:
        raise RuntimeError("memory ingestion producer readiness names a different corpus")
    if readiness.derivation_model != DERIVATION_MODEL:
        raise RuntimeError(
            f"memory ingestion used {readiness.derivation_model!r}, expected {DERIVATION_MODEL!r}"
        )
    state_output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(readiness_path, state_output)
    shutil.copyfile(corpus_path, state_output.with_name("derived-corpus.json"))
    covered = sum(bool(item.memory_ids) for item in readiness.evidence)
    print(f"memory ingestion producer evidence coverage {covered}/{len(readiness.evidence)}")


def verify(
    reports: Path,
    runs_root: Path,
    state_root: Path,
    corpus_path: Path,
    smoke: bool,
    report: str,
) -> None:
    shard = ingestion_shard(report, smoke)
    runs = load_runs(reports)
    by_label = {run.label: run for run in runs}
    corpus = DerivedCorpus.model_validate_json(corpus_path.read_bytes())
    expected_labels = {target_run_label(model) for model in NIGHTLY_MODELS}
    if set(by_label) != expected_labels:
        raise RuntimeError(
            f"expected memory ingestion runs {sorted(expected_labels)}, found {sorted(by_label)}"
        )
    for model in NIGHTLY_MODELS:
        label = target_run_label(model)
        run = by_label[label]
        target_models = {result.target_model for result in run.reports}
        if target_models != {model.id}:
            raise RuntimeError(
                f"memory ingestion recall used {sorted(str(item) for item in target_models)}, "
                f"expected {model.id!r}"
            )
        expected = {report: shard.cases}
        found = {result.name: len(result.cases) for result in run.reports}
        if found != expected:
            raise RuntimeError(
                f"memory ingestion report cases differ: expected {expected}, found {found}"
            )
        excluded = [case.name for result in run.reports for case in result.cases if case.excluded]
        if excluded:
            raise RuntimeError(f"memory ingestion excluded cases: {', '.join(excluded)}")
        states = tuple(runs_root.glob(f"*/{label}/state/*/readiness.json"))
        if len(states) != 1:
            raise RuntimeError(f"expected one {label!r} readiness file, found {len(states)}")
        readiness = IngestionReadiness.model_validate_json(states[0].read_bytes())
        if readiness.derived_corpus_digest != corpus.digest:
            raise RuntimeError(f"memory ingestion {model.id!r} consumed a different corpus")
        output = state_root / model.label / report / "readiness.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(states[0], output)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="nightly_memory_ingestion.py")
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--smoke", action=argparse.BooleanOptionalAction, default=False)
    build_parser = subparsers.add_parser("build")
    build_parser.add_argument("--longmem", type=Path, required=True)
    build_parser.add_argument("--locomo", type=Path, required=True)
    build_parser.add_argument("--dir", type=Path, required=True)
    build_parser.add_argument("--smoke", action=argparse.BooleanOptionalAction, default=False)
    build_parser.add_argument("--report", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--snapshot", type=Path, required=True)
    prepare_parser.add_argument("--corpus", type=Path, required=True)
    prepare_parser.add_argument("--dir", type=Path, required=True)
    prepare_parser.add_argument(
        "--reasoning",
        choices=("auto", "off", "low", "medium", "high"),
        default=DEFAULT_REASONING_EFFORT,
    )
    producer_parser = subparsers.add_parser("verify-producer")
    producer_parser.add_argument("--snapshot", type=Path, required=True)
    producer_parser.add_argument("--corpus", type=Path, required=True)
    producer_parser.add_argument("--readiness", type=Path, required=True)
    producer_parser.add_argument("--state-output", type=Path, required=True)
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--reports", type=Path, required=True)
    verify_parser.add_argument("--runs-root", type=Path, required=True)
    verify_parser.add_argument("--state-root", type=Path, required=True)
    verify_parser.add_argument("--corpus", type=Path, required=True)
    verify_parser.add_argument("--smoke", action=argparse.BooleanOptionalAction, default=False)
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
    if args.command == "build":
        build(
            args.longmem,
            args.locomo,
            args.dir,
            args.smoke,
            args.report,
        )
        return
    if args.command == "prepare":
        write_inputs(args.dir, args.snapshot, args.corpus, args.reasoning)
        return
    if args.command == "verify-producer":
        verify_producer(args.snapshot, args.corpus, args.readiness, args.state_output)
        return
    verify(args.reports, args.runs_root, args.state_root, args.corpus, args.smoke, args.report)


if __name__ == "__main__":
    main()
