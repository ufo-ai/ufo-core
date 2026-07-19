"""WANDR selection and snapshot integrity: deterministic sampling, disjoint subsets, upstream
digest pinning, and load-time refusal of drifted content."""

from pathlib import Path

import pytest
from wandr_corpus import (
    TEST_REVISION,
    TEST_SEED,
    fabricate_selection,
    fabricate_upstream,
    materialized_snapshot,
)

from evals.wandr.models import PER_DIFFICULTY, TIER_BY_DIFFICULTY
from evals.wandr.selection import SelectionBuilder
from evals.wandr.snapshot import load_snapshot


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    return fabricate_upstream(tmp_path / "upstream")


def test_selection_is_deterministic_and_disjoint(upstream: Path) -> None:
    first = fabricate_selection(upstream)
    second = fabricate_selection(upstream)
    assert first == second
    hillclimb = {case.name for case in first.subset_cases("hillclimb")}
    holdout = {case.name for case in first.subset_cases("holdout")}
    assert not hillclimb & holdout
    for subset in (hillclimb, holdout):
        assert len(subset) == PER_DIFFICULTY * len(TIER_BY_DIFFICULTY)


def test_selection_seed_changes_sample(upstream: Path) -> None:
    baseline = fabricate_selection(upstream)
    reseeded = SelectionBuilder(upstream, TEST_REVISION, f"{TEST_SEED}-2").build()
    baseline_hillclimb = {case.name for case in baseline.subset_cases("hillclimb")}
    reseeded_hillclimb = {case.name for case in reseeded.subset_cases("hillclimb")}
    assert baseline_hillclimb != reseeded_hillclimb


def test_selection_rejects_upstream_digest_drift(upstream: Path) -> None:
    sampled = fabricate_selection(upstream).subset_cases("hillclimb")[0]
    victim = upstream / "datasets" / "wandr" / sampled.name / "instruction.md"
    victim.write_text("tampered\n")
    with pytest.raises(ValueError, match="does not match the upstream manifest"):
        fabricate_selection(upstream)


def test_snapshot_round_trip_and_idempotence(upstream: Path, tmp_path: Path) -> None:
    selection = fabricate_selection(upstream)
    first = materialized_snapshot(upstream, tmp_path / "snapshots", selection)
    second = materialized_snapshot(upstream, tmp_path / "snapshots", selection)
    assert first == second
    snapshot = load_snapshot(first, selection)
    assert snapshot.manifest.selection == selection
    assert first.name == snapshot.manifest.digest.removeprefix("sha256:")


def test_snapshot_load_rejects_tampered_task(upstream: Path, tmp_path: Path) -> None:
    selection = fabricate_selection(upstream)
    root = materialized_snapshot(upstream, tmp_path / "snapshots", selection)
    case = selection.cases[0]
    (root / "tasks" / case.name / "instruction.md").write_text("tampered\n")
    with pytest.raises(ValueError, match="does not match its pinned digest"):
        load_snapshot(root, selection)


def test_snapshot_load_rejects_foreign_selection(upstream: Path, tmp_path: Path) -> None:
    selection = fabricate_selection(upstream)
    root = materialized_snapshot(upstream, tmp_path / "snapshots", selection)
    foreign = SelectionBuilder(upstream, TEST_REVISION, f"{TEST_SEED}-2").build()
    with pytest.raises(ValueError, match="checked-in selection"):
        load_snapshot(root, foreign)
