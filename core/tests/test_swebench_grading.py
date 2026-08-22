import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from evals.swebench import grading
from evals.swebench.grading import (
    SWEbenchGrading,
    load_submission_patches,
    official_instance_image,
    write_predictions,
)
from evals.swebench.models import APPROVED_CASE_IDS, SWEbenchSnapshot
from evals.swebench.snapshot import load_snapshot, select_cases, write_snapshot

ALL_IDS = set(APPROVED_CASE_IDS)


def row(instance_id: str, index: int) -> dict[str, str]:
    owner, remainder = instance_id.split("__", maxsplit=1)
    repository = remainder.rsplit("-", maxsplit=1)[0]
    return {
        "repo": f"{owner}/{repository}",
        "instance_id": instance_id,
        "base_commit": f"{index + 1:040x}",
        "patch": f"diff --git a/reference{index}.py b/reference{index}.py\n",
        "test_patch": f"diff --git a/test{index}.py b/test{index}.py\n",
        "problem_statement": f"Fix regression {index}.",
        "hints_text": f"Hint {index}",
        "created_at": f"2024-01-0{index + 1}T00:00:00Z",
        "version": f"{index + 1}.0",
        "FAIL_TO_PASS": json.dumps([f"test_fails_{index}"]),
        "PASS_TO_PASS": json.dumps([f"test_passes_{index}"]),
        "environment_setup_commit": f"{index + 4:040x}",
        "difficulty": ("<15 min fix", "15 min - 1 hour", "1-4 hours")[index],
    }


def snapshot(root: Path) -> SWEbenchSnapshot:
    rows = tuple(row(case_id, index) for index, case_id in enumerate(APPROVED_CASE_IDS))
    write_snapshot(root, select_cases(rows))
    return load_snapshot(root)


def write_patch(submissions: Path, case_id: str, patch: bytes) -> None:
    directory = submissions / case_id
    directory.mkdir(parents=True)
    (directory / f"{case_id}.patch").write_bytes(patch)


def official_report(
    case_ids: tuple[str, ...], *, resolved_ids: tuple[str, ...] = ()
) -> dict[str, object]:
    resolved = set(resolved_ids)
    unresolved_ids = tuple(case_id for case_id in case_ids if case_id not in resolved)
    return {
        "total_instances": len(case_ids),
        "submitted_instances": len(case_ids),
        "completed_instances": len(case_ids),
        "resolved_instances": len(resolved_ids),
        "unresolved_instances": len(unresolved_ids),
        "empty_patch_instances": 0,
        "error_instances": 0,
        "completed_ids": list(case_ids),
        "incomplete_ids": [],
        "empty_patch_ids": [],
        "submitted_ids": list(case_ids),
        "resolved_ids": list(resolved_ids),
        "unresolved_ids": list(unresolved_ids),
        "error_ids": [],
        "schema_version": 2,
    }


def grading_workflow(
    tmp_path: Path,
    snapshot_value: SWEbenchSnapshot,
    *,
    case_ids: tuple[str, ...] = APPROVED_CASE_IDS,
    gold: bool = False,
) -> SWEbenchGrading:
    cases = tuple(case for case in snapshot_value.cases if case.instance_id in set(case_ids))
    submissions = None if gold else tmp_path / "submissions"
    if submissions is not None:
        for case_id in case_ids:
            write_patch(
                submissions,
                case_id,
                f"diff --git a/{case_id}.py b/{case_id}.py\n".encode(),
            )
    parquet = tmp_path / "test.parquet"
    parquet.write_bytes(b"pinned parquet")
    return SWEbenchGrading(
        snapshot=snapshot_value,
        selected_cases=cases,
        parquet=parquet,
        submissions_root=submissions,
        grade_directory=tmp_path / "grade",
        run_id="official-smoke",
        gold=gold,
    )


def accept_test_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grading, "verify_source", lambda _source, _upstream=None: None)
    monkeypatch.setattr(grading, "version", lambda _distribution: "4.1.0")


def test_predictions_include_every_case_in_manifest_order_and_keep_missing_empty(
    tmp_path: Path,
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    submissions = tmp_path / "submissions"
    write_patch(submissions, APPROVED_CASE_IDS[2], b"")
    write_patch(submissions, APPROVED_CASE_IDS[0], b"first\r\n")
    output = tmp_path / "predictions.jsonl"

    patches = load_submission_patches(snapshot_value.cases, ALL_IDS, submissions)
    assert write_predictions(snapshot_value.cases, patches, output, "ufo") == output

    assert output.read_bytes().splitlines() == [
        json.dumps(
            {
                "instance_id": APPROVED_CASE_IDS[0],
                "model_patch": "first\r\n",
                "model_name_or_path": "ufo",
            },
            separators=(",", ":"),
        ).encode(),
        json.dumps(
            {
                "instance_id": APPROVED_CASE_IDS[1],
                "model_patch": "",
                "model_name_or_path": "ufo",
            },
            separators=(",", ":"),
        ).encode(),
        json.dumps(
            {
                "instance_id": APPROVED_CASE_IDS[2],
                "model_patch": "",
                "model_name_or_path": "ufo",
            },
            separators=(",", ":"),
        ).encode(),
    ]


def test_predictions_reject_unexpected_submission_directories(tmp_path: Path) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    submissions = tmp_path / "submissions"
    (submissions / "unexpected__repo-1").mkdir(parents=True)

    with pytest.raises(ValueError, match="unexpected SWE-bench submission directories"):
        load_submission_patches(snapshot_value.cases, ALL_IDS, submissions)


def test_predictions_reject_duplicate_patch_candidates(tmp_path: Path) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    submissions = tmp_path / "submissions"
    case_id = APPROVED_CASE_IDS[0]
    write_patch(submissions, case_id, b"first")
    (submissions / case_id / "duplicate.patch").write_bytes(b"second")

    with pytest.raises(ValueError, match=f"duplicate SWE-bench patches for {case_id}"):
        load_submission_patches(snapshot_value.cases, ALL_IDS, submissions)


def test_subset_grading_keeps_sibling_case_directories(tmp_path: Path) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    submissions = tmp_path / "submissions"
    for case_id in APPROVED_CASE_IDS:
        write_patch(submissions, case_id, f"patch for {case_id}\n".encode())
    one = tuple(c for c in snapshot_value.cases if c.instance_id == APPROVED_CASE_IDS[0])

    patches = load_submission_patches(one, ALL_IDS, submissions)

    assert patches == {APPROVED_CASE_IDS[0]: f"patch for {APPROVED_CASE_IDS[0]}\n"}


def test_non_utf8_patch_becomes_an_empty_submission(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    submissions = tmp_path / "submissions"
    case_id = APPROVED_CASE_IDS[0]
    write_patch(submissions, case_id, b"diff --git a/x b/x\n\xff")

    patches = load_submission_patches(snapshot_value.cases, ALL_IDS, submissions)

    assert patches[case_id] == ""
    assert "not UTF-8" in capsys.readouterr().out


def test_official_instance_images_are_the_pinned_harness_names() -> None:
    assert tuple(official_instance_image(case_id) for case_id in APPROVED_CASE_IDS) == (
        "swebench/sweb.eval.x86_64.django_1776_django-10097:latest",
        "swebench/sweb.eval.x86_64.sympy_1776_sympy-20590:latest",
        "swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-25102:latest",
    )


def test_workflow_pulls_official_images_then_invokes_official_harness_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    workflow = grading_workflow(tmp_path, snapshot_value)
    accept_test_source(monkeypatch)
    calls: list[tuple[tuple[str, ...], Path | None]] = []
    report_bytes = json.dumps(
        official_report(APPROVED_CASE_IDS, resolved_ids=APPROVED_CASE_IDS[:2]),
        indent=1,
    ).encode()

    def run(
        command: Sequence[str], *, cwd: Path | None = None, check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        assert check
        calls.append((tuple(command), cwd))
        if cwd is not None:
            (cwd / "ufo.official-smoke.json").write_bytes(report_bytes)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(grading.subprocess, "run", run)

    summary_path = workflow.run()

    grade = workflow.grade_directory.resolve()
    predictions = grade / "predictions.jsonl"
    assert calls == [
        *(
            (
                (
                    "docker",
                    "pull",
                    "--platform",
                    "linux/amd64",
                    official_instance_image(case_id),
                ),
                None,
            )
            for case_id in APPROVED_CASE_IDS
        ),
        (
            (
                sys.executable,
                "-m",
                "swebench.harness.run_evaluation",
                "--dataset_name",
                str(workflow.parquet.resolve().parent),
                "--split",
                "test",
                "--predictions_path",
                str(predictions),
                "--max_workers",
                "1",
                "--instance_ids",
                *APPROVED_CASE_IDS,
                "--run_id",
                "official-smoke",
            ),
            grade,
        ),
    ]
    official = grade / "ufo.official-smoke.json"
    assert official.read_bytes() == report_bytes
    assert summary_path == grade / "summary.json"
    assert json.loads(summary_path.read_bytes()) == {
        "pins": {
            "harness": "swebench==4.1.0",
            "snapshot": snapshot_value.manifest.digest,
            "dataset": snapshot_value.manifest.upstream.dataset,
            "revision": snapshot_value.manifest.upstream.revision,
            "parquet": snapshot_value.manifest.upstream.parquet.sha256,
        },
        "selected_ids": list(APPROVED_CASE_IDS),
        "official_report": str(official),
        "official_resolved": 2,
    }


def test_gold_mode_uses_official_gold_predictions_and_pulls_only_selected_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    selected = APPROVED_CASE_IDS[1:]
    workflow = grading_workflow(tmp_path, snapshot_value, case_ids=selected, gold=True)
    accept_test_source(monkeypatch)
    commands: list[tuple[str, ...]] = []

    def run(
        command: Sequence[str], *, cwd: Path | None = None, check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        assert check
        commands.append(tuple(command))
        if cwd is None:
            return subprocess.CompletedProcess(command, 0)
        report = official_report(selected, resolved_ids=selected)
        report["submitted_instances"] = 3
        report["submitted_ids"] = [*selected, "another__official-1"]
        (cwd / "gold.official-smoke.json").write_text(json.dumps(report))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(grading.subprocess, "run", run)

    workflow.run()

    pulled = [command[-1] for command in commands if command[:2] == ("docker", "pull")]
    assert pulled == [official_instance_image(case_id) for case_id in selected]
    command = commands[-1]
    assert command[command.index("--predictions_path") + 1] == "gold"
    assert "--namespace" not in command
    assert not (workflow.grade_directory / "predictions.jsonl").exists()


def test_workflow_revalidates_parquet_before_invoking_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    workflow = grading_workflow(tmp_path, snapshot_value)

    def not_reached(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("official harness must not run")

    monkeypatch.setattr(grading.subprocess, "run", not_reached)

    with pytest.raises(ValueError, match="SWE-bench parquet size mismatch"):
        workflow.run()


@pytest.mark.parametrize("failure", ("missing", "error", "omitted"))
def test_workflow_rejects_missing_error_and_omitted_official_outcomes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    workflow = grading_workflow(tmp_path, snapshot_value)
    accept_test_source(monkeypatch)

    def run(
        command: Sequence[str], *, cwd: Path | None = None, check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        assert check
        if failure == "missing" or cwd is None:
            return subprocess.CompletedProcess(command, 0)
        report = official_report(APPROVED_CASE_IDS)
        if failure == "error":
            report["error_instances"] = 1
            report["error_ids"] = [APPROVED_CASE_IDS[0]]
        else:
            report["completed_instances"] = 2
            report["completed_ids"] = list(APPROVED_CASE_IDS[:2])
            report["unresolved_instances"] = 2
            report["unresolved_ids"] = list(APPROVED_CASE_IDS[:2])
        (cwd / "ufo.official-smoke.json").write_text(json.dumps(report))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(grading.subprocess, "run", run)

    with pytest.raises((FileNotFoundError, ValueError), match="official SWE-bench"):
        workflow.run()
    assert not (workflow.grade_directory / "summary.json").exists()


def test_workflow_accepts_empty_patch_as_an_explicit_denominator_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    workflow = grading_workflow(tmp_path, snapshot_value)
    accept_test_source(monkeypatch)
    empty_id = APPROVED_CASE_IDS[2]
    report = official_report(APPROVED_CASE_IDS, resolved_ids=(APPROVED_CASE_IDS[0],))
    report["completed_instances"] = 2
    report["completed_ids"] = list(APPROVED_CASE_IDS[:2])
    report["unresolved_instances"] = 1
    report["unresolved_ids"] = [APPROVED_CASE_IDS[1]]
    report["empty_patch_instances"] = 1
    report["empty_patch_ids"] = [empty_id]
    report_bytes = json.dumps(report, indent=1).encode()

    def run(
        command: Sequence[str], *, cwd: Path | None = None, check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        assert check
        if cwd is not None:
            (cwd / "ufo.official-smoke.json").write_bytes(report_bytes)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(grading.subprocess, "run", run)

    summary_path = workflow.run()

    official_path = workflow.grade_directory.resolve() / "ufo.official-smoke.json"
    assert official_path.read_bytes() == report_bytes
    assert json.loads(summary_path.read_bytes())["official_resolved"] == 1


def test_workflow_refuses_to_reuse_a_grade_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    workflow = grading_workflow(tmp_path, snapshot_value)
    accept_test_source(monkeypatch)
    workflow.grade_directory.mkdir()

    with pytest.raises(FileExistsError):
        workflow.run()


@pytest.mark.parametrize(
    ("arguments", "message"),
    (
        ((), "--submissions is required without --gold"),
        (
            ("--gold", "--submissions", "submissions"),
            "--gold does not accept --submissions",
        ),
    ),
)
def test_cli_requires_exactly_one_prediction_source(
    arguments: tuple[str, ...],
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        grading.main(arguments)
    assert message in capsys.readouterr().err
