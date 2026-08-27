"""WANDR boundary tasks from a materialized snapshot: subset grouping with difficulty tiers, the
fixed submission envelope, and the submission-capture grader's verdicts and saved bytes."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from wandr_corpus import (
    fabricate_selection,
    fabricate_upstream,
    materialized_snapshot,
    required_file,
)

from evals.harness.capability import CapabilityCase, CapabilityOutput, SharedArtifact
from evals.harness.target import TargetResult
from evals.wandr.models import Selection
from evals.wandr.runner import SubmissionCapture, _capability_case, load_boundary
from evals.wandr.snapshot import load_snapshot


@pytest.fixture
def upstream(tmp_path: Path) -> Path:
    return fabricate_upstream(tmp_path / "upstream")


@pytest.fixture
def selection(upstream: Path) -> Selection:
    return fabricate_selection(upstream)


@pytest.fixture
def snapshot_root(
    upstream: Path, selection: Selection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    root = materialized_snapshot(upstream, tmp_path / "snapshots", selection)
    monkeypatch.setattr("evals.wandr.snapshot.checked_in_selection", lambda: selection)
    return root


def test_load_boundary_groups_by_subset(snapshot_root: Path, tmp_path: Path) -> None:
    tasks = load_boundary(snapshot_root, None, submissions_root=tmp_path / "submissions")
    assert [task.name for task in tasks] == ["wandr.smoke", "wandr.hillclimb", "wandr.holdout"]
    assert all(task.pin_runtime for task in tasks)
    assert len(tasks[1].cases) == 21
    smoke_only = load_boundary(snapshot_root, "smoke", submissions_root=tmp_path / "submissions")
    assert [task.name for task in smoke_only] == ["wandr.smoke"]


def test_load_boundary_filters_cases(
    snapshot_root: Path, selection: Selection, tmp_path: Path
) -> None:
    chosen = selection.subset_cases("holdout")[0]
    tasks = load_boundary(
        snapshot_root, None, (chosen.name,), submissions_root=tmp_path / "submissions"
    )
    assert [task.name for task in tasks] == ["wandr.holdout"]
    assert tasks[0].cases == (chosen.name,)
    with pytest.raises(ValueError, match="unknown WANDR cases"):
        load_boundary(snapshot_root, None, ("missing-task",))
    with pytest.raises(ValueError, match="no WANDR cases selected"):
        load_boundary(snapshot_root, "smoke", (chosen.name,))


def test_case_message_carries_instruction_and_envelope(
    snapshot_root: Path, selection: Selection, tmp_path: Path
) -> None:
    case = selection.subset_cases("hillclimb")[0]
    task = load_boundary(
        snapshot_root, "hillclimb", (case.name,), submissions_root=tmp_path / "submissions"
    )[0]
    snapshot = load_snapshot(snapshot_root, selection)
    instruction = (Path(snapshot.root) / "tasks" / case.name / "instruction.md").read_text()
    result = asyncio.run(task.run(_StubTarget(case.required_files), asyncio.Semaphore(2)))
    (case_result,) = result.cases
    assert case_result.tier in (1, 2, 3)
    message = case_result.evidence["message"]
    assert isinstance(message, str)
    assert message.startswith(instruction)
    assert f"- {required_file(case.name)}" in message
    assert "share_file" in message
    capability = _capability_case(
        Path(snapshot.root), snapshot.manifest.digest, tmp_path / "submissions", case
    )
    assert snapshot.manifest.digest in capability.digest_tag
    assert case.digest in capability.digest_tag
    assert capability.wait_for_background is True


def test_subset_run_attaches_tiers(
    snapshot_root: Path, selection: Selection, tmp_path: Path
) -> None:
    cases = selection.subset_cases("hillclimb")
    task = load_boundary(snapshot_root, "hillclimb", submissions_root=tmp_path / "submissions")[0]
    files = tuple(name for case in cases for name in case.required_files)
    report = asyncio.run(task.run(_StubTarget(files), asyncio.Semaphore(4)))
    tiers = {result.tier for result in report.cases}
    assert tiers == {1, 2, 3}
    assert report.tier_rates == ((1, 7, 7), (2, 7, 7), (3, 7, 7))


@dataclass(frozen=True)
class _StubTarget:
    """Stands in for the live-turn target: every case's required files come back as durable
    artifacts so the capture grader and tier plumbing are the things asserted."""

    file_names: tuple[str, ...]

    @property
    def judge(self) -> None:
        return None

    @property
    def simulator(self) -> None:
        return None

    @property
    def agent_id(self) -> UUID:
        return uuid4()

    @property
    def conversations(self) -> None:
        return None

    async def run(self, case: CapabilityCase) -> TargetResult:
        artifacts = tuple(
            SharedArtifact(name, b'{"item": {}, "url": "https://x", "excerpts": ["e"]}\n')
            for name in self.file_names
            if name in case.message
        )
        output = CapabilityOutput(response="done", calls=(), artifacts=artifacts)
        return TargetResult(output, clean=True)

    async def step(self, conversation_id: UUID, message: str, idempotency_key: str) -> TargetResult:
        raise NotImplementedError


def _output(artifacts: tuple[SharedArtifact, ...], artifact_error: str = "") -> CapabilityOutput:
    return CapabilityOutput(
        response="done", calls=(), artifacts=artifacts, artifact_error=artifact_error
    )


async def test_submission_capture_saves_required_bytes(tmp_path: Path) -> None:
    grader = SubmissionCapture("case-a", tmp_path / "submissions", ("results_a.jsonl",))
    artifact = SharedArtifact("nested/results_a.jsonl", b'{"item": {}}\n')
    extra = SharedArtifact("notes.md", b"scratch")
    verdict = await grader(_output((artifact, extra)))
    assert verdict.passed
    saved = tmp_path / "submissions" / "case-a" / "results_a.jsonl"
    assert saved.read_bytes() == artifact.content
    assert verdict.evidence["extraSubmissions"] == ["notes.md"]
    assert not (tmp_path / "submissions" / "case-a" / "notes.md").exists()
    assert "results_a.jsonl" in grader.grading


async def test_submission_capture_requires_every_file(tmp_path: Path) -> None:
    grader = SubmissionCapture(
        "case-b", tmp_path / "submissions", ("results_a.jsonl", "results_b.jsonl")
    )
    verdict = await grader(_output((SharedArtifact("results_a.jsonl", b"{}\n"),)))
    assert not verdict.passed
    assert "results_b.jsonl" in verdict.reason


async def test_submission_capture_surfaces_artifact_errors(tmp_path: Path) -> None:
    grader = SubmissionCapture("case-c", tmp_path / "submissions", ("results_a.jsonl",))
    verdict = await grader(_output((), artifact_error="missing blob"))
    assert not verdict.passed
    assert "missing blob" in verdict.reason
