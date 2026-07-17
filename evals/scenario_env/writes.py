"""Single-intent writes against the office: one email sent or one calendar mutation, graded on
the durable end state — the right row exists (or was cancelled), the wrong rows are untouched —
never on which tools produced it."""

from __future__ import annotations

from datetime import timedelta

from evals.harness.capability import CapabilityVerdict, DescribedGrader
from evals.harness.scenario import ScenarioCase, ScenarioOutcome, ScenarioUser
from evals.scenario_env.office import (
    BOB,
    DEEP_DIVE_DATE,
    FRIDAY,
    MORNING_END,
    OFFSITE_RECIPIENTS,
    WEEK_END,
    WEEK_START,
    WEEKLY_SYNC_DATE,
    all_live_events,
    email_text,
    live_events,
    overlaps,
    seed_office,
    sent_rows,
)
from ufo.workspace import ws_current

FOCUS_DATE = FRIDAY
SYNC_MINUTES = 45


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


async def _graded_morning_sync(outcome: ScenarioOutcome) -> CapabilityVerdict:
    """A 45-minute sync with Bob on a free weekday morning that week: exactly one new event fits
    the constraints, and it clashes with nothing already on the calendar. The tempting failure is
    a slot that overlaps the 1:1, the interview, or the all-hands, or one in the afternoon."""
    events = await all_live_events(ws_current().workspace_id)
    candidates = [
        event
        for event in events
        if BOB in event.attendees
        and WEEK_START <= event.start_at.date() <= WEEK_END
        and (event.end_at - event.start_at) == timedelta(minutes=SYNC_MINUTES)
        and event.start_at.hour < MORNING_END
        and (event.end_at.hour, event.end_at.minute) <= (MORNING_END, 0)
    ]
    if len(candidates) != 1:
        return CapabilityVerdict(
            False, f"expected one 45-min morning sync with Bob, found {len(candidates)}"
        )
    sync = candidates[0]
    clash = next(
        (
            event
            for event in events
            if event.id != sync.id
            and overlaps(sync.start_at, sync.end_at, event.start_at, event.end_at)
        ),
        None,
    )
    if clash is not None:
        return CapabilityVerdict(False, f"the sync clashes with {clash.title!r}")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(
        True, f"45-min morning sync with Bob booked clash-free at {sync.start_at.isoformat()}"
    )


async def _graded_reply_all(outcome: ScenarioOutcome) -> CapabilityVerdict:
    """Reply-all minus self: exactly the offsite thread's other participants (Dana, Bob, Mara)
    receive one email, and the member is never a recipient. The tempting failure is replying only
    to the sender, or including the member."""
    sent = await sent_rows(ws_current().workspace_id)
    if len(sent) != 1:
        return CapabilityVerdict(False, f"expected exactly one sent email, found {len(sent)}")
    recipients = frozenset(sent[0].recipients)
    if recipients != OFFSITE_RECIPIENTS:
        return CapabilityVerdict(
            False, f"recipients were {sorted(recipients)}, expected {sorted(OFFSITE_RECIPIENTS)}"
        )
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "replied to the whole offsite thread except the member")


async def _graded_focus_block(outcome: ScenarioOutcome) -> CapabilityVerdict:
    workspace_id = ws_current().workspace_id
    blocks = [
        event
        for event in await live_events(workspace_id, "focus")
        if event.start_at.date() == FOCUS_DATE
    ]
    if not blocks:
        return CapabilityVerdict(False, "no live focus event lands on next Friday")
    sent = await sent_rows(workspace_id)
    if sent:
        return CapabilityVerdict(False, f"a calendar-only request sent {len(sent)} email(s)")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "focus block created on next Friday with no email side effects")


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
        tier=1,
    ),
    ScenarioCase(
        "focus-block",
        ScenarioUser(
            reason_for_call="You want a focus block on your calendar.",
            known_info="You want 'Focus time' blocked on next Friday from 1pm to 3pm.",
            task_instructions=(
                "Ask the assistant to block next Friday, 1pm to 3pm, as Focus time on your "
                "calendar. Once it confirms the block exists, you are satisfied. You did not ask "
                "for any emails."
            ),
        ),
        DescribedGrader(
            "a live focus event lands on next Friday, no email goes out, and the member "
            "ends satisfied",
            _graded_focus_block,
        ),
        max_turns=5,
        digest_tag="env:focus-block",
        seed=seed_office,
        tier=1,
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
        tier=2,
    ),
    ScenarioCase(
        "morning-sync-with-bob",
        ScenarioUser(
            reason_for_call="You want a short sync with Bob booked next week.",
            known_info=(
                "Bob's address is bob@evalco.test. You want 45 minutes, on a weekday morning the "
                "next week, and it must not clash with anything already on your "
                "calendar."
            ),
            task_instructions=(
                "Ask the assistant to book a 45-minute sync with Bob on a free weekday morning "
                "the next week. Let the assistant pick the slot — you have no preference "
                "beyond 'morning, no clash'. Once it confirms a specific morning slot is booked, "
                "you are satisfied."
            ),
        ),
        DescribedGrader(
            "a 45-minute morning sync with Bob booked in a clash-free weekday slot that week",
            _graded_morning_sync,
        ),
        max_turns=6,
        digest_tag="env:morning-sync-with-bob",
        seed=seed_office,
        tier=2,
    ),
    ScenarioCase(
        "reply-all-except-sender",
        ScenarioUser(
            reason_for_call="You want to reply to Dana's offsite planning email.",
            known_info=(
                "Dana emailed an offsite headcount request to a few people. Your answer is yes, "
                "count you in."
            ),
            task_instructions=(
                "Ask the assistant to reply to Dana's offsite planning email saying you're in — "
                "to everyone on that thread except you. Once it confirms the reply was sent, you "
                "are satisfied."
            ),
        ),
        DescribedGrader(
            "a single reply to the offsite thread reaches Dana, Bob, and Mara but never the member",
            _graded_reply_all,
        ),
        max_turns=5,
        digest_tag="env:reply-all-except-sender",
        seed=seed_office,
        tier=2,
    ),
)
