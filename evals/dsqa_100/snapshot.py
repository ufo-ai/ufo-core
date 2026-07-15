import gzip
import hashlib
import json
from collections import Counter
from io import BytesIO
from pathlib import Path

from pydantic import BaseModel

from evals.dsqa_100.models import (
    SUBSET_SIZE,
    Snapshot,
    SnapshotCase,
    SnapshotFile,
    SnapshotManifest,
    UpstreamAsset,
)

CASES_FILE = "cases.jsonl.gz"
MANIFEST_FILE = "snapshot.json"
EXPECTED_CATEGORIES = {
    "Arts": 3,
    "Arts & Entertainment": 1,
    "Biology": 1,
    "Current Events": 1,
    "Education": 10,
    "Finance & Economics": 14,
    "Geography": 10,
    "Health": 10,
    "History": 5,
    "Linguistics": 1,
    "Media & Entertainment": 3,
    "Other": 7,
    "Politics & Government": 16,
    "Science": 10,
    "Sports": 2,
    "Technology": 2,
    "Travel": 4,
}


def canonical_json(model: BaseModel) -> bytes:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


def content_digest(body: str) -> str:
    return f"sha256:{hashlib.sha256(body.encode()).hexdigest()}"


def write_snapshot(
    root: Path,
    *,
    upstream: UpstreamAsset,
    builder_digest: str,
    cases: tuple[SnapshotCase, ...],
) -> SnapshotManifest:
    _validate_cases(cases)
    root.mkdir(parents=True, exist_ok=True)
    payload = b"".join(canonical_json(case) + b"\n" for case in cases)
    buffer = BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as compressed:
        compressed.write(payload)
    (root / CASES_FILE).write_bytes(buffer.getvalue())
    case_file = SnapshotFile(sha256=f"sha256:{hashlib.sha256(payload).hexdigest()}")
    digest = _snapshot_digest(upstream, builder_digest, case_file)
    manifest = SnapshotManifest(
        digest=digest,
        builder_digest=builder_digest,
        upstream=upstream,
        cases=case_file,
    )
    (root / MANIFEST_FILE).write_bytes(canonical_json(manifest) + b"\n")
    return manifest


def load_snapshot(root: Path) -> Snapshot:
    manifest = SnapshotManifest.model_validate_json((root / MANIFEST_FILE).read_bytes())
    payload = gzip.decompress((root / manifest.cases.path).read_bytes())
    digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    if digest != manifest.cases.sha256:
        raise ValueError("cases.jsonl.gz digest does not match snapshot manifest")
    cases = tuple(SnapshotCase.model_validate_json(line) for line in payload.splitlines())
    if len(cases) != manifest.cases.records:
        raise ValueError("cases.jsonl.gz record count does not match snapshot manifest")
    if manifest.digest != _snapshot_digest(
        manifest.upstream, manifest.builder_digest, manifest.cases
    ):
        raise ValueError("snapshot digest does not match its files")
    _validate_cases(cases)
    return Snapshot(manifest=manifest, cases=cases)


def _snapshot_digest(upstream: UpstreamAsset, builder_digest: str, case_file: SnapshotFile) -> str:
    payload = builder_digest.encode() + b"\n"
    payload += canonical_json(upstream) + b"\n"
    payload += f"{case_file.path}\0{case_file.records}\0{case_file.sha256}\n".encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _validate_cases(cases: tuple[SnapshotCase, ...]) -> None:
    if len(cases) != SUBSET_SIZE:
        raise ValueError(f"dsqa_100 requires exactly {SUBSET_SIZE} cases, found {len(cases)}")
    ids = [case.example_id for case in cases]
    if ids != sorted(ids) or len(set(ids)) != len(ids):
        raise ValueError("dsqa_100 case ids must be sorted and unique")
    bands = Counter(case.pressure_band for case in cases)
    if bands != {"lower": 50, "higher": 50}:
        raise ValueError(f"dsqa_100 pressure bands are invalid: {dict(bands)}")
    categories = Counter(case.category for case in cases)
    if categories != EXPECTED_CATEGORIES:
        raise ValueError(f"dsqa_100 category counts are invalid: {dict(categories)}")
    answer_types = Counter(case.answer_type for case in cases)
    if answer_types != {"Set Answer": 66, "Single Answer": 34}:
        raise ValueError(f"dsqa_100 answer type counts are invalid: {dict(answer_types)}")
    bad_digest = next(
        (case.example_id for case in cases if case.problem_sha256 != content_digest(case.problem)),
        None,
    )
    if bad_digest is not None:
        raise ValueError(f"dsqa_100 case {bad_digest} has an invalid problem digest")
