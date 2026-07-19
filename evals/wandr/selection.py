"""Generate the checked-in WANDR selection: one smoke task plus deterministic 7/7/7 hillclimb and
holdout samples per difficulty label, every task pinned to the upstream dataset manifest digest."""

import argparse
import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path

from evals.wandr.models import (
    PER_DIFFICULTY,
    Difficulty,
    Selection,
    SelectionCase,
    Subset,
    UpstreamRepo,
)
from evals.wandr.snapshot import SELECTION_PATH, canonical_json, task_content_digest

DATASET_ROOT = "datasets/wandr"
DATASET_MANIFEST = "dataset.toml"
UPSTREAM_TASK_PREFIX = "wandr/"
SMOKE_TASK = "smoke"
DEFAULT_SEED = "wandr-hillclimb-2026-07-18"
PINNED_REVISION = "ca82dc224d5c03a8cde5409c6ba49c1c4f67fff3"


@dataclass(frozen=True)
class SelectionBuilder:
    """Builds the selection from an upstream checkout, verifying every sampled task's recomputed
    content digest against the upstream dataset manifest before pinning it."""

    source: Path
    revision: str
    seed: str

    def build(self) -> Selection:
        pinned = self._dataset_digests()
        cases = [self._case(SMOKE_TASK, "smoke", None, pinned)]
        for difficulty, names in self._by_difficulty().items():
            ranked = sorted(names, key=self._rank)
            if len(ranked) < 2 * PER_DIFFICULTY:
                raise ValueError(f"upstream has too few {difficulty} tasks: {len(ranked)}")
            for index, name in enumerate(ranked[: 2 * PER_DIFFICULTY]):
                subset: Subset = "hillclimb" if index < PER_DIFFICULTY else "holdout"
                cases.append(self._case(name, subset, difficulty, pinned))
        ordered = sorted(cases, key=lambda case: (case.subset, case.difficulty or "", case.name))
        return Selection(
            upstream=UpstreamRepo(revision=self.revision), seed=self.seed, cases=tuple(ordered)
        )

    def _dataset_digests(self) -> dict[str, str]:
        manifest = self.source / DATASET_ROOT / DATASET_MANIFEST
        data = tomllib.loads(manifest.read_text())
        return {
            entry["name"].removeprefix(UPSTREAM_TASK_PREFIX): entry["digest"]
            for entry in data["tasks"]
        }

    def _by_difficulty(self) -> dict[Difficulty, list[str]]:
        grouped: dict[Difficulty, list[str]] = {"low": [], "medium": [], "high": []}
        for task_dir in sorted((self.source / DATASET_ROOT).iterdir()):
            manifest = task_dir / "task.toml"
            if task_dir.name == SMOKE_TASK or not manifest.is_file():
                continue
            label = tomllib.loads(manifest.read_text())["metadata"]["difficulty-label"]
            grouped[label].append(task_dir.name)
        return grouped

    def _rank(self, name: str) -> tuple[bytes, str]:
        return hashlib.sha256(f"{self.seed}\0{name}".encode()).digest(), name

    def _case(
        self,
        name: str,
        subset: Subset,
        difficulty: Difficulty | None,
        pinned: dict[str, str],
    ) -> SelectionCase:
        task_dir = self.source / DATASET_ROOT / name
        digest = task_content_digest(task_dir)
        if digest != pinned[name]:
            raise ValueError(f"WANDR task {name!r} digest does not match the upstream manifest")
        metadata = tomllib.loads((task_dir / "task.toml").read_text())["metadata"]
        return SelectionCase(
            name=name,
            subset=subset,
            difficulty=difficulty,
            digest=digest,
            required_files=tuple(metadata["required_file_paths"]),
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.wandr.selection")
    parser.add_argument("--source", type=Path, required=True, help="upstream wandr checkout")
    parser.add_argument("--revision", default=PINNED_REVISION)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=SELECTION_PATH)
    args = parser.parse_args(argv)
    selection = SelectionBuilder(args.source, args.revision, args.seed).build()
    args.out.write_bytes(canonical_json(selection) + b"\n")
    print(f"selection {args.out} · {len(selection.cases)} cases")


if __name__ == "__main__":
    main()
