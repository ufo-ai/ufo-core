import argparse
import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

from pydantic import BaseModel, ConfigDict, Field

from evals.jobbench.models import (
    TASK_ID_PATTERN,
    RubricItem,
    SnapshotAsset,
    SnapshotCase,
    Split,
    UpstreamCorpus,
)
from evals.jobbench.snapshot import (
    JOBBENCH_UPSTREAM,
    OBJECTS_ROOT,
    SPLIT_ROOTS,
    write_snapshot,
)

DOWNLOAD_TIMEOUT_SECONDS = 120
COPY_BUFFER_BYTES = 1024 * 1024


class UpstreamRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(pattern=TASK_ID_PATTERN)
    occupation: str = Field(min_length=1)
    task_num: int = Field(ge=1)
    prompt: str = Field(min_length=1)
    reference_files: tuple[str, ...] = Field(min_length=1)
    reference_file_urls: tuple[str, ...]
    reference_file_hf_uris: tuple[str, ...]
    rubric_json: str = Field(min_length=1)
    task_card: str = Field(min_length=1)


@dataclass(frozen=True)
class SnapshotBuilder:
    tasks_parquet: Path
    easy_parquet: Path
    output_root: Path
    asset_case_ids: frozenset[str] | None = None
    smoke_count: int | None = None
    all_assets: bool = False
    asset_root: Path | None = None
    upstream: UpstreamCorpus = JOBBENCH_UPSTREAM

    def build(self) -> Path:
        cases = (
            *self._split_cases("main", self.tasks_parquet),
            *self._split_cases("easy", self.easy_parquet),
        )
        selected = self._selected_case_ids(cases)
        self.output_root.mkdir(parents=True, exist_ok=True)
        staging_root = Path(tempfile.mkdtemp(prefix=".jobbench-", dir=self.output_root))
        try:
            materialized = self._materialize_assets(staging_root, cases, selected)
            materialized_case_ids = tuple(
                case.case_id for case in cases if case.case_id in selected
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

    def _split_cases(self, split: Split, parquet: Path) -> tuple[SnapshotCase, ...]:
        pinned = self.upstream.split_parquet(split)
        size_bytes = parquet.stat().st_size
        if size_bytes != pinned.size_bytes:
            raise ValueError(
                f"JobBench {split} parquet size mismatch: expected {pinned.size_bytes}, "
                f"found {size_bytes}"
            )
        with parquet.open("rb") as source:
            digest = f"sha256:{hashlib.file_digest(source, 'sha256').hexdigest()}"
        if digest != pinned.sha256:
            raise ValueError(
                f"JobBench {split} parquet digest mismatch: expected {pinned.sha256}, "
                f"found {digest}"
            )
        rows = self._read_parquet(split, parquet)
        cases = tuple(self._case_from_row(split, row) for row in rows)
        case_ids = [case.case_id for case in cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError(f"JobBench {split} parquet contains duplicate task ids")
        return cases

    def _read_parquet(self, split: Split, parquet: Path) -> tuple[UpstreamRow, ...]:
        try:
            import pyarrow.parquet as parquet_reader
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "JobBench snapshot builds require pyarrow; run with "
                "`uv run --with pyarrow python -m evals.jobbench.build`"
            ) from error
        table = parquet_reader.read_table(parquet)
        observed = tuple((field.name, str(field.type)) for field in table.schema)
        expected = tuple((column.name, column.arrow_type) for column in self.upstream.columns)
        if observed != expected:
            raise ValueError(
                f"JobBench {split} parquet schema mismatch: expected {expected}, found {observed}"
            )
        pinned_rows = self.upstream.split_parquet(split).rows
        if table.num_rows != pinned_rows:
            raise ValueError(
                f"JobBench {split} parquet row count mismatch: expected {pinned_rows}, "
                f"found {table.num_rows}"
            )
        return tuple(UpstreamRow.model_validate(row) for row in table.to_pylist())

    def _case_from_row(self, split: Split, row: UpstreamRow) -> SnapshotCase:
        references = self._assets(split, row)
        rubric_body = json.loads(row.rubric_json)
        if not isinstance(rubric_body, dict) or set(rubric_body) != {"rubrics"}:
            raise ValueError(f"JobBench task {row.task_id!r} rubric is not a rubrics object")
        rubric = tuple(_rubric_item(row.task_id, item) for item in rubric_body["rubrics"])
        return SnapshotCase(
            case_id=f"{split}.{row.task_id}",
            split=split,
            task_id=row.task_id,
            occupation=row.occupation,
            task_num=row.task_num,
            prompt=row.prompt,
            references=references,
            rubric=rubric,
        )

    def _assets(self, split: Split, row: UpstreamRow) -> tuple[SnapshotAsset, ...]:
        paths = row.reference_files
        urls = row.reference_file_urls
        hf_uris = row.reference_file_hf_uris
        if len(paths) != len(urls) or len(paths) != len(hf_uris):
            raise ValueError(f"JobBench task {row.task_id!r} reference columns differ in length")
        prefix = f"{SPLIT_ROOTS[split]}/{row.occupation}/task{row.task_num}/task_folder/"
        assets = []
        for path, url, hf_uri in zip(paths, urls, hf_uris, strict=True):
            if not path.startswith(prefix):
                raise ValueError(
                    f"JobBench task {row.task_id!r} reference is outside its task folder: {path!r}"
                )
            encoded = quote(path, safe="/")
            expected_url = (
                f"https://huggingface.co/datasets/{self.upstream.repository}/resolve/main/{encoded}"
            )
            expected_hf_uri = f"hf://datasets/{self.upstream.repository}@main/{path}"
            if url != expected_url or hf_uri != expected_hf_uri:
                raise ValueError(
                    f"JobBench task {row.task_id!r} reference URL does not match its path: {path!r}"
                )
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
            raise ValueError("choose exactly one JobBench asset selection mode")
        known = {case.case_id for case in cases}
        if self.asset_case_ids is not None:
            unknown = sorted(self.asset_case_ids - known)
            if unknown:
                raise ValueError(f"unknown JobBench case ids: {', '.join(unknown)}")
            if not self.asset_case_ids:
                raise ValueError("JobBench case selection is empty")
            return self.asset_case_ids
        if self.smoke_count is not None:
            easy = tuple(case for case in cases if case.split == "easy")
            if not 1 <= self.smoke_count <= len(easy):
                raise ValueError(f"JobBench smoke count must be between 1 and {len(easy)}")
            return frozenset(case.case_id for case in easy[: self.smoke_count])
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
            if case.case_id not in selected:
                materialized.append(case)
                continue
            references = tuple(
                self._materialize_asset(staging_root, asset, cached) for asset in case.references
            )
            materialized.append(case.model_copy(update={"references": references}))
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
                raise FileNotFoundError(f"JobBench asset is missing: {asset_path}")
            shutil.copyfile(asset_path, temporary)
        with temporary.open("rb") as source:
            digest = f"sha256:{hashlib.file_digest(source, 'sha256').hexdigest()}"
        size_bytes = temporary.stat().st_size
        if size_bytes == 0:
            raise ValueError(f"JobBench asset is empty: {asset.relative_path}")
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


def _rubric_item(task_id: str, item: object) -> RubricItem:
    if not isinstance(item, dict) or set(item) != {"rubric", "weight", "criterion"}:
        raise ValueError(f"JobBench task {task_id!r} rubric item has an unexpected shape")
    criterion = item["criterion"]
    if not isinstance(criterion, list):
        raise ValueError(f"JobBench task {task_id!r} rubric criterion is not a list")
    return RubricItem(rubric=item["rubric"], weight=item["weight"], criteria=tuple(criterion))


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.jobbench.build")
    parser.add_argument("--tasks-parquet", type=Path, required=True)
    parser.add_argument("--easy-parquet", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path(".local/jobbench/snapshots"))
    parser.add_argument("--asset-root", type=Path)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--all-assets", action="store_true")
    selection.add_argument("--case", action="append")
    selection.add_argument("--cases", type=Path)
    selection.add_argument("--smoke-count", type=int)
    args = parser.parse_args()
    asset_case_ids = frozenset(args.case) if args.case is not None else None
    if args.cases is not None:
        body = json.loads(args.cases.read_bytes())
        if not isinstance(body, list) or not all(isinstance(value, str) for value in body):
            raise ValueError("JobBench case file must be a JSON list of case ids")
        asset_case_ids = frozenset(body)
    root = SnapshotBuilder(
        tasks_parquet=args.tasks_parquet,
        easy_parquet=args.easy_parquet,
        output_root=args.out,
        asset_case_ids=asset_case_ids,
        smoke_count=args.smoke_count,
        all_assets=args.all_assets,
        asset_root=args.asset_root,
    ).build()
    print(root)


if __name__ == "__main__":
    main()
