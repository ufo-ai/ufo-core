from __future__ import annotations

import argparse
import hashlib
import itertools
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

type Sector = Literal[
    "Information",
    "Manufacturing",
    "Professional, Scientific, and Technical Services",
    "Finance and Insurance",
    "Government",
    "Health Care and Social Assistance",
    "Real Estate and Rental and Leasing",
    "Wholesale Trade",
    "Retail Trade",
]
type Archetype = Literal[
    "spreadsheet_data_analysis",
    "document_report_form",
    "slides_presentation",
    "diagram_visual_pdf",
    "multi_file_multi_format",
    "code_data_archive",
    "live_web_research",
]
type Fold = Literal["development", "validation", "confirmation"]

SECTOR_QUOTAS: dict[Sector, int] = {
    "Information": 12,
    "Manufacturing": 12,
    "Professional, Scientific, and Technical Services": 12,
    "Finance and Insurance": 11,
    "Government": 11,
    "Health Care and Social Assistance": 11,
    "Real Estate and Rental and Leasing": 11,
    "Wholesale Trade": 11,
    "Retail Trade": 9,
}
ARCHETYPE_QUOTAS: dict[Archetype, int] = {
    "spreadsheet_data_analysis": 24,
    "document_report_form": 22,
    "slides_presentation": 14,
    "diagram_visual_pdf": 12,
    "multi_file_multi_format": 12,
    "code_data_archive": 8,
    "live_web_research": 8,
}
FOLD_QUOTAS: dict[Fold, int] = {
    "development": 60,
    "validation": 20,
    "confirmation": 20,
}
ARCHETYPES = tuple(ARCHETYPE_QUOTAS)
TRACKED_ARCHETYPES = ARCHETYPES[:-1]
STABLE_DELTA = 0.15
MIN_STABLE_TASKS = 80
SENSITIVITY_WEIGHT = 0.30
DISCRIMINATION_WEIGHT = 0.20
INTERMEDIATE_WEIGHT = 0.15
REPEAT_STABILITY_WEIGHT = 0.10
TRAJECTORY_OBSERVABILITY_WEIGHT = 0.10
ARTIFACT_REPRODUCIBILITY_WEIGHT = 0.10
COST_EFFICIENCY_WEIGHT = 0.05
INFRA_DRIFT_PENALTY = 0.25
LEAKAGE_RISK_PENALTY = 0.25
INTERMEDIATE_SCORE_NORMALIZER = 4.0
MIN_BOUNDARY_SCORE = 0.20
MAX_BOUNDARY_SCORE = 0.80
MIN_STRONG_ANCHOR_SCORE = 0.50
MAX_INFRA_FAILURE_RATE = 0.02
EXPECTED_TASKS = 220
EXPECTED_OCCUPATIONS = 44
TASKS_PER_OCCUPATION = 5
MAX_MEASUREMENT_INVENTORY_BYTES = 4 * 1024 * 1024


class TaskAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sector: Sector
    occupation: str = Field(min_length=1)
    archetype: Archetype
    unsupported: bool


class TaskMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    id: str = Field(min_length=1)
    annotation: TaskAnnotation
    full_score: float = Field(ge=0.0, le=1.0)
    strong_anchor_score: float = Field(ge=0.0, le=1.0)
    infra_failure_rate: float = Field(ge=0.0, le=1.0)
    stable_ablation_delta: float | None = Field(default=None, ge=-1.0, le=1.0)
    cross_system_discrimination: float = Field(ge=0.0, le=1.0)
    repeat_stability: float = Field(ge=0.0, le=1.0)
    trajectory_observability: float = Field(ge=0.0, le=1.0)
    artifact_reproducibility: float = Field(ge=0.0, le=1.0)
    cost_efficiency: float = Field(ge=0.0, le=1.0)
    infra_drift: float = Field(ge=0.0, le=1.0)
    leakage_risk: float = Field(ge=0.0, le=1.0)


class SelectedTask(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    id: str
    fold: Fold
    sector: Sector
    occupation: str
    archetype: Archetype
    utility: float


class RejectedTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    reasons: tuple[str, ...]


class BoundarySelection(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: Literal["gdpval_boundary_100"] = "gdpval_boundary_100"
    fold_seed: str
    total_utility: float
    selected: tuple[SelectedTask, ...]
    rejected: tuple[RejectedTask, ...]


class SelectionInfeasible(ValueError):
    pass


@dataclass(frozen=True)
class _Candidate:
    ids: tuple[str, ...]
    utility: float


@dataclass(frozen=True)
class _Option:
    candidate: _Candidate
    counts: tuple[int, ...]
    stable: int
    extra: int


def boundary_utility(measurement: TaskMeasurement) -> float:
    sensitivity = max(measurement.stable_ablation_delta or 0.0, 0.0)
    intermediate = (
        INTERMEDIATE_SCORE_NORMALIZER * measurement.full_score * (1 - measurement.full_score)
    )
    return (
        SENSITIVITY_WEIGHT * sensitivity
        + DISCRIMINATION_WEIGHT * measurement.cross_system_discrimination
        + INTERMEDIATE_WEIGHT * intermediate
        + REPEAT_STABILITY_WEIGHT * measurement.repeat_stability
        + TRAJECTORY_OBSERVABILITY_WEIGHT * measurement.trajectory_observability
        + ARTIFACT_REPRODUCIBILITY_WEIGHT * measurement.artifact_reproducibility
        + COST_EFFICIENCY_WEIGHT * measurement.cost_efficiency
        - INFRA_DRIFT_PENALTY * measurement.infra_drift
        - LEAKAGE_RISK_PENALTY * measurement.leakage_risk
    )


def select_boundary_tasks(
    measurements: tuple[TaskMeasurement, ...], fold_seed: str
) -> BoundarySelection:
    _validate_inventory(measurements, fold_seed)
    by_id = {measurement.id: measurement for measurement in measurements}
    ineligible = {
        measurement.id: _ineligibility_reasons(measurement)
        for measurement in measurements
        if _ineligibility_reasons(measurement)
    }
    eligible = tuple(
        measurement for measurement in measurements if measurement.id not in ineligible
    )
    eligible_counts = Counter(task.annotation.occupation for task in eligible)
    underfilled = sorted(
        {
            task.annotation.occupation
            for task in measurements
            if eligible_counts[task.annotation.occupation] < 2
        }
    )
    if underfilled:
        raise SelectionInfeasible(
            "fewer than two eligible tasks for occupations: " + ", ".join(underfilled)
        )
    selected_ids, total_utility = _solve(eligible)
    selected_measurements = tuple(by_id[task_id] for task_id in selected_ids)
    folds = _assign_folds(selected_measurements, fold_seed)
    selected = tuple(
        SelectedTask(
            id=measurement.id,
            fold=folds[measurement.id],
            sector=measurement.annotation.sector,
            occupation=measurement.annotation.occupation,
            archetype=measurement.annotation.archetype,
            utility=boundary_utility(measurement),
        )
        for measurement in sorted(selected_measurements, key=lambda item: item.id)
    )
    rejected = tuple(
        RejectedTask(
            id=measurement.id,
            reasons=ineligible.get(
                measurement.id, ("not selected by the exact boundary-utility optimizer",)
            ),
        )
        for measurement in sorted(measurements, key=lambda item: item.id)
        if measurement.id not in selected_ids
    )
    return BoundarySelection(
        fold_seed=fold_seed,
        total_utility=total_utility,
        selected=selected,
        rejected=rejected,
    )


@dataclass(frozen=True)
class SelectionBuilder:
    measurements: Path
    fold_seed: str
    output: Path

    def build(self) -> BoundarySelection:
        inventory = self._load_measurements()
        selection = select_boundary_tasks(inventory, self.fold_seed)
        self._write_selection(selection)
        return selection

    def _load_measurements(self) -> tuple[TaskMeasurement, ...]:
        with self.measurements.open("rb") as source:
            payload = source.read(MAX_MEASUREMENT_INVENTORY_BYTES + 1)
        if not payload:
            raise ValueError("GDPval measurement inventory is empty")
        if len(payload) > MAX_MEASUREMENT_INVENTORY_BYTES:
            raise ValueError(
                f"GDPval measurement inventory exceeds {MAX_MEASUREMENT_INVENTORY_BYTES} bytes"
            )
        return TypeAdapter(tuple[TaskMeasurement, ...]).validate_json(payload)

    def _write_selection(self, selection: BoundarySelection) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.output.with_name(f".{self.output.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(selection.model_dump_json(indent=2).encode() + b"\n")
            temporary.replace(self.output)
        finally:
            temporary.unlink(missing_ok=True)


def _validate_inventory(measurements: tuple[TaskMeasurement, ...], fold_seed: str) -> None:
    if not fold_seed:
        raise ValueError("fold_seed must not be empty")
    if len(measurements) != EXPECTED_TASKS:
        raise ValueError(f"expected {EXPECTED_TASKS} measurements, got {len(measurements)}")
    ids = [measurement.id for measurement in measurements]
    if len(set(ids)) != len(ids):
        raise ValueError("measurement ids must be unique")
    occupations: dict[str, list[TaskMeasurement]] = defaultdict(list)
    for measurement in measurements:
        occupations[measurement.annotation.occupation].append(measurement)
    if len(occupations) != EXPECTED_OCCUPATIONS:
        raise ValueError(f"expected {EXPECTED_OCCUPATIONS} occupations, got {len(occupations)}")
    wrong_sizes = sorted(
        occupation
        for occupation, tasks in occupations.items()
        if len(tasks) != TASKS_PER_OCCUPATION
    )
    if wrong_sizes:
        raise ValueError(f"occupations must each have five tasks: {', '.join(wrong_sizes)}")
    split = sorted(
        occupation
        for occupation, tasks in occupations.items()
        if len({task.annotation.sector for task in tasks}) != 1
    )
    if split:
        raise ValueError(f"occupations span sectors: {', '.join(split)}")


def _ineligibility_reasons(measurement: TaskMeasurement) -> tuple[str, ...]:
    reasons = []
    if not MIN_BOUNDARY_SCORE <= measurement.full_score <= MAX_BOUNDARY_SCORE:
        reasons.append(
            f"full score is outside [{MIN_BOUNDARY_SCORE:.2f}, {MAX_BOUNDARY_SCORE:.2f}]"
        )
    if measurement.strong_anchor_score < MIN_STRONG_ANCHOR_SCORE:
        reasons.append(f"strong anchor score is below {MIN_STRONG_ANCHOR_SCORE:.2f}")
    if measurement.infra_failure_rate >= MAX_INFRA_FAILURE_RATE:
        reasons.append(f"infrastructure failure rate is not below {MAX_INFRA_FAILURE_RATE:.2f}")
    if measurement.annotation.unsupported:
        reasons.append("task has an essential unsupported dependency")
    return tuple(reasons)


def _solve(measurements: tuple[TaskMeasurement, ...]) -> tuple[frozenset[str], float]:
    occupations: dict[tuple[Sector, str], list[TaskMeasurement]] = defaultdict(list)
    for measurement in measurements:
        key = (measurement.annotation.sector, measurement.annotation.occupation)
        occupations[key].append(measurement)
    missing = sorted(occupation for (_, occupation), tasks in occupations.items() if len(tasks) < 2)
    if missing:
        raise SelectionInfeasible(
            "fewer than two eligible tasks for occupations: " + ", ".join(missing)
        )
    by_sector: dict[Sector, list[tuple[str, tuple[_Option, ...]]]] = defaultdict(list)
    for (sector, occupation), tasks in occupations.items():
        by_sector[sector].append((occupation, _occupation_options(tasks)))
    absent = sorted(set(SECTOR_QUOTAS) - set(by_sector))
    if absent:
        raise SelectionInfeasible("no eligible occupations for sectors: " + ", ".join(absent))
    frontiers = []
    for sector, target in SECTOR_QUOTAS.items():
        groups = sorted(by_sector[sector], key=lambda item: (len(item[1]), item[0]))
        extra = target - 2 * len(groups)
        if not 0 <= extra <= len(groups):
            raise SelectionInfeasible(
                f"sector {sector!r} cannot select {target} tasks from {len(groups)} occupations"
            )
        frontier = _sector_frontier(groups, extra)
        if not frontier:
            raise SelectionInfeasible(f"sector {sector!r} has no quota-feasible selection")
        frontiers.append((sector, frontier))
    frontiers.sort(key=lambda item: (len(item[1]), item[0]))
    states: dict[tuple[tuple[int, ...], int], _Candidate] = {
        ((0,) * len(TRACKED_ARCHETYPES), 0): _Candidate((), 0.0)
    }
    remaining_bounds = _remaining_bounds(frontiers)
    for index, (_, frontier) in enumerate(frontiers):
        next_states: dict[tuple[tuple[int, ...], int], _Candidate] = {}
        minimums, maximums, stable_max = remaining_bounds[index + 1]
        for (counts, stable), current in states.items():
            for (addition, added_stable), candidate in frontier.items():
                merged = tuple(left + right for left, right in zip(counts, addition, strict=True))
                if any(
                    count > ARCHETYPE_QUOTAS[archetype]
                    for count, archetype in zip(merged, TRACKED_ARCHETYPES, strict=True)
                ):
                    continue
                if any(
                    count + low > ARCHETYPE_QUOTAS[archetype]
                    or count + high < ARCHETYPE_QUOTAS[archetype]
                    for count, low, high, archetype in zip(
                        merged, minimums, maximums, TRACKED_ARCHETYPES, strict=True
                    )
                ):
                    continue
                merged_stable = min(MIN_STABLE_TASKS, stable + added_stable)
                if merged_stable + stable_max < MIN_STABLE_TASKS:
                    continue
                merged_candidate = _Candidate(
                    tuple(sorted((*current.ids, *candidate.ids))),
                    current.utility + candidate.utility,
                )
                _keep_best(next_states, (merged, merged_stable), merged_candidate)
        states = _pareto_states(next_states)
        if not states:
            raise SelectionInfeasible("archetype or stable-delta quotas are infeasible")
    target_counts = tuple(ARCHETYPE_QUOTAS[archetype] for archetype in TRACKED_ARCHETYPES)
    winner = states.get((target_counts, MIN_STABLE_TASKS))
    if winner is None:
        raise SelectionInfeasible("no exact 100-task selection satisfies all quotas")
    if len(winner.ids) != 100:
        raise AssertionError("solver produced a selection with the wrong size")
    return frozenset(winner.ids), winner.utility


def _occupation_options(tasks: list[TaskMeasurement]) -> tuple[_Option, ...]:
    options: dict[tuple[tuple[int, ...], int, int], _Option] = {}
    ordered = sorted(tasks, key=lambda item: item.id)
    for size in (2, 3):
        for chosen in itertools.combinations(ordered, size):
            counts = tuple(
                sum(task.annotation.archetype == archetype for task in chosen)
                for archetype in TRACKED_ARCHETYPES
            )
            stable = sum(
                task.stable_ablation_delta is not None
                and task.stable_ablation_delta >= STABLE_DELTA
                for task in chosen
            )
            candidate = _Candidate(
                tuple(task.id for task in chosen),
                sum(boundary_utility(task) for task in chosen),
            )
            option = _Option(candidate, counts, stable, size - 2)
            key = counts, stable, size - 2
            existing = options.get(key)
            if existing is None or _better(candidate, existing.candidate):
                options[key] = option
    return tuple(
        sorted(
            options.values(),
            key=lambda option: (-option.candidate.utility, option.candidate.ids),
        )
    )


def _sector_frontier(
    groups: list[tuple[str, tuple[_Option, ...]]], required_extra: int
) -> dict[tuple[tuple[int, ...], int], _Candidate]:
    states: dict[tuple[tuple[int, ...], int, int], _Candidate] = {
        ((0,) * len(TRACKED_ARCHETYPES), 0, 0): _Candidate((), 0.0)
    }
    for index, (_, options) in enumerate(groups):
        next_states: dict[tuple[tuple[int, ...], int, int], _Candidate] = {}
        remaining = len(groups) - index - 1
        for (counts, stable, extra), current in states.items():
            for option in options:
                merged_extra = extra + option.extra
                if merged_extra > required_extra or merged_extra + remaining < required_extra:
                    continue
                merged = tuple(
                    left + right for left, right in zip(counts, option.counts, strict=True)
                )
                if any(
                    count > ARCHETYPE_QUOTAS[archetype]
                    for count, archetype in zip(merged, TRACKED_ARCHETYPES, strict=True)
                ):
                    continue
                candidate = _Candidate(
                    tuple(sorted((*current.ids, *option.candidate.ids))),
                    current.utility + option.candidate.utility,
                )
                _keep_best(
                    next_states,
                    (merged, stable + option.stable, merged_extra),
                    candidate,
                )
        states = next_states
    frontier = {
        (counts, stable): candidate
        for (counts, stable, extra), candidate in states.items()
        if extra == required_extra
    }
    return _pareto_states(frontier)


def _remaining_bounds(
    frontiers: list[tuple[Sector, dict[tuple[tuple[int, ...], int], _Candidate]]],
) -> list[tuple[tuple[int, ...], tuple[int, ...], int]]:
    zero = (0,) * len(TRACKED_ARCHETYPES)
    bounds: list[tuple[tuple[int, ...], tuple[int, ...], int]] = [(zero, zero, 0)] * (
        len(frontiers) + 1
    )
    for index in range(len(frontiers) - 1, -1, -1):
        _, frontier = frontiers[index]
        previous_min, previous_max, previous_stable = bounds[index + 1]
        keys = tuple(frontier)
        minimum = tuple(
            min(counts[position] for counts, _ in keys)
            for position in range(len(TRACKED_ARCHETYPES))
        )
        maximum = tuple(
            max(counts[position] for counts, _ in keys)
            for position in range(len(TRACKED_ARCHETYPES))
        )
        stable = max(stable for _, stable in keys)
        bounds[index] = (
            tuple(left + right for left, right in zip(minimum, previous_min, strict=True)),
            tuple(left + right for left, right in zip(maximum, previous_max, strict=True)),
            stable + previous_stable,
        )
    return bounds


def _pareto_states(
    states: dict[tuple[tuple[int, ...], int], _Candidate],
) -> dict[tuple[tuple[int, ...], int], _Candidate]:
    by_counts: dict[tuple[int, ...], list[tuple[int, _Candidate]]] = defaultdict(list)
    for (counts, stable), candidate in states.items():
        by_counts[counts].append((stable, candidate))
    result = {}
    for counts, candidates in by_counts.items():
        best_utility = float("-inf")
        for stable, candidate in sorted(candidates, key=lambda item: -item[0]):
            if candidate.utility > best_utility:
                result[counts, stable] = candidate
                best_utility = candidate.utility
    return result


def _keep_best(states: dict[tuple, _Candidate], key: tuple, candidate: _Candidate) -> None:
    existing = states.get(key)
    if existing is None or _better(candidate, existing):
        states[key] = candidate


def _better(candidate: _Candidate, existing: _Candidate) -> bool:
    return candidate.utility > existing.utility or (
        candidate.utility == existing.utility and candidate.ids < existing.ids
    )


def _assign_folds(measurements: tuple[TaskMeasurement, ...], seed: str) -> dict[str, Fold]:
    folds = tuple(FOLD_QUOTAS)
    fractions = {fold: FOLD_QUOTAS[fold] / 100 for fold in folds}
    dimensions: dict[str, Counter[str]] = {
        "sector": Counter(task.annotation.sector for task in measurements),
        "occupation": Counter(task.annotation.occupation for task in measurements),
        "archetype": Counter(task.annotation.archetype for task in measurements),
    }
    counts: dict[tuple[str, str, Fold], int] = defaultdict(int)
    capacities = dict(FOLD_QUOTAS)
    assigned: dict[str, Fold] = {}
    ordered = sorted(
        measurements,
        key=lambda task: (
            dimensions["occupation"][task.annotation.occupation],
            _hash(seed, task.id),
            task.id,
        ),
    )
    for task in ordered:
        values: dict[str, str] = {
            "sector": task.annotation.sector,
            "occupation": task.annotation.occupation,
            "archetype": task.annotation.archetype,
        }
        choices = [fold for fold in folds if capacities[fold]]
        fold = min(
            choices,
            key=lambda candidate: (
                _incremental_fold_cost(candidate, values, counts, dimensions, fractions),
                _hash(seed, task.id, candidate),
                candidate,
            ),
        )
        assigned[task.id] = fold
        capacities[fold] -= 1
        for dimension, value in values.items():
            counts[dimension, value, fold] += 1
    return _improve_folds(measurements, assigned, dimensions, fractions, seed)


def _incremental_fold_cost(
    fold: Fold,
    values: dict[str, str],
    counts: dict[tuple[str, str, Fold], int],
    dimensions: dict[str, Counter[str]],
    fractions: dict[Fold, float],
) -> float:
    cost = 0.0
    for dimension, value in values.items():
        total = dimensions[dimension][value]
        before = counts[dimension, value, fold] - total * fractions[fold]
        after = before + 1
        cost += after * after - before * before
    return cost


def _improve_folds(
    measurements: tuple[TaskMeasurement, ...],
    assigned: dict[str, Fold],
    dimensions: dict[str, Counter[str]],
    fractions: dict[Fold, float],
    seed: str,
) -> dict[str, Fold]:
    by_id = {task.id: task for task in measurements}
    ordered_ids = sorted(by_id, key=lambda task_id: (_hash(seed, task_id), task_id))
    while True:
        baseline = _fold_cost(measurements, assigned, dimensions, fractions)
        best: tuple[float, str, str] | None = None
        for left_index, left_id in enumerate(ordered_ids):
            for right_id in ordered_ids[left_index + 1 :]:
                if assigned[left_id] == assigned[right_id]:
                    continue
                assigned[left_id], assigned[right_id] = assigned[right_id], assigned[left_id]
                improvement = baseline - _fold_cost(measurements, assigned, dimensions, fractions)
                assigned[left_id], assigned[right_id] = assigned[right_id], assigned[left_id]
                if improvement > 1e-12 and (
                    best is None
                    or improvement > best[0]
                    or (improvement == best[0] and (left_id, right_id) < best[1:])
                ):
                    best = improvement, left_id, right_id
        if best is None:
            return assigned
        _, left_id, right_id = best
        assigned[left_id], assigned[right_id] = assigned[right_id], assigned[left_id]


def _fold_cost(
    measurements: tuple[TaskMeasurement, ...],
    assigned: dict[str, Fold],
    dimensions: dict[str, Counter[str]],
    fractions: dict[Fold, float],
) -> float:
    counts: Counter = Counter()
    for task in measurements:
        fold = assigned[task.id]
        counts["sector", task.annotation.sector, fold] += 1
        counts["occupation", task.annotation.occupation, fold] += 1
        counts["archetype", task.annotation.archetype, fold] += 1
    return sum(
        (counts[dimension, value, fold] - total * fractions[fold]) ** 2
        for dimension, totals in dimensions.items()
        for value, total in totals.items()
        for fold in FOLD_QUOTAS
    )


def _hash(seed: str, *parts: str) -> bytes:
    return hashlib.sha256("\0".join((seed, *parts)).encode()).digest()


def main(argv: tuple[str, ...] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.gdpval_100.selection")
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--fold-seed", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    SelectionBuilder(args.measurements, args.fold_seed, args.out).build()
    print(args.out)


if __name__ == "__main__":
    main()
