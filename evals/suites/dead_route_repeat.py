"""Does an agent stop trying an approach that already failed?

RFC 0025 rests on a premise nothing has measured: that work loses the record of what was already
tried, so a route is attempted again after it has been shown to fail. This suite measures the
weakest form of that claim — one agent, one context, several independent units of work, each
carrying the same trap. Here the evidence is in front of the agent, so a repeat is not a durability
failure but an attention failure, and the count is the floor a durable registry has to beat.

Each case stages files whose obvious first approach fails deterministically, and asks a question
that needs all of them parsed. The grader counts how many distinct units the dead route was
attempted on, and each case sets how many of those attempts it tolerates. Where the agent has to
discover the trap, the first attempt is the discovery and only a further one is a repeat; where the
transcript already reports the failure, the first attempt is already the repeat. The answer must
also be right, so an agent that dodges the route by skipping the work fails.

What the first run established, and it is a negative result: the trap never fired. Both cases
scored 0 attempts on the dead route out of 4 and 3 units (run 7b1ce2a1, 2026-08-02). The trajectory
shows why — the agent opened all four files with `read` before parsing anything, saw the
one-object-per-line structure on the page, and wrote a line-by-line parser in a single call. An
approach whose failure is visible on inspection is never taken, so it can never be repeated, and a
suite built on one measures nothing about repetition however green it reads.

Two conclusions follow, and they bound where a durable registry could pay. Within one context the
claim is close to unmeasurable in principle: the failure sits in the transcript the agent is
already reading, so "did it carry the failure forward" collapses into "did it read its own
context", which it does. The claim only has force where the failure is *outside* the context — a
sibling subagent repeating what another already disproved, or a repeat after rollover drops the
failing round. Neither is measurable through this seam: `SubagentHandoff`
(`evals/harness/handoff.py:40-54`) carries counts and a conversation id, never the child's tool
trajectory, so a grader cannot see what a sibling tried. That instrument — a grader reading child
turns by `parent_turn_id` and their transcripts — is the prerequisite for measuring RFC 0025's
premise at all.

The cases stay as the control they turned out to be: they hold the line that an inspectable trap is
not evidence, so a later cross-context suite cannot quietly reuse one.
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

QUARTERS = ("q1", "q2", "q3", "q4")
STATUSES = ("ok", "failed", "ok", "failed", "pending", "failed")
JSON_LOAD = "json.load("
JSON_LOADS = "json.loads("


def _jsonl(quarter: str, rows: int) -> WorkspaceFile:
    """A `.json` file that is really JSON Lines, so `json.load` raises on the second object."""
    lines = [
        json.dumps({"id": f"{quarter}-{index:03d}", "status": STATUSES[index % len(STATUSES)]})
        for index in range(rows)
    ]
    return WorkspaceFile(path=f"reports/{quarter}.json", content="\n".join(lines).encode())


REPORT_ROWS = {"q1": 12, "q2": 18, "q3": 9, "q4": 15}
Q1_FAILED = sum(1 for i in range(REPORT_ROWS["q1"]) if STATUSES[i % len(STATUSES)] == "failed")
REPORT_FILES = tuple(_jsonl(quarter, rows) for quarter, rows in REPORT_ROWS.items())
FAILED_TOTAL = sum(
    1
    for quarter, rows in REPORT_ROWS.items()
    for index in range(rows)
    if STATUSES[index % len(STATUSES)] == "failed"
)


def _dead_route_units(
    calls: tuple[ToolInvocation, ...], marker: str, units: tuple[str, ...]
) -> set[str]:
    """The units named by a failed call that used `marker`. A call naming several units counts for
    each, so one sweep over every file is one attempt per unit it actually touched."""
    touched: set[str] = set()
    for call in calls:
        text = json.dumps(call.input)
        if not call.is_error or marker not in text.replace(JSON_LOADS, ""):
            continue
        touched.update(unit for unit in units if unit in text)
    return touched


def _tolerance(marker: str, units: tuple[str, ...], tolerated: int) -> str:
    route = marker.removesuffix("(")
    if tolerated == 0:
        return f"{route} is not attempted on any of the {len(units)} files"
    return f"{route} failed on at most {tolerated} of the {len(units)} files"


def _repeat_grader(
    marker: str, units: tuple[str, ...], answer: str, tolerated: int, because: str
) -> Grader:
    """`tolerated` is how many units may carry a failed `marker` call before the case fails, and it
    writes its own clause of the recorded criterion so the two cannot say different things."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        repeated = _dead_route_units(output.calls, marker, units)
        worked = {
            unit
            for call in output.calls
            if call.succeeded
            for unit in units
            if unit in json.dumps(call.input) or unit in call.result
        }
        named: list[Json] = [*units]
        hit: list[Json] = [*sorted(repeated)]
        done: list[Json] = [*sorted(worked)]
        evidence: JsonObject = {
            "dead_route": marker,
            "units": named,
            "repeated_on": hit,
            "worked_on": done,
        }
        detail = f"{marker!r} failed on {len(repeated)}/{len(units)} units: {sorted(repeated)}"
        if answer not in output.response:
            return CapabilityVerdict(False, f"answer missing {answer!r} — {detail}", evidence)
        missing = sorted(set(units) - worked)
        if missing:
            return CapabilityVerdict(
                False, f"no succeeding call touched {missing} — {detail}", evidence
            )
        if len(repeated) > tolerated:
            return CapabilityVerdict(False, f"repeated after a failure — {detail}", evidence)
        return CapabilityVerdict(True, detail, evidence)

    statement = f"The total is correct and {_tolerance(marker, units, tolerated)}: {because}"
    return DescribedGrader(grading=statement, grade=grade)


CASES = (
    CapabilityCase(
        name="jsonl_masquerade_four_files",
        message=(
            "The four files in /workspace/reports are quarterly record dumps. How many records "
            "across all four have status failed? Give the single total."
        ),
        workspace_files=REPORT_FILES,
        grader=_repeat_grader(
            JSON_LOAD,
            QUARTERS,
            str(FAILED_TOTAL),
            tolerated=1,
            because=(
                "the first JSONDecodeError is the discovery, and after it the agent must not "
                "parse a later file the same way."
            ),
        ),
    ),
    CapabilityCase(
        name="jsonl_masquerade_after_seen_failure",
        message=(
            "Now count the other three files in /workspace/reports too, and give me the single "
            "total across all four."
        ),
        prior_messages=(
            "Count the records with status failed in /workspace/reports/q1.json.",
            f"{Q1_FAILED} records. json.load raised JSONDecodeError: Extra data — the file is "
            "JSON Lines, one "
            "object per line, so I parsed it line by line instead.",
        ),
        workspace_files=REPORT_FILES,
        grader=_repeat_grader(
            JSON_LOAD,
            ("q2", "q3", "q4"),
            str(FAILED_TOTAL),
            tolerated=0,
            because=(
                "the transcript already states the file format and the failure it caused, so a "
                "first attempt on a remaining file is already the repeat."
            ),
        ),
    ),
)
