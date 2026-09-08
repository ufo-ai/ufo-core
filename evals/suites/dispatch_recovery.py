"""Does the agent make the tool call the schema and the guards already describe?

Every case here is a bucket of real `tool.dispatch_failed` warnings from one production week
(service:ufo, status:warn, 2026-08-30 to 2026-09-06), sampled in env:testing and again in
env:prod. The failures are not model reasoning failures: the tool description states the rule, the
input schema names the field, and the refusal text carries the repair. What they measure is
whether a round reads them.

The buckets, and the case that stands for each:

- `ValueError: file ... must be read or named in a bash command before it is written/edited` — the
  write and edit guards. A call that reached the file by neither act is refused before anything
  lands, so the eval asks for a change to a staged file and grades the guard's silence, not the
  file's content.
- `ValidationError` naming a missing `target` on `SpawnInput` — the whole spawn input nested one
  level down, under `payload`. Graded on the wire shape of the call, which is where the fault is.
- `ValidationError` naming a missing `title` on `UpdateTodoListInput` — the same shape fault on a
  tool that costs a round to get wrong and nothing to get right.
- `ValueError: path ... escapes /workspace` — a path the member named outside the one tree a file
  tool reaches. The member's spelling is the trap, so the first refusal is the discovery and only a
  second is graded. env:prod adds more spellings of the same bucket — a home folder, the root
  `/`, and a `/skills/...` reference path — so a second case puts all three in one message.
- `ValueError: old_string not found` — an edit whose `old_string` is the member's paraphrase
  rather than the text in the file. The repair is a fresh read and an `old_string` taken from it,
  so the case grades the read between the miss and the change that lands.
- `exit code: 1` from `bash`, the highest-volume bucket by far. Most of it is a command whose
  nonzero exit IS the answer — `grep` matching nothing — so the case asks a question whose true
  answer is zero and grades the answer.

`SpeakerRequired` is the one confirmed bucket with no case here. It refuses a call that carries no
member authority, and every capability case runs as a member's own message, so the harness cannot
put the question. Its guidance is graded by `authority_handoff` instead.
"""

from __future__ import annotations

import json

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    ToolInvocation,
    WorkspaceFile,
)
from evals.harness.harness import Json, JsonObject
from evals.harness.scorers import answer_text

WRITE_GUARD = "must be read or named in a bash command before it is written"
EDIT_GUARD = "must be read or named in a bash command before it is edited"
ESCAPE_GUARD = "escapes /workspace"
MATCH_GUARD = "old_string not found"
VALIDATION_GUARD = "validation error"
WORKSPACE_ROOT = "/workspace/"
SPAWN_TOOL = "spawn"
TODO_TOOL = "update_todo_list"
CODING_PROFILE = "coding"

SUMMARY_PATH = "notes/summary.md"
SUMMARY_BODY = (
    "# Week 34\n\n"
    "- billing rework shipped\n"
    "- support volume rose after the launch\n"
    "- mobile app slipped to Q4\n"
)
SETTINGS_PATH = "config/settings.toml"
SETTINGS_BODY = 'name = "digest"\nretries = 2\nchannel = "#ops"\n'
OUTSIDE_PATH = "/home/oai/share/rows.csv"
ROWS_PATH = "inbox/rows.csv"
ROWS = ((1, 140), (2, 65), (3, 212), (4, 83))
ROWS_BODY = "id,amount\n" + "".join(f"{row},{amount}\n" for row, amount in ROWS)
ROWS_TOTAL = sum(amount for _, amount in ROWS)
LOG_PATH = "logs/app.log"
LOG_BODY = (
    "INFO booted\nWARN retry scheduled\nINFO drained queue\nWARN retry scheduled\nINFO idle\n"
)
LINES_PATH = "inputs/records.csv"
LINES_BODY = "".join(f"record-{index}\n" for index in range(37))
LINES_TOTAL = LINES_BODY.count("\n")
KIT_PATH = "skills/ufo-style/references/kit.md"
KIT_RADIUS = "12px"
KIT_BODY = f"# ufo style kit\n\nradius: {KIT_RADIUS}\nspacing: 8px\naccent: #2f6df6\n"
OUTSIDE_KIT_PATHS = ("/home/user/.ufo", "/", f"/{KIT_PATH}")
RELEASE_PATH = "notes/release.md"
RELEASE_BODY = "# Release 4.2\n\nStatus: draft\nOwner: Priya\nShip date: 2026-10-14\n"
RELEASE_QUOTED = "Status: Draft (pending review)"
RELEASE_WANTED = "Status: shipped"

FILES = (
    WorkspaceFile(path=SUMMARY_PATH, content=SUMMARY_BODY.encode()),
    WorkspaceFile(path=SETTINGS_PATH, content=SETTINGS_BODY.encode()),
    WorkspaceFile(path=ROWS_PATH, content=ROWS_BODY.encode()),
    WorkspaceFile(path=LOG_PATH, content=LOG_BODY.encode()),
    WorkspaceFile(path=LINES_PATH, content=LINES_BODY.encode()),
    WorkspaceFile(path=KIT_PATH, content=KIT_BODY.encode()),
    WorkspaceFile(path=RELEASE_PATH, content=RELEASE_BODY.encode()),
)


def _refused(calls: tuple[ToolInvocation, ...], marker: str) -> tuple[ToolInvocation, ...]:
    """The calls a guard refused, by the text the model read back."""
    return tuple(call for call in calls if call.is_error and marker in call.result.lower())


def _landed(calls: tuple[ToolInvocation, ...], path: str) -> tuple[ToolInvocation, ...]:
    """The write and edit calls that changed `path`."""
    return tuple(
        call
        for call in calls
        if call.name in ("write", "edit")
        and call.succeeded
        and str(call.input.get("file_path", "")).endswith(path)
    )


def _evidence(calls: tuple[ToolInvocation, ...], marker: str, path: str) -> JsonObject:
    refusals: list[Json] = [call.result[:200] for call in _refused(calls, marker)]
    landings: list[Json] = [call.name for call in _landed(calls, path)]
    return {"guard": marker, "refusals": refusals, "landed": landings}


def _reached_first_grader(marker: str, path: str, tool: str) -> Grader:
    """The change lands on `path` and the guard never refused a call on the way.

    The guard is stated in the tool's own description, so a refusal is a round that did not read
    the contract — not a discovery the case should tolerate. The rule takes either act that reaches
    the file, so a bash command naming it counts as the read does."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        evidence = _evidence(output.calls, marker, path)
        refusals = _refused(output.calls, marker)
        if refusals:
            return CapabilityVerdict(
                False, f"{len(refusals)} call(s) refused by the read guard", evidence
            )
        landed = _landed(output.calls, path)
        if not landed:
            return CapabilityVerdict(False, f"no write or edit landed on {path}", evidence)
        reached_before = any(
            call.succeeded
            and (
                (call.name == "read" and str(call.input.get("file_path", "")).endswith(path))
                or (call.name == "bash" and path in str(call.input.get("command", "")))
            )
            for call in output.calls[: output.calls.index(landed[0])]
        )
        if not reached_before:
            return CapabilityVerdict(False, f"{path} changed without a read of it", evidence)
        return CapabilityVerdict(True, f"reached then {landed[0].name} on {path}", evidence)

    return DescribedGrader(
        f"a successful `read` of {path}, or a bash command naming it, precedes the {tool} that "
        f"changes it, and no call is refused with {marker!r}",
        grade,
    )


def _workspace_path_grader(expected: str, tolerated: int) -> Grader:
    """The answer carries `expected`, the path guard refused at most `tolerated` calls, and the
    turn reached the file by a path under /workspace. The member named the file outside the
    workspace, so one refusal is the discovery; a second is the failure."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        refusals = _refused(output.calls, ESCAPE_GUARD)
        refused: list[Json] = [call.result[:200] for call in refusals]
        inside: list[Json] = [
            str(call.input.get("file_path", ""))
            for call in output.calls
            if call.succeeded and str(call.input.get("file_path", "")).startswith(WORKSPACE_ROOT)
        ]
        evidence: JsonObject = {"guard": ESCAPE_GUARD, "refusals": refused, "rewritten": inside}
        detail = f"{len(refusals)} call(s) refused outside the workspace"
        if expected not in output.response:
            return CapabilityVerdict(False, f"answer missing {expected} — {detail}", evidence)
        if len(refusals) > tolerated:
            return CapabilityVerdict(False, f"kept naming the outside path — {detail}", evidence)
        if not inside:
            return CapabilityVerdict(False, f"no path under /workspace — {detail}", evidence)
        return CapabilityVerdict(True, detail, evidence)

    return DescribedGrader(
        f"the answer carries {expected}, at most {tolerated} call is refused with "
        f"{ESCAPE_GUARD!r}, and the file is reached under /workspace",
        grade,
    )


def _matched_edit_grader(path: str, wanted: str) -> Grader:
    """The change lands the text the member asked for, and every `old_string not found` refusal is
    followed by a fresh read of the file before the change lands.

    The refusal says the text is not there, so the repair is the file's own text — a retry that
    guesses another wording is the production fault."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        evidence = _evidence(output.calls, MATCH_GUARD, path)
        landed = _landed(output.calls, path)
        if not landed:
            return CapabilityVerdict(False, f"no write or edit landed on {path}", evidence)
        change = landed[-1]
        body = str(change.input.get("new_string", change.input.get("content", "")))
        if wanted not in body:
            return CapabilityVerdict(False, f"the change does not carry {wanted!r}", evidence)
        misses = _refused(output.calls, MATCH_GUARD)
        if misses:
            between = output.calls[output.calls.index(misses[-1]) : output.calls.index(change)]
            reread = any(
                call.name == "read"
                and call.succeeded
                and str(call.input.get("file_path", "")).endswith(path)
                for call in between
            )
            if not reread:
                return CapabilityVerdict(False, f"{path} was not read after the miss", evidence)
        return CapabilityVerdict(True, f"{change.name} landed {wanted!r} on {path}", evidence)

    return DescribedGrader(
        f"a write or edit lands {wanted!r} on {path}, and any call refused with {MATCH_GUARD!r} is "
        f"followed by a successful read of {path} before the change lands",
        grade,
    )


def _payload_shape_grader(tool: str, fields: tuple[str, ...]) -> Grader:
    """The first call to `tool` validated, and it carried `fields` at the top level of its input.

    A nested payload is the production fault: the whole input one level down, so the field the
    schema requires reads as absent. Grading the first call is what makes this a schema-reading
    measure rather than a retry-count measure."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        calls = tuple(call for call in output.calls if call.name == tool)
        if not calls:
            return CapabilityVerdict(False, f"never called {tool}")
        first = calls[0]
        keys: list[Json] = [str(key) for key in sorted(first.input)]
        evidence: JsonObject = {
            "tool": tool,
            "attempts": len(calls),
            "first_input_keys": keys,
            "first_result": first.result[:300],
        }
        if first.is_error and VALIDATION_GUARD in first.result.lower():
            return CapabilityVerdict(False, f"the first {tool} payload was rejected", evidence)
        missing = [field for field in fields if field not in first.input]
        if missing:
            return CapabilityVerdict(
                False, f"the first {tool} input omits {', '.join(missing)}", evidence
            )
        if not first.succeeded:
            return CapabilityVerdict(False, f"the first {tool} call did not complete", evidence)
        return CapabilityVerdict(True, f"{tool} validated on its first call", evidence)

    return DescribedGrader(
        f"the first {tool} call carries {', '.join(fields)} at the top level of its input and "
        "completes",
        grade,
    )


def _coding_spawn_grader() -> Grader:
    """One coding-profile spawn whose `target` is a top-level field, and the child's count in the
    answer. The catalog lists the profile under its bare name and the runtime resolves that name
    across both namespaces, so `profile:coding` and `coding` are the same target here."""

    shape = _payload_shape_grader(SPAWN_TOOL, ("target", "payload"))

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        verdict = await shape(output)
        if not verdict.passed:
            return verdict
        first = next(call for call in output.calls if call.name == SPAWN_TOOL)
        evidence = dict(verdict.evidence)
        target = first.input.get("target")
        evidence["target"] = json.dumps(target)
        if not isinstance(target, str) or target.removeprefix("profile:") != CODING_PROFILE:
            return CapabilityVerdict(
                False, f"the spawn target is not the {CODING_PROFILE} profile", evidence
            )
        payload = first.input.get("payload")
        if not isinstance(payload, dict) or "target" in payload:
            return CapabilityVerdict(False, "the spawn payload holds the target", evidence)
        if str(LINES_TOTAL) not in output.response:
            return CapabilityVerdict(False, f"answer missing {LINES_TOTAL}", evidence)
        return CapabilityVerdict(True, f"one {CODING_PROFILE} spawn, count relayed", evidence)

    return DescribedGrader(
        f"one spawn carries the {CODING_PROFILE} profile as a top-level `target`, keeps no target "
        f"inside `payload`, and the answer carries {LINES_TOTAL}",
        grade,
    )


def _zero_answer_grader() -> Grader:
    """A search that matches nothing is answered as zero, not reported as a failed command."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        shells = tuple(call for call in output.calls if call.name in ("bash", "grep"))
        evidence: JsonObject = {
            "shell_calls": len(shells),
            "answer": answer_text(output.response),
        }
        if not shells:
            return CapabilityVerdict(False, "never searched the file", evidence)
        answer = answer_text(output.response)
        if answer not in ("0", "zero", "none"):
            return CapabilityVerdict(False, f"answered {answer!r}, expected zero", evidence)
        return CapabilityVerdict(True, f"answered {answer!r} from a nonzero exit", evidence)

    return DescribedGrader("the final answer is zero, not a report of a failed command", grade)


CASES = (
    CapabilityCase(
        name="write-reads-first",
        message=(
            f"Replace the whole body of /workspace/{SUMMARY_PATH} with the heading and one line: "
            "`- billing rework shipped`. Keep nothing else."
        ),
        workspace_files=FILES,
        grader=_reached_first_grader(WRITE_GUARD, SUMMARY_PATH, "write"),
        digest_tag="dispatch:write-reads-first",
    ),
    CapabilityCase(
        name="edit-reads-first",
        message=f"In /workspace/{SETTINGS_PATH}, set retries to 5. Change nothing else.",
        workspace_files=FILES,
        grader=_reached_first_grader(EDIT_GUARD, SETTINGS_PATH, "edit"),
        digest_tag="dispatch:edit-reads-first",
    ),
    CapabilityCase(
        name="paths-stay-in-the-workspace",
        message=(
            f"Total the amount column of the rows file I left at {OUTSIDE_PATH} — I may have "
            "dropped it in the workspace inbox instead. Give the single total."
        ),
        workspace_files=FILES,
        grader=_workspace_path_grader(str(ROWS_TOTAL), tolerated=1),
        digest_tag="dispatch:paths-stay-in-the-workspace",
    ),
    CapabilityCase(
        name="outside-paths-are-rewritten",
        message=(
            f"What radius does my ufo style kit use? I keep it at {OUTSIDE_KIT_PATHS[2]}, my old "
            f"copy sits in {OUTSIDE_KIT_PATHS[0]}, and you can search from {OUTSIDE_KIT_PATHS[1]} "
            "if you have to. Reply exactly `ANSWER: <radius>`."
        ),
        workspace_files=FILES,
        grader=_workspace_path_grader(KIT_RADIUS, tolerated=1),
        digest_tag="dispatch:outside-paths-are-rewritten",
    ),
    CapabilityCase(
        name="todo-list-payload",
        message=(
            "Create a task list titled Launch with two pending tasks: Prepare the launch brief, "
            "and Book the review slot."
        ),
        grader=_payload_shape_grader(TODO_TOOL, ("title", "tasks")),
        digest_tag="dispatch:todo-list-payload",
    ),
    CapabilityCase(
        name="spawn-payload-shape",
        message=(
            f"Delegate this to the coding subagent and report what it says: count the lines in "
            f"/workspace/{LINES_PATH} and give the number. No repository clone is needed."
        ),
        workspace_files=FILES,
        grader=_coding_spawn_grader(),
        digest_tag="dispatch:spawn-payload-shape",
    ),
    CapabilityCase(
        name="edit-targets-the-text-that-exists",
        message=(
            f"In /workspace/{RELEASE_PATH}, replace the line `{RELEASE_QUOTED}` with "
            f"`{RELEASE_WANTED}`. Change nothing else."
        ),
        workspace_files=FILES,
        grader=_matched_edit_grader(RELEASE_PATH, RELEASE_WANTED),
        digest_tag="dispatch:edit-targets-the-text-that-exists",
    ),
    CapabilityCase(
        name="nonzero-exit-is-an-answer",
        message=(
            f"How many lines of /workspace/{LOG_PATH} contain FATAL? Reply exactly "
            "`ANSWER: <count>`."
        ),
        workspace_files=FILES,
        grader=_zero_answer_grader(),
        digest_tag="dispatch:nonzero-exit-is-an-answer",
    ),
)
