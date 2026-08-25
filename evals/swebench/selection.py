"""Recompute the pinned SWE-bench roster: frozen smoke, representative seeded draws, and every
hardest-difficulty row, each backed by an official image."""

import argparse
import hashlib
import subprocess
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from evals.swebench.grading import official_instance_image
from evals.swebench.models import (
    SMOKE_CASE_IDS,
    SUBSET_SIZES,
    SubsetSelection,
    SWEbenchUpstream,
)
from evals.swebench.snapshot import SWEBENCH_UPSTREAM, canonical_json, verify_source

SWEBENCH_SELECTION_SEED = "swebench-hillclimb-2026-08-22"
HARD_DIFFICULTY = ">4 hours"


def read_parquet(source: bytes, upstream: SWEbenchUpstream) -> tuple[Mapping[str, object], ...]:
    """Read the pinned parquet, refusing any schema or row-count drift from the pin."""
    try:
        import pyarrow as arrow
        import pyarrow.parquet as parquet_reader
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "SWE-bench parquet reads require pyarrow; run with "
            "`uv run --with pyarrow python -m evals.swebench.selection`"
        ) from error
    table = parquet_reader.read_table(arrow.BufferReader(source))
    observed = tuple((field.name, str(field.type)) for field in table.schema)
    expected = tuple((column.name, column.arrow_type) for column in upstream.parquet.columns)
    if observed != expected:
        raise ValueError(
            f"SWE-bench parquet schema mismatch: expected {expected}, found {observed}"
        )
    if table.num_rows != upstream.parquet.rows:
        raise ValueError(
            "SWE-bench parquet row count mismatch: "
            f"expected {upstream.parquet.rows}, found {table.num_rows}"
        )
    return tuple(table.to_pylist())


@dataclass(frozen=True)
class SubsetBuilder:
    """Draws the hillclimb and holdout subsets one repository at a time, so the roster carries the
    dataset's spread instead of the repository that happens to dominate it."""

    rows: tuple[Mapping[str, object], ...]
    seed: str

    def build(self) -> SubsetSelection:
        picks = self._round_robin(self._ranked_pool())
        hillclimb = SUBSET_SIZES["hillclimb"]
        return SubsetSelection(
            seed=self.seed,
            smoke=SMOKE_CASE_IDS,
            hillclimb=picks[:hillclimb],
            holdout=picks[hillclimb:],
            hard=self._hard_cases(),
        )

    def _hard_cases(self) -> tuple[str, ...]:
        hard = []
        for row in self.rows:
            if row["difficulty"] != HARD_DIFFICULTY:
                continue
            instance_id = row["instance_id"]
            if not isinstance(instance_id, str):
                raise ValueError(f"SWE-bench row has a nontextual identity: {instance_id!r}")
            hard.append(instance_id)
        return tuple(sorted(hard))

    def _ranked_pool(self) -> dict[str, list[str]]:
        pool: dict[str, list[str]] = defaultdict(list)
        for row in self.rows:
            instance_id = row["instance_id"]
            if not isinstance(instance_id, str) or not isinstance(row["repo"], str):
                raise ValueError(f"SWE-bench row has a nontextual identity: {row['instance_id']!r}")
            if instance_id in SMOKE_CASE_IDS or row["difficulty"] == HARD_DIFFICULTY:
                continue
            pool[row["repo"]].append(instance_id)
        if not pool:
            raise ValueError("SWE-bench selection pool is empty")
        return {repo: sorted(ids, key=self._rank) for repo, ids in pool.items()}

    def _rank(self, instance_id: str) -> tuple[bytes, str]:
        return hashlib.sha256(f"{self.seed}\0{instance_id}".encode()).digest(), instance_id

    def _round_robin(self, ranked: dict[str, list[str]]) -> tuple[str, ...]:
        wanted = SUBSET_SIZES["hillclimb"] + SUBSET_SIZES["holdout"]
        order = sorted(ranked, key=lambda repo: (-len(ranked[repo]), repo))
        picks = tuple(
            ranked[repo][index]
            for index in range(max(len(ids) for ids in ranked.values()))
            for repo in order
            if index < len(ranked[repo])
        )[:wanted]
        if len(picks) != wanted:
            raise ValueError(
                f"SWE-bench selection pool holds {len(picks)} gradeable rows, needs {wanted}"
            )
        return picks


def main(argv: list[str] | None = None) -> None:
    """Print the roster block `data/upstream.json` must carry, after proving every image exists."""
    parser = argparse.ArgumentParser(prog="python -m evals.swebench.selection")
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--seed", default=SWEBENCH_SELECTION_SEED)
    args = parser.parse_args(argv)
    source = args.parquet.read_bytes()
    verify_source(source, SWEBENCH_UPSTREAM)
    selection = SubsetBuilder(read_parquet(source, SWEBENCH_UPSTREAM), args.seed).build()
    for instance_id in selection.all_ids:
        image = official_instance_image(instance_id)
        subprocess.run(
            ("docker", "manifest", "inspect", image), check=True, stdout=subprocess.DEVNULL
        )
        print(f"image: {image}")
    print(canonical_json(selection).decode())


if __name__ == "__main__":
    main()
