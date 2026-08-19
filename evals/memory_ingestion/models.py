import gzip
import hashlib
import json
from io import BytesIO
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

type Corpus = Literal["longmem", "locomo"]

CASES_FILE = "cases.jsonl.gz"
PAGES_FILE = "pages.jsonl.gz"
MANIFEST_FILE = "snapshot.json"
NULL_CHARACTER = "\x00"


class UpstreamAsset(BaseModel):
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    license: str = Field(min_length=1)


class IngestionCase(BaseModel):
    id: str = Field(min_length=1)
    corpus: Corpus
    category: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expected_answer: str = Field(min_length=1)
    evidence_refs: tuple[str, ...] = ()


class IngestionPage(BaseModel):
    source_ref: str = Field(min_length=1)
    evidence_ref: str = Field(min_length=1)
    body: str = Field(min_length=1)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    origin: str = Field(min_length=1)


class SnapshotFile(BaseModel):
    path: str = Field(pattern=r"^[a-z0-9_.-]+$")
    records: int = Field(ge=0)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class IngestionManifest(BaseModel):
    name: Literal["memory_ingestion"] = "memory_ingestion"
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    builder_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    upstreams: tuple[UpstreamAsset, ...]
    cases: SnapshotFile
    pages: SnapshotFile


class IngestionSnapshot(BaseModel):
    manifest: IngestionManifest
    cases: tuple[IngestionCase, ...]
    pages: tuple[IngestionPage, ...]


def canonical_json(model: BaseModel) -> bytes:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


def content_digest(body: str) -> str:
    return f"sha256:{hashlib.sha256(body.encode()).hexdigest()}"


def write_snapshot(
    root: Path,
    *,
    upstreams: tuple[UpstreamAsset, ...],
    builder_digest: str,
    cases: tuple[IngestionCase, ...],
    pages: tuple[IngestionPage, ...],
) -> IngestionManifest:
    """Write one deterministic ingestion snapshot."""
    _validate_records(cases, pages)
    root.mkdir(parents=True, exist_ok=True)
    case_file = _write_jsonl(root / CASES_FILE, cases)
    page_file = _write_jsonl(root / PAGES_FILE, pages)
    digest = _snapshot_digest(upstreams, builder_digest, case_file, page_file)
    manifest = IngestionManifest(
        digest=digest,
        builder_digest=builder_digest,
        upstreams=upstreams,
        cases=case_file,
        pages=page_file,
    )
    (root / MANIFEST_FILE).write_bytes(canonical_json(manifest) + b"\n")
    return manifest


def load_snapshot(root: Path) -> IngestionSnapshot:
    """Load and attest one ingestion snapshot."""
    manifest = IngestionManifest.model_validate_json((root / MANIFEST_FILE).read_bytes())
    if (manifest.cases.path, manifest.pages.path) != (CASES_FILE, PAGES_FILE):
        raise ValueError("memory_ingestion manifest names unexpected record files")
    cases = _read_jsonl(root, manifest.cases, IngestionCase)
    pages = _read_jsonl(root, manifest.pages, IngestionPage)
    if manifest.digest != _snapshot_digest(
        manifest.upstreams, manifest.builder_digest, manifest.cases, manifest.pages
    ):
        raise ValueError("memory_ingestion snapshot digest does not match its files")
    _validate_records(cases, pages)
    return IngestionSnapshot(manifest=manifest, cases=cases, pages=pages)


def _write_jsonl(path: Path, records: tuple[BaseModel, ...]) -> SnapshotFile:
    payload = b"".join(canonical_json(record) + b"\n" for record in records)
    buffer = BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as compressed:
        compressed.write(payload)
    path.write_bytes(buffer.getvalue())
    return SnapshotFile(
        path=path.name,
        records=len(records),
        sha256=f"sha256:{hashlib.sha256(payload).hexdigest()}",
    )


def _read_jsonl[ModelT: BaseModel](
    root: Path, expected: SnapshotFile, model: type[ModelT]
) -> tuple[ModelT, ...]:
    payload = gzip.decompress((root / expected.path).read_bytes())
    digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    if digest != expected.sha256:
        raise ValueError(f"{expected.path} digest does not match memory_ingestion manifest")
    records = tuple(model.model_validate_json(line) for line in payload.splitlines())
    if len(records) != expected.records:
        raise ValueError(f"{expected.path} record count does not match memory_ingestion manifest")
    return records


def _snapshot_digest(
    upstreams: tuple[UpstreamAsset, ...], builder_digest: str, *files: SnapshotFile
) -> str:
    payload = builder_digest.encode() + b"\n"
    payload += b"".join(canonical_json(upstream) + b"\n" for upstream in upstreams)
    payload += b"".join(
        f"{file.path}\0{file.records}\0{file.sha256}\n".encode()
        for file in sorted(files, key=lambda item: item.path)
    )
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _validate_records(cases: tuple[IngestionCase, ...], pages: tuple[IngestionPage, ...]) -> None:
    if not cases or len(cases) > 100:
        raise ValueError(f"memory_ingestion requires 1 to 100 cases, found {len(cases)}")
    case_ids = [case.id for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("memory_ingestion snapshot contains duplicate case ids")
    page_refs = [page.source_ref for page in pages]
    if len(set(page_refs)) != len(page_refs):
        raise ValueError("memory_ingestion snapshot contains duplicate page source refs")
    duplicate_evidence = next(
        (case.id for case in cases if len(set(case.evidence_refs)) != len(case.evidence_refs)),
        None,
    )
    if duplicate_evidence is not None:
        raise ValueError(
            f"memory_ingestion case {duplicate_evidence!r} contains duplicate evidence refs"
        )
    evidence_refs = {page.evidence_ref for page in pages}
    missing = sorted({ref for case in cases for ref in case.evidence_refs} - evidence_refs)
    if missing:
        raise ValueError(f"memory_ingestion snapshot is missing evidence: {', '.join(missing)}")
    nonportable = next(
        (
            label
            for label, value in (
                *((f"case {case.id!r} question", case.question) for case in cases),
                *((f"page {page.source_ref!r} source ref", page.source_ref) for page in pages),
                *((f"page {page.source_ref!r} evidence ref", page.evidence_ref) for page in pages),
                *((f"page {page.source_ref!r} body", page.body) for page in pages),
            )
            if NULL_CHARACTER in value
        ),
        None,
    )
    if nonportable is not None:
        raise ValueError(f"memory_ingestion snapshot {nonportable} contains a NUL character")
    bad_page = next(
        (page.source_ref for page in pages if page.digest != content_digest(page.body)), None
    )
    if bad_page is not None:
        raise ValueError(f"memory_ingestion page {bad_page!r} has an invalid digest")
