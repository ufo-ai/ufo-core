"""Each `dispatch_recovery` grader stands for one production `tool.dispatch_failed` bucket, so each
one is proved against the trajectory that bucket records and against the trajectory that clears
it."""

import json

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.registry import TASKS
from evals.suites.dispatch_recovery import (
    CASES,
    CODING_PROFILE,
    KIT_PATH,
    KIT_RADIUS,
    LINES_PATH,
    LINES_TOTAL,
    RELEASE_PATH,
    RELEASE_QUOTED,
    RELEASE_WANTED,
    ROWS_TOTAL,
    SETTINGS_PATH,
    SUMMARY_PATH,
)

BY_NAME = {case.name: case for case in CASES}
WRITE_REFUSAL = (
    f"ValueError: file /workspace/{SUMMARY_PATH} must be read or named in a bash command "
    "before it is written"
)
EDIT_REFUSAL = (
    f"ValueError: file /workspace/{SETTINGS_PATH} must be read or named in a bash command "
    "before it is edited"
)
ESCAPE_REFUSAL = "ValueError: path '/home/oai/share/rows.csv' escapes /workspace"
HOME_REFUSAL = "ValueError: path '/home/user/.ufo' escapes /workspace"
ROOT_REFUSAL = "ValueError: path '/' escapes /workspace"
KIT_REFUSAL = f"ValueError: path '/{KIT_PATH}' escapes /workspace"
MATCH_REFUSAL = f"ValueError: old_string not found in /workspace/{RELEASE_PATH}"
SPAWN_REJECTION = "ValidationError: 1 validation error for SpawnInput\ntarget\n  Field required"
TODO_REJECTION = (
    "ValidationError: 1 validation error for UpdateTodoListInput\ntitle\n  Field required"
)


def _call(
    name: str, payload: dict[str, object], result: str = "{}", is_error: bool = False
) -> ToolInvocation:
    return ToolInvocation(
        name=name, input=payload, result=result, has_result=True, is_error=is_error
    )


def _read(path: str) -> ToolInvocation:
    return _call("read", {"file_path": f"/workspace/{path}"}, result="body")


def test_the_suite_is_registered_for_the_nightly_sweep() -> None:
    assert "dispatch_recovery" in {task.name for task in TASKS}
    assert [case.digest_tag for case in CASES] == [f"dispatch:{case.name}" for case in CASES]


async def test_write_case_fails_the_unread_overwrite_and_passes_a_read_first_write() -> None:
    grader = BY_NAME["write-reads-first"].grader
    path = f"/workspace/{SUMMARY_PATH}"

    refused = await grader(
        CapabilityOutput(
            "done",
            (
                _call("write", {"file_path": path}, result=WRITE_REFUSAL, is_error=True),
                _read(SUMMARY_PATH),
                _call("write", {"file_path": path}),
            ),
        )
    )
    clean = await grader(
        CapabilityOutput("done", (_read(SUMMARY_PATH), _call("write", {"file_path": path})))
    )

    assert not refused.passed
    assert "read guard" in refused.reason
    assert clean.passed


async def test_write_case_fails_a_write_the_turn_never_read() -> None:
    grader = BY_NAME["write-reads-first"].grader

    verdict = await grader(
        CapabilityOutput("done", (_call("write", {"file_path": f"/workspace/{SUMMARY_PATH}"}),))
    )

    assert not verdict.passed
    assert "without a read" in verdict.reason


async def test_write_case_passes_a_write_after_a_bash_command_that_named_the_file() -> None:
    """The guard takes a bash command that names the file as it takes a read, so the case grades
    the trajectory the tools now allow."""
    grader = BY_NAME["write-reads-first"].grader
    path = f"/workspace/{SUMMARY_PATH}"

    verdict = await grader(
        CapabilityOutput(
            "done",
            (
                _call("bash", {"command": f"cat {path}"}, result="# summary"),
                _call("write", {"file_path": path}),
            ),
        )
    )

    assert verdict.passed


async def test_edit_case_reads_the_edit_guard_refusal() -> None:
    grader = BY_NAME["edit-reads-first"].grader
    path = f"/workspace/{SETTINGS_PATH}"

    refused = await grader(
        CapabilityOutput(
            "done", (_call("edit", {"file_path": path}, result=EDIT_REFUSAL, is_error=True),)
        )
    )
    clean = await grader(
        CapabilityOutput("done", (_read(SETTINGS_PATH), _call("edit", {"file_path": path})))
    )

    assert not refused.passed
    assert clean.passed


async def test_workspace_path_case_tolerates_one_escape_and_fails_a_repeat() -> None:
    grader = BY_NAME["paths-stay-in-the-workspace"].grader
    escape = _call("read", {"file_path": "/home/oai/share/rows.csv"}, ESCAPE_REFUSAL, True)
    answer = f"ANSWER: {ROWS_TOTAL}"

    once = await grader(CapabilityOutput(answer, (escape, _read("inbox/rows.csv"))))
    twice = await grader(CapabilityOutput(answer, (escape, escape, _read("inbox/rows.csv"))))
    wrong = await grader(CapabilityOutput("ANSWER: 12", (_read("inbox/rows.csv"),)))

    assert once.passed
    assert not twice.passed
    assert not wrong.passed


async def test_workspace_path_case_wants_the_file_reached_under_the_workspace() -> None:
    grader = BY_NAME["paths-stay-in-the-workspace"].grader
    escape = _call("read", {"file_path": "/home/oai/share/rows.csv"}, ESCAPE_REFUSAL, True)

    outside = await grader(CapabilityOutput(f"ANSWER: {ROWS_TOTAL}", (escape,)))

    assert not outside.passed
    assert "no path under /workspace" in outside.reason


async def test_prod_escape_case_tolerates_one_spelling_and_fails_the_next() -> None:
    grader = BY_NAME["outside-paths-are-rewritten"].grader
    home = _call("read", {"file_path": "/home/user/.ufo/kit.md"}, HOME_REFUSAL, True)
    root = _call("read", {"file_path": "/"}, ROOT_REFUSAL, True)
    skills = _call("read", {"file_path": f"/{KIT_PATH}"}, KIT_REFUSAL, True)
    answer = f"ANSWER: {KIT_RADIUS}"

    once = await grader(CapabilityOutput(answer, (skills, _read(KIT_PATH))))
    twice = await grader(CapabilityOutput(answer, (home, root, _read(KIT_PATH))))
    wrong = await grader(CapabilityOutput("ANSWER: 4px", (_read(KIT_PATH),)))

    assert once.passed
    assert not twice.passed
    assert not wrong.passed


async def test_edit_match_case_wants_a_reread_between_the_miss_and_the_change() -> None:
    grader = BY_NAME["edit-targets-the-text-that-exists"].grader
    path = f"/workspace/{RELEASE_PATH}"
    miss = _call(
        "edit",
        {"file_path": path, "old_string": RELEASE_QUOTED, "new_string": RELEASE_WANTED},
        result=MATCH_REFUSAL,
        is_error=True,
    )
    landed = _call(
        "edit", {"file_path": path, "old_string": "Status: draft", "new_string": RELEASE_WANTED}
    )

    reread = await grader(CapabilityOutput("done", (miss, _read(RELEASE_PATH), landed)))
    guessed = await grader(CapabilityOutput("done", (miss, miss, landed)))
    abandoned = await grader(CapabilityOutput("I could not find that line.", (miss,)))

    assert reread.passed
    assert not guessed.passed
    assert "not read after the miss" in guessed.reason
    assert not abandoned.passed


async def test_edit_match_case_fails_a_change_that_drops_the_wanted_text() -> None:
    grader = BY_NAME["edit-targets-the-text-that-exists"].grader
    path = f"/workspace/{RELEASE_PATH}"

    verdict = await grader(
        CapabilityOutput(
            "done",
            (
                _read(RELEASE_PATH),
                _call("write", {"file_path": path, "content": "# Release 4.2\n\nStatus: draft\n"}),
            ),
        )
    )

    assert not verdict.passed
    assert "does not carry" in verdict.reason


async def test_todo_case_fails_the_payload_the_schema_rejected() -> None:
    grader = BY_NAME["todo-list-payload"].grader

    rejected = await grader(
        CapabilityOutput(
            "done",
            (
                _call("update_todo_list", {"tasks": []}, result=TODO_REJECTION, is_error=True),
                _call("update_todo_list", {"title": "Launch", "tasks": []}),
            ),
        )
    )
    nested = await grader(
        CapabilityOutput("done", (_call("update_todo_list", {"payload": {"title": "Launch"}}),))
    )
    clean = await grader(
        CapabilityOutput("done", (_call("update_todo_list", {"title": "Launch", "tasks": []}),))
    )

    assert not rejected.passed
    assert not nested.passed
    assert "omits title" in nested.reason
    assert clean.passed


async def test_spawn_case_fails_the_nested_target_and_passes_the_top_level_one() -> None:
    grader = BY_NAME["spawn-payload-shape"].grader
    child = json.dumps({"result": f"{LINES_TOTAL} lines"})
    answer = f"The coding subagent counted {LINES_TOTAL} lines."

    nested = await grader(
        CapabilityOutput(
            answer,
            (
                _call(
                    "spawn",
                    {"payload": {"target": CODING_PROFILE, "objective": LINES_PATH}},
                    result=SPAWN_REJECTION,
                    is_error=True,
                ),
            ),
        )
    )
    clean = await grader(
        CapabilityOutput(
            answer,
            (
                _call(
                    "spawn",
                    {"target": CODING_PROFILE, "payload": {"objective": LINES_PATH}},
                    result=child,
                ),
            ),
        )
    )
    unrelayed = await grader(
        CapabilityOutput(
            "The child is done.",
            (
                _call(
                    "spawn",
                    {"target": CODING_PROFILE, "payload": {"objective": LINES_PATH}},
                    result=child,
                ),
            ),
        )
    )
    prefixed = await grader(
        CapabilityOutput(
            answer,
            (
                _call(
                    "spawn",
                    {"target": f"profile:{CODING_PROFILE}", "payload": {"objective": LINES_PATH}},
                    result=child,
                ),
            ),
        )
    )
    other = await grader(
        CapabilityOutput(
            answer,
            (_call("spawn", {"target": "research", "payload": {"objective": LINES_PATH}}, child),),
        )
    )

    assert not nested.passed
    assert clean.passed
    assert not unrelayed.passed
    assert prefixed.passed
    assert not other.passed


async def test_nonzero_exit_case_wants_zero_not_a_failure_report() -> None:
    grader = BY_NAME["nonzero-exit-is-an-answer"].grader
    search = _call("bash", {"command": "grep -c FATAL /workspace/logs/app.log"}, "", True)

    answered = await grader(CapabilityOutput("ANSWER: 0", (search,)))
    reported = await grader(CapabilityOutput("The check failed with exit code: 1.", (search,)))
    unsearched = await grader(CapabilityOutput("ANSWER: 0", ()))

    assert answered.passed
    assert not reported.passed
    assert not unsearched.passed
