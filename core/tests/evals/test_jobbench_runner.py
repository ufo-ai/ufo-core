"""JobBench boundary tasks from a materialized snapshot: dossier staging under `references/`, the
fixed submission envelope, and the submission-capture grader's verdicts and saved deliverables."""

import json
from pathlib import Path

import pytest
from jobbench_corpus import SMOKE_CASE, materialized_snapshot

from evals.harness.capability import CapabilityOutput, SharedArtifact, ToolInvocation
from evals.jobbench.models import RubricItem
from evals.jobbench.runner import SubmissionCapture, _capability_case, load_boundary
from evals.jobbench.snapshot import JOBBENCH_UPSTREAM, load_snapshot


@pytest.fixture
def snapshot_root(tmp_path: Path) -> Path:
    return materialized_snapshot(tmp_path)


def test_load_boundary_builds_one_task_per_case(snapshot_root: Path, tmp_path: Path) -> None:
    tasks = load_boundary(snapshot_root, submissions_root=tmp_path / "submissions")
    assert [task.name for task in tasks] == [f"jobbench.{SMOKE_CASE}"]
    assert tasks[0].cases == (SMOKE_CASE,)


def test_capability_case_stages_dossier_and_envelope(snapshot_root: Path, tmp_path: Path) -> None:
    snapshot = load_snapshot(snapshot_root, JOBBENCH_UPSTREAM)
    case = next(record for record in snapshot.cases if record.case_id == SMOKE_CASE)
    capability = _capability_case(
        Path(snapshot.root), snapshot.manifest.digest, tmp_path / "submissions", case
    )
    assert capability.message.startswith(case.prompt)
    assert "- references/data file.csv" in capability.message
    assert "share_file" in capability.message
    assert not capability.web_dependent
    assert [file.path for file in capability.workspace_files] == [
        "references/data file.csv",
        "references/notes.txt",
    ]
    assert capability.workspace_files[0].content.startswith(b"body of dataset_easy/")
    assert snapshot.manifest.digest in capability.digest_tag


def test_load_boundary_rejects_unknown_and_unmaterialized(snapshot_root: Path) -> None:
    with pytest.raises(ValueError, match="unknown JobBench case ids"):
        load_boundary(snapshot_root, ("easy.missing__task9",))
    with pytest.raises(ValueError, match="not materialized"):
        load_boundary(snapshot_root, ("main.occupation_00__task1",))


RUBRIC = (RubricItem(rubric="Ties out the balance?", weight=10, criteria=("states it",)),)


def _output(
    artifacts: tuple[SharedArtifact, ...],
    shared: int,
    artifact_error: str = "",
) -> CapabilityOutput:
    calls = (
        (
            ToolInvocation(
                name="share_file",
                input={"files": [{"file_path": f"/workspace/file{i}"} for i in range(shared)]},
                result=json.dumps([{"name": f"file{i}"} for i in range(shared)]),
                has_result=True,
            ),
        )
        if shared
        else ()
    )
    return CapabilityOutput(
        response="done",
        calls=calls,
        artifacts=artifacts,
        artifact_error=artifact_error,
    )


async def test_submission_capture_requires_artifacts(tmp_path: Path) -> None:
    grader = SubmissionCapture(SMOKE_CASE, tmp_path / "submissions", RUBRIC)
    verdict = await grader(_output((), shared=0))
    assert not verdict.passed
    assert "no artifact" in verdict.reason


async def test_submission_capture_surfaces_artifact_errors(tmp_path: Path) -> None:
    grader = SubmissionCapture(SMOKE_CASE, tmp_path / "submissions", RUBRIC)
    artifact = SharedArtifact("memo.md", b"memo")
    verdict = await grader(_output((artifact,), shared=1, artifact_error="missing blob"))
    assert not verdict.passed
    assert "missing blob" in verdict.reason


async def test_submission_capture_requires_matching_share_calls(tmp_path: Path) -> None:
    grader = SubmissionCapture(SMOKE_CASE, tmp_path / "submissions", RUBRIC)
    artifact = SharedArtifact("memo.md", b"memo")
    verdict = await grader(_output((artifact,), shared=2))
    assert not verdict.passed
    assert "do not match" in verdict.reason


async def test_submission_capture_rejects_colliding_names(tmp_path: Path) -> None:
    grader = SubmissionCapture(SMOKE_CASE, tmp_path / "submissions", RUBRIC)
    artifacts = (
        SharedArtifact("reports/memo.md", b"one"),
        SharedArtifact("Memo.md", b"two"),
    )
    verdict = await grader(_output(artifacts, shared=2))
    assert not verdict.passed
    assert "collide" in verdict.reason


async def test_submission_capture_saves_deliverables(tmp_path: Path) -> None:
    submissions_root = tmp_path / "submissions"
    grader = SubmissionCapture(SMOKE_CASE, submissions_root, RUBRIC)
    stale = submissions_root / SMOKE_CASE / "stale.txt"
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"old run")
    artifacts = (
        SharedArtifact("memo.md", b"the memo"),
        SharedArtifact("schedule.csv", b"a,b\n1,2\n"),
    )
    verdict = await grader(_output(artifacts, shared=2))
    assert verdict.passed
    assert not stale.exists()
    case_dir = submissions_root / SMOKE_CASE
    assert (case_dir / "memo.md").read_bytes() == b"the memo"
    assert (case_dir / "schedule.csv").read_bytes() == b"a,b\n1,2\n"
    assert verdict.evidence["gradingRubric"] == [
        {"rubric": "Ties out the balance?", "weight": 10, "criteria": ["states it"]}
    ]
    submissions = verdict.evidence["submissions"]
    assert isinstance(submissions, list)
    assert [entry["savedAs"] for entry in submissions] == ["memo.md", "schedule.csv"]
    assert all(str(entry["digest"]).startswith("sha256:") for entry in submissions)
