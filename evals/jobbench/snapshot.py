import gzip
import hashlib
import json
import shutil
from io import BytesIO
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from evals.jobbench.models import (
    Snapshot,
    SnapshotAsset,
    SnapshotCase,
    SnapshotFile,
    SnapshotManifest,
    Split,
    UpstreamCorpus,
)

CASES_FILE = "cases.jsonl.gz"
MANIFEST_FILE = "snapshot.json"
OBJECTS_ROOT = "objects/sha256"
UPSTREAM_FILE = Path(__file__).parent / "data" / "upstream.json"
OCCUPATIONS_PER_SPLIT = 35
SPLIT_ROOTS: dict[Split, str] = {"main": "dataset", "easy": "dataset_easy"}

JOBBENCH_UPSTREAM = UpstreamCorpus.model_validate_json(UPSTREAM_FILE.read_bytes())


def canonical_json(model: BaseModel) -> bytes:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


def content_digest(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def write_snapshot(
    staging_root: Path,
    output_root: Path,
    upstream: UpstreamCorpus,
    cases: tuple[SnapshotCase, ...],
    materialized_case_ids: tuple[str, ...],
) -> Path:
    _validate_cases(cases, upstream)
    _validate_materialization(cases, materialized_case_ids)
    payload = b"".join(canonical_json(case) + b"\n" for case in cases)
    case_file = SnapshotFile(sha256=content_digest(payload))
    digest = _snapshot_digest(upstream, case_file, materialized_case_ids)
    manifest = SnapshotManifest(
        digest=digest,
        upstream=upstream,
        cases=case_file,
        materialized_case_ids=materialized_case_ids,
    )
    buffer = BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as compressed:
        compressed.write(payload)
    (staging_root / CASES_FILE).write_bytes(buffer.getvalue())
    (staging_root / MANIFEST_FILE).write_bytes(canonical_json(manifest) + b"\n")
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / digest.removeprefix("sha256:")
    if destination.exists():
        existing = load_snapshot(destination, upstream)
        if existing.manifest != manifest or existing.cases != cases:
            raise ValueError(f"snapshot destination contains different content: {destination}")
        shutil.rmtree(staging_root)
        return destination
    staging_root.replace(destination)
    return destination


def load_snapshot(root: Path, expected_upstream: UpstreamCorpus = JOBBENCH_UPSTREAM) -> Snapshot:
    manifest = SnapshotManifest.model_validate_json((root / MANIFEST_FILE).read_bytes())
    if manifest.upstream != expected_upstream:
        raise ValueError("JobBench snapshot does not use the pinned upstream corpus")
    payload = gzip.decompress((root / manifest.cases.path).read_bytes())
    if content_digest(payload) != manifest.cases.sha256:
        raise ValueError("JobBench case records do not match the snapshot manifest")
    cases = tuple(SnapshotCase.model_validate_json(line) for line in payload.splitlines())
    _validate_cases(cases, manifest.upstream)
    if (
        _snapshot_digest(manifest.upstream, manifest.cases, manifest.materialized_case_ids)
        != manifest.digest
    ):
        raise ValueError("JobBench snapshot digest does not match its manifest")
    _validate_materialization(cases, manifest.materialized_case_ids)
    for case in cases:
        for asset in case.references:
            _verify_snapshot_asset(root, asset)
    return Snapshot(root=str(root.resolve()), manifest=manifest, cases=cases)


def _snapshot_digest(
    upstream: UpstreamCorpus,
    cases: SnapshotFile,
    materialized_case_ids: tuple[str, ...],
) -> str:
    payload = canonical_json(upstream) + b"\n" + canonical_json(cases) + b"\n"
    payload += b"".join(f"{case_id}\n".encode() for case_id in materialized_case_ids)
    return content_digest(payload)


def _validate_cases(cases: tuple[SnapshotCase, ...], upstream: UpstreamCorpus) -> None:
    expected_rows = upstream.main.rows + upstream.easy.rows
    if len(cases) != expected_rows:
        raise ValueError(f"JobBench requires exactly {expected_rows} cases, found {len(cases)}")
    case_ids = [case.case_id for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("JobBench snapshot contains duplicate case ids")
    for split in ("main", "easy"):
        split_cases = tuple(case for case in cases if case.split == split)
        if len(split_cases) != upstream.split_parquet(split).rows:
            raise ValueError(
                f"JobBench {split} split requires {upstream.split_parquet(split).rows} cases, "
                f"found {len(split_cases)}"
            )
        occupations = {case.occupation for case in split_cases}
        if len(occupations) != OCCUPATIONS_PER_SPLIT:
            raise ValueError(
                f"JobBench {split} split requires {OCCUPATIONS_PER_SPLIT} occupations, "
                f"found {len(occupations)}"
            )
    asset_paths = [asset.relative_path for case in cases for asset in case.references]
    if len(asset_paths) != len(set(asset_paths)):
        raise ValueError("JobBench snapshot contains duplicate upstream asset paths")
    for case in cases:
        _validate_case_assets(case)


def _validate_case_assets(case: SnapshotCase) -> None:
    prefix = f"{SPLIT_ROOTS[case.split]}/{case.occupation}/task{case.task_num}/task_folder"
    names = []
    for asset in case.references:
        path = PurePosixPath(asset.relative_path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"JobBench asset path is unsafe: {asset.relative_path!r}")
        if str(path.parent) != prefix or not path.name:
            raise ValueError(
                f"JobBench case {case.case_id!r} asset is outside its task folder: "
                f"{asset.relative_path!r}"
            )
        names.append(path.name.casefold())
    if len(set(names)) != len(names):
        raise ValueError(f"JobBench case {case.case_id!r} reference basenames collide")


def _validate_materialization(
    cases: tuple[SnapshotCase, ...], materialized_case_ids: tuple[str, ...]
) -> None:
    known_case_ids = {case.case_id for case in cases}
    selected = set(materialized_case_ids)
    if len(selected) != len(materialized_case_ids):
        raise ValueError("JobBench materialized case ids contain duplicates")
    if not selected <= known_case_ids:
        raise ValueError("JobBench materialized case ids contain an unknown case")
    for case in cases:
        for asset in case.references:
            if (asset.snapshot_path is not None) != (case.case_id in selected):
                raise ValueError(
                    f"JobBench case {case.case_id!r} does not match its materialization state"
                )


def _verify_snapshot_asset(root: Path, asset: SnapshotAsset) -> None:
    if asset.snapshot_path is None or asset.sha256 is None or asset.size_bytes is None:
        return
    expected = f"{OBJECTS_ROOT}/{asset.sha256.removeprefix('sha256:')}"
    if asset.snapshot_path != expected:
        raise ValueError(f"JobBench asset {asset.relative_path!r} has a noncanonical path")
    body = (root / asset.snapshot_path).read_bytes()
    if len(body) != asset.size_bytes:
        raise ValueError(f"JobBench asset {asset.relative_path!r} size does not match")
    if content_digest(body) != asset.sha256:
        raise ValueError(f"JobBench asset {asset.relative_path!r} digest does not match")
