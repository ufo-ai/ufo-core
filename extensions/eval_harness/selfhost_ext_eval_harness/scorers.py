"""Graders over a CapabilityOutput. The answer graders (exact/numeric/predicate) read the final
text; the trajectory graders (required_tools/restraint/local_fs/skill/lane) read the agent's real
tool choices, not its prose — no LLM, no variance. `combine` ANDs several graders together for a
case that must satisfy more than one dimension (e.g. a correct answer AND a required trajectory).
Every grader FAILS on bad output."""

from __future__ import annotations

import re
from collections.abc import Callable

from selfhost_ext_eval_harness.capability import CapabilityOutput, CapabilityVerdict, Grader

ANSWER_TOLERANCE = 0.05

# selfhost's tool names: the web pack contributes search_web/fetch_url; the file/shell builtins are
# read/write/edit/bash plus the dedicated grep/glob search tools. Skills load via load_skill;
# delegation spawns via spawn_subagent.
WEB_TOOLS = ("search_web", "fetch_url")
LOCAL_FS_TOOLS = frozenset({"read", "write", "edit", "bash", "grep", "glob"})


def answer_text(text: str) -> str:
    marked = re.search(r"ANSWER:\s*(.+)", text, re.IGNORECASE)
    raw = marked.group(1) if marked else (text.strip().splitlines() or [""])[-1]
    return raw.replace("*", "").strip().strip("`\"' ").rstrip(".").lower()


def exact_scorer(expected: str) -> Grader:
    """Pass iff the agent's ANSWER equals `expected` exactly (case-insensitive)."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        answer = answer_text(output.response)
        if answer == expected.lower():
            return CapabilityVerdict(True, answer)
        return CapabilityVerdict(False, f"got {answer!r}, expected {expected!r}")

    return grade


def numeric_scorer(expected: float) -> Grader:
    """Pass iff the agent's final answer equals `expected` within `ANSWER_TOLERANCE`."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        text = output.response.replace(",", "")
        marked = re.search(r"ANSWER:\s*(-?\d+(?:\.\d+)?)", text, re.IGNORECASE)
        if marked:
            value = float(marked.group(1))
        else:
            numbers = re.findall(r"-?\d+(?:\.\d+)?", text)
            if not numbers:
                return CapabilityVerdict(False, f"no number in answer: {output.response[:80]!r}")
            value = float(numbers[-1])
        if abs(value - expected) <= ANSWER_TOLERANCE:
            return CapabilityVerdict(True, f"{value}")
        return CapabilityVerdict(False, f"got {value}, expected {expected}")

    return grade


Predicate = tuple[str, Callable[[str], bool]]


def predicate_scorer(checks: tuple[Predicate, ...]) -> Grader:
    """Pass iff every format predicate holds on the agent's terminal output."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        text = output.response.strip()
        failed = [desc for desc, predicate in checks if not _safe(predicate, text)]
        if failed:
            return CapabilityVerdict(False, "failed: " + "; ".join(failed))
        return CapabilityVerdict(True, f"{len(checks)}/{len(checks)} constraints met")

    return grade


def _safe(predicate: Callable[[str], bool], text: str) -> bool:
    try:
        return bool(predicate(text))
    except Exception:
        return False


def required_tools_scorer(
    required: tuple[str, ...], orderings: tuple[tuple[str, str], ...] = ()
) -> Grader:
    """Every tool in `required` ran, and each `(before, after)` ordering held."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        names = list(output.tools)
        missing = [tool for tool in required if tool not in names]
        if missing:
            return CapabilityVerdict(False, f"did not call: {', '.join(missing)}")
        for before, after in orderings:
            if names.index(before) > names.index(after):
                return CapabilityVerdict(False, f"{before} must precede {after}")
        return CapabilityVerdict(True, f"trajectory: {', '.join(names) or '(none)'}")

    return grade


def restraint_scorer(forbidden: tuple[str, ...]) -> Grader:
    """A question answerable from the model's own knowledge must NOT trigger `forbidden` tools."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        used = [tool for tool in output.tools if tool in forbidden]
        if used:
            return CapabilityVerdict(False, f"used unnecessary tool(s): {', '.join(used)}")
        return CapabilityVerdict(True, "answered without unnecessary tools")

    return grade


def local_fs_scorer() -> Grader:
    """A local-file task: the agent reaches for file/bash, not the web."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        names = output.tools
        if not any(tool in LOCAL_FS_TOOLS for tool in names):
            return CapabilityVerdict(
                False, f"used no local filesystem tool: {list(names) or '(none)'}"
            )
        web = [tool for tool in names if tool in WEB_TOOLS]
        if web:
            return CapabilityVerdict(False, f"reached for web on a local task: {', '.join(web)}")
        return CapabilityVerdict(True, f"local fs: {', '.join(names)}")

    return grade


def first_loaded_skill(output: CapabilityOutput) -> str | None:
    for call in output.calls:
        if call.name == "load_skill":
            name = call.input.get("name")
            return str(name) if name else None
    return None


def skill_scorer(expected: str, distractor: str) -> Grader:
    """Pass iff the agent's FIRST skill load was the matching skill (not the distractor)."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        first = first_loaded_skill(output)
        if first is None:
            return CapabilityVerdict(
                False, f"did not load matching skill {expected!r} (loaded: none)"
            )
        if first == distractor:
            return CapabilityVerdict(False, f"loaded the irrelevant skill {distractor!r} first")
        if first != expected:
            return CapabilityVerdict(False, f"loaded {first!r} first, expected {expected!r}")
        return CapabilityVerdict(True, f"loaded {expected!r}")

    return grade


def first_spawn_type(output: CapabilityOutput) -> str | None:
    for call in output.calls:
        if call.name == "spawn_subagent":
            kind = call.input.get("subagent_type") or call.input.get("profile")
            return str(kind) if kind else "subagent"
    return None


def lane_scorer(acceptable: frozenset[str]) -> Grader:
    """Pass iff the agent delegated and its chosen subagent type falls in `acceptable`."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        chosen = first_spawn_type(output)
        if chosen is None:
            return CapabilityVerdict(False, "did not delegate")
        if chosen in acceptable:
            return CapabilityVerdict(True, f"spawned {chosen!r}")
        return CapabilityVerdict(False, f"spawned {chosen!r}, expected one of {sorted(acceptable)}")

    return grade


def combine(*graders: Grader) -> Grader:
    """Pass iff every grader passes; the reason concatenates each grader's reason so a failure names
    which dimension (answer, trajectory, ...) fell short."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        verdicts = [await grader(output) for grader in graders]
        return CapabilityVerdict(
            all(verdict.passed for verdict in verdicts),
            "; ".join(verdict.reason for verdict in verdicts),
        )

    return grade
