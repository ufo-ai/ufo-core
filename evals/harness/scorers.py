"""Graders over a CapabilityOutput. The answer graders (exact/numeric/predicate) read the final
text; the trajectory graders (required_tools/restraint/local_fs/skill/lane) read the agent's real
tool choices, not its prose — no LLM, no variance. `combine` ANDs several graders together for a
case that must satisfy more than one dimension (e.g. a correct answer AND a required trajectory).
Every grader FAILS on bad output."""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from evals.harness.artifact_checks import (
    ArtifactCheck,
    board_presentation,
    forecast_workbook,
    site_archive,
)
from evals.harness.capability import (
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    SharedArtifact,
    grading_statement,
)

ANSWER_TOLERANCE = 0.05

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

    return DescribedGrader(f"the final ANSWER equals {expected!r} (case-insensitive)", grade)


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

    return DescribedGrader(
        f"the final numeric answer equals {expected:g} within ±{ANSWER_TOLERANCE:g}", grade
    )


Predicate = tuple[str, Callable[[str], bool]]


def predicate_scorer(checks: tuple[Predicate, ...]) -> Grader:
    """Pass iff every format predicate holds on the agent's terminal output."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        text = output.response.strip()
        failed = [desc for desc, predicate in checks if not _safe(predicate, text)]
        if failed:
            return CapabilityVerdict(False, "failed: " + "; ".join(failed))
        return CapabilityVerdict(True, f"{len(checks)}/{len(checks)} constraints met")

    return DescribedGrader(
        "the final text satisfies: " + "; ".join(desc for desc, _ in checks), grade
    )


def _safe(predicate: Callable[[str], bool], text: str) -> bool:
    try:
        return bool(predicate(text))
    except Exception:
        return False


def required_tools_scorer(
    required: tuple[str, ...], orderings: tuple[tuple[str, str], ...] = ()
) -> Grader:
    """Every required tool completed successfully, and each requested ordering held."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        names = [call.name for call in output.calls if call.succeeded]
        missing = [tool for tool in required if tool not in names]
        if missing:
            return CapabilityVerdict(False, f"did not complete successfully: {', '.join(missing)}")
        for before, after in orderings:
            if names.index(before) > names.index(after):
                return CapabilityVerdict(False, f"{before} must precede {after}")
        return CapabilityVerdict(True, f"trajectory: {', '.join(names) or '(none)'}")

    ordered = "".join(f"; {before} precedes {after}" for before, after in orderings)
    return DescribedGrader(f"{', '.join(required)} complete(s) successfully{ordered}", grade)


def restraint_scorer(forbidden: tuple[str, ...]) -> Grader:
    """A question answerable from the model's own knowledge must NOT trigger `forbidden` tools."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        used = [tool for tool in output.tools if tool in forbidden]
        if used:
            return CapabilityVerdict(False, f"used unnecessary tool(s): {', '.join(used)}")
        return CapabilityVerdict(True, "answered without unnecessary tools")

    return DescribedGrader(f"answers without invoking {', '.join(forbidden)}", grade)


def local_fs_scorer() -> Grader:
    """A local-file task: a file/bash call succeeds and no web call is attempted."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        successful = [call.name for call in output.calls if call.succeeded]
        if not any(tool in LOCAL_FS_TOOLS for tool in successful):
            return CapabilityVerdict(
                False, f"completed no local filesystem tool: {successful or '(none)'}"
            )
        web = [tool for tool in output.tools if tool in WEB_TOOLS]
        if web:
            return CapabilityVerdict(False, f"reached for web on a local task: {', '.join(web)}")
        return CapabilityVerdict(True, f"local fs: {', '.join(successful)}")

    return DescribedGrader(
        "a local file/shell tool completes successfully and no web tool is attempted", grade
    )


def skill_scorer(expected: str, distractor: str) -> Grader:
    """Pass iff the first skill load succeeds and matches the task instead of its distractor."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        first = next((call for call in output.calls if call.name == "load_skill"), None)
        if first is None:
            return CapabilityVerdict(False, f"did not load matching skill {expected!r}")
        loaded = first.input.get("name")
        name = str(loaded) if loaded else None
        if name == distractor:
            return CapabilityVerdict(False, f"loaded the distractor {distractor!r} first")
        if name != expected:
            return CapabilityVerdict(False, f"loaded {name!r} first, expected {expected!r}")
        if not first.has_result:
            return CapabilityVerdict(False, f"skill {expected!r} produced no result")
        if first.is_error:
            return CapabilityVerdict(False, f"skill {expected!r} failed: {first.result[:120]}")
        return CapabilityVerdict(True, f"loaded {expected!r}")

    return DescribedGrader(
        f"the first load_skill loads {expected!r} (not the distractor {distractor!r}) and succeeds",
        grade,
    )


ArtifactValidator = Callable[[bytes], ArtifactCheck]


def shared_artifact_scorer(
    suffix: str, validate: ArtifactValidator | None = None, expectation: str = ""
) -> Grader:
    """Pass iff `share_file` succeeds and its durable artifact passes optional inspection."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        if output.artifact_error:
            return CapabilityVerdict(False, f"artifact inspection failed: {output.artifact_error}")
        delivered = _delivered_artifact(output, suffix)
        if delivered is None:
            return CapabilityVerdict(False, f"did not successfully share a {suffix} artifact")
        if validate is None:
            return CapabilityVerdict(True, f"shared {delivered.name}")
        checked = validate(delivered.content)
        return CapabilityVerdict(checked.passed, f"{delivered.name}: {checked.reason}")

    return DescribedGrader(
        f"share_file delivers a durable {suffix} artifact"
        + (f" whose inspection confirms {expectation}" if expectation else ""),
        grade,
    )


def site_archive_scorer(expected_heading: str) -> Grader:
    return shared_artifact_scorer(
        ".tar.gz",
        lambda content: site_archive(content, expected_heading),
        expectation=f"a site whose sole h1 heading is {expected_heading!r}",
    )


def forecast_workbook_scorer(expected_revenue: tuple[int, ...]) -> Grader:
    return shared_artifact_scorer(
        ".xlsx",
        lambda content: forecast_workbook(content, expected_revenue),
        expectation=(
            "a formula-driven forecast workbook over revenue "
            f"{', '.join(map(str, expected_revenue))}"
        ),
    )


def board_presentation_scorer(expected_slides: int, expected_revenue: tuple[int, ...]) -> Grader:
    return shared_artifact_scorer(
        ".pptx",
        lambda content: board_presentation(content, expected_slides, expected_revenue),
        expectation=(
            f"a {expected_slides}-slide deck with chart and notes over revenue "
            f"{', '.join(map(str, expected_revenue))}"
        ),
    )


def _delivered_artifact(output: CapabilityOutput, suffix: str) -> SharedArtifact | None:
    artifacts = {artifact.name: artifact for artifact in output.artifacts}
    for call in output.calls:
        if call.name != "share_file" or not call.succeeded:
            continue
        try:
            payload = json.loads(call.result)
        except json.JSONDecodeError:
            continue
        match payload:
            case {"name": str() as name} if name.lower().endswith(suffix.lower()):
                if artifact := artifacts.get(name):
                    return artifact
    return None


def lane_scorer(acceptable: frozenset[str]) -> Grader:
    """Pass iff the first delegation succeeds in an acceptable subagent lane."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        first = next((call for call in output.calls if call.name == "spawn_subagent"), None)
        if first is None:
            return CapabilityVerdict(False, "did not delegate")
        chosen_raw = first.input.get("subagent_type") or first.input.get("profile")
        chosen = str(chosen_raw) if chosen_raw else "subagent"
        if not first.has_result:
            return CapabilityVerdict(False, f"{chosen!r} delegation produced no result")
        if first.is_error:
            return CapabilityVerdict(False, f"{chosen!r} delegation failed: {first.result[:120]}")
        if chosen in acceptable:
            return CapabilityVerdict(True, f"spawned {chosen!r}")
        return CapabilityVerdict(False, f"spawned {chosen!r}, expected one of {sorted(acceptable)}")

    return DescribedGrader(
        f"the first spawn_subagent delegation succeeds in one of {sorted(acceptable)}", grade
    )


def combine(*graders: Grader) -> Grader:
    """Pass iff every grader passes; the reason concatenates each grader's reason so a failure names
    which dimension (answer, trajectory, ...) fell short."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        verdicts = [await grader(output) for grader in graders]
        return CapabilityVerdict(
            all(verdict.passed for verdict in verdicts),
            "; ".join(verdict.reason for verdict in verdicts),
        )

    return DescribedGrader(
        "; ".join(statement for statement in map(grading_statement, graders) if statement), grade
    )
