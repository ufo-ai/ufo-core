from dataclasses import replace
from pathlib import Path
from uuid import uuid4

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.harness.handoff import SubagentHandoff
from evals.harness.harness import JsonObject
from evals.harness.timing import CaseTiming, StepTiming, TurnRole, TurnTiming
from evals.registry import TASKS
from evals.suites import code_review

REPOSITORY_ROOT = Path(__file__).parents[3]
INSTRUCTION_DIRECTORIES = (Path(), Path("extensions/web"), Path("docs/handbook"))


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


def _call(
    name: str,
    call_id: str,
    input: JsonObject,
    result: str = "",
    is_error: bool = False,
) -> ToolInvocation:
    return ToolInvocation(
        name,
        input,
        result=result,
        has_result=True,
        is_error=is_error,
        call_id=call_id,
    )


def test_code_review_task_selects_its_agent_and_waits_for_reviewers() -> None:
    task = next(task for task in TASKS if task.name == "code_review")
    review = next(case for case in code_review.CASES if "parallel" in case.name)

    assert task.agent == "code"
    assert review.wait_for_background is True


def test_agents_is_the_regular_instruction_file() -> None:
    for directory in INSTRUCTION_DIRECTORIES:
        agents = REPOSITORY_ROOT / directory / "AGENTS.md"
        claude = REPOSITORY_ROOT / directory / "CLAUDE.md"
        assert agents.is_file()
        assert not agents.is_symlink()
        assert claude.is_symlink()
        assert claude.readlink() == Path("AGENTS.md")


async def test_parallel_review_grader_requires_two_same_round_spawns_and_child_batches() -> None:
    first = _call(
        "spawn",
        "spawn-1",
        {"target": "coding", "background": True, "payload": {"objective": "review one"}},
    )
    second = _call(
        "spawn",
        "spawn-2",
        {"target": "coding", "background": True, "payload": {"objective": "review two"}},
    )
    correctness = f"/workspace/code-review-{code_review.HEAD_SHA}-correctness"
    security = f"/workspace/code-review-{code_review.HEAD_SHA}-security"
    alpha = _call("read", "read-1", {"file_path": f"{correctness}/src/alpha.py"})
    beta = _call("read", "read-2", {"file_path": f"{correctness}/src/beta.py"})
    bulk = _call("bash", "bash-1", {"command": f"cd {security} && sed -n 1p alpha.py beta.py"})
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
                    _turn("child", _model_round(1), _step(bulk, None), _step(bulk, None)),
                )
            }
        ),
    )
    assert (await code_review._grade_parallel_review(durable_only)).passed

    skill = _call("load_skill", "skill-1", {"name": "spawn-catalog"})
    loaded = replace(output, own_calls=(skill, first, second), calls=(skill, *output.calls))
    loaded_verdict = await code_review._grade_parallel_review(loaded)
    assert not loaded_verdict.passed
    assert "loaded a skill" in loaded_verdict.reason

    long = _call(
        "spawn",
        "spawn-3",
        {
            "target": "coding",
            "background": True,
            "payload": {"objective": "x" * (code_review.MAX_REVIEW_OBJECTIVE_CHARS + 1)},
        },
    )
    long_output = replace(
        output,
        own_calls=(long, second),
        calls=(long, second, alpha, beta, bulk),
        timing=timing.model_copy(
            update={
                "turns": (
                    _turn("evaluated", _step(long, 2), _step(second, 2)),
                    *timing.turns[1:],
                )
            }
        ),
    )
    long_verdict = await code_review._grade_parallel_review(long_output)
    assert not long_verdict.passed
    assert "objective exceeded" in long_verdict.reason


async def test_current_objective_grader_rejects_stale_transcript_instructions() -> None:
    def output(objective: str) -> CapabilityOutput:
        first = _call(
            "spawn",
            "spawn-1",
            {"target": "coding", "background": True, "payload": {"objective": objective}},
        )
        second = _call(
            "spawn",
            "spawn-2",
            {"target": "coding", "background": True, "payload": {"objective": objective}},
        )
        return CapabilityOutput(
            "",
            (first, second),
            own_calls=(first, second),
            timing=CaseTiming(
                wall_ms=1,
                turns=(_turn("evaluated", _step(first, 2), _step(second, 2)),),
            ),
        )

    current = "\n".join(code_review.CURRENT_OBJECTIVE_MARKERS)
    stale = f"{current}\n{code_review.STALE_OBJECTIVE_MARKERS[0]}"
    missing = "\n".join(code_review.CURRENT_OBJECTIVE_MARKERS[:-1])

    passed = await code_review._grade_current_objective(output(current))
    stale_verdict = await code_review._grade_current_objective(output(stale))
    missing_verdict = await code_review._grade_current_objective(output(missing))

    assert passed.passed, passed.reason
    assert not stale_verdict.passed
    assert "stale objective" in stale_verdict.reason
    assert not missing_verdict.passed
    assert "omitted current" in missing_verdict.reason


def test_stale_objective_case_contains_the_known_old_rules() -> None:
    case = next(case for case in code_review.CASES if "stale-reviewer-objective" in case.name)

    assert all(marker in case.prior_messages[-1] for marker in code_review.STALE_OBJECTIVE_MARKERS)
    assert all(
        marker not in case.prior_messages[-1] for marker in code_review.CURRENT_OBJECTIVE_MARKERS
    )


async def test_real_review_efficiency_grader_rejects_serial_or_long_reviewers() -> None:
    first = _call(
        "spawn",
        "spawn-1",
        {"target": "coding", "background": True, "payload": {"objective": "review one"}},
    )
    second = _call(
        "spawn",
        "spawn-2",
        {"target": "coding", "background": True, "payload": {"objective": "review two"}},
    )
    read = _call("read", "read-1", {"file_path": "one.py"})
    search = _call("grep", "grep-1", {"pattern": "caller", "path": "src"})
    timing = CaseTiming(
        wall_ms=3,
        turns=(
            _turn("evaluated", _step(first, 2), _step(second, 2)),
            _turn("child", _step(read, 4), _step(search, 4)),
            _turn("child", _step(read, 5), _step(search, 5)),
        ),
    )
    output = CapabilityOutput(
        "",
        (first, second, read, search),
        own_calls=(first, second),
        timing=timing,
    )

    passed = await code_review._grade_real_review_efficiency(output)
    serial = await code_review._grade_real_review_efficiency(
        replace(
            output,
            timing=timing.model_copy(
                update={
                    "turns": (
                        timing.turns[0],
                        _turn("child", _step(read, 4), _step(search, 5)),
                        _turn("child", _step(read, 6), _step(search, 7)),
                    )
                }
            ),
        )
    )
    long_turn = timing.turns[1].model_copy(
        update={"rounds": code_review.MAX_SMALL_REVIEW_ROUNDS + 1}
    )
    long = await code_review._grade_real_review_efficiency(
        replace(
            output,
            timing=timing.model_copy(
                update={"turns": (timing.turns[0], long_turn, timing.turns[2])}
            ),
        )
    )

    assert passed.passed, passed.reason
    assert not serial.passed
    assert "no multi-call" in serial.reason
    assert not long.passed
    assert "round budget" in long.reason


async def test_large_review_efficiency_grader_rejects_later_serial_reads() -> None:
    first = _call(
        "spawn",
        "spawn-1",
        {"target": "coding", "background": True, "payload": {"objective": "review one"}},
    )
    second = _call(
        "spawn",
        "spawn-2",
        {"target": "coding", "background": True, "payload": {"objective": "review two"}},
    )
    checkout = _call("bash", "checkout", {"command": "git fetch"})
    initial = tuple(
        _call("read", f"initial-{index}", {"file_path": f"initial-{index}"}) for index in range(4)
    )
    later = tuple(
        _call("read", f"later-{index}", {"file_path": f"later-{index}"}) for index in range(4)
    )
    serial = tuple(
        _call("read", f"serial-{index}", {"file_path": f"serial-{index}"})
        for index in range(code_review.MAX_LARGE_SINGLE_CALL_ROUNDS + 1)
    )

    def child(later_steps: tuple[StepTiming, ...]) -> TurnTiming:
        return _turn(
            "child",
            _step(checkout, 1),
            *(_step(call, 2) for call in initial),
            *later_steps,
        ).model_copy(update={"rounds": 4})

    passed_timing = CaseTiming(
        wall_ms=3,
        turns=(
            _turn("evaluated", _step(first, 2), _step(second, 2)),
            child(tuple(_step(call, 3) for call in later)),
            child(tuple(_step(call, 4) for call in later)),
        ),
    )
    calls = (first, second, checkout, *initial, *later, *serial)
    output = CapabilityOutput(
        "",
        calls,
        own_calls=(first, second),
        timing=passed_timing,
    )
    serial_timing = passed_timing.model_copy(
        update={
            "turns": (
                passed_timing.turns[0],
                child(
                    (
                        *(_step(call, 3) for call in later),
                        *(_step(call, 10 + index) for index, call in enumerate(serial)),
                    )
                ),
                passed_timing.turns[2],
            )
        }
    )

    passed = await code_review._grade_large_review_efficiency(output)
    failed = await code_review._grade_large_review_efficiency(replace(output, timing=serial_timing))

    assert passed.passed, passed.reason
    assert not failed.passed
    assert "serial evidence rounds" in failed.reason


async def test_large_review_fixture_commits_have_pinned_shas(tmp_path: Path) -> None:
    for item in code_review.LARGE_WORKSPACE_FILES:
        path = tmp_path / item.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(item.content)

    await code_review._prepare_large_review(uuid4(), tmp_path)

    assert await code_review._git(tmp_path / "review-target", "rev-parse", "HEAD") == (
        code_review.LARGE_HEAD_SHA
    )


async def test_no_parent_plan_grader_rejects_durable_plan() -> None:
    first = _call("spawn", "spawn-1", {"target": "coding", "background": True})
    second = _call("spawn", "spawn-2", {"target": "coding", "background": True})
    plan = _call("plan_objective", "plan-1", {"objective": "Review the pull request"})

    passed = await code_review._grade_no_parent_plan(
        CapabilityOutput("", (first, second), own_calls=(first, second))
    )
    replacements = await code_review._grade_no_parent_plan(
        CapabilityOutput(
            "", (first, second, first, second), own_calls=(first, second, first, second)
        )
    )
    failed = await code_review._grade_no_parent_plan(
        CapabilityOutput("", (first, second, plan), own_calls=(first, second, plan))
    )

    assert passed.passed, passed.reason
    assert replacements.passed, replacements.reason
    assert not failed.passed
    assert "durable review planning state" in failed.reason


async def test_strict_reviewer_json_grader_rejects_trailing_text() -> None:
    valid = SubagentHandoff(
        conversation_id=uuid4(),
        closing_chars=0,
        result_chars=2,
        result_json_object=True,
        duplication=0,
    )
    trailing = SubagentHandoff(
        conversation_id=uuid4(),
        closing_chars=0,
        result_chars=7,
        result_json_object=False,
        duplication=0,
    )

    passed = await code_review._grade_strict_reviewer_json(
        CapabilityOutput("", (), handoffs=(valid, valid))
    )
    failed = await code_review._grade_strict_reviewer_json(
        CapabilityOutput("", (), handoffs=(valid, trailing))
    )

    assert passed.passed, passed.reason
    assert not failed.passed
    assert "text outside its JSON object" in failed.reason


async def test_instruction_grader_requires_both_files_without_checkout_errors() -> None:
    instructions = f"{code_review.ROOT_INSTRUCTION}\n{code_review.NESTED_INSTRUCTION}"
    first = _call("bash", "read-1", {"command": "cat AGENTS.md src/AGENTS.md"}, instructions)
    second = _call("bash", "read-2", {"command": "cat AGENTS.md src/AGENTS.md"}, instructions)
    timing = CaseTiming(
        wall_ms=2,
        turns=(
            _turn("child", _step(first, 1)),
            _turn("child", _step(second, 1)),
        ),
    )
    clean = CapabilityOutput("", (first, second), timing=timing)
    failed_read = _call(
        "read",
        "read-3",
        {"file_path": "/tmp/review/AGENTS.md"},
        "ValueError: path '/tmp/review/AGENTS.md' escapes /workspace",
        True,
    )

    passed = await code_review._grade_instruction_reads(clean)
    failed = await code_review._grade_instruction_reads(
        CapabilityOutput(
            "",
            (failed_read, second),
            timing=timing.model_copy(
                update={
                    "turns": (
                        _turn("child", _step(failed_read, 1)),
                        timing.turns[1],
                    )
                }
            ),
        )
    )

    assert passed.passed, passed.reason
    assert not failed.passed
    assert "failed while reading instructions" in failed.reason


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
