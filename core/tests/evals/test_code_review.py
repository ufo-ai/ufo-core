from dataclasses import replace
from uuid import uuid4

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.harness.harness import JsonObject
from evals.harness.timing import CaseTiming, StepTiming, TurnRole, TurnTiming
from evals.suites import code_review


def _step(call: ToolInvocation, message: int | None) -> StepTiming:
    return StepTiming(
        kind="tool_call",
        name=call.name,
        duration_ms=1,
        message_index=message,
        call_id=call.call_id,
    )


def _model_round(number: int) -> StepTiming:
    return StepTiming(
        kind="model_round",
        name="model round",
        duration_ms=1,
        message_index=None,
        number=number,
    )


def _turn(role: TurnRole, *steps: StepTiming) -> TurnTiming:
    return TurnTiming(
        turn_id=uuid4(),
        role=role,
        span_ms=1,
        model_round_ms=0,
        tool_call_ms=1,
        unaccounted_ms=0,
        rounds=1,
        tool_calls=len(steps),
        tokens=0,
        cost_micro_usd=0,
        steps=steps,
    )


def _call(name: str, call_id: str, input: JsonObject) -> ToolInvocation:
    return ToolInvocation(name, input, has_result=True, call_id=call_id)


async def test_parallel_review_grader_requires_two_same_round_spawns_and_child_batches() -> None:
    first = _call("spawn", "spawn-1", {"target": "coding", "background": True, "payload": {}})
    second = _call("spawn", "spawn-2", {"target": "coding", "background": True, "payload": {}})
    alpha = _call("read", "read-1", {"file_path": "/workspace/src/alpha.py"})
    beta = _call("read", "read-2", {"file_path": "/workspace/src/beta.py"})
    bulk = _call("bash", "bash-1", {"command": "sed -n 1p alpha.py beta.py"})
    timing = CaseTiming(
        wall_ms=3,
        turns=(
            _turn("evaluated", _step(first, 2), _step(second, 2)),
            _turn("child", _step(alpha, 4), _step(beta, 4)),
            _turn("child", _step(bulk, 5)),
        ),
    )
    output = CapabilityOutput(
        "",
        (first, second, alpha, beta, bulk),
        own_calls=(first, second),
        timing=timing,
    )

    passed = await code_review._grade_parallel_review(output)
    singleton = await code_review._grade_parallel_review(
        replace(
            output,
            calls=(first, second, alpha, beta, _call("read", "read-3", {"file_path": "x"})),
            timing=timing.model_copy(
                update={
                    "turns": (
                        *timing.turns[:2],
                        _turn("child", _step(_call("read", "read-3", {"file_path": "x"}), 5)),
                    )
                }
            ),
        )
    )

    assert passed.passed, passed.reason
    assert not singleton.passed
    assert "no same-round batch" in singleton.reason

    durable_only = replace(
        output,
        timing=timing.model_copy(
            update={
                "turns": (
                    timing.turns[0],
                    _turn("child", _model_round(1), _step(alpha, None), _step(beta, None)),
                    _turn("child", _model_round(1), _step(alpha, None), _step(beta, None)),
                )
            }
        ),
    )
    assert (await code_review._grade_parallel_review(durable_only)).passed


async def test_preemption_grader_requires_both_old_reviewers_and_no_old_status() -> None:
    cancels = tuple(
        _call("cancel_spawn", f"cancel-{index}", {"spawn_id": spawn_id})
        for index, spawn_id in enumerate(code_review.OLD_SPAWNS)
    )
    clean = CapabilityOutput("", cancels, own_calls=cancels)
    published = _call(
        "call_external_tool",
        "publish",
        {
            "source_id": "github",
            "tool_name": "github_api_request",
            "arguments": {
                "method": "POST",
                "path": f"/repos/eval/review/statuses/{code_review.OLD_HEAD_SHA}",
            },
        },
    )

    passed = await code_review._grade_preemption(clean)
    failed = await code_review._grade_preemption(
        CapabilityOutput("", (*cancels, published), own_calls=(*cancels, published))
    )

    assert passed.passed, passed.reason
    assert not failed.passed
    assert "old head still received" in failed.reason
