import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

import pytest

from evals.__main__ import main as evals_main
from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    SharedArtifact,
    ToolInvocation,
)
from evals.harness.coding import AllOf, PinnedRepositoryRoute
from evals.harness.registry import EvalTask
from evals.harness.scorers import delegation_only_scorer
from evals.harness.target import TargetResult
from evals.swebench.models import APPROVED_CASE_IDS, SWEbenchCase
from evals.swebench.runner import (
    PARENT_FORBIDDEN_TOOLS,
    SWEBENCH_PACKS,
    WORKFLOW_WAIT_SECONDS,
    PatchCapture,
    _capability_case,
    load_swebench,
)
from evals.swebench.snapshot import load_snapshot, select_cases, write_snapshot
from ufo.config import BlobConfig, Config, DatabaseConfig, PackConfig


class RunStarted(Exception):
    pass


def row(instance_id: str, index: int) -> dict[str, str]:
    owner, remainder = instance_id.split("__", maxsplit=1)
    repository = remainder.rsplit("-", maxsplit=1)[0]
    return {
        "repo": f"{owner}/{repository}",
        "instance_id": instance_id,
        "base_commit": f"{index + 1:040x}",
        "patch": f"diff --git a/reference{index}.py b/reference{index}.py\n",
        "test_patch": f"diff --git a/test{index}.py b/test{index}.py\n",
        "problem_statement": f"Fix regression {index} without changing the public API.",
        "hints_text": f"Private hint {index}",
        "created_at": f"2024-01-0{index + 1}T00:00:00Z",
        "version": f"{index + 1}.0",
        "FAIL_TO_PASS": json.dumps([f"test_fails_{index}"]),
        "PASS_TO_PASS": json.dumps([f"test_passes_{index}"]),
        "environment_setup_commit": f"{index + 4:040x}",
        "difficulty": ("<15 min fix", "15 min - 1 hour", "1-4 hours")[index],
    }


def snapshot(root: Path) -> Path:
    rows = tuple(row(instance_id, index) for index, instance_id in enumerate(APPROVED_CASE_IDS))
    write_snapshot(root, select_cases(rows))
    return root


def shared_output(name: str, content: bytes) -> CapabilityOutput:
    return CapabilityOutput(
        response="done",
        calls=(
            ToolInvocation(
                name="share_file",
                input={"files": [{"file_path": f"/workspace/{name}"}]},
                result=json.dumps([{"name": name}]),
                has_result=True,
            ),
        ),
        artifacts=(SharedArtifact(name=name, content=content),),
    )


def successful_output(case: SWEbenchCase, patch: bytes) -> CapabilityOutput:
    name = f"{case.instance_id}.patch"
    return CapabilityOutput(
        response="done",
        calls=(
            ToolInvocation(
                name="spawn",
                input={"target": "coding", "payload": {"objective": "fix it"}},
                result='{"result":"done"}',
                has_result=True,
            ),
            ToolInvocation(
                name="bash",
                input={"command": f"git fetch --depth 1 origin {case.base_commit}"},
                result="",
                has_result=True,
            ),
            ToolInvocation(
                name="share_file",
                input={"files": [{"file_path": f"/workspace/{name}"}]},
                result=json.dumps([{"name": name}]),
                has_result=True,
            ),
        ),
        artifacts=(SharedArtifact(name=name, content=patch),),
    )


@dataclass
class PreparingTarget:
    submissions_root: Path
    all_case_ids: tuple[str, ...]
    outputs: dict[str, CapabilityOutput] = field(default_factory=dict)
    states: list[tuple[str, dict[str, bool]]] = field(default_factory=list)
    seen: list[CapabilityCase] = field(default_factory=list)
    judge: None = None
    simulator: None = None

    async def run(self, case: CapabilityCase) -> TargetResult:
        if case.prepare is not None:
            await case.prepare(uuid4(), self.submissions_root / "workspace")
        self.seen.append(case)
        self.states.append(
            (
                case.name,
                {
                    case_id: (self.submissions_root / case_id).exists()
                    for case_id in self.all_case_ids
                },
            )
        )
        return TargetResult(self.outputs.get(case.name, CapabilityOutput("done", ())), clean=True)


def test_one_selected_case_builds_the_exact_pinned_capability_case(tmp_path: Path) -> None:
    snapshot_root = snapshot(tmp_path / "snapshot")
    selected = APPROVED_CASE_IDS[1]
    (task,) = load_swebench((selected,), snapshot_root, tmp_path / "submissions")
    case = next(case for case in load_snapshot(snapshot_root).cases if case.instance_id == selected)
    capability = _capability_case(
        case,
        load_snapshot(snapshot_root).manifest.digest,
        tmp_path / "submissions",
    )

    assert task.name == "swebench_verified"
    assert task.cases == (selected,)
    assert task.pin_runtime
    assert task.exclusive
    assert capability.message == (
        case.problem_statement
        + "\n\n---\n"
        + "Evaluation setup, to pass on to whoever does the work:\n"
        + f"The repository is https://github.com/{case.repo}.git at commit {case.base_commit}. "
        + "Fetch exactly that commit with depth 1; do not clone a branch or read a later commit.\n"
        + f"Deliverable: share /workspace/{case.instance_id}.patch, the complete unified diff "
        + "relative to the fetched commit. Only that shared file is graded."
    )
    assert case.patch not in capability.message
    assert case.test_patch not in capability.message
    assert isinstance(capability.grader, AllOf)
    route, delegation, capture = capability.grader.graders
    assert route == PinnedRepositoryRoute(case.repo, case.base_commit)
    assert delegation.grading == delegation_only_scorer(PARENT_FORBIDDEN_TOOLS).grading
    assert isinstance(capture, PatchCapture)
    assert capture.case_id == case.instance_id


async def test_capture_keeps_exact_valid_and_invalid_candidate_bytes(tmp_path: Path) -> None:
    case = select_cases(
        tuple(row(instance_id, index) for index, instance_id in enumerate(APPROVED_CASE_IDS))
    )[0]
    root = tmp_path / "submissions"
    capture = PatchCapture(case.instance_id, root)
    name = f"{case.instance_id}.patch"
    valid = b"--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\xff\n"

    accepted = await capture(shared_output(name, valid))
    target = root / case.instance_id / name
    assert accepted.passed, accepted.reason
    assert target.read_bytes() == valid
    assert accepted.evidence["submission"] == str(target)
    multi_file = (
        b"diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
        b"@@ -1 +1 @@\n-old a\n+new a\n"
        b"diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n"
        b"@@ -2 +2 @@\n-old b\n+new b\n"
    )
    accepted_multi = await capture(shared_output(name, multi_file))
    assert accepted_multi.passed, accepted_multi.reason
    assert target.read_bytes() == multi_file
    pure_deletion = b"--- a/a\n+++ b/a\n@@ -1 +0,0 @@\n--- removed text\n"
    accepted_deletion = await capture(shared_output(name, pure_deletion))
    assert accepted_deletion.passed, accepted_deletion.reason
    assert target.read_bytes() == pure_deletion
    no_newline = pure_deletion + b"\\ No newline at end of file\n"
    accepted_marker = await capture(shared_output(name, no_newline))
    assert accepted_marker.passed, accepted_marker.reason
    assert target.read_bytes() == no_newline

    invalid = b"the candidate shared these exact non-diff bytes\x00"
    refused = await capture(shared_output(name, invalid))
    assert not refused.passed
    assert "unified diff" in refused.reason
    assert target.read_bytes() == invalid
    assert refused.evidence["submission"] == str(target)


@pytest.mark.parametrize(
    "candidate",
    (
        b"diff --git a/a.py b/a.py\n",
        b"--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n",
        b"--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n-old\n+new\n",
        b"--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n+extra\n",
        b"---  \n+++ \t\n@@ -1 +1 @@\n-old\n+new\n",
        b"--- \t2024-01-01 00:00:00\n+++ \t2024-01-01 00:00:00\n@@ -1 +1 @@\n-old\n+new\n",
    ),
    ids=(
        "git-header-only",
        "hunk-without-body",
        "hunk-undershoot",
        "hunk-overshoot",
        "empty-file-paths",
        "timestamp-only-file-paths",
    ),
)
async def test_incomplete_diff_is_retained_but_refused(tmp_path: Path, candidate: bytes) -> None:
    case_id = APPROVED_CASE_IDS[0]
    root = tmp_path / "submissions"
    capture = PatchCapture(case_id, root)
    name = f"{case_id}.patch"

    verdict = await capture(shared_output(name, candidate))

    assert not verdict.passed
    assert "unified diff" in verdict.reason
    assert (root / case_id / name).read_bytes() == candidate


async def test_missing_or_unshared_capture_fails_visibly(tmp_path: Path) -> None:
    case = select_cases(
        tuple(row(instance_id, index) for index, instance_id in enumerate(APPROVED_CASE_IDS))
    )[0]
    root = tmp_path / "submissions"
    capture = PatchCapture(case.instance_id, root)
    name = f"{case.instance_id}.patch"

    missing = await capture(CapabilityOutput("done", ()))
    assert not missing.passed
    assert f"did not share {name}" in missing.reason
    assert not (root / case.instance_id).exists()

    unshared = await capture(
        CapabilityOutput(
            "done",
            (),
            artifacts=(SharedArtifact(name=name, content=b"diff --git a/a b/a\n"),),
        )
    )
    assert not unshared.passed
    assert f"did not share {name}" in unshared.reason
    assert not (root / case.instance_id).exists()


async def test_task_removes_only_a_case_stale_capture_when_that_case_starts(
    tmp_path: Path,
) -> None:
    snapshot_root = snapshot(tmp_path / "snapshot")
    selected = APPROVED_CASE_IDS[:2]
    unselected = APPROVED_CASE_IDS[2]
    submissions = tmp_path / "submissions"
    for case_id in APPROVED_CASE_IDS:
        stale = submissions / case_id / f"{case_id}.patch"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"stale")
    target = PreparingTarget(submissions, APPROVED_CASE_IDS)
    (task,) = load_swebench(selected, snapshot_root, submissions)
    assert all((submissions / case_id).is_dir() for case_id in APPROVED_CASE_IDS)

    await task.run(target, asyncio.Semaphore(2))

    first_name, first_state = target.states[0]
    second_name, second_state = target.states[1]
    assert first_name == selected[0]
    assert not first_state[selected[0]]
    assert first_state[selected[1]]
    assert first_state[unselected]
    assert second_name == selected[1]
    assert not second_state[selected[1]]
    assert second_state[unselected]
    assert (submissions / unselected / f"{unselected}.patch").read_bytes() == b"stale"


async def test_task_runs_all_three_deterministic_graders_and_captures_the_patch(
    tmp_path: Path,
) -> None:
    snapshot_root = snapshot(tmp_path / "snapshot")
    case = load_snapshot(snapshot_root).cases[0]
    patch = b"diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+fixed\n"
    target = PreparingTarget(
        tmp_path / "submissions",
        APPROVED_CASE_IDS,
        outputs={case.instance_id: successful_output(case, patch)},
    )
    (task,) = load_swebench((case.instance_id,), snapshot_root, target.submissions_root)

    report = await task.run(target, asyncio.Semaphore(1))

    assert report.cases[0].passed, report.cases[0].reason
    saved = target.submissions_root / case.instance_id / f"{case.instance_id}.patch"
    assert saved.read_bytes() == patch
    grader_evidence = report.cases[0].evidence["attempts"][0]["grader"]
    assert grader_evidence["codingSpawns"] == 1
    assert grader_evidence["ownTools"] == []
    assert grader_evidence["sizeBytes"] == len(patch)


def test_unknown_case_fails_during_loading(tmp_path: Path) -> None:
    snapshot_root = snapshot(tmp_path / "snapshot")
    with pytest.raises(ValueError, match="unknown SWE-bench case ids: not-a-case"):
        load_swebench(("not-a-case",), snapshot_root, tmp_path / "submissions")


def test_cli_refuses_case_without_suite_and_corpus_conflicts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        evals_main(["--swebench-case", APPROVED_CASE_IDS[0]])
    assert "--swebench-case requires --swebench" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        evals_main(["--swebench", "--coding-repo"])
    assert "corpus-backed evals are separate eval runs" in capsys.readouterr().err


def test_cli_refuses_missing_snapshot_before_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def not_reached(*_args: object) -> NoReturn:
        raise AssertionError("_run must not be reached")

    monkeypatch.setattr("evals.__main__._run", not_reached)
    with pytest.raises(SystemExit):
        evals_main(
            [
                "--swebench",
                "--swebench-snapshot",
                str(tmp_path / "missing"),
            ]
        )
    assert "SWE-bench snapshot root must be a symbolic link" in capsys.readouterr().err


def test_cli_refuses_unknown_case_before_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot_root = snapshot(tmp_path / "snapshot")

    async def not_reached(*_args: object) -> NoReturn:
        raise AssertionError("_run must not be reached")

    monkeypatch.setattr("evals.__main__._run", not_reached)
    with pytest.raises(SystemExit):
        evals_main(
            [
                "--swebench",
                "--swebench-snapshot",
                str(snapshot_root),
                "--swebench-case",
                "not-a-case",
            ]
        )
    assert "unknown SWE-bench case ids: not-a-case" in capsys.readouterr().err


def test_cli_mints_a_distinct_default_submissions_root_each_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots: list[Path] = []

    def loaded(_cases: tuple[str, ...], _snapshot: Path, submissions: Path) -> tuple[EvalTask, ...]:
        roots.append(submissions)
        return ()

    monkeypatch.setattr("evals.__main__.load_swebench", loaded)
    evals_main(["--swebench", "--list"])
    evals_main(["--swebench", "--list"])

    assert len(roots) == 2
    assert roots[0] != roots[1]
    assert roots[0].parent == Path(".local/swebench/submissions")
    assert roots[1].parent == Path(".local/swebench/submissions")
    assert len(roots[0].name.rsplit("-", maxsplit=1)[-1]) == 8
    assert len(roots[1].name.rsplit("-", maxsplit=1)[-1]) == 8


def test_cli_validates_pack_before_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot_root = snapshot(tmp_path / "snapshot")
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///tenant.db"),
        blob=BlobConfig(backend="filesystem", root=tmp_path / "blobs"),
        pack=PackConfig(name="gdpval_full"),
    )
    monkeypatch.setattr("evals.__main__.load_config", lambda: config)

    async def not_reached(*_args: object) -> NoReturn:
        raise AssertionError("_run must not be reached")

    monkeypatch.setattr("evals.__main__._run", not_reached)
    with pytest.raises(SystemExit):
        evals_main(["--swebench", "--swebench-snapshot", str(snapshot_root)])
    error = capsys.readouterr().err
    assert f"swebench requires [pack] name in {SWEBENCH_PACKS}" in error


def test_cli_loads_task_prints_submissions_and_uses_two_hour_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot_root = snapshot(tmp_path / "snapshot")
    submissions = tmp_path / "submissions"
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///tenant.db"),
        blob=BlobConfig(backend="filesystem", root=tmp_path / "blobs"),
        pack=PackConfig(name="assistant"),
    )
    monkeypatch.setattr("evals.__main__.load_config", lambda: config)
    reached: list[tuple[tuple[str, ...], float, str]] = []

    async def run(
        _config: object,
        tasks: tuple[EvalTask, ...],
        _agent: object,
        _recorder: object,
        _workspace: object,
        _collector: object,
        workflow_wait_seconds: float,
        *_rest: object,
    ) -> NoReturn:
        reached.append(
            (
                tuple(task.name for task in tasks),
                workflow_wait_seconds,
                capsys.readouterr().out,
            )
        )
        raise RunStarted

    monkeypatch.setattr("evals.__main__._run", run)
    with pytest.raises(RunStarted):
        evals_main(
            [
                "--swebench",
                "--swebench-snapshot",
                str(snapshot_root),
                "--swebench-submissions",
                str(submissions),
            ]
        )

    assert reached == [
        (("swebench_verified",), WORKFLOW_WAIT_SECONDS, f"SWE-bench submissions {submissions}\n")
    ]
