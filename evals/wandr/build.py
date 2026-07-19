"""Materialize the pinned WANDR task packages into a content-addressed local snapshot. The
selected packages are fetched from the pinned upstream revision by blob-filtered sparse checkout
(or copied from an existing checkout), verified against their pinned content digests, and staged
whole so each task's vendored verifier travels with it."""

import argparse
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from evals.wandr.models import Selection
from evals.wandr.snapshot import (
    TASKS_ROOT,
    VERIFIER_LOCK,
    checked_in_selection,
    content_digest,
    task_content_digest,
    write_snapshot,
)

SNAPSHOTS_ROOT = Path(".local/wandr/snapshots")
UPSTREAM_URL = "https://github.com/perplexityai/wandr"
DATASET_ROOT = "datasets/wandr"


@dataclass(frozen=True)
class SnapshotBuilder:
    """Stages every selected task package from the upstream source, pins its content digest and
    the shared verifier lock, and writes the content-addressed snapshot."""

    output_root: Path
    selection: Selection
    source: Path | None = None

    def build(self) -> Path:
        self.output_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self.output_root) as scratch:
            source = self.source if self.source is not None else self._fetch(Path(scratch))
            staging = Path(scratch) / "staging"
            (staging / TASKS_ROOT).mkdir(parents=True)
            lock_digest = self._stage_tasks(source, staging)
            return write_snapshot(staging, self.output_root, self.selection, lock_digest)

    def _fetch(self, scratch: Path) -> Path:
        checkout = scratch / "upstream"
        checkout.mkdir()
        paths = [f"{DATASET_ROOT}/{case.name}" for case in self.selection.cases]
        self._git(checkout, "init", "--quiet")
        self._git(checkout, "remote", "add", "origin", UPSTREAM_URL)
        self._git(checkout, "sparse-checkout", "set", "--no-cone", *paths)
        self._git(
            checkout,
            "fetch",
            "--quiet",
            "--depth",
            "1",
            "--filter=blob:none",
            "origin",
            self.selection.upstream.revision,
        )
        self._git(checkout, "checkout", "--quiet", "FETCH_HEAD")
        return checkout

    def _git(self, checkout: Path, *args: str) -> None:
        subprocess.run(("git", "-C", str(checkout), *args), check=True)

    def _stage_tasks(self, source: Path, staging: Path) -> str:
        lock_digest = ""
        for case in self.selection.cases:
            task_dir = source / DATASET_ROOT / case.name
            digest = task_content_digest(task_dir)
            if digest != case.digest:
                raise ValueError(
                    f"WANDR task {case.name!r} content does not match its pinned digest"
                )
            case_lock = content_digest((task_dir / VERIFIER_LOCK).read_bytes())
            if lock_digest and case_lock != lock_digest:
                raise ValueError(f"WANDR task {case.name!r} verifier lock diverges from siblings")
            lock_digest = case_lock
            shutil.copytree(task_dir, staging / TASKS_ROOT / case.name)
        return lock_digest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.wandr.build")
    parser.add_argument("--out", type=Path, default=SNAPSHOTS_ROOT)
    parser.add_argument("--source", type=Path, help="existing upstream checkout to copy from")
    args = parser.parse_args(argv)
    destination = SnapshotBuilder(args.out, checked_in_selection(), args.source).build()
    print(f"snapshot {destination}")


if __name__ == "__main__":
    main()
