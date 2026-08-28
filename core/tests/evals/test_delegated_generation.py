"""Reading what delegated children generated out of a run archive: how much output reached no
parent, over which child turns, and when the archive cannot answer at all. The archive is built
from the recorder's own types, so a field either end renames stops the reader rather than leaving
it reporting a reduction that never happened."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from evals.delegated_generation import DELIVERED_METRIC_NAME, METRIC_NAME, generation_in
from evals.delegated_generation import main as generation_main
from evals.harness.handoff import handoff_record
from evals.harness.harness import EvalCaseResult, EvalReport, Json
from evals.harness.timing import (
    MODEL_ROUND_STEP,
    TOOL_CALL_STEP,
    TurnStep,
    case_timing,
    turn_timing,
)
from evals.harness.viewer import EvalRun
from evals.suites.response_register import DELEGATED_CASES, DELEGATED_TASK
from ufo.schema.records import TurnStatus
from ufo.sdk.models import Message, TextBlock, ToolUseBlock

CASE = DELEGATED_CASES[0].name
PARENT = uuid4()


def child_timing(*output_tokens: int | None, status: TurnStatus = "done") -> Json:
    """One child turn that worked for a few rounds and closed on the last one, timed exactly as the
    harness times it."""
    steps = tuple(
        TurnStep(
            function_name=f"ufo.loop.engine.Engine.{MODEL_ROUND_STEP}",
            started_at_epoch_ms=index * 1_000,
            completed_at_epoch_ms=index * 1_000 + 500,
            output_tokens=tokens,
        )
        for index, tokens in enumerate(output_tokens)
    )
    working = (
        TurnStep(
            function_name=f"ufo.loop.engine.Engine.{TOOL_CALL_STEP}",
            started_at_epoch_ms=600,
            completed_at_epoch_ms=900,
            call_id="c1",
        ),
    )
    return case_timing(
        9_000,
        (
            turn_timing(PARENT, "evaluated", (), {}),
            turn_timing(uuid4(), "child", steps + working, {"c1": "read"}, status=status),
        ),
    ).model_dump(mode="json")


def archive(
    tmp_path: Path,
    *timings: Json,
    task: str = DELEGATED_TASK,
    name: str = "run-1",
    attempts: Json | None = None,
) -> Path:
    recorded = [{"timing": timing} for timing in timings] if attempts is None else attempts
    run = EvalRun(
        id=uuid4(),
        created_at=datetime.now(UTC),
        label=name,
        agent="assistant",
        ufo_version="0",
        revision="0",
        reports=(
            EvalReport(
                name=task,
                suite="capability",
                digest="d",
                cases=(
                    EvalCaseResult(
                        name=CASE, passed=False, reason="", evidence={"attempts": recorded}
                    ),
                ),
            ),
        ),
    )
    path = tmp_path / f"{name}.json"
    path.write_text(run.model_dump_json(indent=2, by_alias=True, exclude_none=True))
    return path


def test_intermediate_output_is_every_round_but_the_one_that_delivered(tmp_path: Path) -> None:
    """A child that narrated through three rounds and handed over on the fourth paid for all four;
    only the fourth reached its parent."""
    (case,) = generation_in(archive(tmp_path, child_timing(400, 300, 200, 100)))
    assert case.case == CASE
    assert len(case.turns) == 1
    assert case.rounds == 4
    assert case.output_tokens == 1_000
    assert case.intermediate_output_tokens == 900
    assert case.measured


def test_an_unfinished_child_delivered_nothing(tmp_path: Path) -> None:
    """A child that failed or was cancelled returned no payload, so every round it paid for is
    intermediate — counting its last round as delivered would credit a turn that delivered."""
    (case,) = generation_in(archive(tmp_path, child_timing(400, 100, status="cancelled")))
    assert case.output_tokens == 500
    assert case.intermediate_output_tokens == 500


def test_a_case_that_failed_still_reports_what_its_child_generated(tmp_path: Path) -> None:
    """The verdict is not the gate: a case recorded as failed is exactly the case whose child may
    have generated the most, and dropping it would report the waste as an improvement."""
    path = archive(tmp_path, child_timing(500, 100))
    assert EvalRun.model_validate_json(path.read_text()).reports[0].cases[0].passed is False
    (case,) = generation_in(path)
    assert case.intermediate_output_tokens == 500


def test_the_delivered_side_separates_a_thinner_report_from_less_waste(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A child asked for an artifact writes it in an intermediate round, so those tokens are the
    deliverable rather than waste. The payload and the bytes it left in files are reported beside
    the token count: a candidate that cut tokens by writing less shows both numbers falling."""
    handoff = handoff_record(
        uuid4(),
        (
            Message(role="assistant", content=(TextBlock(text="Reading both files."),)),
            Message(
                role="assistant",
                content=(
                    ToolUseBlock(
                        id="w1",
                        name="write",
                        input={"file_path": "/workspace/evidence.md", "content": "x" * 6_000},
                    ),
                ),
            ),
        ),
        "Note contradicts the code. Evidence in /workspace/evidence.md.",
    ).model_dump(mode="json")
    attempts: Json = [{"timing": child_timing(400, 2_000, 300), "handoffs": [handoff]}]
    (case,) = generation_in(archive(tmp_path, attempts=attempts))
    assert case.intermediate_output_tokens == 2_400
    assert case.prose_chars == len("Reading both files.")
    assert case.delivered_chars == 6_000 + len(
        "Note contradicts the code. Evidence in /workspace/evidence.md."
    )
    generation_main(["--runs", str(tmp_path), "--metric-stdout"])
    assert f"{DELIVERED_METRIC_NAME}: 6062.0" in capsys.readouterr().out


def test_an_attempt_that_delegated_nothing_records_no_handoff(tmp_path: Path) -> None:
    """`handoffs` is null when a turn spawned nothing, which is not a renamed field."""
    attempts: Json = [{"timing": child_timing(400, 100), "handoffs": None}]
    (case,) = generation_in(archive(tmp_path, attempts=attempts))
    assert case.handoffs == ()
    assert case.prose_chars == 0
    assert case.delivered_chars == 0


def test_a_renamed_handoff_field_stops_the_reader(tmp_path: Path) -> None:
    attempts: Json = [
        {
            "timing": child_timing(400, 100),
            "handoffs": [{"conversation_id": str(uuid4()), "prose_chars": 10}],
        }
    ]
    with pytest.raises(ValueError, match="does not match the recorder's own shape"):
        generation_in(archive(tmp_path, attempts=attempts))


def test_a_run_with_no_delegated_task_reports_nothing(tmp_path: Path) -> None:
    assert generation_in(archive(tmp_path, child_timing(500, 100), task="response_register")) == ()


def test_a_renamed_recorder_field_stops_the_reader(tmp_path: Path) -> None:
    """A timing row that silently validated with its turns defaulted away would report every arm as
    generating nothing, which reads as a total reduction."""
    renamed = {"wall_ms": 10, "turn_timings": [{"turn_id": str(uuid4()), "role": "child"}]}
    with pytest.raises(ValueError, match="does not match the recorder's own shape"):
        generation_in(archive(tmp_path, renamed))


def test_evidence_with_no_attempt_list_stops_the_reader(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no attempt list"):
        generation_in(archive(tmp_path, attempts={"timing": None}))


def test_the_metric_is_the_mean_over_child_turns_across_every_archive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An arm's repeats are one figure, normalized per child turn: an arm that delegated half as
    often must not read as half the generation."""
    archive(tmp_path, child_timing(400, 100), child_timing(200, 100), name="run-1")
    archive(tmp_path, child_timing(300, 100), name="run-2")
    written = tmp_path / "figures.json"
    generation_main(["--runs", str(tmp_path), "--metric-stdout", "--json", str(written)])
    assert f"{METRIC_NAME}: 300.0" in capsys.readouterr().out
    assert json.loads(written.read_text()) == {
        "task": DELEGATED_TASK,
        "attempts": 3,
        "unmeasuredAttempts": 0,
        "childTurns": 3,
        "outputTokens": 1_200,
        "intermediateOutputTokens": 900,
        "proseChars": 0,
        "deliveredChars": 0,
        "intermediatePerChildTurn": 300.0,
        "deliveredPerChildTurn": 0.0,
    }


def test_an_attempt_missing_output_usage_is_excluded_not_counted_as_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A round the provider reported no usage for makes that attempt unreadable, and reading it as
    zero would be the cheapest possible way to claim a reduction."""
    archive(tmp_path, child_timing(400, 100), child_timing(None, 100), name="run-1")
    generation_main(["--runs", str(tmp_path), "--metric-stdout"])
    printed = capsys.readouterr().out
    assert "UNMEASURED" in printed
    assert "1 attempt(s) excluded for missing output usage" in printed
    assert f"{METRIC_NAME}: 400.0" in printed


def test_a_run_with_no_measured_child_turn_refuses_a_metric(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Absent data is not a reduction, so the reader exits non-zero rather than printing zero."""
    archive(tmp_path, child_timing(None, None), name="run-1")
    with pytest.raises(SystemExit) as exit_info:
        generation_main(["--runs", str(tmp_path), "--metric-stdout"])
    assert exit_info.value.code == 3
    assert METRIC_NAME not in capsys.readouterr().out
