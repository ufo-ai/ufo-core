"""Register cases: the reply's shape must track the exchange, not a constant default.

Each case seeds a thread with `prior_messages` and grades the closing text's measured shape —
words, lines, headers, bullet lines — against the register the turn earns. One case per register
the shell declares, run in opposing pairs so a suite score cannot be bought by going uniformly
terse or uniformly structured. Each dispute and report crosses one boundary: chat carries a short
standalone summary, while one Markdown report linked from the reply — never sent as
a file, because the ask named none — carries the structured detail. Two cases flip register
mid-thread — an acknowledgement after a report, an analysis after banter — because the register is
chosen per turn, never inherited from the thread. One case arrives with other people @-mentioned
beside the ask, because a mention of a colleague does not move who the message is addressed to:
the turn still owes the member the answer, and reading the mentions as the address leaves the
member with no reply at all.

Sending that report as a file is decided by the trigger in the ask and by nothing else, so the
cases sit on both sides of the line: an ask that names a file has to arrive through share_file,
while an ask that only says "send me" or "give me" leaves the same report linked. A suite that
graded one side alone would score highest on a turn that always shares or never does. One further
case grades a chat register against the workspace, because a discuss reply that quietly writes a
report satisfies its word budget while breaking the rule that an ack, answer, or discuss delivery
has no report.

Where a case asks about a shipped change whose note claims the opposite of what its code does,
shape is only half of it: the rubric there passes the reply that took the precedence off the
code rather than the note, settled the yes-or-no premise in its first sentence, and stayed in the
behavior a member can observe instead of the code's own names for its parts. Grounding and
vocabulary are not measurable shape, so the rubric carries them.

A seeded assistant turn asserts nothing the live agent could not know without tools, and never
contradicts the message it precedes. The agent reads those turns as its own: give it a fact it
could not have had and it spends the turn retracting it, give it a position the new message
overrides and it correctly disputes instead of acknowledging. Either way the case stops measuring
register. The chat cases carry `samples=3` because a single reply's length swings wider than the
effect any prompt change produces.

Every run needs a workspace no earlier run touched. The cases carry decisions and open questions
that read as durable facts, the agent stores them, and the next run recalls them: it acknowledges
a decision it already holds and answers a question it has already worked through, so replies
shorten with run order rather than with the prompt. Two runs are comparable only when each began
from an empty workspace.

Delegation cases also record the child's completed model-round output. Intermediate output is every
completed round before each child turn's final round. A missing round or usage record fails the
case instead of reporting a partial low count."""

import json
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from ufo_ext_coding.manifest import SKILLS_ROOT as CODING_SKILLS_ROOT
from ufo_ext_research.manifest import SKILLS_ROOT as RESEARCH_SKILLS_ROOT
from ufo_ext_scheduled_tasks.runner import REPORT_INSTRUCTION

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    WorkspaceFile,
    shared_file_names,
    written_markdown,
)
from evals.harness.harness import JsonObject
from ufo.runtime.skills.runtime import LoadedSkill, loaded_context, parse_skill
from ufo.sdk.models import Message, ToolResultBlock, ToolUseBlock

HEADER_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s+\S|\*\*[^*\n]{1,60}\*\*:?\s*$)", re.MULTILINE)
BULLET_RE = re.compile(r"^\s{0,3}(?:[-*•]\s+\S|\d{1,2}[.)]\s+\S)", re.MULTILINE)
BULLET_MARKER_RE = re.compile(r"^\s{0,3}(?:[-*•]|\d{1,2}[.)])\s+")
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,}).*?(?:^\s{0,3}\1\s*$|\Z)", re.MULTILINE | re.DOTALL)
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]\n]+\]\(\s*(?P<target><[^>\n]+>|[^)\s]+)")
HTTP_URL_RE = re.compile(r"https?://", re.IGNORECASE)
WORKSPACE_PATH_RE = re.compile(r"/workspace(?:/[A-Za-z0-9._/-]+)?")
REPORT_GLOB = "*.md"
DELEGATED_TASK = "delegated_response_register"
DELEGATED_CLOSING_MAX_CHARS = 400
DELEGATED_DUPLICATION_MAX = 0.3


@dataclass(frozen=True)
class Shape:
    """The measured shape of one reply. A bold-only line counts as a header: a pseudo-header
    imposes the same reading cost as a real one, so both fail a chat register. Each bullet's own
    length is recorded separately from the counts, because only a grader that sets a bullet-length
    floor reads it: two replies of the same counts are the same shape."""

    words: int
    lines: int
    headers: int
    bullets: int
    bullet_words: tuple[int, ...] = field(default=(), compare=False)

    @property
    def evidence(self) -> JsonObject:
        return {
            "words": self.words,
            "lines": self.lines,
            "headers": self.headers,
            "bullets": self.bullets,
        }

    @property
    def bullet_evidence(self) -> JsonObject:
        return self.evidence | {"bulletWords": list(self.bullet_words)}


@dataclass(frozen=True)
class SubagentGeneration:
    """Completed child model-round output, split between intermediate and delivered output."""

    turns: int
    rounds: int
    output_tokens: int
    intermediate_output_tokens: int

    @property
    def evidence(self) -> JsonObject:
        return {
            "turns": self.turns,
            "rounds": self.rounds,
            "outputTokens": self.output_tokens,
            "intermediateOutputTokens": self.intermediate_output_tokens,
            "finalOutputTokens": self.output_tokens - self.intermediate_output_tokens,
            "intermediateShare": (
                self.intermediate_output_tokens / self.output_tokens if self.output_tokens else 0.0
            ),
        }


def _subagent_generation(output: CapabilityOutput) -> tuple[SubagentGeneration | None, str]:
    if output.timing is None:
        return None, "subagent generation was not measured"
    if output.timing.error:
        return None, f"subagent generation was not measured: {output.timing.error}"
    turns = tuple(turn for turn in output.timing.turns if turn.role == "child")
    if not turns:
        return None, "subagent generation has no child turns"
    incomplete = next(
        (
            turn
            for turn in turns
            if turn.output_tokens is None or turn.intermediate_output_tokens is None
        ),
        None,
    )
    if incomplete is not None:
        return None, f"subagent generation for child turn {incomplete.turn_id} lacks output usage"
    return (
        SubagentGeneration(
            turns=len(turns),
            rounds=sum(turn.rounds for turn in turns),
            output_tokens=sum(
                turn.output_tokens for turn in turns if turn.output_tokens is not None
            ),
            intermediate_output_tokens=sum(
                turn.intermediate_output_tokens
                for turn in turns
                if turn.intermediate_output_tokens is not None
            ),
        ),
        "",
    )


def measure(text: str) -> Shape:
    """Words and lines count the whole reply; headers and bullets count only outside fenced code,
    where a `#` comment or a diff's `-` line carries no document structure. A bullet's own length
    excludes its marker, so a one-word item counts as one word."""
    prose = FENCE_RE.sub("", text)
    bullets = [line for line in prose.splitlines() if BULLET_RE.match(line)]
    return Shape(
        words=len(text.split()),
        lines=len([line for line in text.splitlines() if line.strip()]),
        headers=len(HEADER_RE.findall(prose)),
        bullets=len(bullets),
        bullet_words=tuple(len(BULLET_MARKER_RE.sub("", line).split()) for line in bullets),
    )


def _unreachable_markdown_targets(output: CapabilityOutput) -> tuple[str, ...]:
    shared_urls: set[str] = set()
    for call in output.calls:
        if call.name != "share_file" or not call.succeeded:
            continue
        try:
            payload = json.loads(call.result)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, list):
            shared_urls.update(
                entry["url"]
                for entry in payload
                if isinstance(entry, dict) and isinstance(entry.get("url"), str)
            )
    prose = FENCE_RE.sub("", output.response)
    targets = tuple(match.group("target").strip("<>") for match in MARKDOWN_LINK_RE.finditer(prose))
    return tuple(
        target
        for target in targets
        if HTTP_URL_RE.match(target) is None and target not in shared_urls
    )


def _workspace_paths(text: str) -> tuple[str, ...]:
    return tuple(
        path.rstrip(".,;:!?") for path in WORKSPACE_PATH_RE.findall(FENCE_RE.sub("", text))
    )


def conversational_scorer(max_words: int, max_lines: int) -> Grader:
    """A chat-register reply: inside the word and line budget, no headers, no bullet list."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        shape = measure(output.response.strip())
        failures = []
        unreachable_targets = _unreachable_markdown_targets(output)
        workspace_paths = _workspace_paths(output.response)
        if shape.words > max_words:
            failures.append(f"{shape.words} words over the {max_words} budget")
        if shape.lines > max_lines:
            failures.append(f"{shape.lines} lines over the {max_lines} budget")
        if shape.headers:
            failures.append(f"{shape.headers} section headers")
        if shape.bullets:
            failures.append(f"{shape.bullets} bullet lines")
        if unreachable_targets:
            failures.append(
                "member-unreachable Markdown targets: " + ", ".join(unreachable_targets)
            )
        if workspace_paths:
            failures.append("member-inaccessible workspace paths: " + ", ".join(workspace_paths))
        evidence: JsonObject = shape.evidence | {
            "unreachableMarkdownTargets": list(unreachable_targets),
            "workspacePaths": list(workspace_paths),
        }
        if failures:
            return CapabilityVerdict(False, "report register: " + ", ".join(failures), evidence)
        return CapabilityVerdict(True, f"chat register: {shape.words} words", evidence)

    return DescribedGrader(
        f"a chat-register reply: at most {max_words} words and {max_lines} lines, "
        "no section headers, no bullet list",
        grade,
    )


def unwritten_reply_scorer(max_words: int, max_lines: int) -> Grader:
    """A chat-register reply that produced no report at all.

    `conversational_scorer` reads the reply and nothing else, so a turn that answered a discuss ask
    in eighty words and also wrote and sent a report passed it. An ack, answer, or discuss delivery
    has no report, and the workspace and the share calls are where that shows."""
    conversational = conversational_scorer(max_words, max_lines)

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        verdict = await conversational(output)
        failures = [] if verdict.passed else [verdict.reason]
        written = written_markdown(output, REPORT_GLOB)
        shared = tuple(name for call in output.calls for name in shared_file_names(call))
        delivered = max(len(shared), len(output.artifacts))
        if written:
            failures.append(f"wrote {len(written)} Markdown reports for a chat-register reply")
        if delivered:
            failures.append(f"shared {delivered} files for an ask that named none")
        evidence: JsonObject = verdict.evidence | {
            "writtenReports": len(written),
            "sharedFiles": delivered,
        }
        if failures:
            return CapabilityVerdict(False, "unwritten reply: " + ", ".join(failures), evidence)
        return CapabilityVerdict(True, f"{verdict.reason}, nothing written or shared", evidence)

    return DescribedGrader(
        f"a chat-register reply: at most {max_words} words and {max_lines} lines, no section "
        "headers, no bullet list, with no Markdown report written to the workspace and no file "
        "shared",
        grade,
    )


def addressed_reply_scorer(min_words: int, max_words: int, max_lines: int) -> Grader:
    """A chat-register reply to a member message that also @-mentions other people.

    `conversational_scorer` caps length and nothing else, so a turn that read the message as
    addressed to the named colleagues and left a few words of deferral passed it. The floor is
    what separates an answer to the member from a step aside."""
    conversational = conversational_scorer(max_words, max_lines)

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        verdict = await conversational(output)
        shape = measure(output.response.strip())
        failures = [] if verdict.passed else [verdict.reason]
        if shape.words < min_words:
            failures.append(f"{shape.words} words under the {min_words} floor")
        if failures:
            return CapabilityVerdict(
                False, "addressed reply: " + ", ".join(failures), verdict.evidence
            )
        return CapabilityVerdict(True, verdict.reason, verdict.evidence)

    return DescribedGrader(
        f"a chat-register reply to the member: at least {min_words} and at most {max_words} "
        f"words, at most {max_lines} lines, no section headers, no bullet list",
        grade,
    )


def carried_report_scorer(
    summary_min_words: int,
    summary_max_words: int,
    summary_max_lines: int,
    report_min_words: int,
    report_min_headers: int,
    expected_name: str = "",
    expected_body: bytes = b"",
) -> Grader:
    """A standalone chat summary, plus one Markdown report the reply carried in a Markdown link
    and no file sent.

    The report keeps the word and header floors, because the floors are what stops brevity from
    paying: a dispute clipped to its verdict and an analysis clipped to a stub both still fail. The
    summary keeps its own budget, so the substance cannot move into the message instead. A file
    shared for an ask that named none fails the case, and so does a reply that carried no report:
    the surface has nothing to offer beside it. `expected_name` pins the name a revision reuses;
    `expected_body` pins the bytes, for a write-up that already stood in the workspace and must
    cross as written."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        summary = measure(output.response.strip())
        failures = []
        if summary.words < summary_min_words:
            failures.append(
                f"summary has {summary.words} words under the {summary_min_words} floor"
            )
        if summary.words > summary_max_words:
            failures.append(
                f"summary has {summary.words} words over the {summary_max_words} budget"
            )
        if summary.lines > summary_max_lines:
            failures.append(
                f"summary has {summary.lines} lines over the {summary_max_lines} budget"
            )
        if summary.headers:
            failures.append(f"summary has {summary.headers} section headers")
        if summary.bullets:
            failures.append(f"summary has {summary.bullets} bullet lines")
        unreachable_targets = _unreachable_markdown_targets(output)
        workspace_paths = _workspace_paths(output.response)
        if unreachable_targets:
            failures.append(
                "summary links member-unreachable targets: " + ", ".join(unreachable_targets)
            )
        if workspace_paths:
            failures.append(
                "summary exposes member-inaccessible workspace paths: " + ", ".join(workspace_paths)
            )
        if output.artifact_error:
            failures.append(f"artifact inspection failed: {output.artifact_error}")
        shared = tuple(name for call in output.calls for name in shared_file_names(call))
        sent = tuple(artifact for artifact in output.artifacts if artifact.role == "file")
        delivered = max(len(shared), len(sent))
        if delivered:
            failures.append(f"shared {delivered} files for an ask that named none")
        carried = tuple(artifact for artifact in output.artifacts if artifact.role == "details")
        report = None
        report_shape = None
        if len(carried) != 1 or not carried[0].name.lower().endswith(".md"):
            failures.append(
                f"carried {len(carried)} artifacts in the reply, expected one Markdown report"
            )
        else:
            report = carried[0]
            if expected_name and report.name != expected_name:
                failures.append(f"carried {report.name} instead of reusing {expected_name}")
            if expected_body and report.content != expected_body:
                failures.append("carried report differs from the file it stands for")
            try:
                report_shape = measure(report.content.decode())
            except UnicodeDecodeError:
                failures.append("Markdown report is not UTF-8")
            else:
                if report_shape.words < report_min_words:
                    failures.append(
                        f"report has {report_shape.words} words under the {report_min_words} floor"
                    )
                if report_shape.headers < report_min_headers:
                    failures.append(
                        f"report has {report_shape.headers} headers under the "
                        f"{report_min_headers} floor"
                    )
        evidence: JsonObject = {
            "summary": summary.evidence,
            "sharedFiles": delivered,
            "carriedArtifacts": len(carried),
            "unreachableMarkdownTargets": list(unreachable_targets),
            "workspacePaths": list(workspace_paths),
        }
        if report is not None and report_shape is not None:
            evidence["report"] = {"name": report.name, **report_shape.evidence}
        if failures:
            return CapabilityVerdict(False, "carried delivery: " + ", ".join(failures), evidence)
        assert report_shape is not None
        return CapabilityVerdict(
            True,
            f"carried delivery: {summary.words}-word summary carrying a "
            f"{report_shape.words}-word report",
            evidence,
        )

    reuse = f" under the name {expected_name}" if expected_name else ""
    verbatim = " with the workspace file's own bytes" if expected_body else ""
    return DescribedGrader(
        f"a carried delivery: a plain chat summary of at least {summary_min_words} and at most "
        f"{summary_max_words} words over at most {summary_max_lines} lines without a workspace "
        f"path or member-unreachable Markdown link, plus exactly one Markdown report of at least "
        f"{report_min_words} words under at least {report_min_headers} section headers, carried in "
        f"the reply's Markdown link{reuse}{verbatim} and never sent as a file",
        grade,
    )


def shared_report_scorer(
    summary_max_words: int,
    summary_max_lines: int,
    report_min_words: int,
    report_min_headers: int,
    summary_min_words: int = 0,
    expected_name: str = "",
) -> Grader:
    """A chat summary inside its register's budget, plus one Markdown report the turn sent.

    The mirror of `carried_report_scorer`: here the ask carries a share trigger, so the report has
    to leave the sandbox as a file and a turn that only carried it in the reply fails. The floors
    run over the delivered bytes, which is what fails a report the turn clipped on its way out, and
    `expected_name` pins the name reuse a follow-up ask needs. The workspace still holds one report,
    so a turn that answers a file request by writing a second copy fails."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        summary = measure(output.response.strip())
        failures = []
        if summary.words < summary_min_words:
            failures.append(
                f"summary has {summary.words} words under the {summary_min_words} floor"
            )
        if summary.words > summary_max_words:
            failures.append(
                f"summary has {summary.words} words over the {summary_max_words} budget"
            )
        if summary.lines > summary_max_lines:
            failures.append(
                f"summary has {summary.lines} lines over the {summary_max_lines} budget"
            )
        if summary.headers:
            failures.append(f"summary has {summary.headers} section headers")
        if summary.bullets:
            failures.append(f"summary has {summary.bullets} bullet lines")
        unreachable_targets = _unreachable_markdown_targets(output)
        workspace_paths = _workspace_paths(output.response)
        if unreachable_targets:
            failures.append(
                "summary links member-unreachable targets: " + ", ".join(unreachable_targets)
            )
        if workspace_paths:
            failures.append(
                "summary exposes member-inaccessible workspace paths: " + ", ".join(workspace_paths)
            )
        if output.artifact_error:
            failures.append(f"artifact inspection failed: {output.artifact_error}")
        shared = tuple(name for call in output.calls for name in shared_file_names(call))
        delivered = tuple(
            artifact
            for artifact in output.artifacts
            if artifact.role == "file" and artifact.name.lower().endswith(".md")
        )
        carried = tuple(artifact for artifact in output.artifacts if artifact.role == "details")
        if carried:
            failures.append(
                f"carried {len(carried)} artifacts in the reply beside the file it sent"
            )
        report_shape = None
        if len(shared) != 1 or len(delivered) != 1:
            failures.append(
                f"shared {len(shared)} files and delivered {len(delivered)} Markdown artifacts, "
                "expected one report sent"
            )
        else:
            report = delivered[0]
            if expected_name and report.name != expected_name:
                failures.append(f"sent {report.name} instead of reusing {expected_name}")
            try:
                report_shape = measure(report.content.decode())
            except UnicodeDecodeError:
                failures.append("Markdown report is not UTF-8")
            else:
                if report_shape.words < report_min_words:
                    failures.append(
                        f"report has {report_shape.words} words under the {report_min_words} floor"
                    )
                if report_shape.headers < report_min_headers:
                    failures.append(
                        f"report has {report_shape.headers} headers under the "
                        f"{report_min_headers} floor"
                    )
        written = written_markdown(output, REPORT_GLOB)
        if len(written) != 1:
            failures.append(f"left {len(written)} Markdown reports in the workspace, expected one")
        evidence: JsonObject = {
            "summary": summary.evidence,
            "sharedFiles": len(shared),
            "unreachableMarkdownTargets": list(unreachable_targets),
            "workspacePaths": list(workspace_paths),
        }
        if report_shape is not None:
            evidence["report"] = {"name": delivered[0].name, **report_shape.evidence}
        if failures:
            return CapabilityVerdict(False, "shared delivery: " + ", ".join(failures), evidence)
        assert report_shape is not None
        return CapabilityVerdict(
            True,
            f"shared delivery: {summary.words}-word summary sending a "
            f"{report_shape.words}-word report",
            evidence,
        )

    floor = f"at least {summary_min_words} and " if summary_min_words else ""
    reuse = f" under the name {expected_name}" if expected_name else ""
    return DescribedGrader(
        f"a shared delivery: a plain chat summary of {floor}at most {summary_max_words} words over "
        f"at most {summary_max_lines} lines, plus exactly one Markdown report of at least "
        f"{report_min_words} words under at least {report_min_headers} section headers, sent with "
        f"share_file{reuse} because the ask asked for it",
        grade,
    )


def delegated_report_scorer(
    report_path: str,
    source_paths: tuple[str, ...],
    profile_name: str,
    task_field: str,
    task_max_words: int,
    result_max_words: int,
    result_max_lines: int,
    summary_min_words: int,
    summary_max_words: int,
    summary_max_lines: int,
    report_min_words: int,
    report_min_headers: int,
    *,
    share_report: bool = False,
) -> Grader:
    """One report crosses parent-to-child, child-to-parent, and parent-to-member.

    The child writes its report to /workspace, which is how agents hand work to each other, and the
    final scorer follows the member's delivery request: it either proves the report arrived as a
    file or proves the closing reply carried it."""
    report_name = PurePosixPath(report_path).name
    final_delivery = (
        shared_report_scorer(
            summary_max_words,
            summary_max_lines,
            report_min_words,
            report_min_headers,
            summary_min_words,
            report_name,
        )
        if share_report
        else carried_report_scorer(
            summary_min_words,
            summary_max_words,
            summary_max_lines,
            report_min_words,
            report_min_headers,
        )
    )

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        final = await final_delivery(output)
        failures = [] if final.passed else [final.reason]
        generation, generation_error = _subagent_generation(output)
        if generation_error:
            failures.append(generation_error)
        if share_report and report_name not in output.response:
            failures.append(f"member summary does not name {report_name}")
        spawns = tuple(
            (index, call)
            for index, call in enumerate(output.calls)
            if call.name == "spawn"
            and call.succeeded
            and str(call.input.get("target", "")).removeprefix("profile:") == profile_name
        )
        task_shape = None
        result_shape = None
        spawn_index = None
        if len(spawns) != 1:
            failures.append(f"expected one {profile_name} delegation, found {len(spawns)}")
        else:
            spawn_index, spawn = spawns[0]
            payload = spawn.input.get("payload")
            task = payload.get(task_field) if isinstance(payload, dict) else None
            if not isinstance(task, str):
                failures.append("delegation has no prose task")
            else:
                task_shape = measure(task)
                if task_shape.words > task_max_words:
                    failures.append(
                        f"delegated summary has {task_shape.words} words over the "
                        f"{task_max_words} budget"
                    )
                if task_shape.headers or task_shape.bullets:
                    failures.append("delegated summary uses document structure")
                for path in source_paths:
                    references = (
                        path,
                        path.removeprefix("/workspace/"),
                        path.removeprefix("/workspace/repo/"),
                    )
                    if not any(reference in task for reference in references):
                        failures.append(f"delegated summary does not reference {path}")
                if report_path not in task:
                    failures.append(f"delegated summary does not reference {report_path}")
            try:
                returned = json.loads(spawn.result)
            except (AttributeError, json.JSONDecodeError):
                returned = None
            result = returned.get("result") if isinstance(returned, dict) else None
            if not isinstance(result, str):
                failures.append("delegation returned no prose result")
            else:
                result_shape = measure(result)
                if result_shape.words > result_max_words:
                    failures.append(
                        f"subagent summary has {result_shape.words} words over the "
                        f"{result_max_words} budget"
                    )
                if result_shape.lines > result_max_lines:
                    failures.append(
                        f"subagent summary has {result_shape.lines} lines over the "
                        f"{result_max_lines} budget"
                    )
                if result_shape.headers or result_shape.bullets:
                    failures.append("subagent summary uses document structure")
                if report_path not in result:
                    failures.append("subagent summary does not reference its report")

        handoff = None
        if len(output.handoffs) != 1:
            failures.append(f"expected one recorded subagent handoff, found {len(output.handoffs)}")
        else:
            handoff = output.handoffs[0]
            if handoff.closing_chars > DELEGATED_CLOSING_MAX_CHARS:
                failures.append(
                    f"subagent left {handoff.closing_chars} characters of standing prose over "
                    f"the {DELEGATED_CLOSING_MAX_CHARS} budget"
                )
            if handoff.duplication > DELEGATED_DUPLICATION_MAX:
                failures.append(
                    f"subagent repeated {handoff.duplication:.0%} of its standing prose in the "
                    "finish result"
                )

        writes = tuple(
            (index, call)
            for index, call in enumerate(output.calls)
            if call.name == "write"
            and call.succeeded
            and call.input.get("file_path") == report_path
        )
        if len(writes) != 1:
            failures.append(f"expected one subagent report write, found {len(writes)}")
        if spawn_index is not None and len(writes) == 1 and not spawn_index < writes[0][0]:
            failures.append("the report was not written by the delegated subagent")

        evidence: JsonObject = {"final": final.evidence}
        if generation is not None:
            evidence["subagentGeneration"] = generation.evidence
        if task_shape is not None:
            evidence["delegatedSummary"] = task_shape.evidence
        if result_shape is not None:
            evidence["subagentSummary"] = result_shape.evidence
        if handoff is not None:
            evidence["handoff"] = {
                "closingChars": handoff.closing_chars,
                "resultChars": handoff.result_chars,
                "duplication": handoff.duplication,
            }
        if failures:
            return CapabilityVerdict(False, "delegated delivery: " + "; ".join(failures), evidence)
        assert task_shape is not None and result_shape is not None
        return CapabilityVerdict(
            True,
            f"delegated delivery: {task_shape.words}-word task, "
            f"{result_shape.words}-word subagent summary; {final.reason}",
            evidence,
        )

    return DescribedGrader(
        f"a three-hop delivery through {profile_name}: a parent task of at most {task_max_words} "
        "words referencing source "
        f"artifacts, one parent-facing subagent result of at most {result_max_words} words "
        f"over at most {result_max_lines} lines referencing its report path, and a member-facing "
        f"summary of at most {summary_max_words} words that names the subagent's written report "
        f"and {'shares it' if share_report else 'does not share it'}, with no more than "
        f"{DELEGATED_CLOSING_MAX_CHARS} characters of standing child prose and no more than "
        f"{DELEGATED_DUPLICATION_MAX:.0%} repeated in its finish result",
        grade,
    )


def delegated_inline_result_scorer(
    source_paths: tuple[str, ...],
    task_max_words: int,
    result_max_words: int,
    result_max_lines: int,
    summary_max_words: int,
    summary_max_lines: int,
) -> Grader:
    """A result-only delegation returns one short payload and creates no report file."""

    final_delivery = unwritten_reply_scorer(summary_max_words, summary_max_lines)

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        final = await final_delivery(output)
        failures = [] if final.passed else [final.reason]
        generation, generation_error = _subagent_generation(output)
        if generation_error:
            failures.append(generation_error)
        spawns = tuple(
            call
            for call in output.calls
            if call.name == "spawn"
            and call.succeeded
            and str(call.input.get("target", "")).removeprefix("profile:") == "general_purpose"
        )
        task_shape = None
        result_shape = None
        if len(spawns) != 1:
            failures.append(f"expected one general-purpose delegation, found {len(spawns)}")
        else:
            spawn = spawns[0]
            payload = spawn.input.get("payload")
            task = payload.get("task") if isinstance(payload, dict) else None
            if not isinstance(task, str):
                failures.append("delegation has no prose task")
            else:
                task_shape = measure(task)
                if task_shape.words > task_max_words:
                    failures.append(
                        f"delegated summary has {task_shape.words} words over the "
                        f"{task_max_words} budget"
                    )
                if task_shape.headers or task_shape.bullets:
                    failures.append("delegated summary uses document structure")
                for path in source_paths:
                    references = (
                        path,
                        path.removeprefix("/workspace/"),
                        path.removeprefix("/workspace/repo/"),
                    )
                    if not any(reference in task for reference in references):
                        failures.append(f"delegated summary does not reference {path}")
            try:
                returned = json.loads(spawn.result)
            except (AttributeError, json.JSONDecodeError):
                returned = None
            result = returned.get("result") if isinstance(returned, dict) else None
            if not isinstance(result, str):
                failures.append("delegation returned no prose result")
            else:
                result_shape = measure(result)
                if result_shape.words > result_max_words:
                    failures.append(
                        f"subagent summary has {result_shape.words} words over the "
                        f"{result_max_words} budget"
                    )
                if result_shape.lines > result_max_lines:
                    failures.append(
                        f"subagent summary has {result_shape.lines} lines over the "
                        f"{result_max_lines} budget"
                    )
                if result_shape.headers or result_shape.bullets:
                    failures.append("subagent summary uses document structure")

        handoff = None
        if len(output.handoffs) != 1:
            failures.append(f"expected one recorded subagent handoff, found {len(output.handoffs)}")
        else:
            handoff = output.handoffs[0]
            if handoff.closing_chars > DELEGATED_CLOSING_MAX_CHARS:
                failures.append(
                    f"subagent left {handoff.closing_chars} characters of standing prose over "
                    f"the {DELEGATED_CLOSING_MAX_CHARS} budget"
                )
            if handoff.duplication > DELEGATED_DUPLICATION_MAX:
                failures.append(
                    f"subagent repeated {handoff.duplication:.0%} of its standing prose in the "
                    "finish result"
                )
            if handoff.documents:
                failures.append(
                    f"subagent wrote {len(handoff.documents)} files for a result-only task"
                )

        evidence: JsonObject = {"final": final.evidence}
        if generation is not None:
            evidence["subagentGeneration"] = generation.evidence
        if task_shape is not None:
            evidence["delegatedSummary"] = task_shape.evidence
        if result_shape is not None:
            evidence["subagentSummary"] = result_shape.evidence
        if handoff is not None:
            evidence["handoff"] = {
                "closingChars": handoff.closing_chars,
                "resultChars": handoff.result_chars,
                "duplication": handoff.duplication,
                "documents": len(handoff.documents),
            }
        if failures:
            return CapabilityVerdict(False, "delegated result: " + "; ".join(failures), evidence)
        assert task_shape is not None and result_shape is not None
        return CapabilityVerdict(
            True,
            f"delegated result: {task_shape.words}-word task, "
            f"{result_shape.words}-word subagent result, no file; {final.reason}",
            evidence,
        )

    return DescribedGrader(
        f"a result-only delegation: a parent task of at most {task_max_words} words referencing "
        f"the source files, one parent-facing result of at most {result_max_words} words over at "
        f"most {result_max_lines} lines, no file written, no more than "
        f"{DELEGATED_CLOSING_MAX_CHARS} characters of standing child prose, no more than "
        f"{DELEGATED_DUPLICATION_MAX:.0%} repeated in its finish result, and a member reply of at "
        f"most {summary_max_words} words",
        grade,
    )


def wide_research_structured_scorer(expected: JsonObject) -> Grader:
    """A wide-research child keeps its short handoff while its complete JSON crosses by file."""
    final_delivery = unwritten_reply_scorer(40, 4)

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        final = await final_delivery(output)
        failures = [] if final.passed else [final.reason]
        generation, generation_error = _subagent_generation(output)
        if generation_error:
            failures.append(generation_error)
        calls = tuple(
            call for call in output.calls if call.name == "wide_research" and call.succeeded
        )
        if len(calls) != 1:
            failures.append(f"expected one successful wide_research call, found {len(calls)}")
        payload = None
        if output.workspace_dir is None:
            failures.append("workspace was not recorded")
        else:
            path = output.workspace_dir / "wide_research.json"
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError) as error:
                failures.append(f"wide_research.json is unavailable: {error}")
        payload_fields = payload if isinstance(payload, dict) else {}
        call_id = payload_fields.get("call_id")
        rows = payload_fields.get("rows")
        recovery_keyed = (
            isinstance(call_id, str) and re.fullmatch(r"[0-9a-f]{64}", call_id) is not None
        )
        complete_row = (
            recovery_keyed
            and payload_fields.get("untrusted") is True
            and payload_fields.get("source") == "wide_research"
            and rows == [{"entity": "aster", "result": expected, "error": ""}]
        )
        if not complete_row:
            failures.append("wide_research.json does not contain the complete structured row")
        if len(output.handoffs) != 1:
            failures.append(f"expected one research handoff, found {len(output.handoffs)}")
        else:
            handoff = output.handoffs[0]
            if handoff.closing_chars > DELEGATED_CLOSING_MAX_CHARS:
                failures.append(
                    f"subagent left {handoff.closing_chars} characters of standing prose"
                )
            if handoff.duplication > DELEGATED_DUPLICATION_MAX:
                failures.append(
                    f"subagent repeated {handoff.duplication:.0%} of its standing prose"
                )
        evidence: JsonObject = {
            "final": final.evidence,
            "wideResearchCalls": len(calls),
            "completeRow": complete_row,
            "recoveryKeyed": recovery_keyed,
            "handoffs": len(output.handoffs),
        }
        if generation is not None:
            evidence["subagentGeneration"] = generation.evidence
        if failures:
            return CapabilityVerdict(False, "wide research: " + "; ".join(failures), evidence)
        return CapabilityVerdict(True, f"wide research: complete row; {final.reason}", evidence)

    return DescribedGrader(
        "one wide_research call that writes the complete schema-shaped entity result to "
        "wide_research.json while the child returns one short handoff and the member receives "
        "a chat reply with no shared file",
        grade,
    )


REPORT_REPLY = (
    "## Recommendation\n"
    "Move to an event-driven queue where a job has a real upstream event, and keep cron for the "
    "jobs whose only trigger is the clock.\n\n"
    "## Why\n"
    "Cron fires on wall-clock time, so a job that depends on an upstream step either polls for it "
    "or races it. An event trigger removes the race and makes the dependency explicit.\n\n"
    "## Costs\n"
    "You take on queue operations: dead-letter handling, redelivery semantics, and a class of "
    "partial-failure incident that a clock trigger never produced.\n\n"
    "## Next step\n"
    "Migrate one job first, the one with the cleanest upstream event and the smallest blast "
    "radius, and run it alongside its cron entry until it has a week of clean runs."
)


def _file(path: str, body: str) -> WorkspaceFile:
    return WorkspaceFile(path, body.strip().encode() + b"\n")


def _carried(report: WorkspaceFile) -> str:
    """A seeded assistant message's file link, naming the workspace file a follow-up ask reuses."""
    return f"[{report.path}](/workspace/{report.path})"


CHANGE_NOTE = _file(
    "repo/notes/change-412.md",
    """
# Source credentials move to the workspace (412)

A member's connected account is no longer accepted as a credential for a content source. Sync runs
on the workspace-level API key in the provider's credential slot, so connecting a personal account
grants a source nothing and registration asks for the workspace key instead.
""",
)

SOURCE_CREDENTIALS = _file(
    "repo/src/source_credentials.py",
    """
DIRECT_ACCOUNT = "default"


def resolve_source_credential(source, connections, credential_slot):
    claimed = [item for item in connections if item.provider == source.provider]
    if claimed:
        return Resolved(account=claimed[0].account_id, connection_id=claimed[0].id)
    if credential_slot.is_set(source.provider):
        return Resolved(account=DIRECT_ACCOUNT, connection_id=None)
    raise SourceUnauthenticated(
        f"connect a {source.provider!r} account before registering its sources"
    )
""",
)

JSONL_RESULTS = _file(
    "repo/data/results.jsonl",
    """
{"case":"alpha","passed":true}
{"case":"beta","passed":false}
{"case":"gamma","passed":true}
""",
)

JSONL_READER = _file(
    "repo/src/read_results.py",
    """
import json
from pathlib import Path


def passed_cases(path: Path) -> list[str]:
    records = json.loads(path.read_text())
    return [record["case"] for record in records if record["passed"]]
""",
)

SPAWN_TIMINGS = _file(
    "repo/data/spawn-timings.csv",
    """
phase,seconds
prompt_input,1.4
search_calls,31.8
markdown_report,121.6
standing_conclusion,116.9
finish_result,113.7
""",
)

SPAWN_TIMING_NOTES = _file(
    "repo/data/spawn-timing-notes.txt",
    """
The phases are sequential totals from one delegated research task. markdown_report is a file write.
standing_conclusion is the assistant prose in the round before the end-turn reminder. finish_result
is the payload returned to the parent. The parent reads only finish_result. The member did not ask
for a file.
""",
)

WIDE_RESEARCH_ENTITIES = _file("repo/data/wide-entities.txt", "aster")
WIDE_RESEARCH_SCHEMA = _file(
    "repo/data/company-schema.json",
    """
{
  "company": "string",
  "headquarters": "string",
  "founded": "number",
  "employees": "number",
  "product": "string",
  "customers": "array of strings",
  "certifications": "array of strings",
  "evidence": "string"
}
""",
)
WIDE_RESEARCH_EXPECTED: JsonObject = {
    "company": "Aster Systems",
    "headquarters": "Portland, Oregon",
    "founded": 2012,
    "employees": 184,
    "product": "Managed cold-chain monitoring for regional food distributors",
    "customers": ["Northbank Foods", "Willow Market Cooperative", "Cascade Produce"],
    "certifications": ["SOC 2 Type II", "ISO 27001"],
    "evidence": "Internal company record updated 2026-08-01",
}
WIDE_RESEARCH_RECORD = _file(
    "repo/data/aster.json",
    json.dumps(WIDE_RESEARCH_EXPECTED, indent=2),
)

NIGHTLY_RUNNER_REPORT = _file(
    "nightly-runner-queue.md",
    """
# Nightly runner: cron against an event-driven queue

## Recommendation
Move each job that has a real upstream event onto the queue, and keep cron for the jobs whose only
trigger is the clock. The split costs one more mechanism to operate and removes the class of
incident a clock trigger cannot avoid.

## What cron gets wrong here
Cron fires on wall-clock time, so a job that depends on an upstream step either polls for that step
or races it. The digest job is the clearest case: it reads the rows the aggregation writes, and when
the aggregation runs late the digest sends a partial batch and reports success.

## What the queue costs
The queue adds dead-letter handling, redelivery semantics, and a consumer that must be idempotent,
because a message can arrive twice. It also adds a second place to look when a job does not run,
which lengthens the first minutes of an incident until the team knows the new shape.

## Next step
Migrate the digest job first. It has the cleanest upstream event and the smallest blast radius. Run
it beside its cron entry until it has a week of clean runs, then delete the cron entry.
""",
)

DEDUP_EVIDENCE = _file(
    "dedup-null-emails.md",
    """
# NULL emails under the (tenant_id, email) unique index

## What the index does
Postgres treats two NULLs as distinct inside a unique index. The index on (tenant_id, email)
accepts any number of rows that carry the same tenant_id and a NULL email: each such row is a new
key as far as the index is concerned, so the second insert never conflicts with the first.

## What that means for the ticket
Two imports of the same contact with no email address both land. The dedup report then counts one
member twice, and a later merge has no key to join on. The index blocks a repeated address and
nothing else, so closing the ticket on the index leaves the reported duplicates in place.

## Remedies
UNIQUE NULLS NOT DISTINCT over the same pair makes one NULL email per tenant the rule and needs no
application change. A partial unique index on (tenant_id) WHERE email IS NULL covers the NULL case
only. A NOT NULL column with a synthetic placeholder is the heaviest option and the one that
changes reads.

## How to settle it
Insert two rows with the same tenant_id and a NULL email against a copy of the schema. Both are
accepted today, and only one is accepted after the constraint change.
""",
)

INVOICE_EXPORT_NOTES = _file(
    "research/invoice-export-notes.md",
    """
    # Notes: the weekly invoice export

    - cron fires the export at 02:00 whether or not the ledger close has finished
    - three of the last ten exports carried a partial week; each time the close ran late
    - the close finishes anywhere between 01:40 and 02:30
    - a queue would run the export when the close lands
    - we run Postgres today and no queue service
    - finance re-runs a partial export by hand, about forty minutes each time
    """,
)
CODING_CHILD_REPORT = _file(
    "coding-child-report.md",
    """
# Parser failure and fix

## Root cause
The stream parser held a trailing opening bracket because another chunk could complete a Markdown
link. The provider then ended the stream without another text delta. The round runner flushed its
ordinary text buffer but gave the parser no end-of-stream signal, so a final citation marker such
as `[2]` remained inside the parser and never reached the member. The stored closing answer still
contained the marker, which made the live stream and committed answer disagree.

## Change
The text-filter contract now has a finish operation. A cleanly completed model stream calls it
after the provider and byte buffers finish. The Markdown parser uses that signal to release any
incomplete link syntax as ordinary prose. Complete links to workspace files still reduce to their
labels and stage their files, while links inside code, image syntax, web links, and bracketed call
syntax remain untouched.

## Proof
Focused tests split links at every chunk boundary and compare live output with batch projection.
Separate cases cover trailing citation markers, images, inline and fenced code, external URLs,
relative files, absolute workspace files, and expression syntax such as `handlers[name](event)`.
The engine test proves a linked file becomes one durable detail row while the visible answer keeps
only its label.
""",
)
CODING_SKILL = CODING_SKILLS_ROOT / "coding"
RESEARCH_REPORT_SKILL = RESEARCH_SKILLS_ROOT / "research-report"


CODING_SKILL_LOADED = (
    Message(
        role="user",
        content="Fix the stream parser and have the coding child leave its complete report.",
    ),
    Message(
        role="assistant",
        content=(ToolUseBlock(id="load-coding", name="load_skill", input={"name": "coding"}),),
    ),
    Message(
        role="user",
        content=(
            ToolResultBlock(
                tool_use_id="load-coding",
                content=loaded_context((LoadedSkill(parse_skill(CODING_SKILL)),)),
            ),
        ),
    ),
    Message(
        role="assistant",
        content="The coding child finished and wrote /workspace/coding-child-report.md.",
    ),
)


RESEARCH_REPORT_LOADED = (
    Message(
        role="user",
        content=(
            "I have notes on our weekly invoice export to write up as a report for the finance "
            "team. I'll drop them in the workspace in a minute."
        ),
    ),
    Message(
        role="assistant",
        content=(
            ToolUseBlock(
                id="load-research-report", name="load_skill", input={"name": "research-report"}
            ),
        ),
    ),
    Message(
        role="user",
        content=(
            ToolResultBlock(
                tool_use_id="load-research-report",
                content=loaded_context((LoadedSkill(parse_skill(RESEARCH_REPORT_SKILL)),)),
            ),
        ),
    ),
    Message(role="assistant", content="Ready when the notes are in."),
)
BANTER = (
    "morning, is the office wifi still doing the thing where it drops every twenty minutes",
    "I can't see the network from here. If it is still dropping, the office IT channel is the "
    "fastest way to get someone on it.",
    "figures. i was about to start working from the coffee shop out of spite",
)


DELEGATED_CASES = (
    CapabilityCase(
        "delegated-pushback-artifact-self-description",
        "Have the research subagent reconcile the claim in "
        "/workspace/repo/notes/change-412.md with the behavior in "
        "/workspace/repo/src/source_credentials.py. Have it write the complete evidence, "
        "uncertainty, and reasoning to /workspace/change-412-evidence.md. Then tell me whether "
        "my connected Drive account is being denied in favor of a workspace API key, and give me "
        "what it found.",
        delegated_report_scorer(
            report_path="/workspace/change-412-evidence.md",
            source_paths=(
                "/workspace/repo/notes/change-412.md",
                "/workspace/repo/src/source_credentials.py",
            ),
            profile_name="research",
            task_field="objective",
            task_max_words=100,
            result_max_words=60,
            result_max_lines=6,
            summary_min_words=25,
            summary_max_words=80,
            summary_max_lines=6,
            report_min_words=150,
            report_min_headers=2,
        ),
        digest_tag="register:delegated-pushback-artifact-self-description",
        workspace_files=(CHANGE_NOTE, SOURCE_CREDENTIALS),
        rubric=(
            "The summary rejects the note's claimed rule in member-visible terms: a matching "
            "connected account takes precedence over the workspace key.",
            "The summary says the files do not establish what happened to this Drive sync or why "
            "it requested the workspace key.",
            "Apart from the deciding product rule — that the inspected sources put a matching "
            "connected account first and the workspace key second — the summary contains only "
            "the member-facing answer, the unknown boundary, and the report reference. That rule "
            "is not implementation evidence. Function names, identifiers, source lines, branch "
            "mechanics, and possible causes are implementation evidence or hypotheses and stay "
            "out of the summary.",
        ),
        artifact_rubric=(
            "The report establishes the complete precedence from the code: a matching connected "
            "account first, the workspace credential only when none matches, and with neither the "
            "member is told to connect an account.",
            "The report identifies the change note as contradicting the code and never presents "
            "the note's workspace-key requirement as shipped behavior.",
            "The report separates evidence about credential precedence from hypotheses about why "
            "this particular Drive sync requested a key.",
        ),
    ),
    CapabilityCase(
        "delegated-pushback-artifact-delivered",
        "Have the research subagent reconcile the claim in "
        "/workspace/repo/notes/change-412.md with the behavior in "
        "/workspace/repo/src/source_credentials.py. Have it write the complete evidence, "
        "uncertainty, and reasoning to /workspace/change-412-evidence.md. Send me that report "
        "file, then tell me whether my connected Drive account is being denied in favor of a "
        "workspace API key.",
        delegated_report_scorer(
            report_path="/workspace/change-412-evidence.md",
            source_paths=(
                "/workspace/repo/notes/change-412.md",
                "/workspace/repo/src/source_credentials.py",
            ),
            profile_name="research",
            task_field="objective",
            task_max_words=100,
            result_max_words=60,
            result_max_lines=6,
            summary_min_words=20,
            summary_max_words=80,
            summary_max_lines=6,
            report_min_words=150,
            report_min_headers=2,
            share_report=True,
        ),
        digest_tag="register:delegated-pushback-artifact-delivered",
        workspace_files=(CHANGE_NOTE, SOURCE_CREDENTIALS),
        rubric=(
            "The summary rejects the note's claimed rule: a matching connected account takes "
            "precedence over the workspace key.",
            "The summary says the files do not establish what happened to this Drive sync or why "
            "it requested the workspace key.",
            "The summary names change-412-evidence.md as the attached report.",
        ),
        artifact_rubric=(
            "The report establishes the complete precedence from the code: a matching connected "
            "account first, the workspace credential only when none matches, and with neither the "
            "member is told to connect an account.",
            "The report identifies the change note as contradicting the code and separates that "
            "evidence from hypotheses about this Drive sync.",
        ),
    ),
    CapabilityCase(
        "delegated-jsonl-result-only",
        "Have a general-purpose subagent inspect /workspace/repo/data/results.jsonl and "
        "/workspace/repo/src/read_results.py. Tell me why the reader raises JSONDecodeError: "
        "Extra data and the direct fix. I do not need a file.",
        delegated_inline_result_scorer(
            source_paths=(
                "/workspace/repo/data/results.jsonl",
                "/workspace/repo/src/read_results.py",
            ),
            task_max_words=100,
            result_max_words=60,
            result_max_lines=6,
            summary_max_words=80,
            summary_max_lines=6,
        ),
        digest_tag="register:delegated-jsonl-result-only",
        workspace_files=(JSONL_RESULTS, JSONL_READER),
        rubric=(
            "The answer says the file is newline-delimited JSON but the reader passes the whole "
            "file to a parser for one JSON value.",
            "The answer fixes the root cause by parsing each nonempty line as its own JSON object.",
            "The answer does not recommend a retry or say the records are corrupt.",
        ),
    ),
    CapabilityCase(
        "delegated-latency-result-only",
        "Have a general-purpose subagent inspect /workspace/repo/data/spawn-timings.csv and "
        "/workspace/repo/data/spawn-timing-notes.txt. Tell me what dominates the elapsed time and "
        "which result production should remain. I do not need a file.",
        delegated_inline_result_scorer(
            source_paths=(
                "/workspace/repo/data/spawn-timings.csv",
                "/workspace/repo/data/spawn-timing-notes.txt",
            ),
            task_max_words=100,
            result_max_words=60,
            result_max_lines=6,
            summary_max_words=80,
            summary_max_lines=6,
        ),
        digest_tag="register:delegated-latency-result-only",
        workspace_files=(SPAWN_TIMINGS, SPAWN_TIMING_NOTES),
        rubric=(
            "The answer says the three result-writing phases dominate the prompt and search time.",
            "The answer keeps the finish result because it is the only result the parent reads.",
            "The answer removes the standing conclusion and the unrequested Markdown report.",
        ),
    ),
    CapabilityCase(
        "delegated-wide-research-structured-result",
        "Use wide_research once with /workspace/repo/data/wide-entities.txt as entities_file, "
        "`Read /workspace/repo/data/{entity}.json and return every field exactly.` as "
        "prompt_template, and /workspace/repo/data/company-schema.json as output_schema_file. "
        "Then tell me the output file path. Do not share a file.",
        wide_research_structured_scorer(WIDE_RESEARCH_EXPECTED),
        digest_tag="register:delegated-wide-research-structured-result",
        workspace_files=(WIDE_RESEARCH_ENTITIES, WIDE_RESEARCH_SCHEMA, WIDE_RESEARCH_RECORD),
    ),
)


CASES = (
    CapabilityCase(
        "ack-decision-accepted",
        "Agreed, per-member local it is.",
        conversational_scorer(max_words=25, max_lines=2),
        digest_tag="register:ack-decision-accepted",
        samples=3,
        prior_messages=(
            "still speccing the nightly digest. 7am in each member's local timezone, or one 9am "
            "UTC blast for everyone?",
            "Per-member local time. It costs one scheduled job per timezone instead of one, but a "
            "digest that lands at 3am is a digest nobody reads.",
        ),
        rubric=(
            "The reply acknowledges the decision in the register of a quick chat message.",
            "The reply does not restate the reasoning, re-summarize the options, or add sections, "
            "headers, or a bulleted plan.",
        ),
    ),
    CapabilityCase(
        "ack-after-report",
        "This is great, thanks. Let's go with that.",
        conversational_scorer(max_words=25, max_lines=2),
        digest_tag="register:ack-after-report",
        samples=3,
        prior_messages=(
            "give me a writeup on whether we should move the nightly job runner off cron",
            REPORT_REPLY,
        ),
        rubric=(
            "The reply is a brief acknowledgement, even though the message it answers follows a "
            "long structured report.",
            "The reply does not restate or re-summarize the report's recommendation, costs, or "
            "next step, and does not append open questions or caveats that were not asked for.",
        ),
    ),
    CapabilityCase(
        "discuss-thinking-out-loud",
        "I keep going back and forth on where the retry belongs, the client or the worker. What's "
        "your instinct?",
        conversational_scorer(max_words=80, max_lines=4),
        digest_tag="register:discuss-thinking-out-loud",
        samples=3,
        prior_messages=(
            "the enqueue call fails maybe once a day and I have not decided who should retry it",
            "Once a day is rare enough that either place will do the job. The question is which "
            "one can tell a duplicate from a first attempt.",
        ),
        rubric=(
            "The reply gives an opinion on where the retry belongs rather than laying out options "
            "for the asker to decide.",
            "The reply reads as one person talking to another, not as a written-up analysis.",
        ),
    ),
    CapabilityCase(
        "discuss-writes-no-report",
        "we could keep the digest cron and just add a retry, or move the whole thing to the queue "
        "— which would you rather defend in a postmortem?",
        unwritten_reply_scorer(max_words=80, max_lines=4),
        digest_tag="register:discuss-writes-no-report",
        samples=3,
        prior_messages=(
            "the digest missed its 7am slot twice this week and both times the enqueue call had "
            "timed out",
            "Twice in a week is a retry problem before it is a scheduler problem. The question is "
            "which side can tell a missed run from a second one.",
        ),
        rubric=(
            "The reply picks one of the two options and gives the one reason that decides it.",
            "The reply reads as one person talking to another, and does not defer its substance to "
            "a written report or a promised document.",
        ),
    ),
    CapabilityCase(
        "fact-quick-question",
        "quick one: in Postgres, does a unique index on a nullable column stop two rows from both "
        "having NULL there?",
        conversational_scorer(max_words=55, max_lines=4),
        digest_tag="register:fact-quick-question",
        samples=3,
        prior_messages=(
            "you around? got a couple of quick Postgres questions before standup",
            "Go ahead.",
        ),
        rubric=(
            "The reply answers no: a standard unique index treats NULLs as distinct, so any "
            "number of rows may hold NULL in that column.",
            "The answer leads the reply rather than arriving after preamble or setup.",
        ),
    ),
    CapabilityCase(
        "fact-followup-in-thread",
        "and what's statement_timeout set to in a stock Postgres install?",
        conversational_scorer(max_words=35, max_lines=3),
        digest_tag="register:fact-followup-in-thread",
        samples=3,
        prior_messages=(
            "is there anything in Postgres that kills a query on its own after a while?",
            "Only if you set it. statement_timeout aborts a statement that runs past the limit, "
            "and idle_in_transaction_session_timeout kills a transaction left open.",
        ),
        rubric=(
            "The reply states that the default is 0, meaning no timeout.",
            "The answer leads the reply rather than arriving after preamble or setup.",
        ),
    ),
    CapabilityCase(
        "fact-question-mentions-other-people",
        "@dana @priya adding the reindex step now — does VACUUM FULL block reads on the table "
        "while it runs?",
        addressed_reply_scorer(min_words=8, max_words=55, max_lines=4),
        digest_tag="register:fact-question-mentions-other-people",
        samples=3,
        prior_messages=(
            "@dana @priya Thursday's failover drill moves to 09:00. the runbook is half written, "
            "I'll post the gaps here as I hit them.",
        ),
        rubric=(
            "The reply answers the question: VACUUM FULL takes an exclusive lock on the table, so "
            "reads block until it finishes.",
            "The reply answers the member who wrote the message. It does not read the message as "
            "addressed to Dana or Priya, does not defer the question to them, and does not ask "
            "whether the question was meant for it.",
        ),
    ),
    CapabilityCase(
        "pushback-false-premise",
        "Closing the dedup ticket. Our unique index on (tenant_id, email) already prevents two "
        "rows with a NULL email for the same tenant, since Postgres treats NULLs as equal inside "
        "a unique index. Confirm and I'll close it out.",
        carried_report_scorer(
            summary_min_words=15,
            summary_max_words=80,
            summary_max_lines=4,
            report_min_words=150,
            report_min_headers=2,
        ),
        digest_tag="register:pushback-false-premise",
        rubric=(
            "The summary contradicts the premise directly: Postgres treats NULLs as distinct in a "
            "unique index, so the index does not block multiple NULL-email rows per tenant.",
            "The summary advises against closing the ticket and names the next action.",
        ),
        artifact_rubric=(
            "The report supports the contradiction with reasoning or a concrete demonstration, "
            "such as the rows that would both be accepted, rather than asserting it flatly.",
            "The report names at least one concrete remedy, such as UNIQUE NULLS NOT DISTINCT, a "
            "partial or expression unique index, or a NOT NULL constraint.",
        ),
    ),
    CapabilityCase(
        "pushback-correlation-causation",
        "p95 dropped right after we raised the connection pool from 20 to 50, so the pool was the "
        "bottleneck. I'm taking it to 200 tonight and closing the perf ticket. Sound good?",
        carried_report_scorer(
            summary_min_words=15,
            summary_max_words=80,
            summary_max_lines=4,
            report_min_words=150,
            report_min_headers=2,
        ),
        digest_tag="register:pushback-correlation-causation",
        prior_messages=BANTER,
        rubric=(
            "The summary pushes back rather than agreeing, even though the thread it answers is "
            "casual.",
            "The summary advises against raising the pool to 200 or closing the ticket and names "
            "the measurement needed next.",
        ),
        artifact_rubric=(
            "The report explains why the timing alone does not establish the pool as the "
            "bottleneck, naming at least one confound or missing measurement.",
            "The report explains a concrete risk in going to 200, such as exceeding "
            "max_connections, per-connection memory, or added contention rather than less.",
            "The report proposes a specific way to settle the question before the change is made "
            "permanent.",
        ),
    ),
    CapabilityCase(
        "pushback-artifact-self-description",
        "change 412 shipped last week and now my Drive sync is asking me for an API key. is it "
        "denying a member's connected account as a source and requiring a workspace level key? "
        "why? it's in /workspace/repo, notes/change-412.md and src/source_credentials.py.",
        carried_report_scorer(
            summary_min_words=25,
            summary_max_words=120,
            summary_max_lines=6,
            report_min_words=150,
            report_min_headers=2,
        ),
        digest_tag="register:pushback-artifact-self-description",
        workspace_files=(CHANGE_NOTE, SOURCE_CREDENTIALS),
        rubric=(
            "The first sentence answers no and gives the deciding fact: a matching connected "
            "account is used before the workspace key.",
            "The summary repeats no claim from the change note that a workspace key is now "
            "required or that a connected account is no longer accepted.",
            "The summary describes the member's action and result without code identifiers or a "
            "tour of functions, fields, branches, constants, or internal components.",
            "The summary distinguishes what the files prove from the reported incident's unknown "
            "cause rather than inventing a provider mismatch or connection-scoping diagnosis.",
        ),
        artifact_rubric=(
            "The report establishes the complete precedence from the code: a matching connected "
            "account first, the workspace credential only when none matches, and with neither the "
            "member is told to connect an account.",
            "The report identifies the change note as contradicting the code and never presents "
            "the note's workspace-key requirement as shipped behavior.",
            "The report separates evidence about credential precedence from hypotheses about why "
            "this particular Drive sync requested a key.",
        ),
    ),
    CapabilityCase(
        "workspace-report-followup-stays-undelivered",
        "Where is the full write-up? Do not send the file yet.",
        conversational_scorer(max_words=50, max_lines=4),
        samples=1,
        digest_tag="register:workspace-report-followup-stays-undelivered",
        workspace_files=(
            WorkspaceFile(
                "glm-provider-routing.md",
                b"# Provider routing\n\nSet one provider order for glm-5.3 and another for "
                b"glm-5.3-flash. Parse each comma-separated environment value at startup and "
                b"reject empty provider names. Build the OpenRouter provider object with order "
                b"and allow_fallbacks. Omit the object when its setting is absent. Keep provider "
                b"selection separate from response parsing.\n\n## Tests\n\nCheck valid and invalid "
                b"environment values. Check the request body for both models. Check that an "
                b"unset value sends no provider object. Check that the client preserves the "
                b"response model, usage, and provider data. Run the focused tests without a "
                b"network key.\n\n## Rollout\n\nSet the variables in testing first. Confirm the "
                b"served provider in request records. Then set production and watch error rate, "
                b"latency, and fallback use.",
            ),
        ),
        prior_messages=(
            "GitHub access is unavailable. Write the complete OpenRouter provider-routing plan "
            "to glm-provider-routing.md, but do not send it yet.",
            "The complete plan is written. It is not sent.",
        ),
        rubric=(
            "The reply names glm-provider-routing.md and does not claim that the file was sent.",
        ),
    ),
    CapabilityCase(
        "report-workspace-link-opens-detail",
        "GitHub access is unavailable. Write up the full OpenRouter provider-routing plan as "
        "glm-provider-routing.md. Cover the environment value for glm-5.3 and glm-5.3-flash, "
        "client parsing, the provider object, and tests. In chat, give me only the conclusion. "
        "Do not send the file.",
        carried_report_scorer(
            summary_min_words=20,
            summary_max_words=100,
            summary_max_lines=5,
            report_min_words=120,
            report_min_headers=2,
            expected_name="glm-provider-routing.md",
        ),
        samples=1,
        digest_tag="register:report-workspace-link-opens-detail",
        rubric=(
            "The summary gives the conclusion without a workspace path and without claiming that "
            "a file was sent or saying where the write-up is.",
        ),
        artifact_rubric=(
            "The report covers both named model slugs, the environment value, client parsing, the "
            "provider object, and tests.",
        ),
    ),
    CapabilityCase(
        "report-existing-file-carried-by-path",
        "The nightly runner write-up is finished in the workspace as nightly-runner-queue.md. Give "
        "me the conclusion.",
        carried_report_scorer(
            summary_min_words=15,
            summary_max_words=100,
            summary_max_lines=5,
            report_min_words=150,
            report_min_headers=3,
            expected_name="nightly-runner-queue.md",
            expected_body=NIGHTLY_RUNNER_REPORT.content,
        ),
        digest_tag="register:report-existing-file-carried-by-path",
        workspace_files=(NIGHTLY_RUNNER_REPORT,),
        rubric=(
            "The summary states the write-up's recommendation and the one fact that decides it.",
        ),
    ),
    CapabilityCase(
        "report-tradeoff-analysis",
        "Put together an analysis for the team on whether we should move our nightly job runner "
        "from cron to an event-driven queue. Cover the tradeoffs, the failure modes we would take "
        "on, and your recommendation. Answer from your own knowledge, no need to research it.",
        carried_report_scorer(
            summary_min_words=25,
            summary_max_words=120,
            summary_max_lines=6,
            report_min_words=200,
            report_min_headers=3,
        ),
        digest_tag="register:report-tradeoff-analysis",
        rubric=(
            "The summary recommends whether to move the nightly runner and gives the one tradeoff "
            "that decides the recommendation.",
        ),
        artifact_rubric=(
            "The report covers tradeoffs, failure modes, and a recommendation, each developed "
            "rather than named.",
            "The failure modes are specific to an event-driven queue, such as redelivery, "
            "dead-letter handling, ordering, or lost events.",
            "The recommendation takes a position instead of listing considerations for the reader "
            "to weigh.",
        ),
    ),
    CapabilityCase(
        "report-after-banter",
        "Different topic. Write up a comparison of Postgres LISTEN/NOTIFY against a durable queue "
        "for our job triggers. Cover delivery guarantees, what happens across a restart, and how "
        "each behaves under load. Answer from your own knowledge, no need to research it.",
        carried_report_scorer(
            summary_min_words=25,
            summary_max_words=120,
            summary_max_lines=6,
            report_min_words=200,
            report_min_headers=3,
        ),
        digest_tag="register:report-after-banter",
        prior_messages=BANTER,
        rubric=(
            "The summary recommends a durable queue or durable record for job triggers and gives "
            "the delivery guarantee that decides it.",
        ),
        artifact_rubric=(
            "The comparison covers delivery guarantees, restart behavior, and behavior under "
            "load for both options.",
            "The comparison states that LISTEN/NOTIFY drops notifications for a listener that is "
            "not connected, so a restart loses them, while a durable queue retains them.",
            "The comparison reaches a clear conclusion about which fits job triggers.",
        ),
    ),
    CapabilityCase(
        "report-coding-skill-links-child-report",
        "Give me what the coding child found. Do not send the file.",
        carried_report_scorer(
            summary_min_words=15,
            summary_max_words=100,
            summary_max_lines=5,
            report_min_words=150,
            report_min_headers=2,
            expected_name="coding-child-report.md",
            expected_body=CODING_CHILD_REPORT.content,
        ),
        digest_tag="register:report-coding-skill-links-child-report",
        workspace_files=(CODING_CHILD_REPORT,),
        prior_transcript=CODING_SKILL_LOADED,
        rubric=(
            "The summary says the stream parser failed to release an incomplete trailing link or "
            "citation when the stream ended and that the finish operation fixes it.",
        ),
    ),
    CapabilityCase(
        "report-research-skill-links-the-report",
        "The notes are in research/invoice-export-notes.md. Turn them into the report on whether "
        "the weekly invoice export stays on cron or moves onto a queue. In chat, give me only the "
        "conclusion.",
        carried_report_scorer(
            summary_min_words=15,
            summary_max_words=100,
            summary_max_lines=5,
            report_min_words=150,
            report_min_headers=2,
        ),
        digest_tag="register:report-research-skill-links-the-report",
        workspace_files=(INVOICE_EXPORT_NOTES,),
        prior_transcript=RESEARCH_REPORT_LOADED,
        rubric=(
            "The summary recommends whether the export stays on cron or moves onto a queue and "
            "gives the one fact from the notes that decides it.",
        ),
        artifact_rubric=(
            "The report develops the notes into findings and a recommendation rather than listing "
            "the notes back.",
            "The report states that the export runs before the close finishes on the late nights, "
            "which is what produces a partial week.",
        ),
    ),
    CapabilityCase(
        "scheduled-worthy-result-broadcasts",
        "<scheduled_task>\n"
        "scheduled_fire: 2026-09-07T09:00:00Z\n"
        "</scheduled_task>\n"
        "Prepare the weekly capacity report. Requests rose from 42,100 to 51,800, p95 latency "
        "rose from 210 ms to 460 ms, and the primary pool exceeded 85 percent for six hours. "
        "This is the first report of these changes.\n"
        "<scheduled_task_instruction>\n"
        f"{REPORT_INSTRUCTION}\n"
        "</scheduled_task_instruction>",
        shared_report_scorer(
            summary_min_words=15,
            summary_max_words=80,
            summary_max_lines=6,
            report_min_words=120,
            report_min_headers=2,
        ),
        samples=1,
        digest_tag="register:scheduled-worthy-result-broadcasts",
        rubric=(
            "The reply says capacity or latency needs attention and cites at least one deciding "
            "change from the scheduled input.",
        ),
        artifact_rubric=(
            "The report includes requests, p95 latency, and primary-pool utilization with the "
            "figures from the scheduled input.",
        ),
    ),
    CapabilityCase(
        "report-summary-request-shares-nothing",
        "send me a short summary of whether we should move image thumbnailing off the web "
        "request path onto a background worker, and cover the failure modes we would take on. "
        "Answer from your own knowledge, no need to research it.",
        carried_report_scorer(
            summary_min_words=25,
            summary_max_words=120,
            summary_max_lines=6,
            report_min_words=200,
            report_min_headers=3,
        ),
        digest_tag="register:report-summary-request-shares-nothing",
        rubric=(
            "The summary recommends whether to move thumbnailing onto a background worker and "
            "gives the one tradeoff that decides the recommendation.",
        ),
        artifact_rubric=(
            "The report develops the failure modes the move takes on rather than naming them.",
            "The failure modes are specific to a queue, such as redelivery, dead-letter handling, "
            "ordering, or lost events.",
            "The report reaches a recommendation instead of listing considerations for the reader "
            "to weigh.",
        ),
    ),
    CapabilityCase(
        "report-file-asked-for-up-front",
        "write me a markdown file comparing Postgres LISTEN/NOTIFY against a durable queue for our "
        "job triggers, and send it over. Cover delivery guarantees, what happens across a restart, "
        "and how each behaves under load. In the thread just give me your recommendation. Answer "
        "from your own knowledge, no need to research it.",
        shared_report_scorer(
            summary_min_words=25,
            summary_max_words=60,
            summary_max_lines=6,
            report_min_words=200,
            report_min_headers=3,
        ),
        digest_tag="register:report-file-asked-for-up-front",
        rubric=(
            "The reply recommends a durable queue or durable record for job triggers and gives the "
            "delivery guarantee that decides it.",
        ),
        artifact_rubric=(
            "The comparison covers delivery guarantees, restart behavior, and behavior under load "
            "for both options.",
            "The comparison states that LISTEN/NOTIFY drops notifications for a listener that is "
            "not connected, so a restart loses them, while a durable queue retains them.",
            "The comparison reaches a clear conclusion about which fits job triggers.",
        ),
    ),
    CapabilityCase(
        "report-then-file-requested",
        "can you send me that as a file?",
        shared_report_scorer(
            summary_max_words=25,
            summary_max_lines=2,
            report_min_words=150,
            report_min_headers=3,
            expected_name="nightly-runner-queue.md",
        ),
        digest_tag="register:report-then-file-requested",
        workspace_files=(NIGHTLY_RUNNER_REPORT,),
        prior_messages=(
            "give me an analysis of whether we should move the nightly job runner off cron",
            "Move the jobs that have a real upstream event onto a queue, and keep cron for those "
            "whose only trigger is the clock: cron races the upstream step a job depends on, which "
            "is why the digest sends partial batches.\n\n" + _carried(NIGHTLY_RUNNER_REPORT),
        ),
        rubric=(
            "The reply is a brief acknowledgement that the file is sent, in the register of a "
            "quick chat message.",
            "The reply does not restate the analysis, its costs, or its next step.",
        ),
    ),
    CapabilityCase(
        "dispute-evidence-requested",
        "I don't buy it. show me the evidence.",
        carried_report_scorer(
            summary_min_words=0,
            summary_max_words=80,
            summary_max_lines=4,
            report_min_words=150,
            report_min_headers=2,
            expected_name="dedup-null-emails.md",
        ),
        digest_tag="register:dispute-evidence-requested",
        workspace_files=(DEDUP_EVIDENCE,),
        prior_messages=(
            "Closing the dedup ticket. Our unique index on (tenant_id, email) already prevents two "
            "rows with a NULL email for the same tenant, since Postgres treats NULLs as equal "
            "inside a unique index. Confirm and I'll close it out.",
            "Postgres treats NULLs as distinct inside a unique index, so that index accepts any "
            "number of NULL-email rows for one tenant. Keep the ticket open and add UNIQUE NULLS "
            "NOT DISTINCT or a NOT NULL column.\n\n" + _carried(DEDUP_EVIDENCE),
        ),
        rubric=(
            "The reply holds the same verdict: the unique index does not block two NULL-email rows "
            "for one tenant.",
            "The reply does not reproduce the queries or their outputs in the thread; those stay "
            "in the carried report.",
        ),
    ),
)
