"""Three skills a member would really ask for, two of them neighbours. `escalation-triage` and
`escalation-recap` share a domain, a vocabulary and a channel and differ only in intent — a new
escalation to handle against a settled week to write up — so each one's probes are the other's
confusion test, and a description that names its workflow instead of the member's intent loses
them. `pricing-quote` sits far enough away that a probe it steals is a description firing on a
domain word rather than on an intent.

Every rule the requests state is anchored on something the agent cannot paraphrase away: a severity
label, a channel, a threshold, an address, a price. A rule with no such token is not in `must` —
the anchor is what makes a body check exact rather than a second judgement call.

The declining probes are near misses inside each domain, not unrelated queries: a customer reply
that is not an escalation, a Friday reminder that is not the Friday writeup, a payment-terms lookup
that is not a quote. A skill that answers those is one whose description fires on its vocabulary."""

from __future__ import annotations

from textwrap import dedent

from evals.skill_authoring.runner import CriticalInstruction, LoadProbe, SkillAuthorCase


def _request(body: str) -> str:
    return " ".join(dedent(body).split())


ESCALATION_TRIAGE = SkillAuthorCase(
    name="escalation-triage",
    request=_request(
        """
        Save a skill called escalation-triage so you handle customer escalations the same way every
        time. Rate every escalation P1, P2 or P3: P1 is a customer who is down or is threatening to
        churn, P2 is a broken workflow that has a workaround, P3 is everything else. Post every P1
        to #escalations within 30 minutes with the account name, the ARR and the account owner.
        Never promise a refund or a credit — send those to finance@metalcraft.ai and say so in the
        reply.
        """
    ),
    must=(
        CriticalInstruction("severity-ladder", r"\bP1\b.{0,800}\bP2\b.{0,800}\bP3\b"),
        CriticalInstruction("churn-is-p1", r"P1[\s\S]{0,200}churn|churn[\s\S]{0,200}P1"),
        CriticalInstruction("escalations-channel", r"#escalations"),
        CriticalInstruction("thirty-minute-post", r"\b(?:30|thirty)[\s-]*min(?:ute)?s?\b"),
        CriticalInstruction("finance-routing", r"finance@metalcraft\.ai"),
    ),
    probes=(
        LoadProbe(
            "churn-threat",
            "The VP of ops at Acme just emailed saying they are pulling the plug on Friday unless "
            "the sync bug is fixed. What do we do with this?",
        ),
        LoadProbe(
            "locked-out",
            "Northwind says their whole team has been locked out since this morning and they are "
            "furious. Take it from here.",
        ),
        LoadProbe(
            "demo-followup",
            "Draft a short reply to Acme's VP thanking her for yesterday's call and confirming "
            "Thursday's demo.",
            loads=False,
        ),
    ),
)

ESCALATION_RECAP = SkillAuthorCase(
    name="escalation-recap",
    request=_request(
        """
        Save a skill called escalation-recap for the weekly writeup I send the exec team every
        Friday. The escalations live in Zendesk and the week runs Monday to Friday. Open with the
        count of escalations opened and closed that week. Then one line per P1 with the account name
        and how long it stayed open. Then a Themes section for anything that came up more than
        twice. Keep the whole thing under 300 words and post it to #exec-brief in Slack, never as an
        email attachment.
        """
    ),
    must=(
        CriticalInstruction("opened-and-closed-counts", r"\bopen(?:ed)?\b[\s\S]{0,120}\bclosed\b"),
        CriticalInstruction(
            "one-line-per-p1",
            r"\b(?:one )?line per P1\b|\bP1\b[\s\S]{0,80}\bone line\b"
            r"|\bone line\b[\s\S]{0,80}\bP1\b",
        ),
        CriticalInstruction("themes-section", r"\bthemes\b"),
        CriticalInstruction(
            "three-hundred-words", r"\b(?:300|three hundred)\b[\s\S]{0,20}\bwords?\b"
        ),
        CriticalInstruction("exec-brief-channel", r"#?exec[- ]brief"),
    ),
    probes=(
        LoadProbe(
            "friday-writeup",
            "It's Friday — put together the escalation writeup for the exec team.",
        ),
        LoadProbe(
            "weekly-rollup",
            "Can you pull together this week's rollup of everything that escalated, for "
            "leadership?",
        ),
        LoadProbe(
            "friday-reminder",
            "Set up a recurring reminder for the Friday team sync at 4pm.",
            loads=False,
        ),
    ),
)

PRICING_QUOTE = SkillAuthorCase(
    name="pricing-quote",
    request=_request(
        """
        Save a skill called pricing-quote for putting quotes together for prospects. List price is
        $180 per seat per year. You can discount up to 15% on your own; anything deeper needs VP
        approval before the quote goes out. Every quote carries the $2,500 onboarding fee and a
        12-month term — we do not sell monthly. Quotes are valid for 30 days and must say the date
        they expire.
        """
    ),
    must=(
        CriticalInstruction("list-price", r"\b180\b"),
        CriticalInstruction("discount-ceiling", r"\b(?:15|fifteen)\s*(?:%|percent)"),
        CriticalInstruction(
            "vp-approval",
            r"\bVP\b[\s\S]{0,120}(?:approv|sign[-\s]?off)|(?:approv|sign[-\s]?off)[\s\S]{0,120}\bVP\b",
        ),
        CriticalInstruction("onboarding-fee", r"\b2,?500\b"),
        CriticalInstruction("twelve-month-term", r"\b(?:12|twelve)[\s-]*month"),
    ),
    probes=(
        LoadProbe(
            "seat-quote",
            "Meridian wants 40 seats starting next month. What do we quote them?",
        ),
        LoadProbe(
            "discount-ask",
            "Northwind is asking for 25% off if they sign this quarter. Can I put that in front "
            "of them?",
        ),
        LoadProbe(
            "payment-terms",
            "Do we invoice net 30 or net 60?",
            loads=False,
        ),
    ),
)

CASES = (ESCALATION_TRIAGE, ESCALATION_RECAP, PRICING_QUOTE)
