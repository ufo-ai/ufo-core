"""Single-intent writes against the office: one email sent or one calendar mutation, graded on
the durable end state — the right row exists (or was cancelled), the wrong rows are untouched —
never on which tools produced it."""

from __future__ import annotations

from datetime import UTC, datetime

from evals.harness.capability import CapabilityVerdict, DescribedGrader
from evals.harness.scenario import ScenarioCase, ScenarioOutcome, ScenarioUser
from evals.scenario_env.office import (
    BOB,
    DANA,
    DEEP_DIVE_DATE,
    MARA,
    WEEKLY_SYNC_DATE,
    email_text,
    live_events,
    seed_office,
    sent_rows,
)
from ufo.workspace import ws_current

FOCUS_DATE = datetime(2026, 7, 24, tzinfo=UTC).date()
STANDUP_DATE = datetime(2026, 7, 20, tzinfo=UTC).date()


def _sent_one(recipient: str, *fragments: str):
    """Pass iff exactly one email went out, to the named recipient, carrying every fragment."""

    async def grade(outcome: ScenarioOutcome) -> CapabilityVerdict:
        sent = await sent_rows(ws_current().workspace_id)
        if len(sent) != 1:
            return CapabilityVerdict(False, f"expected exactly one sent email, found {len(sent)}")
        if recipient not in tuple(sent[0].recipients):
            return CapabilityVerdict(False, f"email went to {sent[0].recipients}, not {recipient}")
        text = email_text(sent[0])
        missing = [fragment for fragment in fragments if fragment not in text]
        if missing:
            return CapabilityVerdict(False, f"the email never mentions: {', '.join(missing)}")
        if not outcome.stopped:
            return CapabilityVerdict(False, "the member never signalled satisfaction")
        return CapabilityVerdict(True, f"one email to {recipient} carrying {', '.join(fragments)}")

    return DescribedGrader(
        f"exactly one email goes out, to {recipient}, mentioning {', '.join(fragments)}, and "
        "the member ends satisfied",
        grade,
    )


async def _graded_focus_block(outcome: ScenarioOutcome) -> CapabilityVerdict:
    workspace_id = ws_current().workspace_id
    blocks = [
        event
        for event in await live_events(workspace_id, "focus")
        if event.start_at.date() == FOCUS_DATE
    ]
    if not blocks:
        return CapabilityVerdict(False, "no live focus event lands on July 24")
    sent = await sent_rows(workspace_id)
    if sent:
        return CapabilityVerdict(False, f"a calendar-only request sent {len(sent)} email(s)")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "focus block created on July 24 with no email side effects")


async def _graded_cancel_deep_dive(outcome: ScenarioOutcome) -> CapabilityVerdict:
    workspace_id = ws_current().workspace_id
    syncs = await live_events(workspace_id, "design sync")
    if any(event.start_at.date() == DEEP_DIVE_DATE for event in syncs):
        return CapabilityVerdict(False, "the monthly deep dive is still live")
    if not any(event.start_at.date() == WEEKLY_SYNC_DATE for event in syncs):
        return CapabilityVerdict(False, "the weekly Design sync was cancelled too")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "deep dive cancelled, weekly sync untouched")


async def _graded_standup_event(outcome: ScenarioOutcome) -> CapabilityVerdict:
    standups = [
        event
        for event in await live_events(ws_current().workspace_id, "standup")
        if event.start_at.date() == STANDUP_DATE
    ]
    if not standups:
        return CapabilityVerdict(False, "no live standup event lands on July 20")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "standup on the calendar for July 20")


CASES = (
    ScenarioCase(
        "send-dinner-confirmation",
        ScenarioUser(
            reason_for_call="You want the assistant to email Bob confirming dinner plans.",
            known_info=(
                "Bob's address is bob@evalco.test. Dinner is this Friday at 7pm at Luigi's."
            ),
            task_instructions=(
                "Ask the assistant to send Bob a short email confirming dinner on Friday at 7pm "
                "at Luigi's. Answer any questions from what you know. Once it confirms the email "
                "was sent, you are satisfied."
            ),
        ),
        _sent_one(BOB, "friday"),
        max_turns=5,
        digest_tag="env:send-dinner-confirmation",
        seed=seed_office,
    ),
    ScenarioCase(
        "mockup-feedback",
        ScenarioUser(
            reason_for_call="You want to send Mara quick feedback on her mockups.",
            known_info=(
                "Mara (mara@evalco.test) emailed that the new mockups are in the shared folder. "
                "Your feedback: they look great, and you will send minor spacing notes later."
            ),
            task_instructions=(
                "Ask the assistant to email Mara that the mockups look great and spacing notes "
                "will follow. Once it confirms the email was sent, you are satisfied."
            ),
        ),
        _sent_one(MARA, "mockup"),
        max_turns=5,
        digest_tag="env:mockup-feedback",
        seed=seed_office,
    ),
    ScenarioCase(
        "focus-block",
        ScenarioUser(
            reason_for_call="You want a focus block on your calendar.",
            known_info="You want 'Focus time' blocked on Friday July 24 from 1pm to 3pm.",
            task_instructions=(
                "Ask the assistant to block Friday July 24, 1pm to 3pm, as Focus time on your "
                "calendar. Once it confirms the block exists, you are satisfied. You did not ask "
                "for any emails."
            ),
        ),
        DescribedGrader(
            "a live focus event lands on Friday July 24, no email goes out, and the member "
            "ends satisfied",
            _graded_focus_block,
        ),
        max_turns=5,
        digest_tag="env:focus-block",
        seed=seed_office,
    ),
    ScenarioCase(
        "cancel-deep-dive",
        ScenarioUser(
            reason_for_call="You want the monthly design deep dive cancelled.",
            known_info=(
                "You have a weekly Design sync and a separate monthly deep-dive session; only "
                "the monthly deep dive should be cancelled."
            ),
            task_instructions=(
                "Ask the assistant to cancel the monthly design deep dive. Make clear the weekly "
                "Design sync stays. Once it confirms the deep dive is cancelled, you are "
                "satisfied."
            ),
        ),
        DescribedGrader(
            "the monthly deep dive is cancelled, the weekly Design sync stays live, and the "
            "member ends satisfied",
            _graded_cancel_deep_dive,
        ),
        max_turns=5,
        digest_tag="env:cancel-deep-dive",
        seed=seed_office,
    ),
    ScenarioCase(
        "expenses-submitted-reply",
        ScenarioUser(
            reason_for_call="You want Dana told that your June expenses are submitted.",
            known_info="Dana (dana@evalco.test) sent you an expense-report reminder.",
            task_instructions=(
                "Ask the assistant to email Dana that your June expense report is submitted. "
                "Once it confirms the email was sent, you are satisfied."
            ),
        ),
        _sent_one(DANA, "expense"),
        max_turns=5,
        digest_tag="env:expenses-submitted-reply",
        seed=seed_office,
    ),
    ScenarioCase(
        "standup-on-calendar",
        ScenarioUser(
            reason_for_call="You want the relocated eng standup on your calendar.",
            known_info=(
                "Standup is moving to 9:30am; you want it on your calendar for Monday July 20 at "
                "9:30am, 15 minutes long."
            ),
            task_instructions=(
                "Ask the assistant to put the eng standup on your calendar for Monday July 20 at "
                "9:30am for 15 minutes. Once it confirms the event exists, you are satisfied."
            ),
        ),
        DescribedGrader(
            "a live standup event lands on Monday July 20 and the member ends satisfied",
            _graded_standup_event,
        ),
        max_turns=5,
        digest_tag="env:standup-on-calendar",
        seed=seed_office,
    ),
)
