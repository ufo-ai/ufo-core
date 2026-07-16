"""The compaction snapshot's disk form: one gzip jsonl of cases pinned by a digesting manifest.
The digest folds the builder source, the harvested corpus, and the case bytes, so a report is
comparable only against runs of the byte-identical snapshot."""

import gzip
import hashlib
import json
from io import BytesIO
from pathlib import Path

from pydantic import BaseModel

from evals.compaction.models import (
    CompactionCase,
    CompactionManifest,
    CompactionSnapshot,
    CorpusFile,
    SnapshotFile,
)

CASES_FILE = "cases.jsonl.gz"
MANIFEST_FILE = "snapshot.json"


def canonical_json(model: BaseModel) -> bytes:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


def write_snapshot(
    root: Path,
    *,
    builder_digest: str,
    target_tokens: int,
    corpus: tuple[CorpusFile, ...],
    cases: tuple[CompactionCase, ...],
) -> CompactionManifest:
    _validate_cases(cases)
    root.mkdir(parents=True, exist_ok=True)
    case_file = _write_jsonl(root / CASES_FILE, cases)
    manifest = CompactionManifest(
        digest=_snapshot_digest(builder_digest, target_tokens, corpus, case_file),
        builder_digest=builder_digest,
        target_tokens=target_tokens,
        corpus=corpus,
        cases=case_file,
    )
    (root / MANIFEST_FILE).write_bytes(canonical_json(manifest) + b"\n")
    return manifest


def load_snapshot(root: Path) -> CompactionSnapshot:
    manifest = CompactionManifest.model_validate_json((root / MANIFEST_FILE).read_bytes())
    if manifest.cases.path != CASES_FILE:
        raise ValueError("compaction snapshot manifest names an unexpected case file")
    payload = gzip.decompress((root / manifest.cases.path).read_bytes())
    digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    if digest != manifest.cases.sha256:
        raise ValueError("compaction snapshot cases do not match the manifest digest")
    cases = tuple(CompactionCase.model_validate_json(line) for line in payload.splitlines())
    if len(cases) != manifest.cases.records:
        raise ValueError("compaction snapshot case count does not match the manifest")
    if manifest.digest != _snapshot_digest(
        manifest.builder_digest, manifest.target_tokens, manifest.corpus, manifest.cases
    ):
        raise ValueError("compaction snapshot digest does not match its files")
    _validate_cases(cases)
    return CompactionSnapshot(manifest=manifest, cases=cases)


def _write_jsonl(path: Path, cases: tuple[CompactionCase, ...]) -> SnapshotFile:
    payload = b"".join(canonical_json(case) + b"\n" for case in cases)
    buffer = BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as compressed:
        compressed.write(payload)
    path.write_bytes(buffer.getvalue())
    return SnapshotFile(
        path=path.name,
        records=len(cases),
        sha256=f"sha256:{hashlib.sha256(payload).hexdigest()}",
    )


def _snapshot_digest(
    builder_digest: str,
    target_tokens: int,
    corpus: tuple[CorpusFile, ...],
    cases: SnapshotFile,
) -> str:
    payload = f"{builder_digest}\n{target_tokens}\n".encode()
    payload += b"".join(f"{file.path}\0{file.sha256}\n".encode() for file in corpus)
    payload += f"{cases.path}\0{cases.records}\0{cases.sha256}\n".encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _validate_cases(cases: tuple[CompactionCase, ...]) -> None:
    if not cases:
        raise ValueError("compaction snapshot contains no cases")
    case_ids = [case.id for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("compaction snapshot contains duplicate case ids")
    for case in cases:
        fact_ids = [fact.id for fact in case.facts]
        if len(set(fact_ids)) != len(fact_ids):
            raise ValueError(f"compaction case {case.id!r} contains duplicate fact ids")
        if case.leaf == "chain" and not case.extensions:
            raise ValueError(f"chain case {case.id!r} carries no window extensions")
        if case.leaf != "chain" and case.extensions:
            raise ValueError(f"case {case.id!r} carries extensions outside the chain leaf")
        if case.leaf == "behavior" and not case.probes:
            raise ValueError(f"behavior case {case.id!r} carries no probes")
        if case.leaf != "behavior" and case.probes:
            raise ValueError(f"case {case.id!r} carries probes outside the behavior leaf")
        if case.leaf == "image" and not any(fact.kind == "image" for fact in case.facts):
            raise ValueError(f"image case {case.id!r} plants no image fact")
        broken_reference = next(
            (
                fact.id
                for fact in case.facts
                if fact.kind == "reference" and not (fact.path and fact.body)
            ),
            None,
        )
        if broken_reference is not None:
            raise ValueError(
                f"reference fact {broken_reference!r} in case {case.id!r} lacks a path or body"
            )
