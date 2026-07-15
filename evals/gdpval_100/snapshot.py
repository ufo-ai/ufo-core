import gzip
import hashlib
import json
import shutil
from io import BytesIO
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from evals.gdpval_100.models import (
    Snapshot,
    SnapshotAsset,
    SnapshotCase,
    SnapshotFile,
    SnapshotManifest,
    UpstreamParquet,
)

CASES_FILE = "cases.jsonl.gz"
MANIFEST_FILE = "snapshot.json"
OBJECTS_ROOT = "objects/sha256"
UPSTREAM_FILE = Path(__file__).parent / "data" / "upstream.json"
EXPECTED_OCCUPATIONS = 44
TASKS_PER_OCCUPATION = 5

GDPVAL_UPSTREAM = UpstreamParquet.model_validate_json(UPSTREAM_FILE.read_bytes())


def canonical_json(model: BaseModel) -> bytes:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


def content_digest(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def write_snapshot(
    staging_root: Path,
    output_root: Path,
    upstream: UpstreamParquet,
    cases: tuple[SnapshotCase, ...],
    materialized_case_ids: tuple[str, ...],
) -> Path:
    _validate_cases(cases)
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


def load_snapshot(root: Path, expected_upstream: UpstreamParquet = GDPVAL_UPSTREAM) -> Snapshot:
    manifest = SnapshotManifest.model_validate_json((root / MANIFEST_FILE).read_bytes())
    if manifest.upstream != expected_upstream:
        raise ValueError("GDPval snapshot does not use the pinned upstream parquet")
    payload = gzip.decompress((root / manifest.cases.path).read_bytes())
    if content_digest(payload) != manifest.cases.sha256:
        raise ValueError("GDPval case records do not match the snapshot manifest")
    cases = tuple(SnapshotCase.model_validate_json(line) for line in payload.splitlines())
    _validate_cases(cases)
    if (
        _snapshot_digest(manifest.upstream, manifest.cases, manifest.materialized_case_ids)
        != manifest.digest
    ):
        raise ValueError("GDPval snapshot digest does not match its manifest")
    _validate_materialization(cases, manifest.materialized_case_ids)
    for case in cases:
        for asset in (*case.references, *case.deliverables):
            _verify_snapshot_asset(root, asset)
    return Snapshot(root=str(root.resolve()), manifest=manifest, cases=cases)


def _snapshot_digest(
    upstream: UpstreamParquet,
    cases: SnapshotFile,
    materialized_case_ids: tuple[str, ...],
) -> str:
    payload = canonical_json(upstream) + b"\n" + canonical_json(cases) + b"\n"
    payload += b"".join(f"{task_id}\n".encode() for task_id in materialized_case_ids)
    return content_digest(payload)


def _validate_cases(cases: tuple[SnapshotCase, ...]) -> None:
    if len(cases) != GDPVAL_UPSTREAM.rows:
        raise ValueError(f"GDPval requires exactly 220 cases, found {len(cases)}")
    task_ids = [case.task_id for case in cases]
    if len(set(task_ids)) != len(task_ids):
        raise ValueError("GDPval snapshot contains duplicate task ids")
    occupations = {case.occupation for case in cases}
    if len(occupations) != EXPECTED_OCCUPATIONS:
        raise ValueError(
            f"GDPval requires exactly {EXPECTED_OCCUPATIONS} occupations, found {len(occupations)}"
        )
    bad_count = next(
        (
            occupation
            for occupation in occupations
            if sum(case.occupation == occupation for case in cases) != TASKS_PER_OCCUPATION
        ),
        None,
    )
    if bad_count is not None:
        raise ValueError(
            f"GDPval occupation {bad_count!r} does not contain {TASKS_PER_OCCUPATION} cases"
        )
    asset_paths = [
        asset.relative_path for case in cases for asset in (*case.references, *case.deliverables)
    ]
    if len(asset_paths) != len(set(asset_paths)):
        raise ValueError("GDPval snapshot contains duplicate upstream asset paths")
    for case in cases:
        for prefix, assets in (
            ("reference_files", case.references),
            ("deliverable_files", case.deliverables),
        ):
            for asset in assets:
                _validate_relative_path(asset.relative_path, prefix)


def _validate_relative_path(value: str, prefix: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.parts[0] != prefix:
        raise ValueError(f"GDPval asset path is not a safe {prefix} path: {value!r}")


def _validate_materialization(
    cases: tuple[SnapshotCase, ...], materialized_case_ids: tuple[str, ...]
) -> None:
    known_case_ids = {case.task_id for case in cases}
    selected = set(materialized_case_ids)
    if len(selected) != len(materialized_case_ids):
        raise ValueError("GDPval materialized case ids contain duplicates")
    if not selected <= known_case_ids:
        raise ValueError("GDPval materialized case ids contain an unknown task")
    for case in cases:
        for asset in (*case.references, *case.deliverables):
            if (asset.snapshot_path is not None) != (case.task_id in selected):
                raise ValueError(
                    f"GDPval case {case.task_id!r} does not match its asset materialization state"
                )


def _verify_snapshot_asset(root: Path, asset: SnapshotAsset) -> None:
    if asset.snapshot_path is None or asset.sha256 is None or asset.size_bytes is None:
        return
    expected = f"{OBJECTS_ROOT}/{asset.sha256.removeprefix('sha256:')}"
    if asset.snapshot_path != expected:
        raise ValueError(f"GDPval asset {asset.relative_path!r} has a noncanonical snapshot path")
    path = root / asset.snapshot_path
    body = path.read_bytes()
    if len(body) != asset.size_bytes:
        raise ValueError(f"GDPval asset {asset.relative_path!r} size does not match")
    if content_digest(body) != asset.sha256:
        raise ValueError(f"GDPval asset {asset.relative_path!r} digest does not match")
