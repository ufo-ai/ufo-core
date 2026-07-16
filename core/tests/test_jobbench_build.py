"""JobBench snapshot building against the synthetic pinned-shape corpus: split-qualified case
identity, URL/path agreement, rubric parsing, selection modes, and the content-addressed
write/load round trip with asset materialization."""

from pathlib import Path

import pytest
from jobbench_corpus import (
    asset_tree,
    corpus_builder,
    corpus_cases,
    corpus_row,
)

from evals.jobbench.build import _rubric_item
from evals.jobbench.snapshot import JOBBENCH_UPSTREAM, load_snapshot, write_snapshot


def test_case_from_row_maps_identity_and_rubric(tmp_path: Path) -> None:
    builder = corpus_builder(tmp_path)
    case = builder._case_from_row("easy", corpus_row("easy", "occupation_00", 1))
    assert case.case_id == "easy.occupation_00__task1"
    assert case.split == "easy"
    assert [asset.relative_path for asset in case.references] == [
        "dataset_easy/occupation_00/task1/task_folder/data file.csv",
        "dataset_easy/occupation_00/task1/task_folder/notes.txt",
    ]
    assert case.rubric[0].weight == 10
    assert case.rubric[0].criteria == ("states the adjusted balance", "flags the open item")


def test_case_from_row_rejects_url_drift(tmp_path: Path) -> None:
    builder = corpus_builder(tmp_path)
    row = corpus_row("easy", "occupation_00", 1)
    drifted = row.model_copy(
        update={"reference_file_urls": tuple(url + "?x" for url in row.reference_file_urls)}
    )
    with pytest.raises(ValueError, match="does not match its path"):
        builder._case_from_row("easy", drifted)


def test_case_from_row_rejects_foreign_task_folder(tmp_path: Path) -> None:
    builder = corpus_builder(tmp_path)
    row = corpus_row("easy", "occupation_00", 1)
    foreign = "dataset_easy/occupation_01/task1/task_folder/data.csv"
    drifted = row.model_copy(
        update={
            "reference_files": (foreign,),
            "reference_file_urls": (
                f"https://huggingface.co/datasets/JobBench/job-bench/resolve/main/{foreign}",
            ),
            "reference_file_hf_uris": (f"hf://datasets/JobBench/job-bench@main/{foreign}",),
        }
    )
    with pytest.raises(ValueError, match="outside its task folder"):
        builder._case_from_row("easy", drifted)


def test_rubric_item_rejects_unexpected_shapes() -> None:
    with pytest.raises(ValueError, match="unexpected shape"):
        _rubric_item("occupation_00__task1", {"rubric": "r", "weight": 5})
    with pytest.raises(ValueError, match="not a list"):
        _rubric_item("occupation_00__task1", {"rubric": "r", "weight": 5, "criterion": "one"})


def test_selection_modes_are_exclusive(tmp_path: Path) -> None:
    builder = corpus_builder(tmp_path, all_assets=True, smoke_count=1)
    with pytest.raises(ValueError, match="exactly one"):
        builder._selected_case_ids(corpus_cases(builder))


def test_smoke_selection_takes_easy_cases(tmp_path: Path) -> None:
    builder = corpus_builder(tmp_path, all_assets=False, smoke_count=2)
    selected = builder._selected_case_ids(corpus_cases(builder))
    assert selected == {"easy.occupation_00__task1", "easy.occupation_00__task2"}


def test_unknown_case_selection_fails(tmp_path: Path) -> None:
    builder = corpus_builder(
        tmp_path, all_assets=False, asset_case_ids=frozenset(("easy.missing__task1",))
    )
    with pytest.raises(ValueError, match="unknown JobBench case ids"):
        builder._selected_case_ids(corpus_cases(builder))


def test_snapshot_round_trip_is_content_addressed(tmp_path: Path) -> None:
    builder = corpus_builder(tmp_path)
    cases = corpus_cases(builder)
    selected = frozenset(("easy.occupation_00__task1",))
    with_assets = corpus_builder(tmp_path, all_assets=False, asset_root=asset_tree(tmp_path, cases))
    output_root = tmp_path / "snapshots"

    def _write(staging_name: str) -> Path:
        staging = output_root / staging_name
        staging.mkdir(parents=True)
        materialized = with_assets._materialize_assets(staging, cases, selected)
        return write_snapshot(
            staging, output_root, JOBBENCH_UPSTREAM, materialized, tuple(selected)
        )

    first = _write(".stage-one")
    second = _write(".stage-two")
    assert first == second
    snapshot = load_snapshot(first, JOBBENCH_UPSTREAM)
    assert snapshot.manifest.materialized_case_ids == tuple(selected)
    materialized_case = next(
        case for case in snapshot.cases if case.case_id == "easy.occupation_00__task1"
    )
    for asset in materialized_case.references:
        assert asset.snapshot_path is not None
        body = (first / asset.snapshot_path).read_bytes()
        assert body == f"body of {asset.relative_path}".encode()
    untouched = next(case for case in snapshot.cases if case.case_id == "main.occupation_00__task1")
    assert all(asset.snapshot_path is None for asset in untouched.references)


def test_snapshot_rejects_materialization_drift(tmp_path: Path) -> None:
    builder = corpus_builder(tmp_path)
    cases = corpus_cases(builder)
    staging = tmp_path / "snapshots" / ".stage"
    staging.mkdir(parents=True)
    with pytest.raises(ValueError, match="materialization state"):
        write_snapshot(
            staging,
            tmp_path / "snapshots",
            JOBBENCH_UPSTREAM,
            cases,
            ("easy.occupation_00__task1",),
        )
