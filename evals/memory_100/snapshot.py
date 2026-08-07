import gzip
import hashlib
import json
from io import BytesIO
from pathlib import Path

from pydantic import BaseModel

from evals.memory_100.models import (
    Snapshot,
    SnapshotCase,
    SnapshotFile,
    SnapshotManifest,
    SnapshotMemory,
    SnapshotPage,
    UpstreamAsset,
)

CASES_FILE = "cases.jsonl.gz"
PAGES_FILE = "pages.jsonl.gz"
MEMORIES_FILE = "memory_items.jsonl.gz"
MANIFEST_FILE = "snapshot.json"
NULL_CHARACTER = "\x00"
CORPUS_COUNTS = {"enterprise": 60, "longmem": 30, "ufo": 12}
CASE_COUNT = sum(CORPUS_COUNTS.values())


def canonical_json(model: BaseModel) -> bytes:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


def content_digest(body: str) -> str:
    return f"sha256:{hashlib.sha256(body.encode()).hexdigest()}"


def write_snapshot(
    root: Path,
    *,
    upstreams: tuple[UpstreamAsset, ...],
    builder_digest: str,
    cases: tuple[SnapshotCase, ...],
    pages: tuple[SnapshotPage, ...],
    memories: tuple[SnapshotMemory, ...],
) -> SnapshotManifest:
    _validate_records(cases, pages, memories)
    root.mkdir(parents=True, exist_ok=True)
    case_file = _write_jsonl(root / CASES_FILE, cases)
    page_file = _write_jsonl(root / PAGES_FILE, pages)
    memory_file = _write_jsonl(root / MEMORIES_FILE, memories)
    digest = _snapshot_digest(upstreams, builder_digest, case_file, page_file, memory_file)
    manifest = SnapshotManifest(
        digest=digest,
        builder_digest=builder_digest,
        upstreams=upstreams,
        cases=case_file,
        pages=page_file,
        memories=memory_file,
    )
    (root / MANIFEST_FILE).write_bytes(canonical_json(manifest) + b"\n")
    return manifest


def load_snapshot(root: Path) -> Snapshot:
    manifest = SnapshotManifest.model_validate_json((root / MANIFEST_FILE).read_bytes())
    expected_paths = (manifest.cases.path, manifest.pages.path, manifest.memories.path)
    if expected_paths != (CASES_FILE, PAGES_FILE, MEMORIES_FILE):
        raise ValueError("snapshot manifest names unexpected record files")
    cases = _read_jsonl(root, manifest.cases, SnapshotCase)
    pages = _read_jsonl(root, manifest.pages, SnapshotPage)
    memories = _read_jsonl(root, manifest.memories, SnapshotMemory)
    if manifest.digest != _snapshot_digest(
        manifest.upstreams,
        manifest.builder_digest,
        manifest.cases,
        manifest.pages,
        manifest.memories,
    ):
        raise ValueError("snapshot digest does not match its files")
    _validate_records(cases, pages, memories)
    return Snapshot(manifest=manifest, cases=cases, pages=pages, memories=memories)


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
        raise ValueError(f"{expected.path} digest does not match snapshot manifest")
    records = tuple(model.model_validate_json(line) for line in payload.splitlines())
    if len(records) != expected.records:
        raise ValueError(f"{expected.path} record count does not match snapshot manifest")
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


def _validate_records(
    cases: tuple[SnapshotCase, ...],
    pages: tuple[SnapshotPage, ...],
    memories: tuple[SnapshotMemory, ...],
) -> None:
    if len(cases) != CASE_COUNT:
        raise ValueError(f"memory_100 requires exactly {CASE_COUNT} cases, found {len(cases)}")
    counts = {corpus: sum(case.corpus == corpus for case in cases) for corpus in CORPUS_COUNTS}
    if counts != CORPUS_COUNTS:
        raise ValueError(f"memory_100 corpus counts are invalid: {counts}")
    nonportable = next(
        (
            label
            for label, value in (
                *((f"case {case.id!r} question", case.question) for case in cases),
                *((f"page {page.source_ref!r} source ref", page.source_ref) for page in pages),
                *((f"page {page.source_ref!r} body", page.body) for page in pages),
                *(
                    (f"memory {memory.source_ref!r} source ref", memory.source_ref)
                    for memory in memories
                ),
                *((f"memory {memory.source_ref!r} body", memory.body) for memory in memories),
            )
            if NULL_CHARACTER in value
        ),
        None,
    )
    if nonportable is not None:
        raise ValueError(f"snapshot {nonportable} contains a database-incompatible NUL character")
    case_ids = [case.id for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("snapshot contains duplicate case ids")
    duplicate_evidence = next(
        (case.id for case in cases if len(set(case.evidence_refs)) != len(case.evidence_refs)),
        None,
    )
    if duplicate_evidence is not None:
        raise ValueError(f"snapshot case {duplicate_evidence!r} contains duplicate evidence refs")
    page_refs = [page.source_ref for page in pages]
    if len(set(page_refs)) != len(page_refs):
        raise ValueError("snapshot contains duplicate page source refs")
    memory_refs = [memory.source_ref for memory in memories]
    if len(set(memory_refs)) != len(memory_refs):
        raise ValueError("snapshot contains duplicate memory source refs")
    if set(page_refs) & set(memory_refs):
        raise ValueError("snapshot page and memory source refs overlap")
    evidence_refs = set(page_refs) | set(memory_refs)
    missing_evidence = sorted(
        {ref for case in cases for ref in case.evidence_refs if ref not in evidence_refs}
    )
    if missing_evidence:
        raise ValueError(f"snapshot is missing evidence refs: {', '.join(missing_evidence)}")
    bad_page = next(
        (page.source_ref for page in pages if page.digest != content_digest(page.body)), None
    )
    if bad_page is not None:
        raise ValueError(f"page {bad_page!r} has an invalid content digest")
    bad_memory = next(
        (memory.source_ref for memory in memories if memory.digest != content_digest(memory.body)),
        None,
    )
    if bad_memory is not None:
        raise ValueError(f"memory {bad_memory!r} has an invalid content digest")
