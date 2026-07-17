"""Read-only lookups over the office: the agent must find a seeded fact through the real connector
dispatch and communicate it, mutating nothing — every grader also asserts the sent folder stayed
empty. The negative cases (no email from the CEO, someone else's calendar) grade through the
simulator's stop condition: the member is satisfied only when told the truthful 'nothing'."""

from __future__ import annotations

from evals.harness.capability import CapabilityVerdict, DescribedGrader
from evals.harness.scenario import ScenarioCase, ScenarioOutcome, ScenarioUser
from evals.scenario_env.office import dispatched, seed_office, sent_rows
from ufo.workspace import ws_current

BUSIEST_DAY_REFS = ("thursday",)
BUSIEST_DAY_EVENTS = ("interview", "design sync")


def _looked_up(*fragments: str):
    """Pass iff the agent dispatched the connector seam, communicated every fragment, wrote
    nothing, and the member ended satisfied."""

    async def grade(outcome: ScenarioOutcome) -> CapabilityVerdict:
        if not dispatched(outcome):
            return CapabilityVerdict(False, "agent never dispatched call_external_tool")
        replies = "\n".join(outcome.replies).lower()
        missing = [fragment for fragment in fragments if fragment.lower() not in replies]
        if missing:
            return CapabilityVerdict(False, f"never communicated: {', '.join(missing)}")
        sent = await sent_rows(ws_current().workspace_id)
        if sent:
            return CapabilityVerdict(False, f"a read-only lookup sent {len(sent)} email(s)")
        if not outcome.stopped:
            return CapabilityVerdict(False, "the member never signalled satisfaction")
        detail = ", ".join(fragments) if fragments else "the truthful answer"
        return CapabilityVerdict(True, f"communicated {detail} without writing")

    communicated = (
        f"the replies carry {', '.join(fragments)}"
        if fragments
        else "the truthful absence is communicated"
    )
    return DescribedGrader(
        f"call_external_tool is dispatched, {communicated}, no email is sent, and the member "
        "ends satisfied",
        grade,
    )


async def _graded_busiest_day(outcome: ScenarioOutcome) -> CapabilityVerdict:
    """next Thursday is the only day next week carrying two events (the interview and the
    Design sync); a correct answer names that day and both events, and reads without writing."""
    if not dispatched(outcome):
        return CapabilityVerdict(False, "agent never dispatched call_external_tool")
    replies = "\n".join(outcome.replies).lower()
    if not any(ref in replies for ref in BUSIEST_DAY_REFS):
        return CapabilityVerdict(False, "never identified Thursday as the busiest day")
    missing = [event for event in BUSIEST_DAY_EVENTS if event not in replies]
    if missing:
        return CapabilityVerdict(False, f"never named Thursday's events: {', '.join(missing)}")
    sent = await sent_rows(ws_current().workspace_id)
    if sent:
        return CapabilityVerdict(False, f"a read-only lookup sent {len(sent)} email(s)")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(True, "named Thursday and both of its events without writing")


CASES = (
    ScenarioCase(
        "budget-project",
        ScenarioUser(
            reason_for_call="You want to know which project Dana's recent email said was approved.",
            known_info="Dana emailed you in the last few days about the budget.",
            unknown_info="You do not remember the project name — that is why you are asking.",
            task_instructions=(
                "Ask the assistant to check your email for Dana's message about the budget and "
                "tell you which project was approved. Once it names the project, you are "
                "satisfied."
            ),
        ),
        _looked_up("bluefin"),
        max_turns=4,
        digest_tag="env:budget-project",
        seed=seed_office,
        tier=1,
    ),
    ScenarioCase(
        "pipeline-forecast",
        ScenarioUser(
            reason_for_call="You need the Q3 pipeline forecast figure Bob emailed you.",
            known_info="Bob sent you the Q3 pipeline numbers recently.",
            unknown_info="You do not remember the forecast amount.",
            task_instructions=(
                "Ask the assistant what forecast figure Bob's pipeline email gave. Once it tells "
                "you the dollar figure, you are satisfied."
            ),
        ),
        _looked_up("1.9"),
        max_turns=4,
        digest_tag="env:pipeline-forecast",
        seed=seed_office,
        tier=1,
    ),
    ScenarioCase(
        "nothing-from-ceo",
        ScenarioUser(
            reason_for_call="You are wondering whether the CEO emailed you this week.",
            unknown_info=(
                "You do not know whether any such email exists; you have no reason to believe it "
                "does."
            ),
            task_instructions=(
                "Ask the assistant whether you received any email from the CEO this week. You have "
                "no particular person in mind and none of your known contacts — Dana, Bob, Mara, "
                "or Priya — is the CEO; if it asks who your CEO is, say exactly that and that you "
                "just want to know whether anyone identified as the CEO wrote to you. You are "
                "satisfied only when it clearly tells you there is no such email; if it describes "
                "one, ask it to double-check who the sender actually is."
            ),
        ),
        _looked_up(),
        max_turns=4,
        digest_tag="env:nothing-from-ceo",
        seed=seed_office,
        tier=1,
    ),
    ScenarioCase(
        "free-slot-check",
        ScenarioUser(
            reason_for_call="You want to know if you are free on next Thursday at 11am.",
            unknown_info="You do not remember what is on your calendar that day.",
            task_instructions=(
                "Ask the assistant whether you are free on next Thursday at 11am. You are "
                "satisfied once it tells you what is blocking that slot."
            ),
        ),
        _looked_up("interview"),
        max_turns=4,
        digest_tag="env:free-slot-check",
        seed=seed_office,
        tier=1,
    ),
    ScenarioCase(
        "busiest-day",
        ScenarioUser(
            reason_for_call="You want to know which day next week is busiest on your calendar.",
            known_info="Next week is the next week.",
            unknown_info="You do not remember your schedule for that week.",
            task_instructions=(
                "Ask the assistant which day next week (next Monday-24) has the most on your "
                "calendar, and what is on that day. You are satisfied once it names the busiest "
                "day and lists what is scheduled on it."
            ),
        ),
        DescribedGrader(
            "names Thursday as the busiest day and both events on it, without writing anything",
            _graded_busiest_day,
        ),
        max_turns=4,
        digest_tag="env:busiest-day",
        seed=seed_office,
        tier=3,
    ),
)
