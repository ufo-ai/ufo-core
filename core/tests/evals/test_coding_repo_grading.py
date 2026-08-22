"""Offline grading of captured coding deliverables: the score a judge's verdicts produce, what a
case with nothing captured is worth, and what the run does when the judge itself does not answer."""

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from coding_repo_history import pinned_history  # noqa: F401
from pydantic import ValidationError

from evals.coding_repo.cases import CASES, PATCH_CASES
from evals.coding_repo.grading import (
    MAX_DELIVERABLE_CHARS,
    MAX_JUDGE_TIMEOUT_SECONDS,
    MAX_REFERENCE_CHARS,
    METRIC_NAME,
    CaseGrade,
    GradeReport,
    _reference_diff,
    _report,
    grade_case,
    main,
)
from evals.coding_repo.runner import REFUSED_DIR, REPO_ROOT
from evals.harness.judge import SubprocessJudge

SURVEY_CASE = next(case for case in CASES if case.deliverable == "document")
ROOT_COMMIT = subprocess.run(
    ("git", "-C", str(REPO_ROOT), "rev-list", "--max-parents=0", "HEAD"),
    capture_output=True,
    check=True,
    text=True,
).stdout.split()[0]
FAILING_JUDGE = (sys.executable, "-c", "raise SystemExit(1)")
RECORDED_REQUEST = "request.json"
STUB_JUDGE = """
import json, pathlib, sys
request = sys.stdin.read()
pathlib.Path({RECORD}).write_text(request)
payload = json.loads(json.loads(request)["prompt"].split("\\n")[1])
count = len(payload["criteria"])
passing = count if {PASSING} is None else {PASSING}
print(json.dumps({"items": [
    {"passed": index < passing, "reason": "saw it"} for index in range(count)
]}))
"""


def stub_judge(tmp_path: Path, passing: int | None = None, prints: str = "") -> SubprocessJudge:
    """A judge that records the request it was handed, so a test can read the prompt the grader
    actually built. `prints` replaces the verdict with a literal answer."""
    script = tmp_path / "judge.py"
    body = (
        f"print(r'''{prints}''')\n"
        if prints
        else STUB_JUDGE.replace("{RECORD}", repr(str(tmp_path / RECORDED_REQUEST))).replace(
            "{PASSING}", repr(passing)
        )
    )
    script.write_text(body)
    return SubprocessJudge(name="stub", argv=(sys.executable, str(script)), timeout_seconds=60.0)


def judge_spec(tmp_path: Path, judge: SubprocessJudge) -> Path:
    spec = tmp_path / "judge.json"
    spec.write_text(
        json.dumps({"name": judge.name, "argv": list(judge.argv), "timeout_seconds": 60.0})
    )
    return spec


def recorded_prompt(tmp_path: Path) -> dict[str, object]:
    request = json.loads((tmp_path / RECORDED_REQUEST).read_text())
    payload: dict[str, object] = json.loads(request["prompt"].split("\n")[1])
    return payload


def capture(submissions: Path, case_name: str, name: str, body: bytes) -> Path:
    case_dir = submissions / case_name
    case_dir.mkdir(parents=True, exist_ok=True)
    target = case_dir / name
    target.write_bytes(body)
    return target


async def test_a_deliverable_scores_the_fraction_of_criteria_it_satisfies(tmp_path: Path) -> None:
    case = PATCH_CASES[0]
    submissions = tmp_path / "submissions"
    capture(submissions, case.name, f"{case.name}.patch", b"diff --git a/x b/x\n")
    grade = await grade_case(case, stub_judge(tmp_path, passing=3), submissions)
    assert grade.criterion_count == len(case.criteria)
    assert grade.passed_count == 3
    assert grade.score == pytest.approx(3 / len(case.criteria))
    assert grade.reference_sha == case.reference_sha
    assert grade.judge == "stub"
    assert not grade.error and not grade.judge_error
    assert [item.criterion for item in grade.criteria] == list(case.criteria)


async def test_the_patch_and_its_note_reach_the_judge_as_one_submission(tmp_path: Path) -> None:
    """The criteria span both files, so the note is not a second submission — and the reference diff
    of the merged commit is what the judge is told the answer looked like."""
    case = PATCH_CASES[0]
    submissions = tmp_path / "submissions"
    capture(submissions, case.name, f"{case.name}.patch", b"diff --git a/x b/x\n+one\n")
    capture(submissions, case.name, case.notes_name, b"Verified by rerunning the slack tests.\n")
    grade = await grade_case(case, stub_judge(tmp_path), submissions)
    assert grade.submission == f"{case.name}-notes.md, {case.name}.patch"
    payload = recorded_prompt(tmp_path)
    deliverable = str(payload["candidateDeliverable"])
    assert f"=== {case.name}.patch ===" in deliverable
    assert f"=== {case.notes_name} ===" in deliverable
    assert "Verified by rerunning the slack tests." in deliverable
    assert "diff --git a/x b/x" in deliverable
    assert payload["brief"] == case.brief
    assert payload["criteria"] == list(case.criteria)
    reference = str(payload["reference"])
    assert reference.startswith("diff --git ")
    assert case.expected_paths[0] in reference


async def test_an_oversize_deliverable_and_reference_are_bounded(tmp_path: Path) -> None:
    """Both sides are bounded next to the call, because a judge request past its byte limit raises
    and would cost the case its score."""
    case = PATCH_CASES[0]
    submissions = tmp_path / "submissions"
    body = b"diff --git a/x b/x\n" + b"+line\n" * (MAX_DELIVERABLE_CHARS // 2)
    capture(submissions, case.name, f"{case.name}.patch", body)
    grade = await grade_case(case, stub_judge(tmp_path), submissions)
    assert not grade.judge_error, grade.judge_error
    payload = recorded_prompt(tmp_path)
    assert len(str(payload["candidateDeliverable"])) == MAX_DELIVERABLE_CHARS
    assert len(str(payload["reference"])) <= MAX_REFERENCE_CHARS


async def test_a_case_that_captured_nothing_scores_zero(tmp_path: Path) -> None:
    grade = await grade_case(PATCH_CASES[1], stub_judge(tmp_path), tmp_path / "submissions")
    assert grade.score == 0.0
    assert grade.passed_count == 0
    assert grade.error == "no deliverable was captured for this case"
    assert not grade.judge_error


async def test_a_judge_answering_the_wrong_shape_is_not_a_score(tmp_path: Path) -> None:
    case = PATCH_CASES[0]
    submissions = tmp_path / "submissions"
    capture(submissions, case.name, f"{case.name}.patch", b"diff --git a/x b/x\n")
    judge = stub_judge(tmp_path, prints='{"items": [{"passed": true, "reason": "one"}]}')
    grade = await grade_case(case, judge, submissions)
    assert grade.score == 0.0
    assert "verdicts for" in grade.judge_error
    assert not grade.error
    assert grade.submission == f"{case.name}.patch"


async def test_a_document_case_is_judged_on_the_captured_document(tmp_path: Path) -> None:
    submissions = tmp_path / "submissions"
    name = Path(SURVEY_CASE.document_path).name
    capture(submissions, SURVEY_CASE.name, name, b"- ufo/<deploy>/api-keys\n")
    grade = await grade_case(SURVEY_CASE, stub_judge(tmp_path), submissions)
    assert grade.submission == name
    assert grade.score == 1.0
    assert grade.reference_sha == ""
    assert recorded_prompt(tmp_path)["reference"] == ""


async def test_a_reference_commit_whose_parent_is_absent_is_refused(tmp_path: Path) -> None:
    """`git show` on a parentless commit diffs it against the empty tree and yields the whole
    repository, which would be judged as the reference answer at a normal-looking size. Naming the
    parent makes that a loud failure instead."""
    with pytest.raises(RuntimeError, match="failed"):
        await _reference_diff(ROOT_COMMIT)


async def test_an_unreadable_reference_costs_no_case_a_score(tmp_path: Path) -> None:
    case = PATCH_CASES[0]
    submissions = tmp_path / "submissions"
    capture(submissions, case.name, f"{case.name}.patch", b"diff --git a/x b/x\n")
    unknown = "0" * 40
    grade = await grade_case(
        replace(case, reference_sha=unknown), stub_judge(tmp_path), submissions
    )
    assert grade.score == 0.0
    assert unknown in grade.judge_error
    assert not grade.error


def test_a_grade_carries_its_own_bounds(tmp_path: Path) -> None:
    """Every persisted grade is constructed rather than copied over, so the bounds run."""
    case = PATCH_CASES[0]
    fields = {
        "case": case.name,
        "kind": case.kind,
        "deliverable": case.deliverable,
        "base_sha": case.base_sha,
        "reference_sha": case.reference_sha,
        "judge": "stub",
        "prompt_revision": "r",
        "submission": "x.patch",
        "criterion_count": len(case.criteria),
    }
    assert CaseGrade(**fields, score=1.0, passed_count=1).score == 1.0
    with pytest.raises(ValidationError):
        CaseGrade(**fields, score=9.0, passed_count=1)
    with pytest.raises(ValidationError):
        CaseGrade(**fields, score=1.0, passed_count=-5)


def test_grading_writes_a_report_and_the_metric_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = PATCH_CASES[0]
    submissions = tmp_path / "submissions"
    capture(submissions, case.name, f"{case.name}.patch", b"diff --git a/x b/x\n")
    out = tmp_path / "grades"
    main(
        [
            "--case",
            case.name,
            "--submissions",
            str(submissions),
            "--out",
            str(out),
            "--metric-stdout",
            "--judge",
            str(judge_spec(tmp_path, stub_judge(tmp_path))),
        ]
    )
    printed = capsys.readouterr().out
    assert f"{METRIC_NAME}: 1.0000" in printed
    report = json.loads((out / "report.json").read_text())
    assert report["mean_score"] == 1.0
    assert report["by_kind"] == {case.kind: 1.0}
    saved = json.loads((out / f"{case.name}.json").read_text())
    assert saved["passed_count"] == len(case.criteria)
    assert saved["prompt_revision"]
    assert saved["judge"] == "stub"


def test_a_flag_after_the_judge_reaches_this_parser(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The judge is a spec file, not a trailing command line, so `--submissions` written after it is
    this run's flag rather than an argument the judge swallows."""
    case = PATCH_CASES[0]
    submissions = tmp_path / "submissions"
    capture(submissions, case.name, f"{case.name}.patch", b"diff --git a/x b/x\n")
    out = tmp_path / "grades"
    main(
        [
            "--case",
            case.name,
            "--out",
            str(out),
            "--judge",
            str(judge_spec(tmp_path, stub_judge(tmp_path))),
            "--metric-stdout",
            "--submissions",
            str(submissions),
        ]
    )
    printed = capsys.readouterr().out
    assert f"{METRIC_NAME}: 1.0000" in printed


def test_a_judge_that_never_answers_reports_no_metric(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A judge that exits non-zero is our failure, not the candidate's: folding it into a zero
    would report a total quality regression to whoever reads the metric."""
    case = PATCH_CASES[0]
    submissions = tmp_path / "submissions"
    capture(submissions, case.name, f"{case.name}.patch", b"diff --git a/x b/x\n")
    spec = tmp_path / "broken.json"
    spec.write_text(json.dumps({"name": "broken", "argv": list(FAILING_JUDGE)}))
    out = tmp_path / "grades"
    with pytest.raises(SystemExit) as exit_info:
        main(
            [
                "--case",
                case.name,
                "--submissions",
                str(submissions),
                "--out",
                str(out),
                "--metric-stdout",
                "--judge",
                str(spec),
            ]
        )
    assert "returned no verdict" in str(exit_info.value)
    assert METRIC_NAME not in capsys.readouterr().out
    assert json.loads((out / f"{case.name}.json").read_text())["judge_error"]
    report = json.loads((out / "report.json").read_text())
    assert report["unscored"] == [case.name]
    assert report["mean_score"] is None
    assert report["by_kind"] == {}


def test_a_report_states_a_mean_only_when_every_case_was_scored() -> None:
    """A judge failure is this run's failure, so folding it in as a zero would durably record a
    quality regression the candidate never caused."""
    case = PATCH_CASES[0]
    fields = {
        "case": case.name,
        "kind": case.kind,
        "deliverable": case.deliverable,
        "base_sha": case.base_sha,
        "reference_sha": case.reference_sha,
        "judge": "stub",
        "prompt_revision": "r",
        "submission": "x.patch",
        "criterion_count": len(case.criteria),
    }
    scored = CaseGrade(**fields, score=0.5, passed_count=1)
    unanswered = CaseGrade(**fields, score=0.0, passed_count=0, judge_error="no verdict")
    assert _report((scored,)).mean_score == 0.5
    unscored = _report((scored, unanswered))
    assert unscored.unscored == (case.name,)
    assert unscored.mean_score is None and unscored.by_kind == {}
    with pytest.raises(ValidationError, match="went unscored"):
        GradeReport(cases=(unanswered,), unscored=(case.name,), mean_score=0.0)
    with pytest.raises(ValidationError, match="states its mean"):
        GradeReport(cases=(scored,))


def test_a_failed_essential_criterion_is_named_on_the_printed_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A respectable fraction must not hide a missed root cause, so the run says so on the case's
    own line rather than only in the persisted grade."""
    case = PATCH_CASES[0]
    submissions = tmp_path / "submissions"
    capture(submissions, case.name, f"{case.name}.patch", b"diff --git a/x b/x\n")
    arguments = [
        "--case",
        case.name,
        "--submissions",
        str(submissions),
        "--out",
        str(tmp_path / "grades"),
    ]
    main([*arguments, "--judge", str(judge_spec(tmp_path, stub_judge(tmp_path, passing=0)))])
    assert "ESSENTIAL CRITERION FAILED" in capsys.readouterr().out
    main([*arguments, "--judge", str(judge_spec(tmp_path, stub_judge(tmp_path)))])
    assert "ESSENTIAL CRITERION FAILED" not in capsys.readouterr().out


def test_the_judge_spec_timeout_reaches_the_judge_and_is_bounded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The spec's `timeout_seconds` is what bounds a judge that hangs, and a spec asking for more
    than the ceiling is refused at startup rather than after the run has spent its wall clock."""
    case = PATCH_CASES[0]
    submissions = tmp_path / "submissions"
    capture(submissions, case.name, f"{case.name}.patch", b"diff --git a/x b/x\n")
    hangs = tmp_path / "hangs.py"
    hangs.write_text("import sys, time\nsys.stdin.read()\ntime.sleep(30)\n")
    spec = tmp_path / "slow.json"
    spec.write_text(
        json.dumps(
            {
                "name": "slow",
                "argv": [sys.executable, str(hangs)],
                "timeout_seconds": 0.25,
            }
        )
    )
    out = tmp_path / "grades"
    with pytest.raises(SystemExit, match="returned no verdict"):
        main(
            [
                "--case",
                case.name,
                "--submissions",
                str(submissions),
                "--out",
                str(out),
                "--judge",
                str(spec),
            ]
        )
    assert "timed out" in json.loads((out / f"{case.name}.json").read_text())["judge_error"]
    unbounded = tmp_path / "unbounded.json"
    unbounded.write_text(
        json.dumps(
            {
                "name": "slow",
                "argv": [sys.executable, str(hangs)],
                "timeout_seconds": MAX_JUDGE_TIMEOUT_SECONDS + 1,
            }
        )
    )
    with pytest.raises(SystemExit):
        main(["--case", case.name, "--judge", str(unbounded)])
    assert "is not a judge spec" in capsys.readouterr().err


async def test_a_case_whose_patch_the_gate_refused_is_not_judged(tmp_path: Path) -> None:
    """The run sets a refused patch aside so it stays readable; the offline judge must not reach it,
    or a diff that never applied is scored on its prose."""
    case = PATCH_CASES[0]
    submissions = tmp_path / "submissions"
    refused = f"{case.name}/{REFUSED_DIR}"
    capture(submissions, refused, f"{case.name}.patch", b"prose, not a diff\n")
    capture(submissions, refused, case.notes_name, b"What was verified.\n")
    grade = await grade_case(case, stub_judge(tmp_path), submissions)
    assert grade.score == 0.0
    assert grade.error == "no deliverable was captured for this case"
    assert not grade.judge_error


def test_grading_refuses_an_unknown_case(tmp_path: Path) -> None:
    spec = judge_spec(tmp_path, stub_judge(tmp_path))
    with pytest.raises(SystemExit):
        main(["--case", "nope", "--judge", str(spec)])


def test_grading_refuses_a_judge_that_is_not_a_spec(tmp_path: Path) -> None:
    spec = tmp_path / "judge.json"
    spec.write_text(json.dumps({"name": "stub"}))
    with pytest.raises(SystemExit):
        main(["--case", PATCH_CASES[0].name, "--judge", str(spec)])


async def test_a_failed_essential_criterion_is_reported_beside_the_score(tmp_path: Path) -> None:
    """A respectable fraction must not hide a missed root cause, so the case names the criterion
    without which the answer is wrong and grading reports whether it held."""
    case = PATCH_CASES[0]
    assert case.essential == 0
    submissions = tmp_path / "submissions"
    capture(submissions, case.name, f"{case.name}.patch", b"diff --git a/x b/x\n")
    missed = await grade_case(case, stub_judge(tmp_path, passing=0), submissions)
    assert missed.essential_met is False
    assert missed.score == 0.0
    held = await grade_case(case, stub_judge(tmp_path, passing=len(case.criteria)), submissions)
    assert held.essential_met is True
