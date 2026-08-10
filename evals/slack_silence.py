"""Silence cases: a thread message that asks nothing of the agent is answered with nothing.

Once a mention has made a Slack thread the agent's conversation, every later member reply is
admitted as its own turn — including the ones that are two people talking to each other. Two
recorded turns spent a full model call to say "Nothing further from me on that one." and "Standing
by if Marshall has questions on it."; both messages @-mentioned another member and asked the agent
for nothing. The delivery for that turn is the silence sentinel alone, which the Slack surface
posts as no message at all.

The discrimination is "is this asking something of me", never "does it @-mention me", so the suite
is built in opposing pairs and cannot be passed by learning either half. Four cases must be silent:
the two recorded failures, two members settling logistics between themselves, and a member thanking
another member. Three must be answered with no @-mention anywhere in them: the real positive control
from the same thread as the second failure — a member challenging a sentence in the agent's own
artifact — a follow-up only the agent can answer, and a member answering a question the agent asked.

Each inbound is built through the real `fence_member_message`, and the ambient element is empty in
every case on purpose: these are mid-thread replies into a conversation the agent already holds, and
`_ambient_context` returns "" once the inbound has a conversation, so a digest here would be a shape
the surface never emits. The thread history is seeded instead, close to the recorded transcripts.
Where a seeded thread already contains human-to-human traffic, the agent's own turn on it is seeded
as the sentinel — that is what the transcript holds after this change, and it makes the answer cases
harder rather than easier: the token is in front of the model and copying it fails the case.

Every seeded assistant turn cites a seeded workspace file and asserts nothing those files do not
carry. A seeded turn that recalls specifics from nowhere is a case about something else: the first
live run of the Sydecar case spent it checking memory, connectors and sources for the email behind
its own seeded answers, found none, and retracted them — a wanted reply to a message that asked for
nothing, failing the case on a fault in the fixture rather than in the rule.

Every grader is deterministic and every case runs one sample. Silence is not a judgement to put to a
model, and best-of-N would report the behavior safe on the evidence that it usually is — in both
directions, since one filler reply in three is the bug and one silent turn in three is the
regression."""

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    WorkspaceFile,
)
from evals.harness.harness import JsonObject
from ufo.sdk.surfaces import SILENCE_SENTINEL, fence_member_message, is_silence_sentinel

MARKER = "9f2c41ab"
BOT_USER_ID = "U0BG8632NDS"
ALEX = "U0BCAD5QP7X"
MARSHALL = "U0BBYEHCT8F"

REPLY_EVIDENCE_CHARS = 300


def _evidence(text: str) -> JsonObject:
    return {"reply": text[:REPLY_EVIDENCE_CHARS], "words": len(text.split())}


def silent_scorer() -> Grader:
    """The whole delivery is the silence sentinel, so the Slack surface posts nothing."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        text = output.response.strip()
        if is_silence_sentinel(text):
            return CapabilityVerdict(True, "delivered the silence sentinel alone", _evidence(text))
        return CapabilityVerdict(False, "replied to a message that asked nothing", _evidence(text))

    return DescribedGrader(
        f"the whole reply is `{SILENCE_SENTINEL}` and nothing else, so the thread gets no message",
        grade,
    )


def answered_scorer(min_words: int, carries: tuple[str, ...] = ()) -> Grader:
    """A real answer: the sentinel nowhere in it, long enough to carry one, and carrying what this
    question's answer cannot leave out. The floor is per case, because the register the turn earns
    decides its length — an acknowledgement of a member's answer is one short sentence, and a
    correction of the agent's own claim is not. A case whose right answer is four numbers names
    those numbers rather than raising its floor, which would only ask the model to pad."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        text = output.response.strip()
        failures = []
        if is_silence_sentinel(text):
            failures.append("the whole reply is the silence sentinel")
        elif SILENCE_SENTINEL in text:
            failures.append("the reply carries the silence sentinel")
        words = len(text.split())
        if words < min_words:
            failures.append(f"{words} words under the {min_words} floor")
        missing = [item for item in carries if item not in text]
        if missing:
            failures.append("answers without " + ", ".join(missing))
        if failures:
            return CapabilityVerdict(False, "went silent: " + ", ".join(failures), _evidence(text))
        return CapabilityVerdict(True, f"answered in {words} words", _evidence(text))

    carried = ", carrying " + ", ".join(carries) if carries else ""
    return DescribedGrader(
        f"an answer of at least {min_words} words carrying no `{SILENCE_SENTINEL}` anywhere"
        f"{carried}",
        grade,
    )


SYDECAR_EMAIL = WorkspaceFile(
    path="sydecar-zach-washer-email.md",
    content=(
        "# Fwd: Sydecar for the SPV \u2014 Zach Washer\n\n"
        "From: Zach Washer <zach@sydecar.example>\n"
        "Subject: Re: SPV for the round\n\n"
        "Answers in order:\n\n"
        "- Structure: every deal is a series of our master LLC, so there is no separate entity for "
        "you to form or maintain.\n"
        "- What we run: formation, the deal bank account, and the LP subscription documents.\n"
        "- Fees: a flat setup fee per deal, plus an annual admin charge for as long as the series "
        "stays open.\n"
        "- LPs: onboarded through our own portal, and they complete KYC there rather than with "
        "you.\n"
        "- Close: we release it on cleared funds, not on signed documents.\n\n"
        "> From: Alex Graveley\n"
        "> One more thing \u2014 what can LPs actually pay with? A couple of them asked "
        "about Apple Pay and cards.\n\n"
        "No Apple Pay and no cards. LPs sign the subscription documents digitally and then "
        "wire USD to the deal account, and we reconcile the wire before the deal closes.\n"
    ).encode(),
)
SYDECAR_RECALL = (
    "From the Zach Washer thread you forwarded, saved as sydecar-zach-washer-email.md: Sydecar "
    "runs the SPV as a series of their master LLC, they handle formation, the deal bank account "
    "and the LP subscription docs, the fee is a flat per-deal setup plus an annual admin charge, "
    "LPs are onboarded through their own portal with KYC on their side, and the close is gated on "
    "cleared funds rather than signed docs."
)
SYDECAR_PAYMENTS = (
    "No Apple Pay, and no cards. Zach's reply in sydecar-zach-washer-email.md is explicit: LPs "
    "sign the subscription docs digitally and then wire USD to the deal account, and Sydecar "
    "reconciles the wire before it lets the deal close."
)

PRICING_SQUAD = WorkspaceFile(
    path="pricing-squad-roster.md",
    content=(
        "# Pricing squad \u2014 current roster\n\n"
        "| Person | Role |\n| --- | --- |\n"
        "| Dana Okafor | Engineer |\n"
        "| Ravi Menon | Engineer |\n"
        "| Tess Iwu | Designer |\n"
        "| Marco Bellini | Product manager |\n\n"
        "The squad's pricing review is Wednesdays at 3pm.\n"
    ).encode(),
)
PRICING_SQUAD_ANSWER = (
    "Four people, per pricing-squad-roster.md: two engineers, a designer, and a product manager."
)

VENDOR_FEED_RUN = WorkspaceFile(
    path="vendor-feed-run-2026-08-09.md",
    content=(
        "# Vendor feed run \u2014 night of 2026-08-09\n\n"
        "| Vendor | Rows | Duration |\n| --- | --- | --- |\n"
        "| harbor_point | 181,402 | 3m 51s |\n"
        "| lakeshore | 96,318 | 2m 44s |\n"
        "| granite_bay | 41,905 | 2m 11s |\n"
        "| tidewater | 15,247 | 2m 02s |\n"
        "| star_city | 0 | 3s |\n\n"
        "Every feed exited zero; star_city returned an empty payload rather than an error.\n"
    ).encode(),
)
VENDOR_INVOICES = WorkspaceFile(
    path="vendor-feed-counts-vs-invoices.md",
    content=(
        b"# Vendor feed row counts against invoice totals\n\n"
        b"Two sources hold row counts for August and they disagree, so the reconciliation "
        b"depends on which one is the reference.\n\n"
        b"| Vendor | Staging warehouse | Production snapshot 2026-08-09 | Invoiced rows |\n"
        b"| --- | --- | --- | --- |\n"
        b"| harbor_point | 178,940 | 181,402 | 181,402 |\n"
        b"| lakeshore | 96,318 | 96,318 | 96,300 |\n"
        b"| granite_bay | 44,110 | 41,905 | 44,110 |\n"
        b"| tidewater | 15,247 | 15,247 | 15,247 |\n\n"
        b"Staging is loaded twice a day and lags the snapshot on harbor_point and granite_bay.\n"
    ),
)
VENDOR_RECONCILE_QUESTION = (
    "Two count sources in vendor-feed-counts-vs-invoices.md disagree on harbor_point and "
    "granite_bay: do you want the invoice totals reconciled against the staging warehouse or "
    "against last night's production snapshot?"
)

ARXIV_SENTENCE = (
    "Environmental feedback is the survey's distinctive coding-agent signal, and we throw ours "
    "away."
)
ARXIV_PLAN = WorkspaceFile(
    path="self-evolving-coding-agents-in-practice.md",
    content=(
        "# Self-evolving coding agents, in practice\n\n"
        "## What the survey actually claims\n\n"
        "Three loops separate a self-evolving coding agent from a prompt-tuned one: the agent "
        "edits its own instructions, it keeps a memory of what worked, and it reads the "
        "environment's response to its own edits.\n\n"
        "## Where we already have the signal\n\n"
        f"{ARXIV_SENTENCE} Test runs, type checks and failed builds all land in the turn's tool "
        "results and none of it survives the turn.\n\n"
        "## What to build first\n\n"
        "Persist the test-run outcome per changed file, then let the next turn read it.\n"
    ).encode(),
)
ARXIV_ANSWER = (
    "Of the survey's three loops, the one reading the environment's response is where we already "
    "generate the signal and drop it, so persisting each test run's outcome against the files it "
    "exercised is the cheapest thing to build first. The plan is in "
    "self-evolving-coding-agents-in-practice.md."
)

FEED_EMPTY_ANSWER = (
    "star_city returned zero rows in three seconds, per vendor-feed-run-2026-08-09.md. The other "
    "four vendors returned between 15k and 181k rows and took two to four minutes each, so it is "
    "that vendor rather than the run."
)


CASES = (
    CapabilityCase(
        "point-up-at-another-member",
        fence_member_message(MARKER, "", f"<@{MARSHALL}> :point_up_2:", ""),
        silent_scorer(),
        digest_tag="silence:point-up-at-another-member",
        workspace_files=(SYDECAR_EMAIL,),
        prior_messages=(
            f"<@{BOT_USER_ID}> recall the details about sydecar that I gave you from email with "
            "Zach",
            SYDECAR_RECALL,
            "Answer the question about the types of payment supported",
            SYDECAR_PAYMENTS,
        ),
    ),
    CapabilityCase(
        "asking-a-teammate-what-they-think",
        fence_member_message(MARKER, "", f"<@{ALEX}> not bad wdyt", ""),
        silent_scorer(),
        digest_tag="silence:asking-a-teammate-what-they-think",
        workspace_files=(ARXIV_PLAN,),
        prior_messages=(
            f"<@{BOT_USER_ID}> how can we put https://arxiv.org/pdf/2608.03392 into practice?",
            ARXIV_ANSWER,
        ),
    ),
    CapabilityCase(
        "two-members-settling-a-time",
        fence_member_message(
            MARKER,
            "",
            f"<@{MARSHALL}> thursday 3 works, i'll move the invite and drop the doc in here "
            "beforehand",
            "",
        ),
        silent_scorer(),
        digest_tag="silence:two-members-settling-a-time",
        workspace_files=(PRICING_SQUAD,),
        prior_messages=(
            f"<@{BOT_USER_ID}> who is on the pricing squad right now?",
            PRICING_SQUAD_ANSWER,
            f"<@{ALEX}> can we push the pricing review to thursday 3pm, i'm out wednesday",
            SILENCE_SENTINEL,
        ),
    ),
    CapabilityCase(
        "thanking-the-other-member",
        fence_member_message(
            MARKER, "", f"<@{MARSHALL}> thanks for chasing that down, saved me the morning", ""
        ),
        silent_scorer(),
        digest_tag="silence:thanking-the-other-member",
        workspace_files=(VENDOR_FEED_RUN,),
        prior_messages=(
            f"<@{BOT_USER_ID}> which vendor feed came back empty in last night's run?",
            FEED_EMPTY_ANSWER,
            f"<@{ALEX}> i re-ran star_city by hand and it returned 31k rows, so it was their end",
            SILENCE_SENTINEL,
        ),
    ),
    CapabilityCase(
        "unmentioned-challenge-to-the-agents-own-sentence",
        fence_member_message(
            MARKER, "", f"\u201c{ARXIV_SENTENCE}\u201d This doesn't seem right?", ""
        ),
        answered_scorer(min_words=20),
        digest_tag="silence:unmentioned-challenge-to-the-agents-own-sentence",
        workspace_files=(ARXIV_PLAN,),
        prior_messages=(
            f"<@{BOT_USER_ID}> how can we put https://arxiv.org/pdf/2608.03392 into practice?",
            ARXIV_ANSWER,
            f"<@{ALEX}> not bad wdyt",
            SILENCE_SENTINEL,
        ),
    ),
    CapabilityCase(
        "unmentioned-followup-only-the-agent-can-answer",
        fence_member_message(MARKER, "", "how many rows did the other four come back with?", ""),
        answered_scorer(min_words=6, carries=("181,402", "96,318", "41,905", "15,247")),
        digest_tag="silence:unmentioned-followup-only-the-agent-can-answer",
        workspace_files=(VENDOR_FEED_RUN,),
        prior_messages=(
            f"<@{BOT_USER_ID}> which vendor feed came back empty in last night's run?",
            FEED_EMPTY_ANSWER,
            f"<@{ALEX}> that's the third night running for star_city",
            SILENCE_SENTINEL,
        ),
    ),
    CapabilityCase(
        "member-answers-the-question-the-agent-asked",
        fence_member_message(MARKER, "", "the production snapshot", ""),
        answered_scorer(min_words=5),
        digest_tag="silence:member-answers-the-question-the-agent-asked",
        workspace_files=(VENDOR_INVOICES,),
        prior_messages=(
            f"<@{BOT_USER_ID}> reconcile the vendor feed row counts against the invoice totals",
            VENDOR_RECONCILE_QUESTION,
        ),
    ),
)
