"""Reading a case's latency out of the engine's durable step record: what each turn spent, which
individual step was expensive, and how a tool call gets its name."""

from uuid import uuid4

from evals.harness.timing import (
    MODEL_ROUND_STEP,
    SLOWEST_STEPS,
    TOOL_CALL_STEP,
    UNNAMED_TOOL,
    TurnStep,
    case_timing,
    turn_timing,
)
from ufo.sdk.models import Message, TextBlock, ToolResultBlock, ToolUseBlock

TURN = uuid4()
CHILD = uuid4()


def step(name: str, started: int, completed: int, call_id: str = "") -> TurnStep:
    return TurnStep(
        function_name=name,
        started_at_epoch_ms=started,
        completed_at_epoch_ms=completed,
        call_id=call_id,
    )


def test_a_turn_divides_its_span_into_model_rounds_tools_and_the_rest() -> None:
    steps = (
        step(f"ufo.runtime.engine.Engine.{MODEL_ROUND_STEP}", 1_000, 3_000),
        step(f"ufo.runtime.engine.Engine.{TOOL_CALL_STEP}", 3_100, 9_100, "call-1"),
        step(f"ufo.runtime.engine.Engine.{MODEL_ROUND_STEP}", 9_500, 10_000),
    )
    timing = turn_timing(
        TURN, "evaluated", steps, {"call-1": "bash"}, tokens=1_234, cost_micro_usd=9
    )
    assert timing.span_ms == 9_000
    assert timing.model_round_ms == 2_500
    assert timing.tool_call_ms == 6_000
    assert timing.unaccounted_ms == 500
    assert timing.rounds == 2
    assert timing.tool_calls == 1
    assert timing.tokens == 1_234
    assert timing.cost_micro_usd == 9
    assert [item.name for item in timing.steps] == ["model round", "bash", "model round"]
    assert [item.kind for item in timing.steps] == ["model_round", "tool_call", "model_round"]


def test_steps_carry_resources_and_link_to_their_exact_transcript_rows() -> None:
    messages = (
        Message(role="user", content="prior question"),
        Message(role="assistant", content="prior answer"),
        Message(role="user", content="begin"),
        Message(
            role="assistant",
            content=(
                TextBlock(text="checking"),
                ToolUseBlock(id="call-1", name="bash", input={"command": "pwd"}),
            ),
        ),
        Message(role="user", content=(ToolResultBlock(tool_use_id="call-1", content="ok"),)),
        Message(role="assistant", content="done"),
    )
    steps = (
        TurnStep(
            function_name=f"E.{MODEL_ROUND_STEP}",
            started_at_epoch_ms=0,
            completed_at_epoch_ms=10,
            call_ids=("call-1",),
            tokens=120,
            cost_micro_usd=8,
        ),
        step(f"E.{TOOL_CALL_STEP}", 10, 30, "call-1"),
        TurnStep(
            function_name=f"E.{MODEL_ROUND_STEP}",
            started_at_epoch_ms=30,
            completed_at_epoch_ms=40,
            tokens=40,
            cost_micro_usd=3,
        ),
    )

    timing = turn_timing(TURN, "evaluated", steps, {"call-1": "bash"}, messages=messages)

    first_round, tool, final_round = timing.steps
    assert [item.number for item in timing.steps] == [1, 2, 3]
    assert [item.turn_id for item in timing.steps] == [TURN, TURN, TURN]
    assert [item.message_index for item in timing.steps] == [4, 4, 6]
    assert (first_round.tokens, first_round.cost_micro_usd) == (120, 8)
    assert (final_round.tokens, final_round.cost_micro_usd) == (40, 3)
    assert tool.call_id == "call-1"
    assert tool.tokens is None
    assert tool.cost_micro_usd is None


def test_a_tool_call_is_named_by_the_call_id_its_step_recorded() -> None:
    steps = (
        step(f"E.{TOOL_CALL_STEP}", 0, 10, "second"),
        step(f"E.{TOOL_CALL_STEP}", 20, 30, "first"),
    )
    names = {"first": "read", "second": "grep"}
    timing = turn_timing(TURN, "evaluated", steps, names)
    assert [item.name for item in timing.steps] == ["grep", "read"]


def test_an_unresolved_call_id_is_named_generically_rather_than_mislabeled() -> None:
    steps = (
        step(f"E.{TOOL_CALL_STEP}", 0, 10, "known"),
        step(f"E.{TOOL_CALL_STEP}", 10, 20, "vanished"),
    )
    timing = turn_timing(TURN, "evaluated", steps, {"known": "bash"})
    assert [item.name for item in timing.steps] == ["bash", UNNAMED_TOOL]
    assert timing.tool_calls == 2


def test_an_unfinished_step_is_counted_but_not_timed() -> None:
    steps = (
        step(f"E.{TOOL_CALL_STEP}", 0, 100, "call-1"),
        TurnStep(function_name=f"E.{TOOL_CALL_STEP}", started_at_epoch_ms=200, call_id="call-2"),
    )
    timing = turn_timing(TURN, "evaluated", steps, {"call-1": "bash", "call-2": "edit"})
    assert timing.tool_calls == 2
    assert len(timing.steps) == 1
    assert timing.tool_call_ms == 100


def test_a_turn_with_no_recorded_step_reports_no_span() -> None:
    timing = turn_timing(TURN, "evaluated", (), {})
    assert timing.span_ms == 0
    assert timing.steps == ()
    assert timing.rounds == 0


def test_a_cancelled_turn_counts_every_completed_round_as_intermediate() -> None:
    timing = turn_timing(
        TURN,
        "child",
        (
            TurnStep(
                function_name=f"E.{MODEL_ROUND_STEP}",
                started_at_epoch_ms=0,
                completed_at_epoch_ms=10,
                output_tokens=20,
            ),
            TurnStep(
                function_name=f"E.{MODEL_ROUND_STEP}",
                started_at_epoch_ms=10,
                completed_at_epoch_ms=20,
                output_tokens=30,
            ),
        ),
        {},
        status="cancelled",
    )

    assert timing.status == "cancelled"
    assert timing.output_tokens == 50
    assert timing.intermediate_output_tokens == 50


def test_the_engines_own_bookkeeping_is_neither_model_nor_tool() -> None:
    timing = turn_timing(TURN, "evaluated", (step("E._claim_arrivals", 0, 40),), {})
    assert timing.steps[0].kind == "other"
    assert timing.steps[0].name == "_claim_arrivals"
    assert timing.model_round_ms == 0
    assert timing.tool_call_ms == 0


def test_a_case_reports_each_turn_and_the_slowest_steps_across_them() -> None:
    parent = turn_timing(
        TURN,
        "evaluated",
        (step(f"E.{MODEL_ROUND_STEP}", 0, 500),),
        {},
    )
    child = turn_timing(
        CHILD,
        "child",
        (
            step(f"E.{TOOL_CALL_STEP}", 1_000, 61_000, "slow"),
            step(f"E.{TOOL_CALL_STEP}", 61_000, 61_200, "quick"),
        ),
        {"slow": "bash", "quick": "read"},
    )
    timing = case_timing(75_000, (parent, child))
    assert timing.wall_ms == 75_000
    assert [turn.role for turn in timing.turns] == ["evaluated", "child"]
    assert [item.name for item in timing.slowest][:2] == ["bash", "model round"]
    assert timing.slowest[0].duration_ms == 60_000
    assert not timing.error


def test_the_slowest_list_is_bounded() -> None:
    many = turn_timing(
        TURN,
        "evaluated",
        tuple(
            step(f"E.{MODEL_ROUND_STEP}", index, index + 1) for index in range(SLOWEST_STEPS * 3)
        ),
        {},
    )
    assert len(case_timing(10, (many,)).slowest) == SLOWEST_STEPS


def test_concurrent_tool_calls_cost_the_wall_they_occupied() -> None:
    """A round dispatches its tool calls concurrently, so summing their durations invents time the
    turn never spent: three sixty-second reads inside one minute cost that minute."""
    steps = (
        step(f"E.{TOOL_CALL_STEP}", 1_000, 61_000, "a"),
        step(f"E.{TOOL_CALL_STEP}", 1_100, 61_000, "b"),
        step(f"E.{TOOL_CALL_STEP}", 1_200, 60_500, "c"),
    )
    timing = turn_timing(TURN, "evaluated", steps, {"a": "read", "b": "read", "c": "read"})
    assert timing.tool_calls == 3
    assert timing.span_ms == 60_000
    assert timing.tool_call_ms == 60_000
    assert timing.unaccounted_ms == 0


def test_sequential_steps_still_add_up() -> None:
    steps = (
        step(f"E.{TOOL_CALL_STEP}", 0, 1_000, "a"),
        step(f"E.{TOOL_CALL_STEP}", 2_000, 3_000, "b"),
    )
    timing = turn_timing(TURN, "evaluated", steps, {"a": "read", "b": "grep"})
    assert timing.tool_call_ms == 2_000
    assert timing.span_ms == 3_000
    assert timing.unaccounted_ms == 1_000
