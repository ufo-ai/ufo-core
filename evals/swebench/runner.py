"""Admit pinned SWE-bench cases through the coding lane and retain submitted patches."""

from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    SharedArtifact,
    shared_file_names,
)
from evals.harness.coding import AllOf, PinnedRepositoryRoute
from evals.harness.harness import JsonObject
from evals.harness.registry import EvalTask, capability_task, rewrapped
from evals.harness.scorers import delegation_only_scorer
from evals.swebench.models import SUBSET_SIZES, Subset, SWEbenchCase
from evals.swebench.snapshot import load_snapshot

SUITE_NAME = "swebench_verified"
SUBSETS: tuple[Subset, ...] = tuple(SUBSET_SIZES)
LOCAL_ROOT = Path(".local/swebench")
SNAPSHOT_ROOT = LOCAL_ROOT / "snapshot"
SUBMISSIONS_ROOT = LOCAL_ROOT / "submissions"
SWEBENCH_PACKS = ("assistant", "assistant_hosted")
WORKFLOW_WAIT_SECONDS = 7_200.0
PARENT_FORBIDDEN_TOOLS = ("bash", "edit", "grep", "glob")
ENVELOPE_REVISION = "pinned-fetch-share-exact-half"
DIFF_HEADER = b"diff --git "
OLD_FILE_HEADER = b"--- "
NEW_FILE_HEADER = b"+++ "
HUNK_HEADER = re.compile(
    rb"@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    rb"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?: .*)?"
)
NO_NEWLINE_MARKER = b"\\ No newline at end of file"


def new_submissions_root() -> Path:
    """Mint an isolated capture root without creating it."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return SUBMISSIONS_ROOT / f"{stamp}-{uuid4().hex[:8]}"


def load_swebench(
    case_names: tuple[str, ...],
    snapshot_root: Path,
    submissions_root: Path,
    subset: Subset | None,
) -> tuple[EvalTask, ...]:
    """Load selected snapshot cases as one concurrent capability task per subset."""
    snapshot = load_snapshot(snapshot_root)
    selection = snapshot.manifest.upstream.subsets
    parquet_sha256 = snapshot.manifest.upstream.parquet.sha256
    requested = frozenset(case_names)
    unknown = sorted(requested - {case.instance_id for case in snapshot.cases})
    if unknown:
        raise ValueError(f"unknown SWE-bench case ids: {', '.join(unknown)}")
    tasks = []
    for group in SUBSETS:
        if subset is not None and group != subset:
            continue
        members = frozenset(selection.ids(group))
        cases = tuple(
            _capability_case(case, parquet_sha256, submissions_root)
            for case in snapshot.cases
            if case.instance_id in members and (not requested or case.instance_id in requested)
        )
        if cases:
            tasks.append(
                rewrapped(
                    capability_task(f"{SUITE_NAME}.{group}", cases, packs=SWEBENCH_PACKS),
                    lambda built: replace(built, pin_runtime=True),
                )
            )
    if not tasks:
        raise ValueError("no SWE-bench cases selected")
    return tuple(tasks)


def _capability_case(
    case: SWEbenchCase, parquet_sha256: str, submissions_root: Path
) -> CapabilityCase:
    lines = case.problem_statement.splitlines(keepends=True)
    midpoint = len(lines) // 2
    problem_statement = (
        "".join(lines[:midpoint])
        if lines and len(lines) % 2 == 0 and lines[:midpoint] == lines[midpoint:]
        else case.problem_statement
    )
    return CapabilityCase(
        name=case.instance_id,
        message=problem_statement + _envelope(case),
        grader=AllOf(
            (
                PinnedRepositoryRoute(case.repo, case.base_commit),
                delegation_only_scorer(PARENT_FORBIDDEN_TOOLS),
                PatchCapture(case.instance_id, submissions_root),
            )
        ),
        digest_tag=(f"{parquet_sha256}:{ENVELOPE_REVISION}:{case.instance_id}:{case.base_commit}"),
        prepare=_CaptureStart(case.instance_id, submissions_root),
    )


def _envelope(case: SWEbenchCase) -> str:
    return (
        "\n\n---\n"
        "Evaluation setup, to pass on to whoever does the work:\n"
        f"The repository is https://github.com/{case.repo}.git at commit {case.base_commit}. "
        "Fetch exactly that commit with depth 1; do not clone a branch or read a later commit.\n"
        f"Deliverable: share /workspace/{case.instance_id}.patch, the complete unified diff "
        "relative to the fetched commit. Only that shared file is graded."
    )


def _carries_complete_diff(content: bytes) -> bool:
    lines = content.splitlines()
    index = 0
    changed = False
    while index < len(lines) - 1:
        if not _file_header_pair(lines, index):
            index += 1
            continue
        index += 2
        hunks = 0
        while index < len(lines):
            match = HUNK_HEADER.fullmatch(lines[index])
            if match is not None:
                consumed = _consume_hunk(lines, index, match)
                if consumed is None:
                    return False
                index, hunk_changed = consumed
                changed = changed or hunk_changed
                hunks += 1
                continue
            if lines[index].startswith(b"@@"):
                return False
            if lines[index].startswith(DIFF_HEADER) or _file_header_pair(lines, index):
                break
            index += 1
        if not hunks:
            return False
    return changed


def _consume_hunk(
    lines: list[bytes], index: int, match: re.Match[bytes]
) -> tuple[int, bool] | None:
    old_count = int(match.group("old_count") or 1)
    new_count = int(match.group("new_count") or 1)
    old_seen = 0
    new_seen = 0
    changed = False
    index += 1
    while old_seen < old_count or new_seen < new_count:
        if index >= len(lines):
            return None
        line = lines[index]
        if line == NO_NEWLINE_MARKER:
            index += 1
            continue
        if line.startswith(b" "):
            old_seen += 1
            new_seen += 1
        elif line.startswith(b"-"):
            old_seen += 1
            changed = True
        elif line.startswith(b"+"):
            new_seen += 1
            changed = True
        else:
            return None
        if old_seen > old_count or new_seen > new_count:
            return None
        index += 1
    while index < len(lines) and lines[index] == NO_NEWLINE_MARKER:
        index += 1
    if index < len(lines) and lines[index].startswith((b" ", b"+", b"-")):
        if not _file_header_pair(lines, index):
            return None
    return index, changed


def _file_header_pair(lines: list[bytes], index: int) -> bool:
    if not (
        index + 1 < len(lines)
        and lines[index].startswith(OLD_FILE_HEADER)
        and lines[index + 1].startswith(NEW_FILE_HEADER)
    ):
        return False
    old_path = lines[index][len(OLD_FILE_HEADER) :].partition(b"\t")[0].strip()
    new_path = lines[index + 1][len(NEW_FILE_HEADER) :].partition(b"\t")[0].strip()
    return bool(old_path and new_path)


@dataclass(frozen=True)
class _CaptureStart:
    case_id: str
    submissions_root: Path

    async def __call__(self, _workspace_id: UUID, _workspace: Path) -> None:
        case_dir = self.submissions_root / self.case_id
        if case_dir.exists():
            await asyncio.to_thread(shutil.rmtree, case_dir)


@dataclass(frozen=True)
class PatchCapture:
    """Capture the named shared candidate, then require it to carry a unified diff."""

    case_id: str
    submissions_root: Path

    @property
    def grading(self) -> str:
        return f"shares {self.case_id}.patch as a unified diff and captures its exact bytes"

    async def __call__(self, output: CapabilityOutput) -> CapabilityVerdict:
        wanted = f"{self.case_id}.patch"
        shared = {
            PurePosixPath(name).name for call in output.calls for name in shared_file_names(call)
        }
        candidates = tuple(
            artifact
            for artifact in output.artifacts
            if PurePosixPath(artifact.name).name == wanted and wanted in shared
        )
        evidence: JsonObject = {"expectedSubmission": wanted}
        selected = candidates[-1] if candidates else None
        if selected is not None:
            target = await self._save(selected)
            evidence |= {
                "submission": str(target),
                "sizeBytes": len(selected.content),
                "sha256": f"sha256:{sha256(selected.content).hexdigest()}",
            }
        if output.artifact_error:
            return CapabilityVerdict(
                False, f"artifact collection failed: {output.artifact_error}", evidence
            )
        if selected is None:
            return CapabilityVerdict(False, f"did not share {wanted}", evidence)
        if not _carries_complete_diff(selected.content):
            return CapabilityVerdict(False, f"{wanted} carries no unified diff", evidence)
        return CapabilityVerdict(
            True, f"captured {wanted} ({len(selected.content)} bytes)", evidence
        )

    async def _save(self, artifact: SharedArtifact) -> Path:
        directory = self.submissions_root / self.case_id
        await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
        target = directory / f"{self.case_id}.patch"
        await asyncio.to_thread(target.write_bytes, artifact.content)
        return target
