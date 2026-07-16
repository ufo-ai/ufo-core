"""Each pinned JobBench task runs as one capability case: the dossier is staged under the
conversation's `references/` folder, the upstream prompt is preserved ahead of a fixed submission
envelope, and the grader captures the `share_file` deliverables into a local submissions folder
for offline rubric grading (`evals.jobbench.grading`)."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path, PurePosixPath

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    WorkspaceFile,
)
from evals.harness.harness import Json, JsonObject
from evals.harness.registry import EvalTask, capability_task
from evals.jobbench.models import RubricItem, SnapshotCase
from evals.jobbench.snapshot import load_snapshot

JOBBENCH_PACKS = ("assistant", "assistant_hosted")
ENVELOPE_REVISION = "references-share-file-1"
WORKFLOW_WAIT_SECONDS = 1_800.0
SUBMISSIONS_ROOT = Path(".local/jobbench/submissions")


def load_boundary(
    snapshot_root: Path,
    case_ids: tuple[str, ...] = (),
    submissions_root: Path = SUBMISSIONS_ROOT,
) -> tuple[EvalTask, ...]:
    snapshot = load_snapshot(snapshot_root)
    requested = frozenset(case_ids)
    unknown = sorted(requested - {case.case_id for case in snapshot.cases})
    if unknown:
        raise ValueError(f"unknown JobBench case ids: {', '.join(unknown)}")
    materialized = frozenset(snapshot.manifest.materialized_case_ids)
    unavailable = sorted(requested - materialized)
    if unavailable:
        raise ValueError(f"JobBench case assets are not materialized: {', '.join(unavailable)}")
    selected = requested or materialized
    if not selected:
        raise ValueError("JobBench snapshot has no materialized cases")
    return tuple(
        replace(
            capability_task(
                f"jobbench.{case.case_id}",
                (
                    _capability_case(
                        Path(snapshot.root), snapshot.manifest.digest, submissions_root, case
                    ),
                ),
            ),
            pin_runtime=True,
        )
        for case in snapshot.cases
        if case.case_id in selected
    )


def _capability_case(
    snapshot_root: Path,
    snapshot_digest: str,
    submissions_root: Path,
    case: SnapshotCase,
) -> CapabilityCase:
    files = _workspace_files(snapshot_root, case)
    return CapabilityCase(
        name=case.case_id,
        message=case.prompt + _envelope(tuple(file.path for file in files)),
        grader=SubmissionCapture(case.case_id, submissions_root, case.rubric),
        web_dependent=case.split == "main",
        digest_tag=(
            f"{snapshot_digest}:{ENVELOPE_REVISION}:wait-{WORKFLOW_WAIT_SECONDS:g}:"
            f"{case.case_id}:" + ",".join(asset.sha256 or "" for asset in case.references)
        ),
        workspace_files=files,
    )


def _workspace_files(snapshot_root: Path, case: SnapshotCase) -> tuple[WorkspaceFile, ...]:
    files = []
    for asset in case.references:
        if asset.snapshot_path is None:
            raise ValueError(
                f"JobBench case {case.case_id!r} reference is not materialized: "
                f"{asset.relative_path!r}"
            )
        name = PurePosixPath(asset.relative_path).name
        content = (snapshot_root / asset.snapshot_path).read_bytes()
        files.append(WorkspaceFile(path=f"references/{name}", content=content))
    return tuple(files)


def _envelope(reference_paths: tuple[str, ...]) -> str:
    references = "\n".join(f"- {path}" for path in reference_paths)
    return (
        "\n\n---\n"
        "Evaluation workspace:\n"
        "Reference files:\n"
        f"{references}\n"
        "Complete the task in the workspace. Submit every requested deliverable with a separate "
        "share_file call. Only files submitted with share_file are included in evaluation."
    )


@dataclass(frozen=True)
class SubmissionCapture:
    """Passes when the turn delivered durable `share_file` submissions, and saves their bytes
    under the submissions folder so `evals.jobbench.grading` can judge them offline. The verdict
    evidence carries the case's pinned rubric so the run archive shows what the captured
    submissions will be judged against."""

    case_id: str
    submissions_root: Path
    rubric: tuple[RubricItem, ...]

    async def __call__(self, output: CapabilityOutput) -> CapabilityVerdict:
        share_calls = tuple(
            call for call in output.calls if call.name == "share_file" and call.succeeded
        )
        evidence: JsonObject = {
            "submissionCount": len(output.artifacts),
            "gradingRubric": [
                {"rubric": item.rubric, "weight": item.weight, "criteria": list(item.criteria)}
                for item in self.rubric
            ],
        }
        if output.artifact_error:
            return CapabilityVerdict(
                False, f"artifact collection failed: {output.artifact_error}", evidence
            )
        if not output.artifacts:
            return CapabilityVerdict(
                False, "no artifact was submitted through share_file", evidence
            )
        if len(share_calls) != len(output.artifacts):
            return CapabilityVerdict(
                False, "successful share_file calls do not match collected artifacts", evidence
            )
        names = [PurePosixPath(artifact.name).name for artifact in output.artifacts]
        if any(not name or name in (".", "..") for name in names):
            return CapabilityVerdict(False, "a submitted artifact name is unsafe", evidence)
        if len(set(name.casefold() for name in names)) != len(names):
            return CapabilityVerdict(False, "submitted artifact names collide", evidence)
        case_dir = self.submissions_root / self.case_id
        if case_dir.exists():
            await asyncio.to_thread(shutil.rmtree, case_dir)
        await asyncio.to_thread(case_dir.mkdir, parents=True)
        saved: list[Json] = []
        for name, artifact in zip(names, output.artifacts, strict=True):
            target = case_dir / name
            await asyncio.to_thread(target.write_bytes, artifact.content)
            saved.append(
                {
                    "name": artifact.name,
                    "savedAs": name,
                    "sizeBytes": len(artifact.content),
                    "digest": f"sha256:{sha256(artifact.content).hexdigest()}",
                }
            )
        evidence["submissions"] = saved
        evidence["submissionsDir"] = str(case_dir)
        return CapabilityVerdict(True, f"captured {len(names)} submitted artifacts", evidence)
