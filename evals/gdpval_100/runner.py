from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from evals.gdpval_100.models import SnapshotAsset, SnapshotCase
from evals.gdpval_100.snapshot import load_snapshot
from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityReference,
    CapabilityVerdict,
)
from evals.harness.harness import JsonObject
from evals.harness.registry import EvalTask, capability_task

type Treatment = Literal[
    "gdpval_core",
    "gdpval_documents",
    "gdpval_research",
    "gdpval_full",
]

TREATMENTS: tuple[Treatment, ...] = (
    "gdpval_core",
    "gdpval_documents",
    "gdpval_research",
    "gdpval_full",
)
ENVELOPE_REVISION = "references-share-file-1"
WORKFLOW_WAIT_SECONDS = 1_800.0


@dataclass(frozen=True)
class GDPvalCalibration:
    treatment: Treatment
    tasks: tuple[EvalTask, ...]


def load_calibration(
    snapshot_root: Path,
    treatment: Treatment,
    task_ids: tuple[str, ...] = (),
) -> GDPvalCalibration:
    if treatment not in TREATMENTS:
        raise ValueError(f"unknown GDPval treatment: {treatment}")
    snapshot = load_snapshot(snapshot_root)
    requested = frozenset(task_ids)
    unknown = sorted(requested - {case.task_id for case in snapshot.cases})
    if unknown:
        raise ValueError(f"unknown GDPval task ids: {', '.join(unknown)}")
    materialized = frozenset(snapshot.manifest.materialized_case_ids)
    unavailable = sorted(requested - materialized)
    if unavailable:
        raise ValueError(f"GDPval task assets are not materialized: {', '.join(unavailable)}")
    selected = requested or materialized
    cases = tuple(case for case in snapshot.cases if case.task_id in selected)
    tasks = tuple(
        capability_task(
            f"gdpval_100.calibration.{treatment}.{case.task_id}",
            (_capability_case(Path(snapshot.root), snapshot.manifest.digest, treatment, case),),
        )
        for case in cases
    )
    return GDPvalCalibration(treatment, tasks)


def _capability_case(
    snapshot_root: Path,
    snapshot_digest: str,
    treatment: Treatment,
    case: SnapshotCase,
) -> CapabilityCase:
    references = _references(snapshot_root, case)
    message = case.prompt + _envelope(tuple(reference.path for reference in references))
    return CapabilityCase(
        name=case.task_id,
        message=message,
        grader=_submission_grader,
        digest_tag=(
            f"{snapshot_digest}:{treatment}:{ENVELOPE_REVISION}:"
            f"wait-{WORKFLOW_WAIT_SECONDS:g}:{case.task_id}:"
            + ",".join(reference.digest for reference in references)
        ),
        references=references,
    )


def _references(snapshot_root: Path, case: SnapshotCase) -> tuple[CapabilityReference, ...]:
    missing = tuple(asset.relative_path for asset in case.references if asset.snapshot_path is None)
    if missing:
        raise ValueError(
            f"GDPval case {case.task_id} has unmaterialized references: {', '.join(missing)}"
        )
    paths = _workspace_reference_paths(case.references)
    references: list[CapabilityReference] = []
    for asset, path in zip(case.references, paths, strict=True):
        if asset.snapshot_path is None or asset.sha256 is None or asset.size_bytes is None:
            raise RuntimeError(f"GDPval case {case.task_id} reference metadata is incomplete")
        references.append(
            CapabilityReference(
                path,
                snapshot_root / asset.snapshot_path,
                asset.sha256,
                asset.size_bytes,
            )
        )
    return tuple(references)


def _workspace_reference_paths(assets: tuple[SnapshotAsset, ...]) -> tuple[str, ...]:
    basenames = tuple(PurePosixPath(asset.relative_path).name for asset in assets)
    counts = Counter(name.casefold() for name in basenames)
    collisions = {name for name, count in counts.items() if count > 1}
    return tuple(
        f"{index:02d}-{name}" if name.casefold() in collisions else name
        for index, name in enumerate(basenames, start=1)
    )


def _envelope(reference_paths: tuple[str, ...]) -> str:
    references = "\n".join(f"- references/{path}" for path in reference_paths) or "- none"
    return (
        "\n\n---\n"
        "Evaluation workspace:\n"
        "Reference files:\n"
        f"{references}\n"
        "Complete the task in the workspace. Submit every requested deliverable with a separate "
        "share_file call. Only files submitted with share_file are included in evaluation."
    )


async def _submission_grader(output: CapabilityOutput) -> CapabilityVerdict:
    share_calls = tuple(
        call for call in output.calls if call.name == "share_file" and call.succeeded
    )
    references = output.artifact_references
    evidence: JsonObject = {
        "submissionCount": len(references),
        "submissionDigests": [artifact.digest for artifact in references],
    }
    if not references:
        return CapabilityVerdict(False, "no artifact was submitted through share_file", evidence)
    if len(share_calls) != len(references):
        return CapabilityVerdict(
            False,
            "successful share_file calls do not match durable submitted artifacts",
            evidence,
        )
    return CapabilityVerdict(True, f"captured {len(references)} durable submissions", evidence)
