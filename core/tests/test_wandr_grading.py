"""Offline WANDR grading against the fabricated verifier: captured submissions score, missing
submissions grade zero through the verifier's own path, verifier faults raise, and the metric
line lands on stdout for the optimizer loop."""

import asyncio
from pathlib import Path

import pytest
from wandr_corpus import (
    CAPTURED_HARD_F1,
    CAPTURED_SOFT_F1,
    fabricate_selection,
    fabricate_upstream,
    materialized_snapshot,
)

from evals.wandr import grading
from evals.wandr.grading import VerifierRun
from evals.wandr.models import Selection
from evals.wandr.snapshot import Snapshot, load_snapshot


@pytest.fixture
def selection(tmp_path: Path) -> Selection:
    return fabricate_selection(fabricate_upstream(tmp_path / "upstream"))


@pytest.fixture
def snapshot(tmp_path: Path, selection: Selection, monkeypatch: pytest.MonkeyPatch) -> Snapshot:
    root = materialized_snapshot(tmp_path / "upstream", tmp_path / "snapshots", selection)
    monkeypatch.setattr("evals.wandr.snapshot.checked_in_selection", lambda: selection)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PERPLEXITY_API_KEY", "test-key")
    return load_snapshot(root, selection)


def _verifier(snapshot: Snapshot, tmp_path: Path) -> VerifierRun:
    return VerifierRun(
        snapshot=snapshot,
        submissions_root=tmp_path / "submissions",
        output_root=tmp_path / "grades",
        venv=tmp_path / "venv",
        timeout_seconds=60.0,
    )


def test_grades_captured_and_missing_submissions(snapshot: Snapshot, tmp_path: Path) -> None:
    selection = snapshot.manifest.selection
    captured, absent = selection.subset_cases("hillclimb")[:2]
    case_dir = tmp_path / "submissions" / captured.name
    case_dir.mkdir(parents=True)
    for name in captured.required_files:
        (case_dir / name).write_bytes(b'{"item": {}}\n')
    report = asyncio.run(_verifier(snapshot, tmp_path).grade((captured, absent)))
    by_name = {grade.name: grade for grade in report.cases}
    assert by_name[captured.name].captured
    assert by_name[captured.name].soft_f1 == CAPTURED_SOFT_F1
    assert by_name[captured.name].hard_f1 == CAPTURED_HARD_F1
    assert not by_name[absent.name].captured
    assert by_name[absent.name].soft_f1 == 0.0
    assert report.mean_soft_f1 == pytest.approx(CAPTURED_SOFT_F1 / 2)
    assert all(grade.tier in (1, 2, 3) for grade in report.cases)


def test_tier_rollups_cover_every_difficulty(snapshot: Snapshot, tmp_path: Path) -> None:
    cases = snapshot.manifest.selection.subset_cases("holdout")
    report = asyncio.run(_verifier(snapshot, tmp_path).grade(cases))
    assert [rollup.tier for rollup in report.tiers] == [1, 2, 3]
    assert all(rollup.cases == 7 for rollup in report.tiers)
    assert all(rollup.mean_soft_f1 == 0.0 for rollup in report.tiers)


def test_verifier_fault_raises(
    snapshot: Snapshot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WANDR_FAKE_FAIL", "1")
    case = snapshot.manifest.selection.subset_cases("hillclimb")[0]
    with pytest.raises(RuntimeError, match="fake verifier exploded"):
        asyncio.run(_verifier(snapshot, tmp_path).grade((case,)))


def test_missing_judge_keys_fail_loud(
    snapshot: Snapshot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PERPLEXITY_API_KEY")
    case = snapshot.manifest.selection.subset_cases("hillclimb")[0]
    with pytest.raises(RuntimeError, match="PERPLEXITY_API_KEY"):
        asyncio.run(_verifier(snapshot, tmp_path).grade((case,)))


def test_main_prints_metric_line(
    snapshot: Snapshot, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    grading.main(
        [
            "--snapshot",
            snapshot.root,
            "--subset",
            "smoke",
            "--submissions",
            str(tmp_path / "submissions"),
            "--out",
            str(tmp_path / "grades"),
            "--venv",
            str(tmp_path / "venv"),
            "--metric-stdout",
        ]
    )
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines[-1] == "soft_f1: 0.0000"
    assert (tmp_path / "grades" / "smoke.json").is_file()
