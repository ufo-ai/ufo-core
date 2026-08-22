import json
from collections import Counter
from pathlib import Path

import pytest

import evals.gdpval_100.selection as gdpval_selection
from evals.gdpval_100.selection import (
    ARCHETYPE_QUOTAS,
    FOLD_QUOTAS,
    SECTOR_QUOTAS,
    Archetype,
    BoundarySelection,
    SelectionInfeasible,
    TaskAnnotation,
    TaskMeasurement,
    main,
    select_boundary_tasks,
)

ARCHETYPE_OCCUPATIONS: dict[Archetype, tuple[int, int]] = {
    "spreadsheet_data_analysis": (10, 4),
    "document_report_form": (10, 2),
    "slides_presentation": (6, 2),
    "diagram_visual_pdf": (5, 2),
    "multi_file_multi_format": (5, 2),
    "code_data_archive": (4, 0),
    "live_web_research": (4, 0),
}


def _inventory() -> tuple[TaskMeasurement, ...]:
    archetypes = []
    for archetype, (occupations, extras) in ARCHETYPE_OCCUPATIONS.items():
        archetypes.extend([(archetype, True)] * extras)
        archetypes.extend([(archetype, False)] * (occupations - extras))
    sectors = []
    for sector, quota in SECTOR_QUOTAS.items():
        occupations = 5 if quota in {11, 12} else 4
        extras = quota - 2 * occupations
        sectors.extend([(sector, True)] * extras)
        sectors.extend([(sector, False)] * (occupations - extras))
    extra_archetypes = [item for item in archetypes if item[1]]
    plain_archetypes = [item for item in archetypes if not item[1]]
    extra_sectors = [item for item in sectors if item[1]]
    plain_sectors = [item for item in sectors if not item[1]]
    occupation_specs = [
        (sector, archetype, True)
        for (sector, _), (archetype, _) in zip(extra_sectors, extra_archetypes, strict=True)
    ] + [
        (sector, archetype, False)
        for (sector, _), (archetype, _) in zip(plain_sectors, plain_archetypes, strict=True)
    ]
    measurements = []
    for occupation_index, (sector, archetype, _) in enumerate(occupation_specs):
        for task_index in range(5):
            measurements.append(
                TaskMeasurement(
                    id=f"task-{occupation_index:02}-{task_index}",
                    annotation=TaskAnnotation(
                        sector=sector,
                        occupation=f"occupation-{occupation_index:02}",
                        archetype=archetype,
                        unsupported=occupation_index == 0 and task_index == 4,
                    ),
                    full_score=0.5,
                    strong_anchor_score=0.8,
                    infra_failure_rate=0.0,
                    stable_ablation_delta=0.15 + task_index / 100,
                    cross_system_discrimination=task_index / 10,
                    repeat_stability=1,
                    trajectory_observability=1,
                    artifact_reproducibility=1,
                    cost_efficiency=1,
                    infra_drift=0,
                    leakage_risk=0,
                )
            )
    return tuple(measurements)


def test_exact_selection_and_stratified_folds_are_deterministic() -> None:
    inventory = _inventory()
    first = select_boundary_tasks(inventory, "fold-seed")
    second = select_boundary_tasks(tuple(reversed(inventory)), "fold-seed")
    assert isinstance(first, BoundarySelection)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert len(first.selected) == 100
    assert len(first.rejected) == 120
    assert Counter(task.sector for task in first.selected) == SECTOR_QUOTAS
    assert Counter(task.archetype for task in first.selected) == ARCHETYPE_QUOTAS
    occupation_counts = Counter(task.occupation for task in first.selected)
    assert set(occupation_counts.values()) == {2, 3}
    assert Counter(occupation_counts.values()) == {2: 32, 3: 12}
    assert Counter(task.fold for task in first.selected) == FOLD_QUOTAS
    for sector, total in SECTOR_QUOTAS.items():
        counts = Counter(task.fold for task in first.selected if task.sector == sector)
        assert all(
            abs(counts[fold] - total * quota / 100) <= 1 for fold, quota in FOLD_QUOTAS.items()
        )
    for archetype, total in ARCHETYPE_QUOTAS.items():
        counts = Counter(task.fold for task in first.selected if task.archetype == archetype)
        assert all(
            abs(counts[fold] - total * quota / 100) <= 1 for fold, quota in FOLD_QUOTAS.items()
        )
    rejected = {task.id: task.reasons for task in first.rejected}
    assert rejected["task-00-4"] == ("task has an essential unsupported dependency",)
    first.model_dump_json()


def test_inventory_must_cover_all_220_unique_tasks() -> None:
    inventory = _inventory()
    with pytest.raises(ValueError, match="expected 220 measurements"):
        select_boundary_tasks(inventory[:-1], "fold-seed")


def test_selector_fails_loud_when_an_occupation_has_fewer_than_two_eligible_tasks() -> None:
    inventory = list(_inventory())
    for index in range(4):
        task = inventory[index]
        inventory[index] = task.model_copy(update={"full_score": 0.9})
    with pytest.raises(SelectionInfeasible, match="fewer than two eligible tasks"):
        select_boundary_tasks(tuple(inventory), "fold-seed")


def test_cli_writes_the_exact_selected_manifest_atomically(tmp_path: Path) -> None:
    inventory = _inventory()
    measurements = tmp_path / "measurements.json"
    output = tmp_path / "selection" / "manifest.json"
    measurements.write_text(
        json.dumps([measurement.model_dump(mode="json") for measurement in inventory])
    )
    output.parent.mkdir()
    output.write_text("stale")

    main(
        (
            "--measurements",
            str(measurements),
            "--fold-seed",
            "operator-fold-seed",
            "--out",
            str(output),
        )
    )

    manifest = BoundarySelection.model_validate_json(output.read_bytes())
    assert output.read_bytes() == manifest.model_dump_json(indent=2).encode() + b"\n"
    assert manifest.fold_seed == "operator-fold-seed"
    assert len(manifest.selected) == 100
    assert len(manifest.rejected) == 120
    assert Counter(task.fold for task in manifest.selected) == FOLD_QUOTAS
    assert Counter(task.sector for task in manifest.selected) == SECTOR_QUOTAS
    assert Counter(task.archetype for task in manifest.selected) == ARCHETYPE_QUOTAS
    assert {task.id for task in manifest.selected} | {task.id for task in manifest.rejected} == {
        measurement.id for measurement in inventory
    }
    assert list(output.parent.iterdir()) == [output]


def test_cli_rejects_an_oversized_measurement_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    measurements = tmp_path / "measurements.json"
    measurements.write_bytes(b"[]")
    monkeypatch.setattr(gdpval_selection, "MAX_MEASUREMENT_INVENTORY_BYTES", 1)

    with pytest.raises(ValueError, match="exceeds 1 bytes"):
        gdpval_selection.SelectionBuilder(
            measurements,
            "fold-seed",
            tmp_path / "selection.json",
        ).build()
