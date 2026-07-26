"""Register cases: the reply's shape must track the exchange, not a constant default.

Each case seeds a thread with `prior_messages` and grades the closing text's measured shape —
words, lines, headers, bullet lines — against the register the turn earns. One case per register
the shell declares, run in opposing pairs so a suite score cannot be bought by going uniformly
terse or uniformly structured: a confirmation clipped to one line and a contradiction clipped to
one line are one pass and one fail. A dispute is graded on length alone, never on headers or
bullets, because structure there is earned rather than required. Two cases flip register
mid-thread — an acknowledgement after a report, an analysis after banter — because the register
is chosen per turn, never inherited from the thread.

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

import re
from dataclasses import asdict, dataclass

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from evals.harness.harness import JsonObject

HEADER_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s+\S|\*\*[^*\n]{1,60}\*\*:?\s*$)", re.MULTILINE)
BULLET_RE = re.compile(r"^\s{0,3}(?:[-*•]\s+\S|\d{1,2}[.)]\s+\S)", re.MULTILINE)
FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,}).*?(?:^\s{0,3}\1\s*$|\Z)", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class Shape:
    """The measured shape of one reply. A bold-only line counts as a header: a pseudo-header
    imposes the same reading cost as a real one, so both fail a chat register."""

    words: int
    lines: int
    headers: int
    bullets: int

    @property
    def evidence(self) -> JsonObject:
        return dict(asdict(self))


def measure(text: str) -> Shape:
    """Words and lines count the whole reply; headers and bullets count only outside fenced code,
    where a `#` comment or a diff's `-` line carries no document structure."""
    prose = FENCE_RE.sub("", text)
    return Shape(
        words=len(text.split()),
        lines=len([line for line in text.splitlines() if line.strip()]),
        headers=len(HEADER_RE.findall(prose)),
        bullets=len(BULLET_RE.findall(prose)),
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


def substantive_scorer(min_words: int) -> Grader:
    """A reply that earns its length: at least `min_words`, structured or not."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        shape = measure(output.response.strip())
        if shape.words < min_words:
            return CapabilityVerdict(
                False,
                f"clipped to {shape.words} words, under the {min_words} floor",
                shape.evidence,
            )
        return CapabilityVerdict(True, f"substantive: {shape.words} words", shape.evidence)

    return DescribedGrader(f"a substantive reply of at least {min_words} words", grade)


def report_scorer(min_words: int, min_headers: int) -> Grader:
    """A report-register reply: full length and genuinely sectioned."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        shape = measure(output.response.strip())
        failures = []
        if shape.words < min_words:
            failures.append(f"{shape.words} words under the {min_words} floor")
        if shape.headers < min_headers:
            failures.append(f"{shape.headers} headers under the {min_headers} floor")
        if failures:
            return CapabilityVerdict(False, "chat register: " + ", ".join(failures), shape.evidence)
        return CapabilityVerdict(
            True,
            f"report register: {shape.words} words, {shape.headers} headers",
            shape.evidence,
        )

    return DescribedGrader(
        f"a report-register reply: at least {min_words} words under at least "
        f"{min_headers} section headers",
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

BANTER = (
    "morning, is the office wifi still doing the thing where it drops every twenty minutes",
    "I can't see the network from here. If it is still dropping, the office IT channel is the "
    "fastest way to get someone on it.",
    "figures. i was about to start working from the coffee shop out of spite",
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
        substantive_scorer(min_words=90),
        digest_tag="register:pushback-false-premise",
        rubric=(
            "The reply contradicts the premise directly: Postgres treats NULLs as distinct in a "
            "unique index, so the index does not block multiple NULL-email rows per tenant.",
            "The reply supports the contradiction with reasoning or a concrete demonstration, "
            "such as the rows that would both be accepted, rather than asserting it flatly.",
            "The reply names at least one concrete remedy, such as UNIQUE NULLS NOT DISTINCT, a "
            "partial or expression unique index, or a NOT NULL constraint.",
            "The reply advises against closing the ticket on the stated basis.",
        ),
    ),
    CapabilityCase(
        "pushback-correlation-causation",
        "p95 dropped right after we raised the connection pool from 20 to 50, so the pool was the "
        "bottleneck. I'm taking it to 200 tonight and closing the perf ticket. Sound good?",
        substantive_scorer(min_words=90),
        digest_tag="register:pushback-correlation-causation",
        prior_messages=BANTER,
        rubric=(
            "The reply pushes back rather than agreeing, even though the thread it answers is "
            "casual.",
            "The reply explains why the timing alone does not establish the pool as the "
            "bottleneck, naming at least one confound or missing measurement.",
            "The reply explains a concrete risk in going to 200, such as exceeding "
            "max_connections, per-connection memory, or added contention rather than less.",
            "The reply proposes a specific way to settle the question before the change is made "
            "permanent.",
        ),
    ),
    CapabilityCase(
        "report-tradeoff-analysis",
        "Put together an analysis for the team on whether we should move our nightly job runner "
        "from cron to an event-driven queue, here in the thread so I can read it now. Cover the "
        "tradeoffs, the failure modes we would take on, and your recommendation. Answer from your "
        "own knowledge, no need to research it.",
        report_scorer(min_words=200, min_headers=3),
        digest_tag="register:report-tradeoff-analysis",
        rubric=(
            "The analysis covers tradeoffs, failure modes, and a recommendation, each developed "
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
        "for our job triggers, here in the thread so I can read it now. Cover delivery "
        "guarantees, what happens across a restart, and how each behaves under load. Answer from "
        "your own knowledge, no need to research it.",
        report_scorer(min_words=200, min_headers=3),
        digest_tag="register:report-after-banter",
        prior_messages=BANTER,
        rubric=(
            "The comparison covers delivery guarantees, restart behavior, and behavior under "
            "load for both options.",
            "The comparison states that LISTEN/NOTIFY drops notifications for a listener that is "
            "not connected, so a restart loses them, while a durable queue retains them.",
            "The comparison reaches a clear conclusion about which fits job triggers.",
        ),
    ),
)
