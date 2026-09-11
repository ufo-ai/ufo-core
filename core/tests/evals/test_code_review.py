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
    correctness = f"/workspace/code-review-eval-review-target-7-{code_review.HEAD_SHA}-correctness"
    security = f"/workspace/code-review-eval-review-target-7-{code_review.HEAD_SHA}-security"
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


async def test_launch_grader_separates_a_payloadless_launch_from_a_repeated_one() -> None:
    """The two ways the launch act fails on a weaker model, told apart by what each call carried.

    A parent that names the target and drops the whole payload sends a call the reviewer contract
    refuses, reads that refusal as a fault in the deploy, and repeats it — production sent twelve.
    A parent that reissues a complete launch sends many valid ones. Both are 'not two spawns' in a
    count, so the verdict and the evidence name the payload first."""

    def output(*payloads: JsonObject) -> CapabilityOutput:
        calls = tuple(
            _call("spawn", f"spawn-{index}", {"target": "profile:coding", **payload})
            for index, payload in enumerate(payloads, start=1)
        )
        return CapabilityOutput(
            "",
            calls,
            own_calls=calls,
            timing=CaseTiming(
                wall_ms=1,
                turns=(_turn("evaluated", *(_step(call, 2) for call in calls)),),
            ),
        )

    complete = {"payload": {"objective": "\n".join(code_review.CURRENT_OBJECTIVE_MARKERS)}}
    named_only: JsonObject = {"background": True, "name": "correctness reviewer"}

    dropped = await code_review._grade_launch(output(named_only, named_only))
    repeated = await code_review._grade_launch(output(*([complete] * 11)))
    passed = await code_review._grade_launch(output(complete, complete))

    assert not dropped.passed
    assert "carried no payload" in dropped.reason
    assert dropped.evidence["launches"] == [
        {"payload_keys": [], "objective_chars": 0},
        {"payload_keys": [], "objective_chars": 0},
    ]
    assert not repeated.passed
    assert "started 11 reviewers" in repeated.reason
    assert passed.passed, passed.reason


async def test_launch_grader_rejects_an_incomplete_objective() -> None:
    def output(objective: str) -> CapabilityOutput:
        calls = tuple(
            _call(
                "spawn",
                f"spawn-{index}",
                {"target": "coding", "background": True, "payload": {"objective": objective}},
            )
            for index in (1, 2)
        )
        return CapabilityOutput(
            "",
            calls,
            own_calls=calls,
            timing=CaseTiming(
                wall_ms=1,
                turns=(_turn("evaluated", *(_step(call, 2) for call in calls)),),
            ),
        )

    empty = await code_review._grade_launch(output(""))
    partial = await code_review._grade_launch(
        output("\n".join(code_review.CURRENT_OBJECTIVE_MARKERS[:-1]))
    )
    oversized = await code_review._grade_launch(
        output(
            "\n".join(code_review.CURRENT_OBJECTIVE_MARKERS)
            + "x" * code_review.MAX_REVIEW_OBJECTIVE_CHARS
        )
    )

    assert not empty.passed
    assert "carried no objective" in empty.reason
    assert not partial.passed
    assert "omitted objective instructions" in partial.reason
    assert not oversized.passed
    assert "exceeded the spawn budget" in oversized.reason


async def test_recovery_grader_passes_a_repaired_launch_and_fails_one_that_never_lands() -> None:
    """The two cases divide the act: the launch case fails any refused call, this one fails only a
    turn where the reviewers never both started. Without that split an arm that repairs its call
    on the second try scores the same as one that repeats it forever, and the refusal's wording
    can move neither."""

    def output(*objectives: str) -> CapabilityOutput:
        calls = tuple(
            _call(
                "spawn",
                f"spawn-{index}",
                {"target": "coding", "background": True, "payload": {"objective": objective}}
                if objective
                else {"target": "coding", "background": True},
            )
            for index, objective in enumerate(objectives, start=1)
        )
        return CapabilityOutput("", calls, own_calls=calls)

    complete = "\n".join(code_review.CURRENT_OBJECTIVE_MARKERS)
    repaired = await code_review._grade_recovered_launch(output("", "", complete, complete))
    never = await code_review._grade_recovered_launch(output("", "", "", ""))
    one_only = await code_review._grade_recovered_launch(output("", complete))

    assert repaired.passed, repaired.reason
    assert repaired.evidence["refused_launches"] == 2
    assert not never.passed
    assert "never both started" in never.reason
    assert not one_only.passed


def test_launch_case_grades_the_parents_own_response() -> None:
    """It must not wait for the reviewers: the launch is graded when a parent that later loops or
    never settles would otherwise leave the case with no verdict about the launch at all."""
    case = next(case for case in code_review.CASES if "launch-carries-objective" in case.name)

    assert case.wait_for_background is False
    assert str(code_review.LAUNCH_PAGE_ID) in case.message


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


async def test_current_batch_grader_keeps_pull_requests_with_one_head_separate() -> None:
    reads = tuple(
        _call("object_get", f"read-{index}", {"ref": f"page/{page_id}"})
        for index, page_id in enumerate(code_review.CURRENT_BATCH_PAGE_IDS)
    )
    spawns = tuple(
        _call(
            "spawn",
            f"spawn-{number}-{focus}",
            {
                "target": "coding",
                "background": True,
                "payload": {"objective": f"Pull request number: {number}. Focus: {focus}."},
            },
        )
        for number in (7, 8)
        for focus in ("correctness", "security")
    )
    passed = await code_review._grade_current_batch(
        CapabilityOutput("", (*reads, *spawns), own_calls=(*reads, *spawns))
    )
    retried = await code_review._grade_current_batch(
        CapabilityOutput(
            "", (*reads, *spawns, *spawns[:2]), own_calls=(*reads, *spawns, *spawns[:2])
        )
    )
    merged = await code_review._grade_current_batch(
        CapabilityOutput("", (*reads, *spawns[:2]), own_calls=(*reads, *spawns[:2]))
    )

    assert passed.passed, passed.reason
    assert retried.passed, retried.reason
    assert not merged.passed
    assert "expected at least 4" in merged.reason


def test_current_batch_case_names_both_pages_in_order() -> None:
    case = next(case for case in code_review.CASES if case.name == "code-review-current-batch")
    first, second = (str(page_id) for page_id in code_review.CURRENT_BATCH_PAGE_IDS)

    assert case.message.index(first) < case.message.index(second)
    assert "pass each ref unchanged to object_get" in case.message


def test_change_log_batch_case_names_the_complete_log() -> None:
    case = next(case for case in code_review.CASES if case.name == "code-review-change-log-batch")

    assert code_review.CHANGE_LOG_PATH in case.message
    assert "Every changed page is one JSON line" in case.message
    assert len(code_review.CHANGE_LOG_BATCH_PAGE_IDS) == 6


async def test_a_foreground_launch_reads_as_a_foreground_launch() -> None:
    """The count and the background rule are two facts, and the verdict names which one failed.

    Folded together they lied: a suite run reported "started 0 background reviewers" for a parent
    that had spawned two reviewers, both of which reviewed the pull request and returned findings.
    The launch was never the problem in that case."""
    calls = tuple(
        _call(
            "spawn",
            f"spawn-{index}",
            {"target": "coding", "payload": {"objective": "review it"}},
        )
        for index in (1, 2)
    )
    output = CapabilityOutput("", calls, own_calls=calls)

    verdict = await code_review._grade_no_parent_plan(output)

    assert not verdict.passed
    assert "2 of 2 launches ran in the foreground" in verdict.reason
    assert verdict.evidence["spawn_count"] == 2
    assert verdict.evidence["foreground_launches"] == 2


async def test_published_verdict_grader_holds_the_head_the_context_and_the_state() -> None:
    """The suite could not reach this fact before: the eval workspace had no granted GitHub
    account, so every publication was refused and three cases failed for the gap rather than for
    anything the agent did."""

    def output(*statuses: JsonObject, is_error: bool = False) -> CapabilityOutput:
        calls = tuple(
            _call(
                "call_external_tool",
                f"publish-{index}",
                {
                    "source_id": "eval_github",
                    "slug": code_review.PUBLISH_STATUS_TOOL,
                    "arguments": status,
                },
                is_error=is_error,
            )
            for index, status in enumerate(statuses, start=1)
        )
        return CapabilityOutput("", calls, own_calls=calls)

    passing = {
        "sha": code_review.HEAD_SHA,
        "state": "success",
        "context": code_review.PUBLISH_CONTEXT,
    }
    none = await code_review._grade_published_verdict(CapabilityOutput("", (), own_calls=()))
    twice = await code_review._grade_published_verdict(output(passing, passing))
    stale = await code_review._grade_published_verdict(
        output({**passing, "sha": code_review.OLD_HEAD_SHA})
    )
    failed = await code_review._grade_published_verdict(output({**passing, "state": "failure"}))
    unread = await code_review._grade_published_verdict(
        output({**passing, "context": "code-review"})
    )
    published = await code_review._grade_published_verdict(output(passing))

    refused = await code_review._grade_published_verdict(output(passing, is_error=True))
    repaired_calls = (
        _call(
            "call_external_tool",
            "publish-refused",
            {"slug": code_review.PUBLISH_STATUS_TOOL, "arguments": passing},
            is_error=True,
        ),
        _call(
            "call_external_tool",
            "publish-ok",
            {"slug": code_review.PUBLISH_STATUS_TOOL, "arguments": passing},
        ),
    )
    repaired = await code_review._grade_published_verdict(
        CapabilityOutput("", repaired_calls, own_calls=repaired_calls)
    )

    assert not none.passed
    assert "no publication call succeeded" in none.reason
    assert not refused.passed
    assert "no publication call succeeded" in refused.reason
    assert repaired.passed, repaired.reason
    assert repaired.evidence["published"] == 1
    assert not twice.passed
    assert "expected 1" in twice.reason
    assert not stale.passed
    assert "other than the head" in stale.reason
    assert not failed.passed
    assert not unread.passed
    assert "no branch rule reads" in unread.reason
    assert published.passed, published.reason
    assert published.evidence["states"] == ["success"]
