"""A synthetic JobBench corpus with the pinned shape — 65 main + 63 easy cases across 35
occupations — for exercising the snapshot builder and runner without upstream bytes."""

from pathlib import Path

from evals.jobbench.build import SnapshotBuilder, UpstreamRow
from evals.jobbench.models import SnapshotCase
from evals.jobbench.snapshot import JOBBENCH_UPSTREAM, SPLIT_ROOTS, write_snapshot

OCCUPATIONS = tuple(f"occupation_{index:02d}" for index in range(35))
SMOKE_CASE = "easy.occupation_00__task1"


def corpus_builder(tmp_path: Path, **overrides: object) -> SnapshotBuilder:
    fields: dict = {
        "tasks_parquet": tmp_path / "tasks.parquet",
        "easy_parquet": tmp_path / "easy.parquet",
        "output_root": tmp_path / "snapshots",
        "upstream": JOBBENCH_UPSTREAM,
        "all_assets": True,
    }
    fields.update(overrides)
    return SnapshotBuilder(**fields)


def corpus_row(split: str, occupation: str, task_num: int) -> UpstreamRow:
    prefix = f"{SPLIT_ROOTS[split]}/{occupation}/task{task_num}/task_folder"
    paths = (f"{prefix}/data file.csv", f"{prefix}/notes.txt")
    return UpstreamRow(
        task_id=f"{occupation}__task{task_num}",
        occupation=occupation,
        task_num=task_num,
        prompt=f"Reconcile the {occupation} records.",
        reference_files=paths,
        reference_file_urls=tuple(
            "https://huggingface.co/datasets/JobBench/job-bench/resolve/main/"
            + path.replace(" ", "%20")
            for path in paths
        ),
        reference_file_hf_uris=tuple(
            f"hf://datasets/JobBench/job-bench@main/{path}" for path in paths
        ),
        rubric_json=(
            '{"rubrics": [{"rubric": "Does the schedule tie out?", "weight": 10,'
            ' "criterion": ["states the adjusted balance", "flags the open item"]}]}'
        ),
        task_card=f"# {occupation} task {task_num}",
    )


def corpus_cases(builder: SnapshotBuilder) -> tuple[SnapshotCase, ...]:
    return (
        *(builder._case_from_row("main", row) for row in _split_rows("main", 65)),
        *(builder._case_from_row("easy", row) for row in _split_rows("easy", 63)),
    )


def _split_rows(split: str, count: int) -> tuple[UpstreamRow, ...]:
    doubled = count - len(OCCUPATIONS)
    rows = []
    for index, occupation in enumerate(OCCUPATIONS):
        rows.append(corpus_row(split, occupation, 1))
        if index < doubled:
            rows.append(corpus_row(split, occupation, 2))
    return tuple(rows)


def asset_tree(tmp_path: Path, cases: tuple[SnapshotCase, ...]) -> Path:
    root = tmp_path / "upstream"
    for case in cases:
        for asset in case.references:
            target = root / asset.relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f"body of {asset.relative_path}".encode())
    return root


def materialized_snapshot(
    tmp_path: Path, selected: frozenset[str] = frozenset((SMOKE_CASE,))
) -> Path:
    builder = corpus_builder(tmp_path)
    cases = corpus_cases(builder)
    root = asset_tree(tmp_path, cases)
    with_assets = corpus_builder(tmp_path, all_assets=False, asset_root=root)
    staging = tmp_path / "snapshots" / ".stage"
    staging.mkdir(parents=True)
    materialized = with_assets._materialize_assets(staging, cases, selected)
    return write_snapshot(
        staging, tmp_path / "snapshots", JOBBENCH_UPSTREAM, materialized, tuple(sorted(selected))
    )
