import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from evals.swebench import grading
from evals.swebench.grading import (
    SWEbenchGrading,
    gold_overlap,
    load_submission_patches,
    official_instance_image,
    write_predictions,
)
from evals.swebench.models import SMOKE_CASE_IDS, Subset, SWEbenchSnapshot
from evals.swebench.snapshot import SWEBENCH_UPSTREAM, load_snapshot, select_cases, write_snapshot

SUBSETS = SWEBENCH_UPSTREAM.subsets
ALL_IDS = set(SUBSETS.all_ids)


def row(instance_id: str, index: int) -> dict[str, str]:
    owner, remainder = instance_id.split("__", maxsplit=1)
    repository = remainder.rsplit("-", maxsplit=1)[0]
    return {
        "repo": f"{owner}/{repository}",
        "instance_id": instance_id,
        "base_commit": f"{index + 1:040x}",
        "patch": (
            f"diff --git a/reference{index}.py b/reference{index}.py\n"
            f"--- a/reference{index}.py\n"
            f"+++ b/reference{index}.py\n"
            "@@ -1 +1 @@\n"
            f"-reference = {index}\n"
            f"+reference = {index + 1}\n"
        ),
        "test_patch": f"diff --git a/test{index}.py b/test{index}.py\n",
        "problem_statement": f"Fix regression {index}.",
        "hints_text": f"Hint {index}",
        "created_at": f"2024-01-01T00:00:{index:02d}Z",
        "version": f"{index + 1}.0",
        "FAIL_TO_PASS": json.dumps([f"test_fails_{index}"]),
        "PASS_TO_PASS": json.dumps([f"test_passes_{index}"]),
        "environment_setup_commit": f"{index + 100:040x}",
        "difficulty": ("<15 min fix", "15 min - 1 hour", "1-4 hours")[index % 3],
    }


def snapshot(root: Path) -> SWEbenchSnapshot:
    rows = tuple(row(case_id, index) for index, case_id in enumerate(SUBSETS.all_ids))
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


def report_with_submissions(
    report: dict[str, object], case_ids: tuple[str, ...]
) -> dict[str, object]:
    report["submitted_instances"] = len(case_ids)
    report["submitted_ids"] = list(case_ids)
    return report


def grading_workflow(
    tmp_path: Path,
    snapshot_value: SWEbenchSnapshot,
    *,
    case_ids: tuple[str, ...] = SMOKE_CASE_IDS,
    subset: Subset = "smoke",
    gold: bool = False,
    prune_images: bool = False,
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
        subset=subset,
        parquet=parquet,
        submissions_root=submissions,
        grade_directory=tmp_path / "grade",
        run_id="official-smoke",
        gold=gold,
        prune_images=prune_images,
    )


def accept_test_source(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grading, "verify_source", lambda _source, _upstream=None: None)
    monkeypatch.setattr(grading, "version", lambda _distribution: "4.1.0")
    monkeypatch.setattr(
        grading.SWEbenchGrading, "_ensure_official_image", lambda _self, _case: None
    )


def test_predictions_include_every_case_in_manifest_order_and_keep_missing_empty(
    tmp_path: Path,
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    submissions = tmp_path / "submissions"
    write_patch(submissions, SMOKE_CASE_IDS[2], b"")
    write_patch(submissions, SMOKE_CASE_IDS[0], b"first\r\n")
    output = tmp_path / "predictions.jsonl"

    cases = tuple(case for case in snapshot_value.cases if case.instance_id in set(SMOKE_CASE_IDS))
    patches = load_submission_patches(cases, ALL_IDS, submissions)
    assert write_predictions(cases, patches, output, "ufo") == output

    assert output.read_bytes().splitlines() == [
        json.dumps(
            {
                "instance_id": SMOKE_CASE_IDS[0],
                "model_patch": "first\r\n",
                "model_name_or_path": "ufo",
            },
            separators=(",", ":"),
        ).encode(),
        json.dumps(
            {
                "instance_id": SMOKE_CASE_IDS[1],
                "model_patch": "",
                "model_name_or_path": "ufo",
            },
            separators=(",", ":"),
        ).encode(),
        json.dumps(
            {
                "instance_id": SMOKE_CASE_IDS[2],
                "model_patch": "",
                "model_name_or_path": "ufo",
            },
            separators=(",", ":"),
        ).encode(),
    ]


def test_predictions_remove_changes_owned_by_the_official_test_patch(tmp_path: Path) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    case = snapshot_value.cases[0]
    output = tmp_path / "predictions.jsonl"
    production = (
        "diff --git a/src/value.py b/src/value.py\n"
        "--- a/src/value.py\n"
        "+++ b/src/value.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    colliding_test = (
        "diff --git a/test0.py b/test0.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/test0.py\n"
        "@@ -0,0 +1 @@\n"
        "+assert True\n"
    )

    write_predictions((case,), {case.instance_id: production + colliding_test}, output, "ufo")

    assert json.loads(output.read_text())["model_patch"] == production


def test_predictions_reject_unexpected_submission_directories(tmp_path: Path) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    submissions = tmp_path / "submissions"
    (submissions / "unexpected__repo-1").mkdir(parents=True)

    with pytest.raises(ValueError, match="unexpected SWE-bench submission directories"):
        load_submission_patches(snapshot_value.cases, ALL_IDS, submissions)


def test_predictions_reject_duplicate_patch_candidates(tmp_path: Path) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    submissions = tmp_path / "submissions"
    case_id = SMOKE_CASE_IDS[0]
    write_patch(submissions, case_id, b"first")
    (submissions / case_id / "duplicate.patch").write_bytes(b"second")

    with pytest.raises(ValueError, match=f"duplicate SWE-bench patches for {case_id}"):
        load_submission_patches(snapshot_value.cases, ALL_IDS, submissions)


def test_partial_selection_keeps_sibling_case_directories(tmp_path: Path) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    submissions = tmp_path / "submissions"
    for case_id in SMOKE_CASE_IDS:
        write_patch(submissions, case_id, f"patch for {case_id}\n".encode())
    one = tuple(c for c in snapshot_value.cases if c.instance_id == SMOKE_CASE_IDS[0])

    patches = load_submission_patches(one, ALL_IDS, submissions)

    assert patches == {SMOKE_CASE_IDS[0]: f"patch for {SMOKE_CASE_IDS[0]}\n"}


def test_non_utf8_patch_becomes_an_empty_submission(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    submissions = tmp_path / "submissions"
    case_id = SMOKE_CASE_IDS[0]
    write_patch(submissions, case_id, b"diff --git a/x b/x\n\xff")

    patches = load_submission_patches(snapshot_value.cases, ALL_IDS, submissions)

    assert patches[case_id] == ""
    assert "not UTF-8" in capsys.readouterr().out


def test_gold_overlap_counts_only_nonblank_changed_lines() -> None:
    gold = (
        "diff --git a/src/value.py b/src/value.py\n"
        "--- a/src/value.py\n"
        "+++ b/src/value.py\n"
        "@@ -1,2 +1,3 @@\n"
        "-old = 1\n"
        "+\n"
        "+new = 2\n"
    )
    headers_only = (
        "diff --git a/src/value.py b/src/value.py\n--- a/src/value.py\n+++ b/src/value.py\n"
    )
    partial = "diff --git a/other.py b/other.py\n+new = 2\n"

    assert gold_overlap(gold, gold) == 1.0
    assert gold_overlap(gold, partial) == 0.5
    assert gold_overlap(gold, headers_only) == 0.0
    assert gold_overlap(headers_only, gold) == 0.0


def test_official_instance_images_are_the_pinned_harness_names() -> None:
    assert tuple(official_instance_image(case_id) for case_id in SMOKE_CASE_IDS) == (
        "swebench/sweb.eval.x86_64.django_1776_django-10097:latest",
        "swebench/sweb.eval.x86_64.sympy_1776_sympy-20590:latest",
        "swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-25102:latest",
    )


def test_matching_local_official_image_skips_the_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    workflow = grading_workflow(tmp_path, snapshot_value)
    case = workflow.selected_cases[0]
    image = official_instance_image(case.instance_id)
    repository = image.rsplit(":", maxsplit=1)[0]
    inspected: list[tuple[str, ...]] = []

    def inspect(command: Sequence[str], **_kwargs: object) -> str:
        inspected.append(tuple(command))
        return f"linux/amd64\n{repository}@sha256:{'a' * 64}\n"

    def not_pulled(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a verified local image must not be pulled")

    monkeypatch.setattr(grading.subprocess, "check_output", inspect)
    monkeypatch.setattr(grading.subprocess, "run", not_pulled)

    workflow._ensure_official_image(case)

    assert inspected == [
        (
            "docker",
            "image",
            "inspect",
            "--format",
            grading.OFFICIAL_IMAGE_INSPECT_FORMAT,
            image,
        )
    ]


@pytest.mark.parametrize(
    "cache_state",
    ("missing", "wrong-platform", "wrong-repository", "malformed-digest"),
)
def test_unverified_local_official_image_is_pulled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_state: str
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    workflow = grading_workflow(tmp_path, snapshot_value)
    case = workflow.selected_cases[0]
    image = official_instance_image(case.instance_id)
    repository = image.rsplit(":", maxsplit=1)[0]
    details = {
        "wrong-platform": f"linux/arm64\n{repository}@sha256:{'a' * 64}\n",
        "wrong-repository": f"linux/amd64\nother/image@sha256:{'a' * 64}\n",
        "malformed-digest": f"linux/amd64\n{repository}@sha256:short\n",
    }

    def inspect(_command: Sequence[str], **_kwargs: object) -> str:
        if cache_state == "missing":
            raise subprocess.CalledProcessError(1, "docker image inspect")
        return details[cache_state]

    pulls: list[tuple[str, ...]] = []

    def run(command: Sequence[str], *, check: bool) -> subprocess.CompletedProcess[bytes]:
        assert check
        pulls.append(tuple(command))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(grading.subprocess, "check_output", inspect)
    monkeypatch.setattr(grading.subprocess, "run", run)

    workflow._ensure_official_image(case)

    assert pulls == [("docker", "pull", "--platform", "linux/amd64", image)]


def test_workflow_grades_each_image_before_aggregating_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    workflow = grading_workflow(tmp_path, snapshot_value)
    accept_test_source(monkeypatch)
    calls: list[tuple[tuple[str, ...], Path | None]] = []
    report_bytes = json.dumps(
        official_report(SMOKE_CASE_IDS, resolved_ids=SMOKE_CASE_IDS[:2]),
        indent=1,
    ).encode()

    def run(
        command: Sequence[str], *, cwd: Path | None = None, check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        assert check
        calls.append((tuple(command), cwd))
        if cwd is not None:
            selected = (
                SMOKE_CASE_IDS
                if "--rewrite_reports" in command
                else (command[command.index("--instance_ids") + 1],)
            )
            report = report_with_submissions(
                official_report(selected, resolved_ids=selected[:2]), SMOKE_CASE_IDS
            )
            (cwd / "ufo.official-smoke.json").write_text(json.dumps(report, indent=1))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(grading.subprocess, "run", run)

    summary_path = workflow.run()

    grade = workflow.grade_directory.resolve()
    predictions = grade / "predictions.jsonl"
    expected_case_calls = [
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
                case_id,
                "--run_id",
                "official-smoke",
            ),
            grade,
        )
        for case_id in SMOKE_CASE_IDS
    ]
    assert calls == [
        *expected_case_calls,
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
                *SMOKE_CASE_IDS,
                "--run_id",
                "official-smoke",
                "--rewrite_reports",
                "true",
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
        "subset": "smoke",
        "selected_ids": list(SMOKE_CASE_IDS),
        "official_report": str(official),
        "official_resolved": 2,
        "official_gold_failed": [],
        "gold_overlap": {case_id: 0.0 for case_id in SMOKE_CASE_IDS},
        "suspect_retrieval": [],
    }


def test_summary_flags_submissions_that_reproduce_gold_changed_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    workflow = grading_workflow(tmp_path, snapshot_value)
    accept_test_source(monkeypatch)
    retrieved, original, untouched = SMOKE_CASE_IDS
    cases = {case.instance_id: case for case in workflow.selected_cases}
    submissions = tmp_path / "submissions"
    (submissions / retrieved / f"{retrieved}.patch").write_bytes(cases[retrieved].patch.encode())
    (submissions / original / f"{original}.patch").write_bytes(
        (
            f"diff --git a/{original}.py b/{original}.py\n"
            f"--- a/{original}.py\n"
            f"+++ b/{original}.py\n"
            "@@ -1 +1 @@\n"
            "-before\n"
            "+after\n"
        ).encode()
    )

    def run(
        command: Sequence[str], *, cwd: Path | None = None, check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        assert check
        if cwd is not None:
            graded = (
                SMOKE_CASE_IDS
                if "--rewrite_reports" in command
                else (command[command.index("--instance_ids") + 1],)
            )
            report = report_with_submissions(
                official_report(graded, resolved_ids=graded), SMOKE_CASE_IDS
            )
            (cwd / "ufo.official-smoke.json").write_text(json.dumps(report))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(grading.subprocess, "run", run)

    summary_path = workflow.run()

    summary = json.loads(summary_path.read_bytes())
    assert summary["official_resolved"] == 3
    assert summary["gold_overlap"] == {retrieved: 1.0, original: 0.0, untouched: 0.0}
    assert summary["suspect_retrieval"] == [retrieved]
    output = capsys.readouterr().out
    assert f"SWE-bench patch for {retrieved} reproduces 100% of gold changed lines" in output
    assert f"SWE-bench patch for {original}" not in output


def test_gold_mode_uses_official_gold_predictions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    selected = SMOKE_CASE_IDS[1:]
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
        graded = (
            selected
            if "--rewrite_reports" in command
            else (command[command.index("--instance_ids") + 1],)
        )
        report = official_report(graded, resolved_ids=graded)
        report_with_submissions(report, (*selected, "another__official-1"))
        (cwd / "gold.official-smoke.json").write_text(json.dumps(report))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(grading.subprocess, "run", run)

    workflow.run()

    command = commands[-1]
    assert command[command.index("--predictions_path") + 1] == "gold"
    assert "--namespace" not in command
    assert not (workflow.grade_directory / "predictions.jsonl").exists()


def test_a_gold_invalid_instance_is_recorded_without_aborting_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    workflow = grading_workflow(tmp_path, snapshot_value)
    accept_test_source(monkeypatch)
    gold_invalid = SMOKE_CASE_IDS[0]
    gold_calls: list[tuple[str, ...]] = []

    def run(
        command: Sequence[str], *, cwd: Path | None = None, check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        assert check
        if cwd is None:
            return subprocess.CompletedProcess(command, 0)
        gold = command[command.index("--predictions_path") + 1] == "gold"
        graded = (
            SMOKE_CASE_IDS
            if "--rewrite_reports" in command
            else (command[command.index("--instance_ids") + 1],)
        )
        if gold:
            gold_calls.append(graded)
        report = report_with_submissions(
            official_report(
                graded,
                resolved_ids=tuple(case_id for case_id in graded if case_id != gold_invalid),
            ),
            SMOKE_CASE_IDS,
        )
        model = "gold" if gold else "ufo"
        (cwd / f"{model}.official-smoke.json").write_text(json.dumps(report))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(grading.subprocess, "run", run)

    summary_path = workflow.run()

    assert gold_calls == [(gold_invalid,)]
    summary = json.loads(summary_path.read_bytes())
    assert summary["official_gold_failed"] == [gold_invalid]
    assert summary["official_resolved"] == 2
    assert f"official gold patch failed for {gold_invalid}" in capsys.readouterr().out


def test_a_failing_gold_patch_is_recorded_in_a_gold_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    workflow = grading_workflow(tmp_path, snapshot_value, gold=True)
    accept_test_source(monkeypatch)
    gold_invalid = SMOKE_CASE_IDS[0]

    def run(
        command: Sequence[str], *, cwd: Path | None = None, check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        assert check
        if cwd is not None:
            graded = (
                SMOKE_CASE_IDS
                if "--rewrite_reports" in command
                else (command[command.index("--instance_ids") + 1],)
            )
            report = report_with_submissions(
                official_report(
                    graded,
                    resolved_ids=tuple(case_id for case_id in graded if case_id != gold_invalid),
                ),
                SMOKE_CASE_IDS,
            )
            (cwd / "gold.official-smoke.json").write_text(json.dumps(report))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(grading.subprocess, "run", run)

    summary_path = workflow.run()

    summary = json.loads(summary_path.read_bytes())
    assert summary["official_gold_failed"] == [gold_invalid]
    assert summary["official_resolved"] == 2


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


def test_workflow_checks_harness_before_minting_grade_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    workflow = grading_workflow(tmp_path, snapshot_value)
    monkeypatch.setattr(grading, "verify_source", lambda _source, _upstream=None: None)

    def missing(_distribution: str) -> str:
        raise grading.PackageNotFoundError("swebench")

    monkeypatch.setattr(grading, "version", missing)

    with pytest.raises(RuntimeError, match=r"official grading requires swebench==4\.1\.0"):
        workflow.run()
    assert not workflow.grade_directory.exists()


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
        report = official_report(SMOKE_CASE_IDS)
        if failure == "error":
            report["error_instances"] = 1
            report["error_ids"] = [SMOKE_CASE_IDS[0]]
        else:
            report["completed_instances"] = 2
            report["completed_ids"] = list(SMOKE_CASE_IDS[:2])
            report["unresolved_instances"] = 2
            report["unresolved_ids"] = list(SMOKE_CASE_IDS[:2])
        (cwd / "ufo.official-smoke.json").write_text(json.dumps(report))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(grading.subprocess, "run", run)

    with pytest.raises((FileNotFoundError, ValueError), match="official SWE-bench"):
        workflow.run()
    assert not (workflow.grade_directory / "summary.json").exists()


def test_workflow_rejects_a_case_error_before_report_aggregation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    workflow = grading_workflow(tmp_path, snapshot_value)
    accept_test_source(monkeypatch)
    aggregated = False

    def run(
        command: Sequence[str], *, cwd: Path | None = None, check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        nonlocal aggregated
        assert check
        if cwd is None:
            return subprocess.CompletedProcess(command, 0)
        if "--rewrite_reports" in command:
            aggregated = True
            report = official_report(SMOKE_CASE_IDS, resolved_ids=SMOKE_CASE_IDS)
        else:
            case_id = command[command.index("--instance_ids") + 1]
            report = report_with_submissions(official_report((case_id,)), SMOKE_CASE_IDS)
            report["completed_instances"] = 0
            report["completed_ids"] = []
            report["unresolved_instances"] = 0
            report["unresolved_ids"] = []
            report["error_instances"] = 1
            report["error_ids"] = [case_id]
        (cwd / "ufo.official-smoke.json").write_text(json.dumps(report))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(grading.subprocess, "run", run)

    with pytest.raises(ValueError, match="official SWE-bench report contains incomplete or error"):
        workflow.run()
    assert not aggregated
    assert not (workflow.grade_directory / "summary.json").exists()


def test_workflow_accepts_empty_patch_as_an_explicit_denominator_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")
    workflow = grading_workflow(tmp_path, snapshot_value)
    accept_test_source(monkeypatch)
    empty_id = SMOKE_CASE_IDS[2]
    report = official_report(SMOKE_CASE_IDS, resolved_ids=(SMOKE_CASE_IDS[0],))
    report["completed_instances"] = 2
    report["completed_ids"] = list(SMOKE_CASE_IDS[:2])
    report["unresolved_instances"] = 1
    report["unresolved_ids"] = [SMOKE_CASE_IDS[1]]
    report["empty_patch_instances"] = 1
    report["empty_patch_ids"] = [empty_id]
    report_bytes = json.dumps(report, indent=1).encode()

    def run(
        command: Sequence[str], *, cwd: Path | None = None, check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        assert check
        if cwd is not None:
            gold = command[command.index("--predictions_path") + 1] == "gold"
            graded = (
                SMOKE_CASE_IDS
                if "--rewrite_reports" in command
                else (command[command.index("--instance_ids") + 1],)
            )
            resolved_ids = (
                graded
                if gold
                else tuple(case_id for case_id in graded if case_id == SMOKE_CASE_IDS[0])
            )
            graded_report = report_with_submissions(
                official_report(graded, resolved_ids=resolved_ids), SMOKE_CASE_IDS
            )
            if empty_id in graded and not gold:
                graded_report["completed_instances"] -= 1
                graded_report["completed_ids"].remove(empty_id)
                graded_report["unresolved_instances"] -= 1
                graded_report["unresolved_ids"].remove(empty_id)
                graded_report["empty_patch_instances"] = 1
                graded_report["empty_patch_ids"] = [empty_id]
            model = "gold" if gold else "ufo"
            (cwd / f"{model}.official-smoke.json").write_text(json.dumps(graded_report, indent=1))
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


def test_workflow_refuses_cases_from_another_subset(tmp_path: Path) -> None:
    snapshot_value = snapshot(tmp_path / "snapshot")

    with pytest.raises(ValueError, match="cases outside the smoke subset"):
        grading_workflow(tmp_path, snapshot_value, case_ids=SUBSETS.hillclimb[:1], subset="smoke")


def cli_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SWEbenchSnapshot:
    snapshot_value = snapshot(tmp_path / "snapshot")
    parquet = tmp_path / "test.parquet"
    parquet.write_bytes(b"pinned parquet")
    monkeypatch.setattr(grading, "load_snapshot", lambda _root: snapshot_value)
    monkeypatch.setattr(grading, "DEFAULT_PARQUET", parquet)
    monkeypatch.setattr(grading, "DEFAULT_GRADES_ROOT", tmp_path / "grades")
    accept_test_source(monkeypatch)
    return snapshot_value


def test_cli_grades_one_subset_into_its_own_directory_and_prunes_after_the_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_environment(tmp_path, monkeypatch)
    submissions = tmp_path / "submissions"
    for case_id in SUBSETS.hillclimb:
        write_patch(submissions, case_id, f"diff --git a/{case_id}.py b/{case_id}.py\n".encode())
    grade = tmp_path / "grades" / "hillclimb" / "official-hillclimb"
    removed: list[tuple[str, bool]] = []

    def run(
        command: Sequence[str], *, cwd: Path | None = None, check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        assert check
        if cwd is not None:
            gold = command[command.index("--predictions_path") + 1] == "gold"
            graded = (
                SUBSETS.hillclimb
                if "--rewrite_reports" in command
                else (command[command.index("--instance_ids") + 1],)
            )
            model = "gold" if gold else "ufo"
            (cwd / f"{model}.official-hillclimb.json").write_text(
                json.dumps(
                    report_with_submissions(
                        official_report(
                            graded,
                            resolved_ids=(
                                graded
                                if gold
                                else tuple(
                                    case_id
                                    for case_id in graded
                                    if case_id in SUBSETS.hillclimb[:3]
                                )
                            ),
                        ),
                        SUBSETS.hillclimb,
                    )
                )
            )
        if command[:2] == ("docker", "rmi"):
            removed.append((command[-1], (grade / "summary.json").is_file()))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(grading.subprocess, "run", run)

    grading.main(
        (
            "--submissions",
            str(submissions),
            "--subset",
            "hillclimb",
            "--run-id",
            "official-hillclimb",
            "--prune-images",
        )
    )

    summary = json.loads((grade / "summary.json").read_bytes())
    assert summary["subset"] == "hillclimb"
    assert summary["selected_ids"] == list(SUBSETS.hillclimb)
    assert summary["official_resolved"] == 3
    assert removed == [(official_instance_image(case_id), True) for case_id in SUBSETS.hillclimb]


def test_cli_all_grades_the_complete_pinned_roster_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_environment(tmp_path, monkeypatch)
    submissions = tmp_path / "submissions"
    for case_id in SUBSETS.all_ids:
        write_patch(submissions, case_id, f"diff --git a/{case_id}.py b/{case_id}.py\n".encode())

    def run(
        command: Sequence[str], *, cwd: Path | None = None, check: bool
    ) -> subprocess.CompletedProcess[bytes]:
        assert check
        if cwd is not None:
            gold = command[command.index("--predictions_path") + 1] == "gold"
            graded = (
                SUBSETS.all_ids
                if "--rewrite_reports" in command
                else (command[command.index("--instance_ids") + 1],)
            )
            model = "gold" if gold else "ufo"
            (cwd / f"{model}.official-all.json").write_text(
                json.dumps(
                    report_with_submissions(
                        official_report(
                            graded,
                            resolved_ids=(
                                graded
                                if gold
                                else tuple(
                                    case_id for case_id in graded if case_id in SUBSETS.all_ids[:5]
                                )
                            ),
                        ),
                        SUBSETS.all_ids,
                    )
                )
            )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(grading.subprocess, "run", run)

    grading.main(
        (
            "--submissions",
            str(submissions),
            "--subset",
            "all",
            "--run-id",
            "official-all",
        )
    )

    summary = json.loads((tmp_path / "grades/all/official-all/summary.json").read_bytes())
    assert summary["subset"] == "all"
    assert summary["selected_ids"] == list(SUBSETS.all_ids)
    assert summary["official_resolved"] == 5


def test_cli_can_grade_the_frozen_smoke_case_as_hard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli_environment(tmp_path, monkeypatch)
    overlapping = next(case_id for case_id in SUBSETS.smoke if case_id in SUBSETS.hard)
    workflows: list[SWEbenchGrading] = []

    def run(workflow: SWEbenchGrading) -> Path:
        workflows.append(workflow)
        return tmp_path / "summary.json"

    monkeypatch.setattr(SWEbenchGrading, "run", run)

    grading.main(
        (
            "--submissions",
            str(tmp_path / "submissions"),
            "--subset",
            "hard",
            "--case",
            overlapping,
        )
    )

    assert workflows[0].subset == "hard"
    assert tuple(case.instance_id for case in workflows[0].selected_cases) == (overlapping,)


@pytest.mark.parametrize(
    ("arguments", "message"),
    (
        ((), "SWE-bench grading requires --subset or --case"),
        (
            ("--case", SUBSETS.smoke[0], "--case", SUBSETS.holdout[0]),
            "SWE-bench cases span subsets: holdout, smoke",
        ),
        (
            ("--case", next(case for case in SUBSETS.smoke if case in SUBSETS.hard)),
            "SWE-bench cases span subsets: hard, smoke",
        ),
        (
            ("--subset", "smoke", "--case", SUBSETS.hillclimb[0]),
            "SWE-bench cases are outside the smoke subset",
        ),
    ),
)
def test_cli_refuses_a_selection_that_is_not_one_subset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    arguments: tuple[str, ...],
    message: str,
) -> None:
    cli_environment(tmp_path, monkeypatch)

    with pytest.raises(SystemExit):
        grading.main(("--submissions", str(tmp_path / "submissions"), *arguments))

    assert message in capsys.readouterr().err
