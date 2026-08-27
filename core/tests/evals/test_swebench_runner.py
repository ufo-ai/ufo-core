import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import NoReturn
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

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
from evals.swebench.models import SMOKE_CASE_IDS, Subset, SWEbenchCase
from evals.swebench.runner import (
    PARENT_FORBIDDEN_TOOLS,
    SWEBENCH_PACKS,
    WORKFLOW_WAIT_SECONDS,
    CapturedPatches,
    PatchCapture,
    _capability_case,
    capture_shared_patches,
    load_swebench,
)
from evals.swebench.snapshot import (
    SWEBENCH_UPSTREAM,
    load_snapshot,
    select_cases,
    write_snapshot,
)
from ufo.blob import FilesystemBlobStore, WorkspaceBlobStore
from ufo.config import BlobConfig, Config, DatabaseConfig, PackConfig
from ufo.db import workspace_tx
from ufo.schema import tables
from ufo.workspace import ws

SUBSETS = SWEBENCH_UPSTREAM.subsets
ALL_IDS = SUBSETS.all_ids


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
        "difficulty": ("<15 min fix", "15 min - 1 hour", "1-4 hours")[index % 3],
    }


def rows(statement: str = "") -> tuple[dict[str, str], ...]:
    built = tuple(row(instance_id, index) for index, instance_id in enumerate(ALL_IDS))
    if not statement:
        return built
    return (*built[:-1], {**built[-1], "problem_statement": statement})


def snapshot(root: Path, statement: str = "") -> Path:
    write_snapshot(root, select_cases(rows(statement)))
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
    selected = SMOKE_CASE_IDS[1]
    (task,) = load_swebench((selected,), snapshot_root, tmp_path / "submissions", None)
    case = next(case for case in load_snapshot(snapshot_root).cases if case.instance_id == selected)
    capability = _capability_case(
        case,
        load_snapshot(snapshot_root).manifest.upstream.parquet.sha256,
        tmp_path / "submissions",
    )

    assert task.name == "swebench_verified.smoke"
    assert task.cases == (selected,)
    assert task.pin_runtime
    assert not task.exclusive
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


@pytest.mark.parametrize(
    ("statement", "expected"),
    (
        ("First constraint.\nSecond constraint.\n" * 2, "First constraint.\nSecond constraint.\n"),
        (
            "First constraint.\nSecond constraint.\nFirst constraint.\nChanged constraint.\n",
            "First constraint.\nSecond constraint.\nFirst constraint.\nChanged constraint.\n",
        ),
    ),
    ids=("exact-repeat", "near-repeat"),
)
def test_only_an_exact_repeated_problem_statement_half_is_collapsed(
    tmp_path: Path, statement: str, expected: str
) -> None:
    case = select_cases(rows())[0].model_copy(update={"problem_statement": statement})

    capability = _capability_case(case, "snapshot", tmp_path / "submissions")

    assert capability.message.startswith(expected + "\n\n---\n")


def test_every_subset_loads_as_its_own_concurrent_task(tmp_path: Path) -> None:
    snapshot_root = snapshot(tmp_path / "snapshot")

    tasks = load_swebench((), snapshot_root, tmp_path / "submissions", None)

    assert tuple(task.name for task in tasks) == (
        "swebench_verified.smoke",
        "swebench_verified.hillclimb",
        "swebench_verified.holdout",
        "swebench_verified.hard",
    )
    assert tuple(task.cases for task in tasks) == (
        SUBSETS.smoke,
        SUBSETS.hillclimb,
        SUBSETS.holdout,
        tuple(
            case_id
            for case_id in SUBSETS.hard
            if case_id not in SUBSETS.smoke + SUBSETS.hillclimb + SUBSETS.holdout
        ),
    )
    assert all(task.pin_runtime and not task.exclusive for task in tasks)
    assert len({task.digest for task in tasks}) == 4


@pytest.mark.parametrize("subset", ("smoke", "hillclimb", "holdout", "hard"))
def test_one_subset_loads_only_its_own_cases(tmp_path: Path, subset: Subset) -> None:
    snapshot_root = snapshot(tmp_path / "snapshot")

    (task,) = load_swebench((), snapshot_root, tmp_path / "submissions", subset)

    assert task.name == f"swebench_verified.{subset}"
    assert task.cases == SUBSETS.ids(subset)


def test_the_hard_subset_keeps_its_frozen_smoke_case_while_all_runs_it_once(
    tmp_path: Path,
) -> None:
    snapshot_root = snapshot(tmp_path / "snapshot")
    representative = SUBSETS.smoke + SUBSETS.hillclimb + SUBSETS.holdout
    overlapping = tuple(case_id for case_id in SUBSETS.hard if case_id in representative)

    (hard,) = load_swebench(overlapping, snapshot_root, tmp_path / "hard", "hard")
    all_tasks = load_swebench(overlapping, snapshot_root, tmp_path / "all", None)

    assert hard.cases == overlapping
    combined = tuple(case for task in all_tasks for case in task.cases)
    assert len(combined) == len(overlapping)
    assert set(combined) == set(overlapping)


def test_named_cases_narrow_the_subsets_that_carry_them(tmp_path: Path) -> None:
    snapshot_root = snapshot(tmp_path / "snapshot")
    named = (SUBSETS.smoke[0], SUBSETS.holdout[2])

    tasks = load_swebench(named, snapshot_root, tmp_path / "submissions", None)

    assert tuple((task.name, task.cases) for task in tasks) == (
        ("swebench_verified.smoke", (named[0],)),
        ("swebench_verified.holdout", (named[1],)),
    )
    with pytest.raises(ValueError, match="no SWE-bench cases selected"):
        load_swebench((SUBSETS.holdout[2],), snapshot_root, tmp_path / "submissions", "hillclimb")


def test_a_case_keeps_its_digest_tag_across_snapshot_versions(tmp_path: Path) -> None:
    first = load_snapshot(snapshot(tmp_path / "first"))
    second = load_snapshot(snapshot(tmp_path / "second", "A later roster case changed."))
    submissions = tmp_path / "submissions"
    unchanged = SMOKE_CASE_IDS[0]

    assert first.manifest.digest != second.manifest.digest
    tags = tuple(
        _capability_case(
            next(case for case in snapshot_value.cases if case.instance_id == unchanged),
            snapshot_value.manifest.upstream.parquet.sha256,
            submissions,
        ).digest_tag
        for snapshot_value in (first, second)
    )
    assert tags[0] == tags[1]
    assert tags[0].startswith(f"{SWEBENCH_UPSTREAM.parquet.sha256}:")


async def test_capture_keeps_exact_valid_and_invalid_candidate_bytes(tmp_path: Path) -> None:
    case = select_cases(rows())[0]
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
    case_id = SMOKE_CASE_IDS[0]
    root = tmp_path / "submissions"
    capture = PatchCapture(case_id, root)
    name = f"{case_id}.patch"

    verdict = await capture(shared_output(name, candidate))

    assert not verdict.passed
    assert "unified diff" in verdict.reason
    assert (root / case_id / name).read_bytes() == candidate


async def test_missing_or_unshared_capture_fails_visibly(tmp_path: Path) -> None:
    case = select_cases(rows())[0]
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


async def test_capture_shared_patches_reads_durable_bytes_without_rerunning(
    db: None, tmp_path: Path
) -> None:
    workspace_id = uuid4()
    agent_id = uuid4()
    conversation_id = uuid4()
    turn_id = uuid4()
    case_id = SMOKE_CASE_IDS[0]
    filename = f"{case_id}.patch"
    blob_key = f"artifacts/{uuid4()}/{filename}"
    patch = b"--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
    blob = WorkspaceBlobStore(backend=FilesystemBlobStore(root=tmp_path / "blob"))

    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.workspace).values(
                    id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                )
            )
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=agent_id,
                    workspace_id=workspace_id,
                    name="assistant",
                    prompt="Solve the task.",
                    model="eval",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    surface="cli",
                    queue_key=str(conversation_id),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=turn_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    seq=1,
                    status="done",
                    inbound="fix it",
                    terminal={"status": "done", "text": "Done.", "model": "eval"},
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.shared_artifact).values(
                    turn_id=turn_id,
                    blob_key=blob_key,
                    workspace_id=workspace_id,
                    filename=filename,
                    subject=None,
                    media_type="text/x-diff",
                    size_bytes=len(patch),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        await blob.put(blob_key, patch)

        captured = await capture_shared_patches(blob, (case_id,), tmp_path / "submissions")
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.shared_artifact)
                .where(tables.shared_artifact.c.turn_id == turn_id)
                .values(size_bytes=len(patch) + 1)
            )
        with pytest.raises(ValueError, match="stored bytes; row declares"):
            await capture_shared_patches(blob, (case_id,), tmp_path / "mismatched")
        duplicate_turn_id = uuid4()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.shared_artifact)
                .where(tables.shared_artifact.c.turn_id == turn_id)
                .values(size_bytes=len(patch))
            )
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=duplicate_turn_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    seq=2,
                    status="done",
                    inbound="fix it again",
                    terminal={"status": "done", "text": "Done.", "model": "eval"},
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.shared_artifact).values(
                    turn_id=duplicate_turn_id,
                    blob_key=f"artifacts/{uuid4()}/{filename}",
                    workspace_id=workspace_id,
                    filename=filename,
                    subject=None,
                    media_type="text/x-diff",
                    size_bytes=len(patch),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        with pytest.raises(ValueError, match=f"multiple shared artifacts named: {filename}"):
            await capture_shared_patches(blob, (case_id,), tmp_path / "duplicate")

    target = tmp_path / "submissions" / case_id / filename
    assert captured.paths == (target,)
    assert captured.missing == ()
    assert target.read_bytes() == patch


async def test_task_removes_only_a_case_stale_capture_when_that_case_starts(
    tmp_path: Path,
) -> None:
    snapshot_root = snapshot(tmp_path / "snapshot")
    selected = SMOKE_CASE_IDS[:2]
    unselected = SMOKE_CASE_IDS[2]
    submissions = tmp_path / "submissions"
    for case_id in ALL_IDS:
        stale = submissions / case_id / f"{case_id}.patch"
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"stale")
    target = PreparingTarget(submissions, ALL_IDS)
    (task,) = load_swebench(selected, snapshot_root, submissions, "smoke")
    assert all((submissions / case_id).is_dir() for case_id in ALL_IDS)

    await task.run(target, asyncio.Semaphore(2))

    assert {case.name for case in target.seen} == set(selected)
    assert all(not (submissions / case_id).exists() for case_id in selected)
    assert (submissions / unselected / f"{unselected}.patch").read_bytes() == b"stale"


async def test_task_runs_all_three_deterministic_graders_and_captures_the_patch(
    tmp_path: Path,
) -> None:
    snapshot_root = snapshot(tmp_path / "snapshot")
    case = load_snapshot(snapshot_root).cases[0]
    patch = b"diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+fixed\n"
    target = PreparingTarget(
        tmp_path / "submissions",
        ALL_IDS,
        outputs={case.instance_id: successful_output(case, patch)},
    )
    (task,) = load_swebench((case.instance_id,), snapshot_root, target.submissions_root, None)

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
        load_swebench(("not-a-case",), snapshot_root, tmp_path / "submissions", None)


def test_cli_refuses_case_without_suite_and_corpus_conflicts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        evals_main(["--swebench-case", SMOKE_CASE_IDS[0]])
    assert "--swebench-subset and --swebench-case require --swebench" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        evals_main(["--swebench"])
    assert "--swebench requires --swebench-subset or --swebench-case" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        evals_main(["--swebench", "--swebench-subset", "smoke", "--coding-repo"])
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
                "--swebench-subset",
                "smoke",
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

    def loaded(
        _cases: tuple[str, ...], _snapshot: Path, submissions: Path, _subset: Subset | None
    ) -> tuple[EvalTask, ...]:
        roots.append(submissions)
        return ()

    monkeypatch.setattr("evals.__main__.load_swebench", loaded)
    evals_main(["--swebench", "--swebench-subset", "smoke", "--list"])
    evals_main(["--swebench", "--swebench-subset", "smoke", "--list"])

    assert len(roots) == 2
    assert roots[0] != roots[1]
    assert roots[0].parent == Path(".local/swebench/submissions")
    assert roots[1].parent == Path(".local/swebench/submissions")
    assert len(roots[0].name.rsplit("-", maxsplit=1)[-1]) == 8
    assert len(roots[1].name.rsplit("-", maxsplit=1)[-1]) == 8


def test_cli_all_loads_every_subset_for_remote_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loaded_subsets: list[Subset | None] = []

    def loaded(
        _cases: tuple[str, ...], _snapshot: Path, _submissions: Path, subset: Subset | None
    ) -> tuple[EvalTask, ...]:
        loaded_subsets.append(subset)
        return ()

    monkeypatch.setattr("evals.__main__.load_swebench", loaded)
    evals_main(
        [
            "--swebench",
            "--swebench-subset",
            "all",
            "--list",
        ]
    )

    assert loaded_subsets == [None]


def test_cli_captures_durable_swebench_patches_without_starting_a_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot_root = snapshot(tmp_path / "snapshot")
    submissions = tmp_path / "submissions"
    workspace_id = uuid4()
    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///tenant.db"),
        blob=BlobConfig(backend="filesystem", root=tmp_path / "blobs"),
        pack=PackConfig(name="assistant"),
    )
    reached: list[tuple[UUID, tuple[str, ...], Path]] = []

    async def capture(
        _config: Config, workspace: UUID, cases: tuple[str, ...], root: Path
    ) -> CapturedPatches:
        assert _config is config
        reached.append((workspace, cases, root))
        target = root / cases[0] / f"{cases[0]}.patch"
        return CapturedPatches(paths=(target,), missing=(cases[1],))

    monkeypatch.setattr("evals.__main__.load_config", lambda: config)
    monkeypatch.setattr("evals.__main__._capture_swebench", capture)

    evals_main(
        [
            "--swebench-capture",
            "--workspace",
            str(workspace_id),
            "--swebench-subset",
            "smoke",
            "--swebench-snapshot",
            str(snapshot_root),
            "--swebench-submissions",
            str(submissions),
        ]
    )

    assert reached == [(workspace_id, SMOKE_CASE_IDS, submissions)]
    assert capsys.readouterr().out == (
        f"captured 1 SWE-bench patches in {submissions}\nmissing 1: {SMOKE_CASE_IDS[1]}\n"
    )


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
        evals_main(
            ["--swebench", "--swebench-subset", "smoke", "--swebench-snapshot", str(snapshot_root)]
        )
    error = capsys.readouterr().err
    assert f"swebench requires [pack] name in {SWEBENCH_PACKS}" in error


@pytest.mark.parametrize(
    "args",
    (
        ("--fresh-workspace",),
        ("--fresh-workspace", "--swebench", "--swebench-subset", "smoke"),
    ),
)
def test_fresh_workspace_requires_remote(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    args: tuple[str, ...],
) -> None:
    with pytest.raises(SystemExit):
        evals_main([*args, "--out", str(tmp_path)])

    assert "--fresh-workspace requires --remote" in capsys.readouterr().err


def test_fresh_workspace_accepts_a_remote_capability_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def load_config() -> NoReturn:
        raise RunStarted

    monkeypatch.setattr("evals.__main__.load_config", load_config)
    with pytest.raises(RunStarted):
        evals_main(
            [
                "--only",
                "coding_subagent",
                "--case",
                "coding-subagent-github-app-api",
                "--fresh-workspace",
                "--remote",
                "--budget-usd",
                "20",
                "--out",
                str(tmp_path),
            ]
        )


def test_fresh_workspace_conflicts_with_an_explicit_workspace(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        evals_main(
            [
                "--fresh-workspace",
                "--remote",
                "--swebench",
                "--swebench-subset",
                "smoke",
                "--workspace",
                str(uuid4()),
                "--out",
                str(tmp_path),
            ]
        )

    assert "--fresh-workspace conflicts with --workspace" in capsys.readouterr().err


def test_cli_loads_task_prints_submissions_and_routes_remote_concurrency(
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
    reached: list[tuple[tuple[str, ...], float, str, bool, bool, int, int]] = []

    async def run(
        _config: object,
        tasks: tuple[EvalTask, ...],
        _agent: object,
        _recorder: object,
        **kwargs: object,
    ) -> NoReturn:
        reached.append(
            (
                tuple(task.name for task in tasks),
                float(kwargs["workflow_wait_seconds"]),
                capsys.readouterr().out,
                bool(kwargs["fresh_workspace"]),
                bool(kwargs["remote"]),
                int(kwargs["concurrency"]),
                int(kwargs["budget_micro_usd"]),
            )
        )
        raise RunStarted

    monkeypatch.setattr("evals.__main__._run", run)
    with pytest.raises(RunStarted):
        evals_main(
            [
                "--swebench",
                "--swebench-subset",
                "hillclimb",
                "--swebench-snapshot",
                str(snapshot_root),
                "--swebench-submissions",
                str(submissions),
                "--fresh-workspace",
                "--remote",
                "--budget-usd",
                "20",
                "--concurrency",
                "8",
            ]
        )

    assert reached == [
        (
            ("swebench_verified.hillclimb",),
            WORKFLOW_WAIT_SECONDS,
            f"SWE-bench submissions {submissions}\n",
            True,
            True,
            8,
            20_000_000,
        )
    ]
