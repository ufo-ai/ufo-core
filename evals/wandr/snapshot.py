"""Content-addressed WANDR snapshots: the checked-in selection pins each task package to the
upstream dataset manifest's content digest, and a snapshot materializes those packages whole so
the vendored task-local verifier grades against exactly the bytes the selection pinned."""

import fnmatch
import hashlib
import json
import shutil
from pathlib import Path

from pydantic import BaseModel

from evals.wandr.models import Selection, Snapshot, SnapshotManifest

SELECTION_PATH = Path(__file__).with_name("selection.json")
MANIFEST_FILE = "snapshot.json"
TASKS_ROOT = "tasks"
VERIFIER_LOCK = "tests/wandr_core/uv.lock"
CONTENT_SINGLE_FILES = ("task.toml", "instruction.md", "README.md")
CONTENT_DIRECTORIES = ("environment", "tests", "solution", "steps")
CONTENT_IGNORES = (
    "__pycache__/",
    "*.pyc",
    ".pytest_cache/",
    ".venv/",
    "_workdir/",
    ".DS_Store",
    "*.swp",
    "*.swo",
    "*~",
)


def checked_in_selection() -> Selection:
    return Selection.model_validate_json(SELECTION_PATH.read_bytes())


def canonical_json(model: BaseModel) -> bytes:
    return json.dumps(model.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()


def content_digest(body: bytes) -> str:
    return f"sha256:{hashlib.sha256(body).hexdigest()}"


def task_content_digest(task_dir: Path) -> str:
    """The upstream `dataset.toml` content digest: SHA-256 over `relpath\\0filehash\\n` for every
    tracked file of the task package in sorted path order."""
    task_dir = task_dir.resolve()
    outer = hashlib.sha256()
    for path in _content_files(task_dir):
        relative = path.relative_to(task_dir).as_posix()
        inner = hashlib.sha256(path.read_bytes()).hexdigest()
        outer.update(f"{relative}\0{inner}\n".encode())
    return f"sha256:{outer.hexdigest()}"


def snapshot_digest(selection: Selection, verifier_lock_sha256: str) -> str:
    return content_digest(canonical_json(selection) + b"\n" + verifier_lock_sha256.encode() + b"\n")


def write_snapshot(
    staging_root: Path,
    output_root: Path,
    selection: Selection,
    verifier_lock_sha256: str,
) -> Path:
    manifest = SnapshotManifest(
        digest=snapshot_digest(selection, verifier_lock_sha256),
        selection=selection,
        verifier_lock_sha256=verifier_lock_sha256,
    )
    (staging_root / MANIFEST_FILE).write_bytes(canonical_json(manifest) + b"\n")
    output_root.mkdir(parents=True, exist_ok=True)
    destination = output_root / manifest.digest.removeprefix("sha256:")
    if destination.exists():
        load_snapshot(destination, selection)
        shutil.rmtree(staging_root)
        return destination
    staging_root.replace(destination)
    return destination


def load_snapshot(root: Path, expected: Selection | None = None) -> Snapshot:
    manifest = SnapshotManifest.model_validate_json((root / MANIFEST_FILE).read_bytes())
    if manifest.selection != (checked_in_selection() if expected is None else expected):
        raise ValueError("WANDR snapshot does not use the checked-in selection")
    if snapshot_digest(manifest.selection, manifest.verifier_lock_sha256) != manifest.digest:
        raise ValueError("WANDR snapshot digest does not match its manifest")
    for case in manifest.selection.cases:
        task_dir = root / TASKS_ROOT / case.name
        if task_content_digest(task_dir) != case.digest:
            raise ValueError(f"WANDR task {case.name!r} content does not match its pinned digest")
        lock = task_dir / VERIFIER_LOCK
        if content_digest(lock.read_bytes()) != manifest.verifier_lock_sha256:
            raise ValueError(f"WANDR task {case.name!r} verifier lock diverges from the pin")
    return Snapshot(root=str(root.resolve()), manifest=manifest)


def _content_files(task_dir: Path) -> tuple[Path, ...]:
    task_dir = task_dir.resolve()
    files = [task_dir / name for name in CONTENT_SINGLE_FILES if (task_dir / name).exists()]
    for directory in CONTENT_DIRECTORIES:
        root = task_dir / directory
        if root.exists():
            files.extend(path for path in root.rglob("*") if path.is_file())
    kept = (
        path
        for path in files
        if not any(
            fnmatch.fnmatch(path.relative_to(task_dir).as_posix(), pattern.rstrip("/"))
            or fnmatch.fnmatch(path.name, pattern.rstrip("/"))
            or path.relative_to(task_dir).as_posix().startswith(pattern.rstrip("/") + "/")
            for pattern in CONTENT_IGNORES
        )
    )
    return tuple(sorted(kept, key=lambda path: path.relative_to(task_dir).as_posix()))
