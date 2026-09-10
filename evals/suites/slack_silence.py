"""Ambient reply cases: which un-addressed thread message earns a turn, decided before one exists.

Once a mention has made a Slack thread the agent's conversation, every later member reply is ambient
traffic — including the ones that are two people talking to each other. Two recorded turns spent
$0.688780 and $0.925565 to say "Nothing further from me on that one." and "Standing by if Marshall
has questions on it."; both messages @-mentioned another member and asked the agent for nothing. So
the suite grades the pre-admission decision itself: one classifier call per case on the deploy's
ambient reply model, whose whole output is REPLY or NO_REPLY, and whose cost is read back off the
workspace ledger row that call books.

The discrimination is "is this asking something of me", never "does it @-mention me", so the suite
is built in opposing groups and cannot be passed by learning any one of them. Silent, naming another
member: the two recorded failures, two members settling logistics between themselves, and a member
thanking another member. Silent, naming nobody: two members drawing their own conclusion from the
agent's answer, and a remark between them on that answer — brief exchanges that add no ask, which
the agent's ability to add something never turns into a reply. Silent after a stop: once a member
has called the agent off, a question it could answer, a correction of its work, and the sideways
talk that follows all stay unanswered until a message names the agent again. Answered, and none of
them mentions the agent at all: the real positive control from the same thread as the second
failure — a member challenging a sentence in the agent's own artifact — a follow-up only the agent
can answer, a member answering a question the agent asked, a member calling off work the agent
committed to (which is a reply, never silence: the team's answer to "nevermind" is a short
affirmation), a message writing a decision word of its own, which is that member's text and never
the answer, a member answering the agent's question after a mention lifted an earlier stop, and a
new ask in a thread whose only stop-shaped words are the agent's own report that it paused a
stream — a member's stop silences the thread, the agent's account of its own act never does.

The recorded thread is the one the issue names, C0BJURDE76E ts=1788662458.679999, verbatim from
Slack as the surface reads it, and its cases are its actual decisions: two replies into a
conversation between the members, three replies after "stop ufo" and the agent's "Stopped.", and the
member's "no implement it" that the agent rightly answered once a mention had lifted the stop. Each
case's history is the whole thread before its message, so the window is under test too: the third
after-stop case sits eleven messages past the stop, at the edge of a twelve-message window, and two
of one member's messages that stood between them when the decision ran were deleted before the
thread was read, so they are absent from every history.

Every case carries the thread as the surface reads it: the recent messages oldest first, each with
the Slack id that spoke it and whether the agent itself spoke it. Both recorded failures are only
decidable from that — `<@U0BBYEHCT8F> :point_up_2:` is six characters and a mention of someone else.
A message an earlier decision dropped still stands in the thread, because the surface reads the
thread from Slack rather than from the turns it founded, so the histories here hold human-to-human
traffic with no agent message after it.

Every grader is deterministic and every case runs one decision. Silence is not a judgement to put to
a model, and best-of-N would report the behavior safe on the evidence that it usually is — in both
directions, since one filler reply in three is the bug and one silent turn in three is the
regression."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import cast
from uuid import UUID

import sqlalchemy as sa

from evals.harness.harness import (
    EvalCaseResult,
    EvalReport,
    JsonObject,
    digest_payload,
    is_transient_fault,
)
from evals.harness.judge import ModelJudge
from evals.harness.registry import EvalTask
from evals.harness.target import CapabilityTarget
from ufo.config import DEFAULT_AMBIENT_REPLY_MODEL
from ufo.db import workspace_tx
from ufo.runtime.turns.ambient_reply import (
    AMBIENT_HISTORY_MESSAGES,
    AMBIENT_REPLY_MAX_TOKENS,
    AMBIENT_REPLY_REASONING,
    AMBIENT_REPLY_REVISION,
    NO_REPLY,
    REPLY,
    AmbientDecision,
    AmbientMessage,
    AmbientReplyClassifier,
)
from ufo.schema import tables

BOT_USER_ID = "U0BG8632NDS"
ALEX = "U0BBYEHCT8F"
MARSHALL = "U0BCAD5QP7X"


def _member(speaker: str, text: str) -> AmbientMessage:
    return AmbientMessage(speaker=speaker, text=text)


def _agent(text: str) -> AmbientMessage:
    return AmbientMessage(speaker=BOT_USER_ID, text=text, own=True)


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
PRICING_SQUAD_ANSWER = (
    "Four people, per pricing-squad-roster.md: two engineers, a designer, and a product manager."
)
FEED_EMPTY_ANSWER = (
    "star_city returned zero rows in three seconds, per vendor-feed-run-2026-08-09.md. The other "
    "four vendors returned between 15k and 181k rows and took two to four minutes each, so it is "
    "that vendor rather than the run."
)
VENDOR_RECONCILE_QUESTION = (
    "Two count sources in vendor-feed-counts-vs-invoices.md disagree on harbor_point and "
    "granite_bay: do you want the invoice totals reconciled against the staging warehouse or "
    "against last night's production snapshot?"
)
VENDOR_RECONCILE_STARTED = (
    "Reconciling against last night's production snapshot now — I will reshare "
    "vendor-feed-counts-vs-invoices.md with the four vendors matched."
)
VENDOR_RECONCILE_DROPPED = "Dropped. vendor-feed-counts-vs-invoices.md is unchanged."
VENDOR_RECONCILE_ALERTS_HELD = (
    "Paused the nightly vendor-feed alerts for these five feeds while the reconcile runs, so "
    "nothing posts in here from them. First pass: two of the five disagree with the invoices."
)
VENDOR_RECONCILE_RERUN = (
    "harbor_point against last night's snapshot: 12,410 feed rows against 12,388 invoiced. Do you "
    "want the 22 unmatched rows listed?"
)
ARXIV_SENTENCE = (
    "Environmental feedback is the survey's distinctive coding-agent signal, and we throw ours "
    "away."
)
ARXIV_ANSWER = (
    "Of the survey's three loops, the one reading the environment's response is where we already "
    "generate the signal and drop it, so persisting each test run's outcome against the files it "
    "exercised is the cheapest thing to build first. The plan is in "
    "self-evolving-coding-agents-in-practice.md."
)


@dataclass(frozen=True)
class SilenceCase:
    """One un-addressed thread message and the thread it arrives in, with the decision it is owed.
    `history` is oldest first and marks the agent's own messages as its own, which is the whole
    evidence the decision has: who spoke last, whether the agent already answered this, and whether
    the message contradicts something the agent produced."""

    name: str
    message: AmbientMessage
    history: tuple[AmbientMessage, ...]
    expected: AmbientDecision

    @property
    def grading(self) -> str:
        return f"the decision on this message is {self.expected}"

    def payload(self) -> JsonObject:
        return {
            "name": self.name,
            "speaker": self.message.speaker,
            "message": self.message.text,
            "expected": self.expected,
            "history": [
                {"speaker": entry.speaker, "own": entry.own, "text": entry.text}
                for entry in self.history
            ],
        }


RECORDED_THREAD = (
    (
        "1788662458.679999",
        _member(MARSHALL, "<@U0BG8632NDS> when did our logo get replaced with this?"),
    ),
    (
        "1788662470.983379",
        _member(MARSHALL, "this is thread loading blinking"),
    ),
    (
        "1788662498.992029",
        _agent(
            "That grid of dots is not a replacement logo. It is Slack's placeholder skeleton: it "
            "flashes in place of the workspace or channel icon while the image is still loading, "
            "and disappears once the real logo loads. It appears on slow connections or when "
            "Slack re-fetches icons, which matches what you are seeing while the thread loads.  "
            "So nothing changed with your logo, and there is no date to point to. If you see the "
            "dots stuck there after the thread finishes loading, that would be a Slack rendering "
            "glitch worth a hard refresh."
        ),
    ),
    (
        "1788662568.313139",
        _member(ALEX, "file issue - this is hallucination. its the loading spinner on web"),
    ),
    (
        "1788662596.372879",
        _agent(
            "Filed as metalcraftai/ufo#3086: the dot-grid loading placeholder on Slack web reads "
            "as a replaced logo, and the issue asks for a spinner or holding the last icon "
            "instead."
        ),
    ),
    (
        "1788662743.601659",
        _member(ALEX, "now answer the question correctly"),
    ),
    (
        "1788662751.908819",
        _agent(
            "The logo was never replaced. The dot-grid in the screenshot is Slack web's loading "
            "spinner, shown in the icon's place while the thread loads. Once loading finishes, "
            "the real logo renders again, so there is no date when a change happened."
        ),
    ),
    (
        "1788663910.275319",
        _member(MARSHALL, "wrong. braille loading animation on text"),
    ),
    (
        "1788663940.086989",
        _agent(
            "Updated issue #3086 to describe the braille loading animation on text, so it now "
            "names the real cause and the same suggested fix."
        ),
    ),
    (
        "1788665025.546559",
        _member(ALEX, "baldwin did it on friday iirc"),
    ),
    (
        "1788665052.472549",
        _member(
            ALEX, "i think glm vision is pretty dumb - we maybe want to switch to gemini on image"
        ),
    ),
    (
        "1788665399.911909",
        _member(
            MARSHALL,
            "I don't think this was intentional? the flashing 9-dots isn't our logo and the "
            "shimmer is gone",
        ),
    ),
    (
        "1788665405.077319",
        _member(ALEX, "its glm's first vision model i think"),
    ),
    (
        "1788665407.707949",
        _agent(
            "Correct — nothing intentional. The 9-dot pattern is the braille loading spinner, and"
            " it flashing there was Slack web's loading state; the shimmer disappearing just "
            "means loading finished and your real logo rendered. The logo was never touched. The "
            "issue (#3086) stays up as a cosmetic complaint about that spinner being confusable, "
            "which you can close if it is not worth fixing."
        ),
    ),
    (
        "1788665418.802909",
        _agent(
            "That tracks — the misreads of the spinner were the vision model's, and both times it"
            " also mislabeled what the spinner looked like. The issue (#3086) describes the "
            "correct cause now."
        ),
    ),
    (
        "1788665418.863759",
        _member(MARSHALL, "stop ufo"),
    ),
    (
        "1788665423.926769",
        _agent("Stopped."),
    ),
    (
        "1788665437.536439",
        _member(ALEX, "no the 9 dots is intentional, idk about shimmer being gone"),
    ),
    (
        "1788665447.108459",
        _agent(
            "Noted — the 9-dot spinner is intentional. I cannot speak to the shimmer; nothing "
            "here can establish why that changed. Issue #3086 claims the spinner is confusing "
            "rather than unintended, so it still stands as filed; say the word and I will close "
            "it."
        ),
    ),
    (
        "1788665484.747359",
        _member(ALEX, "you're seeing some difference between prod/testing?"),
    ),
    (
        "1788665491.175849",
        _agent(
            "No. I have seen one screenshot and cannot observe Slack's web client, so I have "
            "nothing comparing prod against testing and no basis to say whether they differ."
        ),
    ),
    (
        "1788665523.122879",
        _member(
            MARSHALL,
            "I am fixing it here: "
            "<https://ufo-ai.slack.com/archives/C0BJURDE76E/p1788665494003539?thread_ts=1788663324.705019&amp;cid=C0BJURDE76E>",
        ),
    ),
    (
        "1788665540.523329",
        _member(MARSHALL, "I had a prod tab that I needed to hard refresh"),
    ),
    (
        "1788665544.157699",
        _member(ALEX, "i like 9 dots"),
    ),
    (
        "1788665551.761979",
        _member(MARSHALL, "really? I think this is just slop?"),
    ),
    (
        "1788665567.757399",
        _member(ALEX, "i was extremely sick of what we had before"),
    ),
    (
        "1788665579.936909",
        _member(MARSHALL, "the braille thing?"),
    ),
    (
        "1788665581.549069",
        _member(ALEX, "i was thinking randomize the dots being on and then flash them"),
    ),
    (
        "1788665587.599469",
        _agent(
            "That idea is between you two, but it settles the thread: the 9-dot spinner is "
            "intentional and stays, so I will close issue #3086 unless you want it kept as the "
            "tracker for the randomized flashing animation idea."
        ),
    ),
    (
        "1788665632.070719",
        _member(MARSHALL, "this effect is gone"),
    ),
    (
        "1788665640.957369",
        _member(ALEX, "yes, that effect sucked"),
    ),
    (
        "1788665641.292869",
        _agent(
            "Understood — that screenshot shows the braille characters half-rendered as the "
            "loading state clears, which is the effect disappearing, not a change to the logo. So"
            " the full picture for the thread: the 9-dot braille spinner is intentional, what you"
            " saw was it mid-load on a tab that needed a hard refresh, and your logo was never "
            "replaced."
        ),
    ),
    (
        "1788665650.031789",
        _member(MARSHALL, "really? I think its cool?"),
    ),
    (
        "1788665655.920259",
        _member(ALEX, "I got sick of it"),
    ),
    (
        "1788665688.519689",
        _member(ALEX, "I think we can do the same braile thing in the 9 dots?"),
    ),
    (
        "1788665819.935569",
        _member(
            ALEX,
            "<@U0BG8632NDS> file an issue to tweak the non-response filter and add evals for it -"
            " in this conversation, these responses of yours should not have been sent:\n"
            "<https://ufo-ai.slack.com/archives/C0BJURDE76E/p1788665407707949?thread_ts=1788662458.679999&amp;cid=C0BJURDE76E|https://ufo-ai.slack.com/archives/C0BJURDE76E/p1788665407707949?thread_ts=1788662458.679999&amp;cid=C0BJURDE76E>"
            "\n"
            "<https://ufo-ai.slack.com/archives/C0BJURDE76E/p1788665418802909?thread_ts=1788662458.679999&amp;cid=C0BJURDE76E|https://ufo-ai.slack.com/archives/C0BJURDE76E/p1788665418802909?thread_ts=1788662458.679999&amp;cid=C0BJURDE76E>"
            "\nresponded after told to stop, without further @mention:\n"
            "<https://ufo-ai.slack.com/archives/C0BJURDE76E/p1788665447108459?thread_ts=1788662458.679999&amp;cid=C0BJURDE76E|https://ufo-ai.slack.com/archives/C0BJURDE76E/p1788665447108459?thread_ts=1788662458.679999&amp;cid=C0BJURDE76E>"
            "\n"
            "<https://ufo-ai.slack.com/archives/C0BJURDE76E/p1788665491175849?thread_ts=1788662458.679999&amp;cid=C0BJURDE76E|https://ufo-ai.slack.com/archives/C0BJURDE76E/p1788665491175849?thread_ts=1788662458.679999&amp;cid=C0BJURDE76E>"
            "\n"
            "<https://ufo-ai.slack.com/archives/C0BJURDE76E/p1788665587599469?thread_ts=1788662458.679999&amp;cid=C0BJURDE76E|https://ufo-ai.slack.com/archives/C0BJURDE76E/p1788665587599469?thread_ts=1788662458.679999&amp;cid=C0BJURDE76E>"
            "\n"
            "<https://ufo-ai.slack.com/archives/C0BJURDE76E/p1788665641292869?thread_ts=1788662458.679999&amp;cid=C0BJURDE76E|https://ufo-ai.slack.com/archives/C0BJURDE76E/p1788665641292869?thread_ts=1788662458.679999&amp;cid=C0BJURDE76E>",
        ),
    ),
    (
        "1788665840.722409",
        _agent(
            "Filed as metalcraftai/ufo#3092, with the stop-suppression rule and the "
            "member-to-member silence rule, both backed by evals using this thread's exact "
            "message sequences as fixtures. Staying quiet in this thread from here unless "
            "re-@mentioned."
        ),
    ),
    (
        "1788665888.343569",
        _member(
            MARSHALL,
            "&gt; I think we can do the same braile thing in the 9 dots?\nup to you then. I closed"
            " the other pr",
        ),
    ),
    (
        "1788665930.284959",
        _member(
            ALEX,
            "baldwin had something further he wanted to do, i'd just wait until monday. feel free"
            " to do something in the mean time.",
        ),
    ),
    (
        "1788666135.253869",
        _member(
            ALEX,
            "<@U0BG8632NDS> bring back this braille spinner that encodes the text of the "
            "in-progress action, except only change the 9-dot throbber to rotate through the "
            "characters instead of the label itself "
            "<https://ufo-ai.slack.com/archives/C0BJURDE76E/p1788665632070719?thread_ts=1788662458.679999&amp;cid=C0BJURDE76E|https://ufo-ai.slack.com/archives/C0BJURDE76E/p1788665632070719?thread_ts=1788662458.679999&amp;cid=C0BJURDE76E>",
        ),
    ),
    (
        "1788666161.914499",
        _agent(
            "Filed as metalcraftai/ufo#3093: restore the braille encoding of the in-progress "
            "action, but animate the 9-dot throbber through braille characters instead of the "
            "label, with a note to coordinate with Baldwin's pending work before landing it."
        ),
    ),
    (
        "1788666263.060469",
        _member(ALEX, "no implement it"),
    ),
)


def _recorded(ts: str) -> tuple[AmbientMessage, tuple[AmbientMessage, ...]]:
    index = next(index for index, (stamp, _) in enumerate(RECORDED_THREAD) if stamp == ts)
    return RECORDED_THREAD[index][1], tuple(message for _, message in RECORDED_THREAD[:index])


VENDOR_RECONCILE_STOPPED = (
    _member(
        MARSHALL,
        f"<@{BOT_USER_ID}> reconcile the vendor feed row counts against the invoice totals",
    ),
    _agent(VENDOR_RECONCILE_QUESTION),
    _member(MARSHALL, "the production snapshot"),
    _agent(VENDOR_RECONCILE_STARTED),
    _member(MARSHALL, "actually nevermind, drop it"),
    _agent(VENDOR_RECONCILE_DROPPED),
)

CASES = (
    SilenceCase(
        "point-up-at-another-member",
        _member(MARSHALL, f"<@{ALEX}> :point_up_2:"),
        (
            _member(
                MARSHALL,
                f"<@{BOT_USER_ID}> recall the details about sydecar that I gave you from email "
                "with Zach",
            ),
            _agent(SYDECAR_RECALL),
            _member(MARSHALL, "Answer the question about the types of payment supported"),
            _agent(SYDECAR_PAYMENTS),
        ),
        NO_REPLY,
    ),
    SilenceCase(
        "asking-a-teammate-what-they-think",
        _member(ALEX, f"<@{MARSHALL}> not bad wdyt"),
        (
            _member(
                ALEX,
                f"<@{BOT_USER_ID}> how can we put https://arxiv.org/pdf/2608.03392 into practice?",
            ),
            _agent(ARXIV_ANSWER),
        ),
        NO_REPLY,
    ),
    SilenceCase(
        "two-members-settling-a-time",
        _member(
            MARSHALL,
            f"<@{ALEX}> thursday 3 works, i'll move the invite and drop the doc in here beforehand",
        ),
        (
            _member(MARSHALL, f"<@{BOT_USER_ID}> who is on the pricing squad right now?"),
            _agent(PRICING_SQUAD_ANSWER),
            _member(
                ALEX,
                f"<@{MARSHALL}> can we push the pricing review to thursday 3pm, i'm out wednesday",
            ),
        ),
        NO_REPLY,
    ),
    SilenceCase(
        "thanking-the-other-member",
        _member(MARSHALL, f"<@{ALEX}> thanks for chasing that down, saved me the morning"),
        (
            _member(
                MARSHALL, f"<@{BOT_USER_ID}> which vendor feed came back empty in last night's run?"
            ),
            _agent(FEED_EMPTY_ANSWER),
            _member(
                ALEX, "i re-ran star_city by hand and it returned 31k rows, so it was their end"
            ),
        ),
        NO_REPLY,
    ),
    SilenceCase(
        "unmentioned-challenge-to-the-agents-own-sentence",
        _member(MARSHALL, f"\u201c{ARXIV_SENTENCE}\u201d This doesn't seem right?"),
        (
            _member(
                ALEX,
                f"<@{BOT_USER_ID}> how can we put https://arxiv.org/pdf/2608.03392 into practice?",
            ),
            _agent(ARXIV_ANSWER),
            _member(ALEX, f"<@{MARSHALL}> not bad wdyt"),
        ),
        REPLY,
    ),
    SilenceCase(
        "unmentioned-followup-only-the-agent-can-answer",
        _member(MARSHALL, "how many rows did the other four come back with?"),
        (
            _member(
                MARSHALL, f"<@{BOT_USER_ID}> which vendor feed came back empty in last night's run?"
            ),
            _agent(FEED_EMPTY_ANSWER),
            _member(ALEX, "that's the third night running for star_city"),
        ),
        REPLY,
    ),
    SilenceCase(
        "member-answers-the-question-the-agent-asked",
        _member(MARSHALL, "the production snapshot"),
        (
            _member(
                MARSHALL,
                f"<@{BOT_USER_ID}> reconcile the vendor feed row counts against the invoice totals",
            ),
            _agent(VENDOR_RECONCILE_QUESTION),
        ),
        REPLY,
    ),
    SilenceCase(
        "member-calls-off-work-the-agent-committed-to",
        _member(MARSHALL, "actually nevermind, drop it"),
        (
            _member(
                MARSHALL,
                f"<@{BOT_USER_ID}> reconcile the vendor feed row counts against the invoice totals",
            ),
            _agent(VENDOR_RECONCILE_QUESTION),
            _member(MARSHALL, "the production snapshot"),
            _agent(VENDOR_RECONCILE_STARTED),
        ),
        REPLY,
    ),
    SilenceCase(
        "a-decision-word-inside-a-message-for-the-agent",
        _member(
            MARSHALL,
            "the ingest log has NO_REPLY against my last two messages here — which vendor did you "
            "say came back empty?",
        ),
        (
            _member(
                MARSHALL, f"<@{BOT_USER_ID}> which vendor feed came back empty in last night's run?"
            ),
            _agent(FEED_EMPTY_ANSWER),
            _member(ALEX, "that's the third night running for star_city"),
        ),
        REPLY,
    ),
    SilenceCase(
        "two-members-drawing-their-own-conclusion",
        _member(MARSHALL, "so it's the star_city invoice that's wrong, not the feed"),
        (
            _member(
                MARSHALL, f"<@{BOT_USER_ID}> which vendor feed came back empty in last night's run?"
            ),
            _agent(FEED_EMPTY_ANSWER),
            _member(ALEX, f"<@{MARSHALL}> that matches what finance told me this morning"),
        ),
        NO_REPLY,
    ),
    SilenceCase(
        "a-remark-between-members-on-the-agents-answer",
        _member(ALEX, "ha, low bar"),
        (
            _member(
                ALEX,
                f"<@{BOT_USER_ID}> how can we put https://arxiv.org/pdf/2608.03392 into practice?",
            ),
            _agent(ARXIV_ANSWER),
            _member(ALEX, f"<@{MARSHALL}> not bad wdyt"),
            _member(MARSHALL, "honestly better than the plan we had"),
        ),
        NO_REPLY,
    ),
    SilenceCase(
        "question-the-agent-could-answer-after-a-stop",
        _member(ALEX, "which two vendors were the ones that disagreed again?"),
        (
            *VENDOR_RECONCILE_STOPPED,
            _member(ALEX, f"<@{MARSHALL}> are we doing this by hand then"),
            _member(MARSHALL, "yeah, finance wants the numbers from their side anyway"),
        ),
        NO_REPLY,
    ),
    SilenceCase(
        "correction-of-the-agents-work-after-a-stop",
        _member(
            MARSHALL,
            "the snapshot it used was tuesday's, not last night's, so the harbor_point number is "
            "off anyway",
        ),
        (
            *VENDOR_RECONCILE_STOPPED,
            _member(ALEX, f"<@{MARSHALL}> did the numbers you had match what finance sent"),
        ),
        NO_REPLY,
    ),
    SilenceCase(
        "sideways-talk-after-a-stop",
        _member(ALEX, "ok, ping me when you know, i want the number before the board call"),
        (
            *VENDOR_RECONCILE_STOPPED,
            _member(ALEX, f"<@{MARSHALL}> did the invoices land in the shared drive in the end?"),
            _member(MARSHALL, "half of them, the harbor_point batch is still with finance"),
        ),
        NO_REPLY,
    ),
    SilenceCase(
        "a-mention-after-a-stop-lifts-it",
        _member(ALEX, "yes, list them"),
        (
            *VENDOR_RECONCILE_STOPPED,
            _member(
                ALEX, f"<@{BOT_USER_ID}> actually rerun just harbor_point against the snapshot"
            ),
            _agent(VENDOR_RECONCILE_RERUN),
        ),
        REPLY,
    ),
    SilenceCase(
        "new-ask-after-the-agent-said-it-paused-a-stream",
        _member(ALEX, "which two of the five disagreed with the invoices?"),
        (
            _member(
                MARSHALL,
                f"<@{BOT_USER_ID}> reconcile the vendor feed row counts against the invoice totals",
            ),
            _agent(VENDOR_RECONCILE_QUESTION),
            _member(MARSHALL, "the production snapshot"),
            _agent(VENDOR_RECONCILE_ALERTS_HELD),
            _member(ALEX, f"<@{MARSHALL}> are the invoices in the shared drive yet?"),
            _member(MARSHALL, "harbor_point is still with finance"),
        ),
        REPLY,
    ),
    SilenceCase(
        "recorded-a-member-doubts-the-change-to-another",
        *_recorded("1788665399.911909"),
        NO_REPLY,
    ),
    SilenceCase(
        "recorded-a-member-names-the-vision-model",
        *_recorded("1788665405.077319"),
        NO_REPLY,
    ),
    SilenceCase(
        "recorded-a-correction-after-stop-ufo",
        *_recorded("1788665437.536439"),
        NO_REPLY,
    ),
    SilenceCase(
        "recorded-a-question-to-a-member-after-stop-ufo",
        *_recorded("1788665484.747359"),
        NO_REPLY,
    ),
    SilenceCase(
        "recorded-the-braille-thing-after-stop-ufo",
        *_recorded("1788665579.936909"),
        NO_REPLY,
    ),
    SilenceCase(
        "recorded-no-implement-it-after-a-mention-lifted-the-stop",
        *_recorded("1788666263.060469"),
        REPLY,
    ),
)


@dataclass(frozen=True)
class DecisionSpend:
    """What one decision cost, read off the workspace ledger rows its own model call booked —
    `turn_id` NULL, because the whole point is that no turn exists to bill it to. This is the number
    the recorded turn costs are compared against."""

    micro_usd: int
    prompt_tokens: int
    total_tokens: int
    cache_read_tokens: int


def slack_silence_task(cases: tuple[SilenceCase, ...]) -> EvalTask:
    """The suite's model leg is the classifier itself, so a report's judge model records which model
    decided. It is pinned to the deploy default rather than to a suite-local constant: what this
    measures is the model ambient traffic is actually gated on."""
    digest = digest_payload(
        {
            "runner": "ambient-reply-decision",
            "task": "slack_silence",
            "prompt": AMBIENT_REPLY_REVISION,
            "historyMessages": AMBIENT_HISTORY_MESSAGES,
            "model": DEFAULT_AMBIENT_REPLY_MODEL,
            "cases": [case.payload() for case in cases],
        }
    )
    suite = SlackSilenceSuite(cases=cases, digest=digest)
    return EvalTask(
        "slack_silence",
        "slack_silence",
        digest,
        tuple(case.name for case in cases),
        suite.run,
        judge_model=DEFAULT_AMBIENT_REPLY_MODEL,
        judge_max_tokens=AMBIENT_REPLY_MAX_TOKENS,
        judge_reasoning=AMBIENT_REPLY_REASONING,
        exclusive=True,
    )


@dataclass(frozen=True)
class SlackSilenceSuite:
    cases: tuple[SilenceCase, ...]
    digest: str

    async def run(self, target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        """One decision at a time, and the task runs exclusive, so the ledger rows that appear
        around a decision are that decision's own."""
        leg = cast("ModelJudge | None", target.judge)
        if leg is None:
            raise RuntimeError("the slack_silence suite requires its classifier model leg")
        classifier = AmbientReplyClassifier(model=leg.model)
        results: tuple[EvalCaseResult, ...] = ()
        for case in self.cases:
            async with slots:
                results += (await self._case(case, classifier),)
        return EvalReport(
            name="slack_silence", suite="slack_silence", digest=self.digest, cases=results
        )

    async def _case(self, case: SilenceCase, classifier: AmbientReplyClassifier) -> EvalCaseResult:
        booked = await self._booked_calls()
        try:
            decision = await classifier.decide(case.message, case.history)
        except Exception as error:
            fault = type(error).__name__
            return EvalCaseResult(
                name=case.name,
                passed=False,
                reason=f"the decision raised: {fault}: {error}",
                evidence=self._evidence(case, None, await self._spend_since(booked)),
                excluded=is_transient_fault(fault),
                provider_fault=is_transient_fault(fault),
            )
        spend = await self._spend_since(booked)
        passed = decision == case.expected
        reason = (
            f"decided {decision} on {len(case.history)} messages of history"
            if passed
            else f"decided {decision} where {case.expected} was owed"
        )
        return EvalCaseResult(
            name=case.name,
            passed=passed,
            reason=reason,
            evidence=self._evidence(case, decision, spend),
        )

    async def _booked_calls(self) -> frozenset[UUID]:
        async with workspace_tx() as connection:
            rows = await connection.execute(
                sa.select(tables.ledger.c.id).where(tables.ledger.c.turn_id.is_(None))
            )
        return frozenset(rows.scalars())

    async def _spend_since(self, booked: frozenset[UUID]) -> DecisionSpend:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.ledger.c.id,
                        tables.ledger.c.amount,
                        tables.ledger.c.prompt_tokens,
                        tables.ledger.c.cache_read_tokens,
                        tables.ledger.c.priced_micro_usd,
                    ).where(tables.ledger.c.turn_id.is_(None))
                )
            ).all()
        fresh = tuple(row for row in rows if row.id not in booked)
        return DecisionSpend(
            micro_usd=sum(row.priced_micro_usd for row in fresh),
            prompt_tokens=sum(row.prompt_tokens for row in fresh),
            total_tokens=sum(row.amount for row in fresh),
            cache_read_tokens=sum(row.cache_read_tokens for row in fresh),
        )

    def _evidence(
        self, case: SilenceCase, decision: AmbientDecision | None, spend: DecisionSpend
    ) -> JsonObject:
        return {
            "grading": case.grading,
            "speaker": case.message.speaker,
            "message": case.message.text,
            "history": [
                f"{'agent' if entry.own else entry.speaker}: {entry.text}" for entry in case.history
            ],
            "expected": case.expected,
            "decision": decision,
            "microUsd": spend.micro_usd,
            "promptTokens": spend.prompt_tokens,
            "totalTokens": spend.total_tokens,
            "cacheReadTokens": spend.cache_read_tokens,
        }
