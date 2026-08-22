import argparse
from collections.abc import Mapping
from pathlib import Path

from evals.swebench.models import APPROVED_CASE_IDS, SWEbenchUpstream
from evals.swebench.snapshot import SWEBENCH_UPSTREAM, select_cases, verify_source, write_snapshot


def build_snapshot(parquet: Path, output: Path) -> Path:
    source = parquet.read_bytes()
    verify_source(source, SWEBENCH_UPSTREAM)
    rows = _read_parquet(source, SWEBENCH_UPSTREAM)
    approved = set(APPROVED_CASE_IDS)
    selected = tuple(row for row in rows if row["instance_id"] in approved)
    return write_snapshot(output, select_cases(selected), SWEBENCH_UPSTREAM)


def _read_parquet(source: bytes, upstream: SWEbenchUpstream) -> tuple[Mapping[str, object], ...]:
    try:
        import pyarrow as arrow
        import pyarrow.parquet as parquet_reader
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "SWE-bench snapshot builds require pyarrow; run with "
            "`uv run --with pyarrow python -m evals.swebench.build`"
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.swebench.build")
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(".local/swebench/snapshot"))
    args = parser.parse_args()
    root = build_snapshot(args.parquet, args.output)
    print(f"snapshot: {root}")
    print(f"source: {SWEBENCH_UPSTREAM.parquet.sha256}")
    for case_id in APPROVED_CASE_IDS:
        print(f"case: {case_id}")


if __name__ == "__main__":
    main()
