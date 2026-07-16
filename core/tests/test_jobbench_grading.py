"""JobBench offline grading: submission text views, the subprocess judge protocol, upstream
all-criteria rubric semantics, and the weighted scorecard."""

import sys
from pathlib import Path

import openpyxl
import pytest

from evals.jobbench.grading import (
    MAX_FILE_TEXT_CHARS,
    SubprocessJudge,
    grade_case,
    submission_views,
)
from evals.jobbench.models import RubricItem, SnapshotAsset, SnapshotCase

PASS_ALL_JUDGE = """
import json, sys
request = json.loads(sys.stdin.read())
payload = json.loads(request["prompt"].splitlines()[1])
criteria = [
    {"index": index, "passed": True, "reason": "anchored", "evidence": "seen"}
    for index in range(len(payload["criteria"]))
]
print(json.dumps({"criteria": criteria}))
"""

FAIL_SECOND_JUDGE = PASS_ALL_JUDGE.replace('"passed": True', '"passed": index != 1')
INVALID_JUDGE = 'print("not json")'


def _judge(script: str) -> SubprocessJudge:
    return SubprocessJudge(
        name="stub-judge", argv=(sys.executable, "-c", script), timeout_seconds=30.0
    )


def _case(rubric: tuple[RubricItem, ...]) -> SnapshotCase:
    return SnapshotCase(
        case_id="easy.occupation_00__task1",
        split="easy",
        task_id="occupation_00__task1",
        occupation="occupation_00",
        task_num=1,
        prompt="Reconcile the records.",
        references=(
            SnapshotAsset(relative_path="dataset_easy/occupation_00/task1/task_folder/data.csv"),
        ),
        rubric=rubric,
    )


RUBRIC = (
    RubricItem(rubric="Ties out the balance?", weight=10, criteria=("states it", "shows it")),
    RubricItem(rubric="Flags the open item?", weight=6, criteria=("keeps it open",)),
)


def _views(tmp_path: Path) -> tuple:
    memo = tmp_path / "sub" / "memo.md"
    memo.parent.mkdir()
    memo.write_text("Adjusted balance ties out; AP-1 stays open.")
    return submission_views(memo.parent)


async def test_grade_case_scores_weighted_rubrics(tmp_path: Path) -> None:
    grade = await grade_case(
        _case(RUBRIC), "sha256:" + "0" * 64, _views(tmp_path), _judge(PASS_ALL_JUDGE)
    )
    assert grade.total_score == 16
    assert grade.max_score == 16
    assert grade.score_fraction == 1.0
    assert grade.passed_count == 2
    assert all(rubric.passed for rubric in grade.rubrics)
    assert grade.rubrics[0].criteria[0].evidence == "seen"


async def test_one_failed_criterion_zeroes_the_rubric(tmp_path: Path) -> None:
    grade = await grade_case(
        _case(RUBRIC), "sha256:" + "0" * 64, _views(tmp_path), _judge(FAIL_SECOND_JUDGE)
    )
    assert not grade.rubrics[0].passed
    assert grade.rubrics[0].score == 0
    assert grade.rubrics[1].passed
    assert grade.total_score == 6
    assert grade.score_fraction == pytest.approx(6 / 16)


async def test_invalid_judge_output_records_the_error(tmp_path: Path) -> None:
    grade = await grade_case(
        _case(RUBRIC), "sha256:" + "0" * 64, _views(tmp_path), _judge(INVALID_JUDGE)
    )
    assert grade.total_score == 0
    assert all("invalid structured verdict" in rubric.error for rubric in grade.rubrics)


async def test_judge_crash_records_the_error(tmp_path: Path) -> None:
    grade = await grade_case(
        _case(RUBRIC), "sha256:" + "0" * 64, _views(tmp_path), _judge("raise SystemExit(3)")
    )
    assert grade.total_score == 0
    assert all("exited 3" in rubric.error for rubric in grade.rubrics)


async def test_judge_prompt_carries_fenced_submissions(tmp_path: Path) -> None:
    echo_judge = """
import json, sys
request = json.loads(sys.stdin.read())
lines = request["prompt"].splitlines()
assert lines[0] == lines[-1] and lines[0].startswith("UFO_EVAL_INPUT")
payload = json.loads(lines[1])
assert payload["submissions"][0]["name"] == "memo.md"
assert "AP-1" in payload["submissions"][0]["content"]
criteria = [
    {"index": index, "passed": True, "reason": "ok"}
    for index in range(len(payload["criteria"]))
]
print(json.dumps({"criteria": criteria}))
"""
    grade = await grade_case(
        _case(RUBRIC), "sha256:" + "0" * 64, _views(tmp_path), _judge(echo_judge)
    )
    assert grade.total_score == 16


def test_submission_views_convert_text_and_xlsx(tmp_path: Path) -> None:
    folder = tmp_path / "sub"
    folder.mkdir()
    (folder / "notes.txt").write_text("plain text")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Recon"
    sheet.append(("item", "amount"))
    sheet.append(("AP-1", 42))
    workbook.save(folder / "book.xlsx")
    views = {view.name: view.content for view in submission_views(folder)}
    assert views["notes.txt"] == "plain text"
    assert "## Sheet: Recon" in views["book.xlsx"]
    assert "AP-1,42" in views["book.xlsx"]


def test_submission_views_decode_extensionless_text(tmp_path: Path) -> None:
    folder = tmp_path / "sub"
    folder.mkdir()
    (folder / "controller-memo").write_bytes(b"# Memo\npost the entries")
    views = {view.name: view.content for view in submission_views(folder)}
    assert views["controller-memo"] == "# Memo\npost the entries"


def test_submission_views_mark_unreadable_files(tmp_path: Path) -> None:
    folder = tmp_path / "sub"
    folder.mkdir()
    (folder / "broken.pdf").write_bytes(b"not a pdf")
    (folder / "blob.bin").write_bytes(b"\x89PNG\x00\xff\xfe")
    views = {view.name: view.content for view in submission_views(folder)}
    assert views["broken.pdf"].startswith("[file 'broken.pdf' could not be converted")
    assert views["blob.bin"].startswith("[file 'blob.bin' could not be converted")


def test_submission_views_truncate_at_the_upstream_cap(tmp_path: Path) -> None:
    folder = tmp_path / "sub"
    folder.mkdir()
    (folder / "big.txt").write_text("x" * (MAX_FILE_TEXT_CHARS + 100))
    (view,) = submission_views(folder)
    assert f"[truncated at {MAX_FILE_TEXT_CHARS} characters]" in view.content
    assert len(view.content) < MAX_FILE_TEXT_CHARS + 100


def test_empty_submission_folder_fails(tmp_path: Path) -> None:
    folder = tmp_path / "sub"
    folder.mkdir()
    with pytest.raises(ValueError, match="submission folder is empty"):
        submission_views(folder)
