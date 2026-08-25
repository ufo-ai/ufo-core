import gzip
import hashlib
import json
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from evals.swebench.models import (
    SnapshotFile,
    SnapshotManifest,
    SWEbenchCase,
    SWEbenchSnapshot,
    SWEbenchUpstream,
)

CASES_FILE = "cases.jsonl.gz"
MANIFEST_FILE = "snapshot.json"
UPSTREAM_FILE = Path(__file__).parent / "data" / "upstream.json"


def canonical_json(model: BaseModel) -> bytes:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


def content_digest(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def load_upstream() -> SWEbenchUpstream:
    return SWEbenchUpstream.model_validate_json(UPSTREAM_FILE.read_bytes())


SWEBENCH_UPSTREAM = load_upstream()


def verify_source(source: bytes, upstream: SWEbenchUpstream = SWEBENCH_UPSTREAM) -> None:
    """Fail unless the parquet's byte size and digest match the pin."""
    if len(source) != upstream.parquet.size_bytes:
        raise ValueError(
            "SWE-bench parquet size mismatch: "
            f"expected {upstream.parquet.size_bytes}, found {len(source)}"
        )
    digest = f"sha256:{hashlib.sha256(source).hexdigest()}"
    if digest != upstream.parquet.sha256:
        raise ValueError(
            f"SWE-bench parquet digest mismatch: expected {upstream.parquet.sha256}, found {digest}"
        )


def select_cases(
    rows: Iterable[Mapping[str, object]], upstream: SWEbenchUpstream = SWEBENCH_UPSTREAM
) -> tuple[SWEbenchCase, ...]:
    """Order the approved rows as the roster names them, refusing any other membership."""
    approved_ids = upstream.subsets.all_ids
    cases = tuple(SWEbenchCase.model_validate(row) for row in rows)
    case_ids = tuple(case.instance_id for case in cases)
    duplicates = sorted(case_id for case_id in set(case_ids) if case_ids.count(case_id) > 1)
    if duplicates:
        raise ValueError(f"duplicate SWE-bench selected instance ids: {', '.join(duplicates)}")
    unknown = sorted(set(case_ids) - set(approved_ids))
    if unknown:
        raise ValueError(f"unknown SWE-bench selected instance ids: {', '.join(unknown)}")
    missing = tuple(case_id for case_id in approved_ids if case_id not in case_ids)
    if missing:
        raise ValueError(f"missing SWE-bench selected instance ids: {', '.join(missing)}")
    by_id = {case.instance_id: case for case in cases}
    return tuple(by_id[case_id] for case_id in approved_ids)


def write_snapshot(
    output: Path,
    cases: tuple[SWEbenchCase, ...],
    upstream: SWEbenchUpstream = SWEBENCH_UPSTREAM,
) -> Path:
    _validate_cases(cases, upstream.subsets.all_ids)
    if output.exists() and not output.is_symlink():
        raise ValueError(f"SWE-bench snapshot output must be a symbolic link: {output}")
    payload = b"".join(canonical_json(case) + b"\n" for case in cases)
    case_file = SnapshotFile(sha256=content_digest(payload))
    manifest = SnapshotManifest(
        digest=_snapshot_digest(upstream, case_file, upstream.subsets.all_ids),
        upstream=upstream,
        cases=case_file,
        case_ids=upstream.subsets.all_ids,
    )
    versions = output.parent / f"{output.name}.versions"
    if versions.is_symlink():
        raise ValueError(
            f"SWE-bench snapshot versions path must not be a symbolic link: {versions}"
        )
    if versions.exists() and not versions.is_dir():
        raise ValueError(f"SWE-bench snapshot versions path is not a directory: {versions}")
    versions.mkdir(parents=True, exist_ok=True)
    destination = versions / manifest.digest.removeprefix("sha256:")
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=versions))
    try:
        buffer = BytesIO()
        with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as compressed:
            compressed.write(payload)
        (staging / CASES_FILE).write_bytes(buffer.getvalue())
        (staging / MANIFEST_FILE).write_bytes(canonical_json(manifest) + b"\n")
        loaded = _load_snapshot_version(staging, require_digest_path=False)
        if loaded.manifest != manifest or loaded.cases != cases:
            raise ValueError("staged SWE-bench snapshot does not match its source records")
        if destination.exists():
            if destination.is_symlink() or not destination.is_dir():
                raise ValueError(
                    f"SWE-bench snapshot version is not an immutable directory: {destination}"
                )
            existing = _load_snapshot_version(destination)
            if existing.manifest != manifest or existing.cases != cases:
                raise ValueError(
                    f"SWE-bench snapshot version contains different content: {destination}"
                )
            shutil.rmtree(staging)
        else:
            staging.replace(destination)
        _replace_pointer(output, destination)
        return output
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def load_snapshot(root: Path = Path(".local/swebench/snapshot")) -> SWEbenchSnapshot:
    if not root.is_symlink():
        raise ValueError(f"SWE-bench snapshot root must be a symbolic link: {root}")
    version = root.resolve(strict=True)
    return _load_snapshot_version(version)


def _load_snapshot_version(version: Path, *, require_digest_path: bool = True) -> SWEbenchSnapshot:
    manifest = SnapshotManifest.model_validate_json((version / MANIFEST_FILE).read_bytes())
    if manifest.upstream != SWEBENCH_UPSTREAM:
        raise ValueError("SWE-bench snapshot does not use the pinned upstream parquet")
    payload = gzip.decompress((version / manifest.cases.path).read_bytes())
    if content_digest(payload) != manifest.cases.sha256:
        raise ValueError("SWE-bench case records do not match the snapshot manifest")
    cases = tuple(SWEbenchCase.model_validate_json(line) for line in payload.splitlines())
    _validate_cases(cases, manifest.upstream.subsets.all_ids)
    if manifest.case_ids != tuple(case.instance_id for case in cases):
        raise ValueError("SWE-bench snapshot cases do not use manifest order")
    expected_digest = _snapshot_digest(manifest.upstream, manifest.cases, manifest.case_ids)
    if manifest.digest != expected_digest:
        raise ValueError("SWE-bench snapshot digest does not match its manifest")
    if require_digest_path and version.name != manifest.digest.removeprefix("sha256:"):
        raise ValueError("SWE-bench snapshot pointer names a noncanonical version")
    return SWEbenchSnapshot(root=str(version), manifest=manifest, cases=cases)


def _snapshot_digest(
    upstream: SWEbenchUpstream,
    cases: SnapshotFile,
    case_ids: tuple[str, ...],
) -> str:
    payload = canonical_json(upstream) + b"\n" + canonical_json(cases) + b"\n"
    payload += b"".join(f"{case_id}\n".encode() for case_id in case_ids)
    return content_digest(payload)


def _validate_cases(cases: tuple[SWEbenchCase, ...], approved_ids: tuple[str, ...]) -> None:
    if len(cases) != len(approved_ids):
        raise ValueError(
            f"SWE-bench snapshot requires exactly {len(approved_ids)} cases, found {len(cases)}"
        )
    case_ids = tuple(case.instance_id for case in cases)
    if case_ids != approved_ids:
        raise ValueError("SWE-bench snapshot cases do not use the approved order")


def _replace_pointer(output: Path, destination: Path) -> None:
    temporary = output.parent / f".{output.name}-pointer-{uuid4().hex}"
    target = destination.relative_to(output.parent)
    temporary.symlink_to(target, target_is_directory=True)
    try:
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
