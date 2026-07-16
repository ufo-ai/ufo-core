import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.dsqa_100 import build as dsqa_build
from evals.dsqa_100 import runner as dsqa_runner
from evals.dsqa_100.models import AnswerType, Selection, SnapshotCase
from evals.dsqa_100.runner import (
    DSQA_100_LEAVES,
    DSQA_JUDGE_REASONING,
    MAX_CANDIDATE_ANSWER_CHARS,
    AnswerCorrectness,
    DSQAGrader,
    load_dsqa_100,
)
from evals.dsqa_100.snapshot import content_digest, write_snapshot
from evals.harness.capability import CapabilityCase, CapabilityOutput
from evals.harness.target import TargetResult
from ufo.sdk.models import Message

DIGEST = "sha256:" + "b" * 64


@dataclass(frozen=True)
class FixedJudge:
    response: str

    async def complete(self, system: str, messages: tuple[Message, ...]) -> str:
        assert system == ""
        assert len(messages) == 1
        return self.response


@dataclass(frozen=True)
class RaisingJudge:
    async def complete(self, system: str, messages: tuple[Message, ...]) -> str:
        raise RuntimeError("provider unavailable")


@dataclass(frozen=True)
class FixedTarget:
    judge: FixedJudge

    async def run(self, case: CapabilityCase) -> TargetResult:
        return TargetResult(
            CapabilityOutput("candidate answer", (), tokens=120, cost_micro_usd=7),
            clean=True,
        )


def _snapshot(tmp_path: Path) -> Path:
    selection = Selection.model_validate_json(dsqa_build.SELECTION_FILE.read_bytes())
    cases = tuple(
        _case(item, "Set Answer" if index < 66 else "Single Answer")
        for index, item in enumerate(selection.items)
    )
    write_snapshot(tmp_path, upstream=dsqa_build.UPSTREAM, builder_digest=DIGEST, cases=cases)
    return tmp_path


def _case(item, answer_type: AnswerType) -> SnapshotCase:
    problem = f"private problem {item.example_id}"
    return SnapshotCase(
        example_id=item.example_id,
        problem=problem,
        problem_sha256=content_digest(problem),
        category=item.category,
        answer=f"private gold {item.example_id}",
        answer_type=answer_type,
        pressure_band=item.pressure_band,
        tool_pressure_score=item.tool_pressure_score,
        prompt_word_count=item.prompt_word_count,
        signals=item.signals,
    )


def _rating(details: dict[str, bool], excessive: list[str]) -> str:
    return json.dumps(
        {
            "Answer Correctness": {
                "Explanation": "rated",
                "Correctness Details": details,
                "Excessive Answers": excessive,
            }
        }
    )


def _grader() -> DSQAGrader:
    item = Selection.model_validate_json(dsqa_build.SELECTION_FILE.read_bytes()).items[0]
    return DSQAGrader(_case(item, "Single Answer"), FixedJudge(""))


@pytest.mark.parametrize(
    ("details", "excessive", "precision", "recall", "f1", "outcome"),
    (
        ({"a": True, "b": True}, [], 1.0, 1.0, 1.0, "fully_correct"),
        ({"a": True, "b": False}, [], 1.0, 0.5, 2 / 3, "partially_correct"),
        ({"a": False, "b": False}, [], 0.0, 0.0, 0.0, "fully_incorrect"),
        (
            {"a": True, "b": True},
            ["c"],
            2 / 3,
            1.0,
            0.8,
            "correct_with_extraneous",
        ),
    ),
)
def test_dsqa_score_matches_the_official_starter_math(
    details: dict[str, bool],
    excessive: list[str],
    precision: float,
    recall: float,
    f1: float,
    outcome: str,
) -> None:
    score = _grader()._score(
        AnswerCorrectness(
            explanation="rated",
            correctness_details=details,
            excessive_answers=tuple(excessive),
        )
    )

    assert score.precision == pytest.approx(precision)
    assert score.recall == pytest.approx(recall)
    assert score.f1 == pytest.approx(f1)
    assert score.outcome == outcome


def test_dsqa_judge_parser_accepts_fenced_json_and_rejects_non_boolean_details() -> None:
    parsed = _grader()._parse_judge_response(
        f"prefix\n```json\n{_rating({'a': True}, [])}\n```\nsuffix"
    )
    assert parsed.answer_correctness.correctness_details == {"a": True}

    with pytest.raises(ValidationError):
        _grader()._parse_judge_response(_rating({"a": 1}, []))


def test_dsqa_judge_parser_matches_missing_excessive_answers_and_strips_inputs() -> None:
    parsed = _grader()._parse_judge_response(
        json.dumps(
            {
                "Answer Correctness": {
                    "Explanation": "rated",
                    "Correctness Details": {"answer": True},
                }
            }
        )
    )
    case = _case(
        Selection.model_validate_json(dsqa_build.SELECTION_FILE.read_bytes()).items[0],
        "Single Answer",
    ).model_copy(update={"problem": "  prompt  ", "answer": "  answer  "})

    assert parsed.answer_correctness.excessive_answers == ()
    prompt = DSQAGrader(case, FixedJudge(""))._judge_prompt("  response  ")
    assert "<prompt>\nprompt\n</prompt>" in prompt
    assert "<answer>\nanswer\n</answer>" in prompt
    assert "<response>\nresponse\n</response>" in prompt


async def test_dsqa_invalid_judge_output_is_a_visible_failure(tmp_path: Path) -> None:
    case = _case(
        Selection.model_validate_json(dsqa_build.SELECTION_FILE.read_bytes()).items[0],
        "Single Answer",
    )
    verdict = await DSQAGrader(case, FixedJudge("not json"))(CapabilityOutput("candidate", ()))

    assert not verdict.passed
    assert verdict.evidence["judgeStatus"] == "invalid"
    assert verdict.evidence["pressureBand"] == case.pressure_band

    oversized = await DSQAGrader(case, FixedJudge(_rating({"a": True}, [])))(
        CapabilityOutput("x" * (MAX_CANDIDATE_ANSWER_CHARS + 1), ())
    )
    assert oversized.evidence["judgeStatus"] == "candidate_too_long"


async def test_dsqa_judge_failure_does_not_abort_the_leaf(tmp_path: Path) -> None:
    run = load_dsqa_100(_snapshot(tmp_path))
    report = await run.tasks[0].run(FixedTarget(RaisingJudge()))

    assert len(report.cases) == 100
    assert report.metrics[0].name == "rated"
    assert report.metrics[0].value == 0.0
    assert all(
        case.evidence["attempts"][0]["grader"]["judgeStatus"] == "error" for case in report.cases
    )


async def test_dsqa_six_leaves_share_cases_and_emit_macro_metrics(tmp_path: Path) -> None:
    run = load_dsqa_100(_snapshot(tmp_path))
    response = _rating({"private gold answer": True}, [])

    assert tuple(task.name for task in run.tasks) == tuple(leaf.name for leaf in DSQA_100_LEAVES)
    assert len({task.cases for task in run.tasks}) == 1
    assert len(run.tasks[0].cases) == 100
    assert len({task.digest for task in run.tasks}) == 6
    assert {task.judge_reasoning for task in run.tasks} == {DSQA_JUDGE_REASONING}
    report = await run.tasks[0].run(FixedTarget(FixedJudge(response)))
    metrics = {metric.name: metric.value for metric in report.metrics}
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0
    assert metrics["f1"] == 1.0
    assert metrics["fully_correct"] == 1.0
    assert metrics["rated"] == 1.0
    assert all(case.passed for case in report.cases)
    assert all(
        case.evidence["attempts"][0]["tokens"] == 120
        and case.evidence["attempts"][0]["costMicroUsd"] == 7
        for case in report.cases
    )
    case_17 = next(case for case in report.cases if case.name == "dsqa/17")
    grader = case_17.evidence["attempts"][0]["grader"]
    assert grader["goldAnswer"] == "private gold 17"
    assert grader["correctnessDetails"] == {"private gold answer": True}
    assert grader["excessiveAnswers"] == []
    assert grader["judgeExplanation"] == "rated"
    assert "DeepSearchQA judge" in case_17.evidence["grading"]


def test_dsqa_task_digest_tracks_judge_reasoning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = load_dsqa_100(_snapshot(tmp_path)).tasks[0]
    monkeypatch.setattr(dsqa_runner, "DSQA_JUDGE_REASONING", "low")

    changed = load_dsqa_100(_snapshot(tmp_path)).tasks[0]

    assert changed.judge_reasoning == "low"
    assert changed.digest != original.digest


def test_dsqa_pack_selects_only_its_two_workflow_leaves(tmp_path: Path) -> None:
    run = load_dsqa_100(_snapshot(tmp_path))
    tasks = run.tasks_for_pack("dsqa_search")

    assert tuple(task.name for task in tasks) == (
        "dsqa_100.general.search",
        "dsqa_100.research.search",
    )
    run.validate_pack(tasks, "dsqa_search")
    with pytest.raises(ValueError, match="require one pack"):
        run.validate_pack((run.tasks[0], run.tasks[2]), "dsqa_search")
    with pytest.raises(ValueError, match="requires one of these packs"):
        run.tasks_for_pack("assistant")
