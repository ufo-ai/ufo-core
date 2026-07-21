"""Each pinned WANDR task runs as one capability case: the upstream instruction is preserved ahead
of a fixed submission envelope, the grader captures the required `share_file` results files for
the offline task-local verifier (`evals.wandr.grading`), and each subset reports as one task so
difficulty labels roll up as tiers."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from functools import partial
from hashlib import sha256
from pathlib import Path, PurePosixPath

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    SharedArtifact,
    run_capability_case,
)
from evals.harness.harness import EvalReport, Json, JsonObject, digest_payload
from evals.harness.registry import EvalTask, gather_cases
from evals.harness.target import CapabilityTarget
from evals.wandr.models import TIER_BY_DIFFICULTY, SelectionCase, Subset
from evals.wandr.snapshot import TASKS_ROOT, load_snapshot

WANDR_PACKS = ("assistant", "assistant_hosted")
ENVELOPE_REVISION = "wandr-share-file-1"
WORKFLOW_WAIT_SECONDS = 7_200.0
SUBMISSIONS_ROOT = Path(".local/wandr/submissions")
SUBSETS: tuple[Subset, ...] = ("smoke", "hillclimb", "holdout")


def load_boundary(
    snapshot_root: Path,
    subset: Subset | None,
    case_names: tuple[str, ...] = (),
    submissions_root: Path = SUBMISSIONS_ROOT,
) -> tuple[EvalTask, ...]:
    snapshot = load_snapshot(snapshot_root)
    selection = snapshot.manifest.selection
    requested = frozenset(case_names)
    unknown = sorted(requested - {case.name for case in selection.cases})
    if unknown:
        raise ValueError(f"unknown WANDR cases: {', '.join(unknown)}")
    tasks = []
    for group in SUBSETS:
        if subset is not None and group != subset:
            continue
        cases = tuple(
            case
            for case in selection.subset_cases(group)
            if not requested or case.name in requested
        )
        if cases:
            tasks.append(
                _subset_task(
                    Path(snapshot.root), snapshot.manifest.digest, submissions_root, group, cases
                )
            )
    if not tasks:
        raise ValueError("no WANDR cases selected")
    return tuple(tasks)


def _subset_task(
    snapshot_root: Path,
    snapshot_digest: str,
    submissions_root: Path,
    subset: Subset,
    cases: tuple[SelectionCase, ...],
) -> EvalTask:
    name = f"wandr.{subset}"
    built = tuple(
        (
            _capability_case(snapshot_root, snapshot_digest, submissions_root, case),
            None if case.difficulty is None else TIER_BY_DIFFICULTY[case.difficulty],
        )
        for case in cases
    )
    digest = digest_payload(
        {"runner": "wandr", "task": name, "cases": [case.payload() for case, _ in built]}
    )

    async def run(target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        results = await gather_cases(
            slots, tuple(partial(run_capability_case, case, target) for case, _ in built)
        )
        tiered = tuple(
            result.model_copy(update={"tier": tier})
            for result, (_, tier) in zip(results, built, strict=True)
        )
        return EvalReport(name=name, suite="wandr", digest=digest, cases=tiered)

    return EvalTask(
        name,
        "wandr",
        digest,
        tuple(case.name for case, _ in built),
        run,
        pin_runtime=True,
    )


def _capability_case(
    snapshot_root: Path,
    snapshot_digest: str,
    submissions_root: Path,
    case: SelectionCase,
) -> CapabilityCase:
    instruction = (snapshot_root / TASKS_ROOT / case.name / "instruction.md").read_text()
    return CapabilityCase(
        name=case.name,
        message=instruction + _envelope(case.required_files),
        grader=SubmissionCapture(case.name, submissions_root, case.required_files),
        web_dependent=True,
        digest_tag=(
            f"{snapshot_digest}:{ENVELOPE_REVISION}:wait-{WORKFLOW_WAIT_SECONDS:g}:"
            f"{case.name}:{case.digest}"
        ),
    )


def _envelope(required_files: tuple[str, ...]) -> str:
    required = "\n".join(f"- {name}" for name in required_files)
    return (
        "\n\n---\n"
        "Evaluation workspace:\n"
        "Write each required results file in the workspace with exactly these names:\n"
        f"{required}\n"
        "Build each results file incrementally — append batches of at most 50 rows per call, or "
        "assemble it with sandbox code; never write hundreds of rows in a single call. "
        "When the task is complete, submit each required file with a separate share_file call. "
        "Submit only these files; only files submitted with share_file are evaluated. "
        "Before ending the turn, confirm every required file was actually submitted — a results "
        "file that only sits in the workspace scores zero."
    )


@dataclass(frozen=True)
class SubmissionCapture:
    """Passes when every required results file arrived as a durable `share_file` submission, and
    saves those bytes under the submissions folder so `evals.wandr.grading` can run the task's
    vendored verifier offline. Extra submissions are recorded in evidence, never staged."""

    case_name: str
    submissions_root: Path
    required_files: tuple[str, ...]

    @property
    def grading(self) -> str:
        return (
            f"every required results file ({', '.join(self.required_files)}) is submitted "
            "through share_file and its bytes are captured for offline WANDR evidence "
            "verification"
        )

    async def __call__(self, output: CapabilityOutput) -> CapabilityVerdict:
        evidence: JsonObject = {"requiredFiles": list(self.required_files)}
        if output.artifact_error:
            return CapabilityVerdict(
                False, f"artifact collection failed: {output.artifact_error}", evidence
            )
        by_name: dict[str, SharedArtifact] = {}
        for artifact in output.artifacts:
            by_name.setdefault(PurePosixPath(artifact.name).name, artifact)
        extras: list[Json] = [name for name in sorted(set(by_name) - set(self.required_files))]
        if extras:
            evidence["extraSubmissions"] = extras
        missing = tuple(name for name in self.required_files if name not in by_name)
        if missing:
            return CapabilityVerdict(
                False, f"required results files were not submitted: {', '.join(missing)}", evidence
            )
        case_dir = self.submissions_root / self.case_name
        if case_dir.exists():
            await asyncio.to_thread(shutil.rmtree, case_dir)
        await asyncio.to_thread(case_dir.mkdir, parents=True)
        saved: list[Json] = []
        for name in self.required_files:
            artifact = by_name[name]
            await asyncio.to_thread((case_dir / name).write_bytes, artifact.content)
            saved.append(
                {
                    "name": name,
                    "sizeBytes": len(artifact.content),
                    "digest": f"sha256:{sha256(artifact.content).hexdigest()}",
                }
            )
        evidence["submissions"] = saved
        evidence["submissionsDir"] = str(case_dir)
        return CapabilityVerdict(True, f"captured {len(saved)} required results files", evidence)
