"""The go-to-market trio, whose neighbours share a word rather than a domain: `opportunity-kickoff`
and `trial-kickoff` both start something with an account and differ only in what was started — a
deal being sold against a product being evaluated — so the description has to discriminate on the
noun in front of `kickoff`. `outbound-sequence` is the far field: cold, pre-relationship, and a
probe it steals is a description firing on the vocabulary of selling rather than on an intent.

A trial is often how an opportunity starts, so a query naming both motions grades a coin flip
rather than a description. Every loading probe therefore carries facts from one side only and none
from the other: the opportunity probes name a proposal, procurement and a pipeline stage, and never
a pilot, an evaluation or a sandbox; the trial probes name their own data and a two-week eval, and
never a deal, a proposal or a stage. `Kick off the two-week eval` says the confusing word out loud
and leaves exactly one fact to route on, which is the case worth running.

Each declining probe is a metrics question wearing its own skill's vocabulary — a close-date slip,
a trial conversion rate, a bounce rate. The skill is the procedure for starting one of these, never
the place to look up how the last hundred went."""

from __future__ import annotations

from textwrap import dedent

from evals.skill_authoring.runner import CriticalInstruction, LoadProbe, SkillAuthorCase


def _request(body: str) -> str:
    return " ".join(dedent(body).split())


OPPORTUNITY_KICKOFF = SkillAuthorCase(
    name="opportunity-kickoff",
    request=_request(
        """
        Save a skill called opportunity-kickoff for when a new deal gets created. Qualify it with
        MEDDIC first, and I want the economic buyer named before it advances. The Salesforce record
        always carries a stage and a close date, never blank. Agree a mutual action plan with dates
        with the buyer, and post the deal to #deals with its ARR band, which is under 50k, 50 to
        250k, or over 250k.
        """
    ),
    must=(
        CriticalInstruction("meddic-qualification", r"\bMEDDIC\b"),
        CriticalInstruction("economic-buyer", r"economic buyer"),
        CriticalInstruction(
            "stage-and-close-date",
            r"\bstage\b[\s\S]{0,160}close date|close date[\s\S]{0,160}\bstage\b",
        ),
        CriticalInstruction("mutual-action-plan", r"mutual action plan"),
        CriticalInstruction("deals-channel", r"#deals"),
    ),
    probes=(
        LoadProbe(
            "proposal-and-procurement",
            "Northwind's VP wants a proposal and asked us to talk to their procurement team. Set "
            "the deal up properly.",
        ),
        LoadProbe(
            "pipeline-stage-move",
            "We just moved Globex to stage 2 in the pipeline. What needs to be in place?",
        ),
        LoadProbe(
            "close-date-slip",
            "What's our average close-date slip on enterprise deals?",
            loads=False,
        ),
    ),
)

TRIAL_KICKOFF = SkillAuthorCase(
    name="trial-kickoff",
    request=_request(
        """
        Save a skill called trial-kickoff for when a prospect starts a trial. Agree 3 success
        criteria in writing before anything is provisioned. The trial runs 14 days and can be
        extended once at most. Cap the sandbox at 10 seats. Check in on day 3 and day 10, and post
        the trial to #trials.
        """
    ),
    must=(
        CriticalInstruction("three-success-criteria", r"\b(?:3|three)[\s-]+success criteria"),
        CriticalInstruction("fourteen-day-trial", r"\b(?:14|fourteen)[\s-]*days?\b"),
        CriticalInstruction("ten-seat-cap", r"\b(?:10|ten)[\s-]*seats?\b"),
        CriticalInstruction("day-three-check-in", r"\bday[\s-]*(?:3|three)\b"),
        CriticalInstruction("trials-channel", r"#trials"),
    ),
    probes=(
        LoadProbe(
            "own-data-eval",
            "Acme wants to try it with their own data before committing. Set them up.",
        ),
        LoadProbe(
            "two-week-eval",
            "Kick off the two-week eval for Northwind's platform team.",
        ),
        LoadProbe(
            "trial-conversion-rate",
            "How many trials converted last quarter?",
            loads=False,
        ),
    ),
)

OUTBOUND_SEQUENCE = SkillAuthorCase(
    name="outbound-sequence",
    request=_request(
        """
        Save a skill called outbound-sequence for cold outreach to new prospects. Three touches
        over 8 days. Subject lines under 45 characters, and never more than 90 words in an email.
        Never send to a personal address like gmail.com, work addresses only. Every email carries
        the unsubscribe line.
        """
    ),
    must=(
        CriticalInstruction("eight-day-cadence", r"\b(?:8|eight)[\s-]*days?\b"),
        CriticalInstruction(
            "subject-length", r"\b(?:45|forty[- ]five)\b[\s\S]{0,20}char(?:acter)?s?\b"
        ),
        CriticalInstruction("ninety-word-cap", r"\b(?:90|ninety)[\s-]*words?\b"),
        CriticalInstruction("no-personal-address", r"gmail\.com"),
        CriticalInstruction("unsubscribe-line", r"unsubscribe"),
    ),
    probes=(
        LoadProbe(
            "series-b-trigger",
            "Meridian just raised a Series B. Put together cold outreach for their VP Eng.",
        ),
        LoadProbe(
            "fintech-prospects",
            "I want to reach 20 new fintech prospects this week. Draft the emails.",
        ),
        LoadProbe(
            "bounce-rate",
            "What was the bounce rate on last month's outbound?",
            loads=False,
        ),
    ),
)

CASES = (OPPORTUNITY_KICKOFF, TRIAL_KICKOFF, OUTBOUND_SEQUENCE)
