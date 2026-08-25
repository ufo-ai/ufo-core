import argparse
from pathlib import Path

from evals.swebench.models import SUBSET_SIZES
from evals.swebench.selection import SubsetBuilder, read_parquet
from evals.swebench.snapshot import SWEBENCH_UPSTREAM, select_cases, verify_source, write_snapshot


def build_snapshot(parquet: Path, output: Path) -> Path:
    """Write the snapshot carrying every roster case, after the seeded split reproduces the pin."""
    source = parquet.read_bytes()
    verify_source(source, SWEBENCH_UPSTREAM)
    rows = read_parquet(source, SWEBENCH_UPSTREAM)
    pinned = SWEBENCH_UPSTREAM.subsets
    recomputed = SubsetBuilder(rows, pinned.seed).build()
    if recomputed != pinned:
        raise ValueError(
            "SWE-bench seeded split does not match the pinned roster; regenerate it with "
            "`python -m evals.swebench.selection --parquet <parquet>` and review the difference"
        )
    approved = set(pinned.all_ids)
    selected = tuple(row for row in rows if row["instance_id"] in approved)
    return write_snapshot(output, select_cases(selected, SWEBENCH_UPSTREAM), SWEBENCH_UPSTREAM)


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.swebench.build")
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(".local/swebench/snapshot"))
    args = parser.parse_args()
    root = build_snapshot(args.parquet, args.output)
    print(f"snapshot: {root}")
    print(f"source: {SWEBENCH_UPSTREAM.parquet.sha256}")
    for subset in SUBSET_SIZES:
        for case_id in SWEBENCH_UPSTREAM.subsets.ids(subset):
            print(f"case: {subset} {case_id}")


if __name__ == "__main__":
    main()
