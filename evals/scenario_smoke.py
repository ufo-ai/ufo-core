"""Scenario smoke: two multi-turn conversations proving the simulator loop end-to-end. Progressive
disclosure forces a real back-and-forth the agent must drive; the memory case leaves durable state
the grader reads back from the workspace's own tables rather than the prose."""

from __future__ import annotations

import sqlalchemy as sa
from ufo_ext_memory.store import memory_item

from evals.harness.capability import CapabilityVerdict, DescribedGrader
from evals.harness.scenario import ScenarioCase, ScenarioGrader, ScenarioOutcome, ScenarioUser
from ufo.db import workspace_tx
from ufo.workspace import ws_current

MIN_EXCHANGES = 2
REVIEW_DAY = "thursday"


def _communicated(*fragments: str) -> ScenarioGrader:
    """Pass iff every fragment reached the member, the conversation took at least MIN_EXCHANGES
    turns (progressive disclosure means one message cannot suffice), and the member ended it
    satisfied."""

    async def grade(outcome: ScenarioOutcome) -> CapabilityVerdict:
        replies = "\n".join(outcome.replies).lower()
        missing = [fragment for fragment in fragments if fragment.lower() not in replies]
        if missing:
            return CapabilityVerdict(False, f"never communicated: {', '.join(missing)}")
        if len(outcome.turns) < MIN_EXCHANGES:
            return CapabilityVerdict(
                False, f"took {len(outcome.turns)} exchange(s); disclosure requires {MIN_EXCHANGES}"
            )
        if not outcome.stopped:
            return CapabilityVerdict(False, "the member never signalled satisfaction")
        return CapabilityVerdict(
            True, f"communicated {', '.join(fragments)} across {len(outcome.turns)} exchanges"
        )

    return DescribedGrader(
        f"the replies carry {', '.join(fragments)} across at least {MIN_EXCHANGES} exchanges "
        "and the member ends satisfied",
        grade,
    )


async def _remembered_review_day(outcome: ScenarioOutcome) -> CapabilityVerdict:
    """The write must show in the trajectory (tying it to this conversation) and land as a durable
    memory_item row; the recall must reach the member; the member must end satisfied."""
    if not any(call.name == "memory_update" and call.succeeded for call in outcome.output.calls):
        return CapabilityVerdict(False, "agent never made a successful memory_update call")
    async with workspace_tx() as connection:
        stored = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(memory_item)
                .where(
                    memory_item.c.workspace_id == ws_current().workspace_id,
                    memory_item.c.body.ilike(f"%{REVIEW_DAY}%"),
                    memory_item.c.superseded_by.is_(None),
                )
            )
        ).scalar_one()
    if not stored:
        return CapabilityVerdict(False, "no durable memory_item mentions the new review day")
    if REVIEW_DAY not in "\n".join(outcome.replies).lower():
        return CapabilityVerdict(False, "agent never told the member the review is on Thursday")
    if not outcome.stopped:
        return CapabilityVerdict(False, "the member never signalled satisfaction")
    return CapabilityVerdict(
        True, f"{stored} durable memory item(s) hold the review day and the agent recalled it"
    )


CASES = (
    ScenarioCase(
        "progressive-sum",
        ScenarioUser(
            reason_for_call="You need the assistant to add up two purchases you made today.",
            known_info="Your first purchase was $137. Your second purchase was $86.",
            task_instructions=(
                "Open by asking for help totalling two purchases, but mention only the first "
                "amount ($137). Reveal the second amount ($86) only when the assistant asks for "
                "it. Once the assistant tells you the total is $223, you are satisfied."
            ),
        ),
        _communicated("223"),
        max_turns=4,
        digest_tag="scenario:progressive-sum",
    ),
    ScenarioCase(
        "remember-review-day",
        ScenarioUser(
            reason_for_call=(
                "You want the assistant to remember a team decision, then prove it did."
            ),
            known_info="Your team just moved the weekly design review to Thursday at 2pm.",
            task_instructions=(
                "First ask the assistant to remember that the weekly design review moved to "
                "Thursday at 2pm. After it confirms, ask it what day the design review is on. "
                "Once it answers Thursday, you are satisfied."
            ),
        ),
        DescribedGrader(
            "a successful memory_update lands a durable memory_item holding the Thursday "
            "review day, the agent recalls it to the member, and the member ends satisfied",
            _remembered_review_day,
        ),
        max_turns=5,
        digest_tag="scenario:remember-review-day",
    ),
)
