"""Read-only lookups over the office: the agent must find a seeded fact through the real connector
dispatch and communicate it, mutating nothing — every grader also asserts the sent folder stayed
empty. The negative cases (no email from the CEO, someone else's calendar) grade through the
simulator's stop condition: the member is satisfied only when told the truthful 'nothing'."""

from __future__ import annotations

from evals.harness.capability import CapabilityVerdict
from evals.harness.scenario import ScenarioCase, ScenarioOutcome, ScenarioUser
from evals.scenario_env.office import dispatched, seed_office, sent_rows
from ufo.workspace import ws_current


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

    return grade


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
    ),
    ScenarioCase(
        "expense-deadline",
        ScenarioUser(
            reason_for_call="You want to know when your June expenses are due.",
            known_info=(
                "There is a reminder email about the expense report somewhere in your inbox."
            ),
            unknown_info="You do not remember the deadline date.",
            task_instructions=(
                "Ask the assistant when June expenses are due. Once it gives you the date, you "
                "are satisfied."
            ),
        ),
        _looked_up("17"),
        max_turns=4,
        digest_tag="env:expense-deadline",
        seed=seed_office,
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
                "Ask the assistant whether you received any email from the CEO this week. You are "
                "satisfied only when it clearly tells you there is no such email; if it describes "
                "one, ask it to double-check who the sender actually is."
            ),
        ),
        _looked_up(),
        max_turns=4,
        digest_tag="env:nothing-from-ceo",
        seed=seed_office,
    ),
    ScenarioCase(
        "next-meeting-with-mara",
        ScenarioUser(
            reason_for_call="You want to know when your next meeting with Mara is.",
            known_info="You meet Mara for design reviews now and then.",
            unknown_info="You do not remember the date.",
            task_instructions=(
                "Ask the assistant when your next meeting with Mara is. Once it gives you the "
                "date, you are satisfied."
            ),
        ),
        _looked_up("23"),
        max_turns=4,
        digest_tag="env:next-meeting-with-mara",
        seed=seed_office,
    ),
    ScenarioCase(
        "interview-candidate",
        ScenarioUser(
            reason_for_call="You forgot who you are interviewing next Thursday.",
            known_info="Recruiting emailed you about an interview panel on Thursday July 23.",
            unknown_info="You do not remember the candidate's name.",
            task_instructions=(
                "Ask the assistant who you are interviewing on Thursday July 23. Once it names "
                "the candidate, you are satisfied."
            ),
        ),
        _looked_up("jordan"),
        max_turns=4,
        digest_tag="env:interview-candidate",
        seed=seed_office,
    ),
    ScenarioCase(
        "free-slot-check",
        ScenarioUser(
            reason_for_call="You want to know if you are free on Thursday July 23 at 11am.",
            unknown_info="You do not remember what is on your calendar that day.",
            task_instructions=(
                "Ask the assistant whether you are free on Thursday July 23 at 11am. You are "
                "satisfied once it tells you what is blocking that slot."
            ),
        ),
        _looked_up("interview"),
        max_turns=4,
        digest_tag="env:free-slot-check",
        seed=seed_office,
    ),
    ScenarioCase(
        "standup-time",
        ScenarioUser(
            reason_for_call="You want to confirm the new eng standup time.",
            known_info="Priya emailed that standup is moving starting next week.",
            unknown_info="You do not remember the new time.",
            task_instructions=(
                "Ask the assistant what time standup is moving to. Once it tells you the time, "
                "you are satisfied."
            ),
        ),
        _looked_up("9:30"),
        max_turns=4,
        digest_tag="env:standup-time",
        seed=seed_office,
    ),
)
