import argparse
import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from urllib.request import urlopen

from pydantic import BaseModel, ConfigDict, Field

from evals.gdpval_100.models import (
    UUID_PATTERN,
    RubricItem,
    SnapshotAsset,
    SnapshotCase,
    UpstreamParquet,
)
from evals.gdpval_100.snapshot import GDPVAL_UPSTREAM, OBJECTS_ROOT, write_snapshot

DOWNLOAD_TIMEOUT_SECONDS = 120
COPY_BUFFER_BYTES = 1024 * 1024


class UpstreamRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(pattern=UUID_PATTERN)
    sector: str = Field(min_length=1)
    occupation: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    reference_files: tuple[str, ...]
    reference_file_urls: tuple[str, ...]
    reference_file_hf_uris: tuple[str, ...]
    deliverable_files: tuple[str, ...]
    deliverable_file_urls: tuple[str, ...]
    deliverable_file_hf_uris: tuple[str, ...]
    rubric_pretty: str = Field(min_length=1)
    rubric_json: str = Field(min_length=1)


@dataclass(frozen=True)
class SnapshotBuilder:
    parquet: Path
    output_root: Path
    asset_case_ids: frozenset[str] | None = None
    smoke_count: int | None = None
    all_assets: bool = False
    asset_root: Path | None = None
    upstream: UpstreamParquet = GDPVAL_UPSTREAM

    def build(self) -> Path:
        self._verify_parquet()
        cases = self._cases_from_rows(self._read_parquet())
        selected = self._selected_case_ids(cases)
        self.output_root.mkdir(parents=True, exist_ok=True)
        staging_root = Path(tempfile.mkdtemp(prefix=".gdpval-", dir=self.output_root))
        try:
            materialized = self._materialize_assets(staging_root, cases, selected)
            materialized_case_ids = tuple(
                case.task_id for case in cases if case.task_id in selected
            )
            return write_snapshot(
                staging_root,
                self.output_root,
                self.upstream,
                materialized,
                materialized_case_ids,
            )
        except BaseException:
            shutil.rmtree(staging_root, ignore_errors=True)
            raise

    def _verify_parquet(self) -> None:
        size_bytes = self.parquet.stat().st_size
        if size_bytes != self.upstream.size_bytes:
            raise ValueError(
                f"GDPval parquet size mismatch: expected {self.upstream.size_bytes}, "
                f"found {size_bytes}"
            )
        with self.parquet.open("rb") as source:
            digest = f"sha256:{hashlib.file_digest(source, 'sha256').hexdigest()}"
        if digest != self.upstream.sha256:
            raise ValueError(
                f"GDPval parquet digest mismatch: expected {self.upstream.sha256}, found {digest}"
            )

    def _read_parquet(self) -> tuple[UpstreamRow, ...]:
        try:
            import pyarrow.parquet as parquet
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "GDPval snapshot builds require pyarrow; run with "
                "`uv run --with pyarrow python -m evals.gdpval_100.build`"
            ) from error
        table = parquet.read_table(self.parquet)
        observed = tuple((field.name, str(field.type)) for field in table.schema)
        expected = tuple((column.name, column.arrow_type) for column in self.upstream.columns)
        if observed != expected:
            raise ValueError(
                f"GDPval parquet schema mismatch: expected {expected}, found {observed}"
            )
        if table.num_rows != self.upstream.rows:
            raise ValueError(
                f"GDPval parquet row count mismatch: expected {self.upstream.rows}, "
                f"found {table.num_rows}"
            )
        return tuple(UpstreamRow.model_validate(row) for row in table.to_pylist())

    def _cases_from_rows(self, rows: tuple[UpstreamRow, ...]) -> tuple[SnapshotCase, ...]:
        cases = tuple(self._case_from_row(row) for row in rows)
        task_ids = [case.task_id for case in cases]
        if len(cases) != self.upstream.rows:
            raise ValueError(f"GDPval requires exactly 220 rows, found {len(cases)}")
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("GDPval parquet contains duplicate task ids")
        return cases

    def _case_from_row(self, row: UpstreamRow) -> SnapshotCase:
        references = self._assets(
            "reference_files",
            row.reference_files,
            row.reference_file_urls,
            row.reference_file_hf_uris,
        )
        deliverables = self._assets(
            "deliverable_files",
            row.deliverable_files,
            row.deliverable_file_urls,
            row.deliverable_file_hf_uris,
        )
        rubric_body = json.loads(row.rubric_json)
        if not isinstance(rubric_body, list):
            raise ValueError(f"GDPval task {row.task_id!r} rubric is not a list")
        rubric = tuple(RubricItem.model_validate(item) for item in rubric_body)
        return SnapshotCase(
            task_id=row.task_id,
            sector=row.sector,
            occupation=row.occupation,
            prompt=row.prompt,
            references=references,
            deliverables=deliverables,
            rubric=rubric,
        )

    def _assets(
        self,
        prefix: Literal["reference_files", "deliverable_files"],
        paths: tuple[str, ...],
        urls: tuple[str, ...],
        hf_uris: tuple[str, ...],
    ) -> tuple[SnapshotAsset, ...]:
        if len(paths) != len(urls) or len(paths) != len(hf_uris):
            raise ValueError(f"GDPval {prefix} paths and URLs have different lengths")
        assets = []
        for path, url, hf_uri in zip(paths, urls, hf_uris, strict=True):
            encoded = quote(path, safe="/")
            expected_url = (
                f"https://huggingface.co/datasets/{self.upstream.repository}/resolve/main/{encoded}"
            )
            expected_hf_uri = f"hf://datasets/{self.upstream.repository}@main/{encoded}"
            if url != expected_url or hf_uri != expected_hf_uri:
                raise ValueError(f"GDPval {prefix} URL does not match its upstream path: {path!r}")
            assets.append(SnapshotAsset(relative_path=path))
        return tuple(assets)

    def _selected_case_ids(self, cases: tuple[SnapshotCase, ...]) -> frozenset[str]:
        modes = sum(
            (
                self.asset_case_ids is not None,
                self.smoke_count is not None,
                self.all_assets,
            )
        )
        if modes != 1:
            raise ValueError("choose exactly one GDPval asset selection mode")
        known = {case.task_id for case in cases}
        if self.asset_case_ids is not None:
            unknown = sorted(self.asset_case_ids - known)
            if unknown:
                raise ValueError(f"unknown GDPval asset case ids: {', '.join(unknown)}")
            if not self.asset_case_ids:
                raise ValueError("GDPval asset case selection is empty")
            return self.asset_case_ids
        if self.smoke_count is not None:
            if not 1 <= self.smoke_count <= len(cases):
                raise ValueError("GDPval smoke count must be between 1 and 220")
            return frozenset(case.task_id for case in cases[: self.smoke_count])
        return frozenset(known)

    def _materialize_assets(
        self,
        staging_root: Path,
        cases: tuple[SnapshotCase, ...],
        selected: frozenset[str],
    ) -> tuple[SnapshotCase, ...]:
        cached: dict[str, SnapshotAsset] = {}
        materialized = []
        for case in cases:
            if case.task_id not in selected:
                materialized.append(case)
                continue
            references = tuple(
                self._materialize_asset(staging_root, asset, cached) for asset in case.references
            )
            deliverables = tuple(
                self._materialize_asset(staging_root, asset, cached) for asset in case.deliverables
            )
            materialized.append(
                case.model_copy(update={"references": references, "deliverables": deliverables})
            )
        return tuple(materialized)

    def _materialize_asset(
        self,
        staging_root: Path,
        asset: SnapshotAsset,
        cached: dict[str, SnapshotAsset],
    ) -> SnapshotAsset:
        if asset.relative_path in cached:
            return cached[asset.relative_path]
        temporary = staging_root / ".asset"
        if self.asset_root is None:
            with urlopen(
                self._pinned_asset_url(asset.relative_path),
                timeout=DOWNLOAD_TIMEOUT_SECONDS,
            ) as response:
                with temporary.open("wb") as output:
                    shutil.copyfileobj(response, output, COPY_BUFFER_BYTES)
        else:
            asset_path = self.asset_root / asset.relative_path
            if not asset_path.is_file():
                raise FileNotFoundError(f"GDPval asset is missing: {asset_path}")
            shutil.copyfile(asset_path, temporary)
        with temporary.open("rb") as source:
            digest = f"sha256:{hashlib.file_digest(source, 'sha256').hexdigest()}"
        size_bytes = temporary.stat().st_size
        if size_bytes == 0:
            raise ValueError(f"GDPval asset is empty: {asset.relative_path}")
        snapshot_path = f"{OBJECTS_ROOT}/{digest.removeprefix('sha256:')}"
        destination = staging_root / snapshot_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            temporary.unlink()
        else:
            temporary.replace(destination)
        result = asset.model_copy(
            update={
                "snapshot_path": snapshot_path,
                "size_bytes": size_bytes,
                "sha256": digest,
            }
        )
        cached[asset.relative_path] = result
        return result

    def _pinned_asset_url(self, relative_path: str) -> str:
        encoded = quote(relative_path, safe="/")
        return (
            f"https://huggingface.co/datasets/{self.upstream.repository}/resolve/"
            f"{self.upstream.revision}/{encoded}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.gdpval_100.build")
    parser.add_argument("--parquet", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path(".local/gdpval-100/snapshots"))
    parser.add_argument("--asset-root", type=Path)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all-assets", action="store_true")
    selection.add_argument("--asset-case", action="append")
    selection.add_argument("--asset-cases", type=Path)
    selection.add_argument("--smoke-count", type=int)
    args = parser.parse_args()
    asset_case_ids = frozenset(args.asset_case) if args.asset_case is not None else None
    if args.asset_cases is not None:
        body = json.loads(args.asset_cases.read_bytes())
        if not isinstance(body, list) or not all(isinstance(value, str) for value in body):
            raise ValueError("GDPval asset case file must be a JSON list of task ids")
        asset_case_ids = frozenset(body)
    root = SnapshotBuilder(
        parquet=args.parquet,
        output_root=args.out,
        asset_case_ids=asset_case_ids,
        smoke_count=args.smoke_count,
        all_assets=args.all_assets,
        asset_root=args.asset_root,
    ).build()
    print(root)


if __name__ == "__main__":
    main()
