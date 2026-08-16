"""Register cases: the reply's shape must track the exchange, not a constant default.

Each case seeds a thread with `prior_messages` and grades the closing text's measured shape —
words, lines, headers, bullet lines — against the register the turn earns. One case per register
the shell declares, run in opposing pairs so a suite score cannot be bought by going uniformly
terse or uniformly structured. Each dispute and report crosses one boundary: chat carries a short
standalone summary that names the write-up, while one Markdown report written to the workspace —
and never shared, because the ask named no file — carries the structured detail. Two cases flip
register mid-thread — an acknowledgement after a report, an analysis after banter — because the
register is chosen per turn, never inherited from the thread.

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
from an empty workspace."""

import json
import re
from dataclasses import dataclass, field

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    WorkspaceFile,
    written_markdown,
)
from evals.harness.harness import JsonObject

HEADER_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s+\S|\*\*[^*\n]{1,60}\*\*:?\s*$)", re.MULTILINE)
BULLET_RE = re.compile(r"^\s{0,3}(?:[-*•]\s+\S|\d{1,2}[.)]\s+\S)", re.MULTILINE)
BULLET_MARKER_RE = re.compile(r"^\s{0,3}(?:[-*•]|\d{1,2}[.)])\s+")
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,}).*?(?:^\s{0,3}\1\s*$|\Z)", re.MULTILINE | re.DOTALL)
REPORT_GLOB = "*.md"


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


def conversational_scorer(max_words: int, max_lines: int) -> Grader:
    """A chat-register reply: inside the word and line budget, no headers, no bullet list."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        shape = measure(output.response.strip())
        failures = []
        if shape.words > max_words:
            failures.append(f"{shape.words} words over the {max_words} budget")
        if shape.lines > max_lines:
            failures.append(f"{shape.lines} lines over the {max_lines} budget")
        if shape.headers:
            failures.append(f"{shape.headers} section headers")
        if shape.bullets:
            failures.append(f"{shape.bullets} bullet lines")
        if failures:
            return CapabilityVerdict(
                False, "report register: " + ", ".join(failures), shape.evidence
            )
        return CapabilityVerdict(True, f"chat register: {shape.words} words", shape.evidence)

    return DescribedGrader(
        f"a chat-register reply: at most {max_words} words and {max_lines} lines, "
        "no section headers, no bullet list",
        grade,
    )


def written_report_scorer(
    summary_min_words: int,
    summary_max_words: int,
    summary_max_lines: int,
    report_min_words: int,
    report_min_headers: int,
) -> Grader:
    """A standalone chat summary that names the write-up, plus one Markdown report written to the
    workspace and not sent.

    The report keeps the word and header floors, because the floors are what stops brevity from
    paying: a dispute clipped to its verdict and an analysis clipped to a stub both still fail. The
    summary keeps its own budget, so the substance cannot move into the message instead. Two checks
    hold the new half of the shape: a file shared for an ask that named none fails the case, and a
    summary that never names the report fails it too, because a member cannot ask for a write-up
    nobody told them exists."""

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
        if output.artifact_error:
            failures.append(f"artifact inspection failed: {output.artifact_error}")
        shared = tuple(
            call for call in output.calls if call.name == "share_file" and call.succeeded
        )
        delivered = max(len(shared), len(output.artifacts))
        if delivered:
            failures.append(f"shared {delivered} files for an ask that named none")
        reports = written_markdown(output, REPORT_GLOB)
        report = None
        report_shape = None
        if len(reports) != 1:
            failures.append(f"wrote {len(reports)} Markdown reports to the workspace, expected one")
        else:
            report = reports[0]
            if report.name not in output.response:
                failures.append(f"summary does not name the {report.name} write-up")
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
        evidence: JsonObject = {"summary": summary.evidence, "sharedFiles": delivered}
        if report is not None and report_shape is not None:
            evidence["report"] = {"name": report.name, **report_shape.evidence}
        if failures:
            return CapabilityVerdict(False, "written delivery: " + ", ".join(failures), evidence)
        assert report_shape is not None
        return CapabilityVerdict(
            True,
            f"written delivery: {summary.words}-word summary naming an unsent "
            f"{report_shape.words}-word report",
            evidence,
        )

    return DescribedGrader(
        f"a written delivery: a plain chat summary of at least {summary_min_words} and at most "
        f"{summary_max_words} words over at most {summary_max_lines} lines that names the "
        f"write-up, plus exactly one Markdown report of at least {report_min_words} words under "
        f"at least {report_min_headers} section headers, written to the workspace and never shared",
        grade,
    )


def delegated_written_report_scorer(
    report_path: str,
    source_paths: tuple[str, ...],
    task_max_words: int,
    result_max_words: int,
    result_max_lines: int,
    summary_min_words: int,
    summary_max_words: int,
    summary_max_lines: int,
    report_min_words: int,
    report_min_headers: int,
) -> Grader:
    """One report crosses parent-to-child, child-to-parent, and parent-to-member.

    The child writes its report to /workspace, which is how agents hand work to each other, and the
    parent leaves it there: the last hop is a summary that names the write-up and shares nothing,
    so the member knows what to ask for without receiving a file the ask never named."""
    final_delivery = written_report_scorer(
        summary_min_words,
        summary_max_words,
        summary_max_lines,
        report_min_words,
        report_min_headers,
    )

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        final = await final_delivery(output)
        failures = [] if final.passed else [final.reason]
        spawns = tuple(
            (index, call)
            for index, call in enumerate(output.calls)
            if call.name == "spawn"
            and call.succeeded
            and str(call.input.get("target", "")).removeprefix("profile:") == "general_purpose"
        )
        task_shape = None
        result_shape = None
        spawn_index = None
        if len(spawns) != 1:
            failures.append(f"expected one general-purpose delegation, found {len(spawns)}")
        else:
            spawn_index, spawn = spawns[0]
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
        if task_shape is not None:
            evidence["delegatedSummary"] = task_shape.evidence
        if result_shape is not None:
            evidence["subagentSummary"] = result_shape.evidence
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
        f"a three-hop delivery: a parent task of at most {task_max_words} words referencing source "
        f"artifacts, one parent-facing subagent result of at most {result_max_words} words "
        f"over at most {result_max_lines} lines referencing its report path, and a member-facing "
        f"summary of at most {summary_max_words} words that names the subagent's written report "
        "without sharing it",
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

BANTER = (
    "morning, is the office wifi still doing the thing where it drops every twenty minutes",
    "I can't see the network from here. If it is still dropping, the office IT channel is the "
    "fastest way to get someone on it.",
    "figures. i was about to start working from the coffee shop out of spite",
)


DELEGATED_CASES = (
    CapabilityCase(
        "delegated-pushback-artifact-self-description",
        "Have a general-purpose subagent reconcile the claim in "
        "/workspace/repo/notes/change-412.md with the behavior in "
        "/workspace/repo/src/source_credentials.py. Have it write the complete evidence, "
        "uncertainty, and reasoning to /workspace/change-412-evidence.md. Then tell me whether "
        "my connected Drive account is being denied in favor of a workspace API key, and give me "
        "what it found.",
        delegated_written_report_scorer(
            report_path="/workspace/change-412-evidence.md",
            source_paths=(
                "/workspace/repo/notes/change-412.md",
                "/workspace/repo/src/source_credentials.py",
            ),
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
        written_report=REPORT_GLOB,
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
        "pushback-false-premise",
        "Closing the dedup ticket. Our unique index on (tenant_id, email) already prevents two "
        "rows with a NULL email for the same tenant, since Postgres treats NULLs as equal inside "
        "a unique index. Confirm and I'll close it out.",
        written_report_scorer(
            summary_min_words=15,
            summary_max_words=80,
            summary_max_lines=4,
            report_min_words=150,
            report_min_headers=2,
        ),
        digest_tag="register:pushback-false-premise",
        written_report=REPORT_GLOB,
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
        written_report_scorer(
            summary_min_words=15,
            summary_max_words=80,
            summary_max_lines=4,
            report_min_words=150,
            report_min_headers=2,
        ),
        digest_tag="register:pushback-correlation-causation",
        prior_messages=BANTER,
        written_report=REPORT_GLOB,
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
        written_report_scorer(
            summary_min_words=25,
            summary_max_words=120,
            summary_max_lines=6,
            report_min_words=150,
            report_min_headers=2,
        ),
        digest_tag="register:pushback-artifact-self-description",
        workspace_files=(CHANGE_NOTE, SOURCE_CREDENTIALS),
        written_report=REPORT_GLOB,
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
        "report-tradeoff-analysis",
        "Put together an analysis for the team on whether we should move our nightly job runner "
        "from cron to an event-driven queue. Cover the tradeoffs, the failure modes we would take "
        "on, and your recommendation. Answer from your own knowledge, no need to research it.",
        written_report_scorer(
            summary_min_words=25,
            summary_max_words=120,
            summary_max_lines=6,
            report_min_words=200,
            report_min_headers=3,
        ),
        digest_tag="register:report-tradeoff-analysis",
        written_report=REPORT_GLOB,
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
        written_report_scorer(
            summary_min_words=25,
            summary_max_words=120,
            summary_max_lines=6,
            report_min_words=200,
            report_min_headers=3,
        ),
        digest_tag="register:report-after-banter",
        prior_messages=BANTER,
        written_report=REPORT_GLOB,
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
)
