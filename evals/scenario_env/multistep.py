"""Multi-step workflows across the office's tools: read one surface, act on another. Graded on
the combined end state — the calendar rows and sent emails a correct execution leaves — with any
tool order reaching that state accepted."""

from __future__ import annotations

from evals.harness.capability import CapabilityVerdict, DescribedGrader
from evals.harness.scenario import ScenarioCase, ScenarioOutcome, ScenarioUser
from evals.scenario_env.office import (
    BOB,
    DANA,
    MARA,
    PRIYA,
    SYNC_NEW_DATE,
    THURSDAY,
    WEDNESDAY,
    WEEKLY_SYNC_DATE,
    email_text,
    live_events,
    seed_office,
    sent_rows,
)
from ufo.workspace import ws_current

INTERVIEW_DATE = THURSDAY
ONE_ON_ONE_DATE = WEDNESDAY


async def _graded_reschedule_and_notify(outcome: ScenarioOutcome) -> CapabilityVerdict:
    workspace_id = ws_current().workspace_id
    syncs = await live_events(workspace_id, "design sync")
    if not any(event.start_at.date() == SYNC_NEW_DATE for event in syncs):
        return CapabilityVerdict(False, "no live Design sync lands on next Friday")
    if any(event.start_at.date() == WEEKLY_SYNC_DATE for event in syncs):
        return CapabilityVerdict(False, "the original Thursday Design sync is still live")
    sent = await sent_rows(workspace_id)
    to_mara = [email for email in sent if MARA in tuple(email.recipients)]
    if len(to_mara) != 1:
        return CapabilityVerdict(False, f"expected exactly one email to Mara, found {len(to_mara)}")
    text = email_text(to_mara[0])
    if "friday" not in text:
        return CapabilityVerdict(False, "Mara's email never mentions the new day")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "Design sync moved to next Friday and Mara notified once")


async def _graded_interview_prep(outcome: ScenarioOutcome) -> CapabilityVerdict:
    workspace_id = ws_current().workspace_id
    preps = [
        event
        for event in await live_events(workspace_id, "prep")
        if event.start_at.date() == INTERVIEW_DATE
    ]
    if not preps:
        return CapabilityVerdict(False, "no live prep block lands on next Thursday")
    if not await live_events(workspace_id, "Interview: Jordan Lee"):
        return CapabilityVerdict(False, "the interview itself is no longer live")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "prep block sits on next Thursday with the interview intact")


async def _graded_budget_summary_to_priya(outcome: ScenarioOutcome) -> CapabilityVerdict:
    sent = await sent_rows(ws_current().workspace_id)
    to_priya = [email for email in sent if PRIYA in tuple(email.recipients)]
    if len(to_priya) != 1:
        return CapabilityVerdict(
            False, f"expected exactly one email to Priya, found {len(to_priya)}"
        )
    if "bluefin" not in email_text(to_priya[0]):
        return CapabilityVerdict(False, "Priya's summary never mentions Project Bluefin")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "Priya got one summary carrying the Bluefin approval")


async def _graded_cancel_one_on_one(outcome: ScenarioOutcome) -> CapabilityVerdict:
    workspace_id = ws_current().workspace_id
    if any(
        event.start_at.date() == ONE_ON_ONE_DATE for event in await live_events(workspace_id, "1:1")
    ):
        return CapabilityVerdict(False, "the Wednesday 1:1 is still live")
    sent = await sent_rows(workspace_id)
    to_priya = [email for email in sent if PRIYA in tuple(email.recipients)]
    if len(to_priya) != 1:
        return CapabilityVerdict(
            False, f"expected exactly one email to Priya, found {len(to_priya)}"
        )
    text = email_text(to_priya[0])
    if "cancel" not in text and "1:1" not in text:
        return CapabilityVerdict(False, "Priya's email never mentions the cancellation")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "1:1 cancelled and Priya told once")


async def _graded_all_hands_agenda(outcome: ScenarioOutcome) -> CapabilityVerdict:
    sent = await sent_rows(ws_current().workspace_id)
    if len(sent) != 1:
        return CapabilityVerdict(False, f"expected exactly one sent email, found {len(sent)}")
    recipients = set(sent[0].recipients)
    missing = {DANA, BOB, MARA, PRIYA} - recipients
    if missing:
        return CapabilityVerdict(False, f"agenda never reached: {', '.join(sorted(missing))}")
    if "bluefin" not in email_text(sent[0]):
        return CapabilityVerdict(False, "the agenda never mentions Bluefin")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "agenda reached all four attendees in one email")


CASES = (
    ScenarioCase(
        "reschedule-and-notify",
        ScenarioUser(
            reason_for_call=(
                "You need your weekly Design sync moved to a new time and the attendee told."
            ),
            known_info=(
                "The weekly Design sync is on next Thursday at 2pm. Mara (mara@evalco.test) "
                "attends. It must move to next Friday at 3pm, still one hour long."
            ),
            task_instructions=(
                "Ask the assistant to move the weekly Design sync (the next Thursday one) to "
                "next Friday at 3pm and to email Mara the new time. Answer questions from "
                "what you know. Once both are done, you are satisfied."
            ),
        ),
        DescribedGrader(
            "the Design sync moves off Thursday onto next Friday, exactly one email tells "
            "Mara the new day, and the member ends satisfied",
            _graded_reschedule_and_notify,
        ),
        max_turns=8,
        digest_tag="env:reschedule-and-notify",
        seed=seed_office,
        tier=3,
    ),
    ScenarioCase(
        "interview-prep-block",
        ScenarioUser(
            reason_for_call="You want prep time blocked right before your Thursday interview.",
            known_info=(
                "You have an interview on your calendar on next Thursday; you want a "
                "30-minute 'Interview prep' block immediately before it."
            ),
            unknown_info="You do not remember the interview's exact start time.",
            task_instructions=(
                "Ask the assistant to find next Thursday's interview and block 30 minutes of "
                "Interview prep right before it. Once it confirms the block exists, you are "
                "satisfied."
            ),
        ),
        DescribedGrader(
            "a live prep block lands on next Thursday with the Jordan Lee interview intact, and "
            "the member ends satisfied",
            _graded_interview_prep,
        ),
        max_turns=6,
        digest_tag="env:interview-prep-block",
        seed=seed_office,
        tier=2,
    ),
    ScenarioCase(
        "budget-summary-to-priya",
        ScenarioUser(
            reason_for_call="You want Priya briefed on Dana's budget news.",
            known_info=(
                "Dana emailed you about the budget recently. Priya's address is priya@evalco.test."
            ),
            unknown_info="You do not remember the email's details — the assistant should read it.",
            task_instructions=(
                "Ask the assistant to read Dana's budget email and send Priya a two-line summary "
                "of it. Once it confirms the email was sent, you are satisfied."
            ),
        ),
        DescribedGrader(
            "exactly one summary email reaches Priya mentioning Project Bluefin, and the "
            "member ends satisfied",
            _graded_budget_summary_to_priya,
        ),
        max_turns=6,
        digest_tag="env:budget-summary-to-priya",
        seed=seed_office,
        tier=2,
    ),
    ScenarioCase(
        "cancel-and-inform",
        ScenarioUser(
            reason_for_call="You need Wednesday's 1:1 cancelled and Priya told.",
            known_info=(
                "Your 1:1 with Priya is on next Wednesday. Priya's address is priya@evalco.test."
            ),
            task_instructions=(
                "Ask the assistant to cancel Wednesday's 1:1 with Priya and email her an apology "
                "for the cancellation. Once both are done, you are satisfied."
            ),
        ),
        DescribedGrader(
            "the Wednesday 1:1 is cancelled, exactly one email tells Priya, and the member "
            "ends satisfied",
            _graded_cancel_one_on_one,
        ),
        max_turns=8,
        digest_tag="env:cancel-and-inform",
        seed=seed_office,
        tier=2,
    ),
    ScenarioCase(
        "all-hands-agenda",
        ScenarioUser(
            reason_for_call="You want Friday's All hands attendees emailed the agenda.",
            known_info=(
                "The All hands is on your calendar for next Friday. The agenda: Q3 numbers "
                "and the Project Bluefin kickoff."
            ),
            unknown_info="You do not remember who is invited — the assistant should look.",
            task_instructions=(
                "Ask the assistant to check who is invited to Friday's All hands and email all "
                "of them the agenda: Q3 numbers and the Project Bluefin kickoff. Once it "
                "confirms the email went to the attendees, you are satisfied."
            ),
        ),
        DescribedGrader(
            "exactly one agenda email reaches Dana, Bob, Mara, and Priya mentioning Bluefin, "
            "and the member ends satisfied",
            _graded_all_hands_agenda,
        ),
        max_turns=8,
        digest_tag="env:all-hands-agenda",
        seed=seed_office,
        tier=3,
    ),
)
