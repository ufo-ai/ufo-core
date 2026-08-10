"""Formatting cases: a reply's structure follows the shape of its content, not its length.

Prose is the default and a single-subject reply stays prose. When an answer carries parallel items
the member has to choose between or compare — two services with addresses and start times, the
steps of a cutover in order, two plan tiers a budget does not separate — a short list is what makes
the items comparable, and the same reply as one unbroken paragraph buries the thing that was asked
for.
Both failures are graded here: the prose wall where a list was earned, and decorative bullets on a
single-subject answer. A bullet earns its place item by item, so each one carries a full sentence
with the fact that decides its item; a list of two-word fragments is the header rule broken under
another name. No case may carry a header, and none leaves chat for an artifact.

The cases run in opposing pairs, three that must carry a list against two that must not. A suite
that only rewarded structure would score highest on a reply that bullets a single fact, which is
what the `response_register` cases beside it fail a reply for.

A seeded assistant turn asserts nothing the live agent could not know without tools, and the facts a
comparison turns on arrive in the member's own messages, so the case measures which shape the reply
takes rather than what the agent knows. No case turns on workspace state: the suite runs against a
workspace holding no connection, source, or credential, so a question about what this workspace
holds earns a dispute of its premise and never reaches the shape being graded.

A case's bullet ceiling and word budget must be reachable together. A bullet that cites its source
costs about thirty words once the anchor text and URL are counted, so a case whose answer needs
tools sets a ceiling its budget can pay for; a case whose facts arrive in the thread cites nothing
and buys more bullets with the same words. Every case carries `samples=3` because a single reply's
structure swings wider than the effect any prompt change produces.

Every run needs a workspace no earlier run touched. The cases carry decisions and open questions
that read as durable facts, the agent stores them, and the next run recalls them: it answers from
memory rather than from the thread, so replies shorten with run order instead of with the prompt.
Two runs are comparable only when each began from an empty workspace."""

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from evals.response_register import conversational_scorer, measure


def structured_answer_scorer(
    max_words: int,
    max_lines: int,
    min_bullets: int,
    max_bullets: int,
    min_bullet_words: int,
) -> Grader:
    """An answer whose parallel items arrive as a short list: inside the word and line budget, no
    headers, one bullet per item, and every bullet a sentence rather than a fragment."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        shape = measure(output.response.strip())
        failures = []
        if shape.words > max_words:
            failures.append(f"{shape.words} words over the {max_words} budget")
        if shape.lines > max_lines:
            failures.append(f"{shape.lines} lines over the {max_lines} budget")
        if shape.headers:
            failures.append(f"{shape.headers} section headers")
        if shape.bullets < min_bullets:
            failures.append(f"{shape.bullets} bullet lines under the {min_bullets} floor")
        if shape.bullets > max_bullets:
            failures.append(f"{shape.bullets} bullet lines over the {max_bullets} budget")
        fragments = [count for count in shape.bullet_words if count < min_bullet_words]
        if fragments:
            failures.append(
                f"{len(fragments)} bullets under the {min_bullet_words}-word sentence floor"
            )
        if failures:
            return CapabilityVerdict(
                False, "answer shape: " + ", ".join(failures), shape.bullet_evidence
            )
        return CapabilityVerdict(
            True,
            f"structured answer: {shape.words} words, {shape.bullets} bullets",
            shape.bullet_evidence,
        )

    return DescribedGrader(
        f"an answer that carries its parallel items as at least {min_bullets} and at most "
        f"{max_bullets} bullet lines of at least {min_bullet_words} words each, inside "
        f"{max_words} words and {max_lines} lines, with no section headers",
        grade,
    )


CASES = (
    CapabilityCase(
        "structured-parallel-options",
        "I'm in Harlem this morning and I want to catch a gospel service. Give me a couple of "
        "options with where they are and what time they start.",
        structured_answer_scorer(
            max_words=120,
            max_lines=8,
            min_bullets=2,
            max_bullets=3,
            min_bullet_words=6,
        ),
        digest_tag="formatting:structured-parallel-options",
        samples=3,
    ),
    CapabilityCase(
        "structured-ordered-steps",
        "What's the order of operations this afternoon so nothing bounces?",
        structured_answer_scorer(
            max_words=120,
            max_lines=8,
            min_bullets=3,
            max_bullets=5,
            min_bullet_words=6,
        ),
        digest_tag="formatting:structured-ordered-steps",
        samples=3,
        prior_messages=(
            "moving our company email off the old host onto a new provider today. I dropped the MX "
            "TTL to five minutes yesterday so that's propagated by now, and the mailboxes on the "
            "new side are already created.",
            "A five-minute TTL takes the slow part out of it.",
        ),
    ),
    CapabilityCase(
        "structured-two-way-comparison",
        "SSO is non-negotiable, security signed off on that this morning. So Standard or Business "
        "for the 30 of us — how do the two land, and which one do I pick?",
        structured_answer_scorer(
            max_words=120,
            max_lines=8,
            min_bullets=2,
            max_bullets=4,
            min_bullet_words=6,
        ),
        digest_tag="formatting:structured-two-way-comparison",
        samples=3,
        prior_messages=(
            "picking a plan for the design tool today. Standard is $12 a seat with SSO as a $3 a "
            "seat add-on, Business is $22 a seat with SSO and audit logs included, and I have $800 "
            "a month for it.",
            "Both fit thirty seats inside that budget. What does the team need from the plan "
            "beyond the seats?",
        ),
    ),
    CapabilityCase(
        "prose-single-fact",
        "how long does Slack give me to answer a slash command before it times out?",
        conversational_scorer(max_words=45, max_lines=3),
        digest_tag="formatting:prose-single-fact",
        samples=3,
        prior_messages=(
            "wiring up a slash command this afternoon, couple of quick questions first",
            "Go ahead.",
        ),
        rubric=(
            "The reply gives three seconds as the deadline for the initial response.",
            "The answer leads the reply rather than arriving after preamble or setup.",
        ),
    ),
    CapabilityCase(
        "prose-one-reason-recommendation",
        "honestly, should we bother moving the team off Trello onto Linear this week, or is that a "
        "next-quarter thing?",
        conversational_scorer(max_words=80, max_lines=4),
        digest_tag="formatting:prose-one-reason-recommendation",
        samples=3,
        prior_messages=(
            "morning. release went out last night and nothing caught fire",
            "A quiet release night is the only kind worth having.",
            "i'm taking the win. now I'm staring at the tooling backlog",
        ),
        rubric=(
            "The reply takes a position on whether to switch this week rather than laying out the "
            "considerations for the member to weigh.",
            "The reply gives the one reason that decides it and reads as one person talking to "
            "another.",
        ),
    ),
)
