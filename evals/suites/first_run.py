"""First-run cases for the four workspace goals and their plans."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from evals.harness.capability import (
    CapabilityCase,
    CapabilityFollowup,
    CapabilityOutput,
    CapabilitySeed,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from evals.harness.memory_fence import forget_workspace_memory
from ufo.blob import BlobStore

FIRST_RUN_PACKS = ("assistant", "assistant_billing", "assistant_hosted")
FIRST_RUN_SKILL = "first-run"
ASK_TOOL = "ask_user"
APPLY_TOOL = "object_apply"
LIST_TOOL = "object_list"
LOAD_TOOL = "load_skill"
MEMORY_TOOL = "memory_search"
READ_TOOL = "read"


@dataclass(frozen=True)
class GoalCase:
    name: str
    message: str
    answer: str
    reference: str
    question_terms: tuple[tuple[str, ...], ...]
    rubric: tuple[str, ...]


GOALS = (
    GoalCase(
        name="faster-product-development",
        message=(
            "I just set up this workspace. I want to develop products faster, and we use Slack, "
            "GitHub, and Linear. More context: Cut cycle time from issue to production."
        ),
        answer=(
            "The main delay is review and unclear acceptance criteria. We ship a B2B web product "
            "twice a week. In 30 days, I want median issue-to-production time below two days."
        ),
        reference="faster-product-development.md",
        question_terms=(
            ("bottleneck", "delay", "slow"),
            ("repository", "repo", "code"),
            ("issue", "tracker", "backlog"),
            ("measure", "outcome", "cycle", "time"),
        ),
        rubric=(
            "The answer gives a short plan tied to the stated two-day delivery target.",
            "The plan uses the existing coding capability, the Code application, GitHub, and "
            "issue tracking when useful instead of assuming a new application is required.",
            "The plan offers to measure the current and next issue-to-production cycle time from "
            "GitHub and Linear instead of asking the member to calculate it.",
            "The plan names a first measurable action and does not claim that an application was "
            "created.",
        ),
    ),
    GoalCase(
        name="more-revenue",
        message=(
            "I just set up this workspace. I want to increase revenue, and we use HubSpot, Stripe, "
            "Gmail, Google Sheets, and Slack. More context: I need to know where to focus first."
        ),
        answer=(
            "We sell a $12,000 annual B2B subscription to support leaders. Most leads come from "
            "founder referrals. We have 40 qualified leads, a 15 percent close rate, and a goal of "
            "$100,000 in new annual revenue this quarter."
        ),
        reference="more-revenue.md",
        question_terms=(
            ("customer", "buyer", "segment", "whom"),
            ("revenue", "pricing", "price", "business model", "sell"),
            ("funnel", "pipeline", "conversion", "sales", "deal", "paid"),
            ("target", "goal", "baseline", "number", "when"),
        ),
        rubric=(
            "The answer states a factual business and funnel baseline from the member's data "
            "before the ordered plan.",
            "The plan identifies the main revenue constraint before it proposes automation or a "
            "new application.",
            "The plan offers a metrics application as the recurring review point and a daily "
            "progress cadence through Slack.",
            "The plan names a measurable first action and does not claim that an application was "
            "created.",
        ),
    ),
    GoalCase(
        name="automate-operations",
        message=(
            "I just set up this workspace. I want to automate operations, and we use Slack, Gmail, "
            "Notion, and HubSpot. More context: Start with the work that wastes the most time."
        ),
        answer=(
            "Customer onboarding is the worst workflow. An operator copies signed deals from "
            "HubSpot into Notion, emails the customer, and posts a Slack update about 12 times a "
            "week. A person must approve dates and contract exceptions."
        ),
        reference="automate-operations.md",
        question_terms=(
            ("workflow", "process", "task"),
            ("frequency", "often", "recurring"),
            ("input", "output", "system", "tool"),
            ("approval", "exception", "risk"),
        ),
        rubric=(
            "The answer maps the current onboarding workflow before it proposes a future workflow.",
            "The plan keeps date and contract exceptions behind human approval and gives routine "
            "steps a clear automation boundary.",
            "The plan names a measurable first action and does not claim that an application was "
            "created.",
        ),
    ),
    GoalCase(
        name="find-product-market-fit",
        message=(
            "I just set up this workspace. I want to find product-market fit, and we use Gmail, "
            "Google Calendar, Google Meet, HubSpot, and Notion. More context: Help us learn from "
            "the right users."
        ),
        answer=(
            "Our hypothesis is that support leaders at 50 to 200 person SaaS companies need faster "
            "ticket-theme analysis. We have six active design partners and have completed four "
            "interviews. We need 12 more interviews in six weeks."
        ),
        reference="find-product-market-fit.md",
        question_terms=(
            ("customer", "user", "segment"),
            ("problem", "hypothesis"),
            ("evidence", "interview", "learned"),
            ("recruit", "find", "reach"),
        ),
        rubric=(
            "The answer states the current hypothesis and evidence gap before it proposes more "
            "work.",
            "The plan sets a target of speaking with 10 to 20 suitable people and offers to draft "
            "and send approved outreach that puts meetings on Google Calendar.",
            "The plan prepares a brief with product-market-fit questions for each meeting and "
            "offers to turn granted Google Meet transcripts into notes.",
            "The plan synthesizes the interview evidence and gives a decision rule for the "
            "hypothesis.",
            "The plan names a measurable first action and does not claim that an application was "
            "created.",
        ),
    ),
)


def _goal_plan_scorer(goal: GoalCase) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        memory = [
            index
            for index, call in enumerate(output.calls)
            if call.call == MEMORY_TOOL and call.succeeded
        ]
        applications = [
            index
            for index, call in enumerate(output.calls)
            if call.name == LIST_TOOL and call.input.get("kind") == "agent" and call.succeeded
        ]
        loaded = [
            index
            for index, call in enumerate(output.calls)
            if call.name == LOAD_TOOL
            and call.input.get("name") == FIRST_RUN_SKILL
            and call.succeeded
        ]
        asks = [
            (index, call)
            for index, call in enumerate(output.calls)
            if call.name == ASK_TOOL and call.succeeded
        ]
        if not memory or not applications:
            return CapabilityVerdict(False, "did not read memory and existing applications")
        if not loaded:
            return CapabilityVerdict(False, "did not load first-run")
        if not asks:
            return CapabilityVerdict(False, "did not ask discovery questions")
        ask_index, ask = asks[0]
        if max(memory[0], applications[0], loaded[0]) > ask_index:
            return CapabilityVerdict(False, "asked before reading workspace context")
        references = [
            (index, candidate.reference)
            for index, call in enumerate(output.calls)
            if call.name == READ_TOOL
            and call.succeeded
            and "/first-run/references/" in str(call.input.get("file_path", ""))
            for candidate in GOALS
            if str(call.input.get("file_path", "")).endswith(candidate.reference)
        ]
        selected = [index for index, reference in references if reference == goal.reference]
        if not selected or {reference for _, reference in references} != {goal.reference}:
            return CapabilityVerdict(False, "did not read only the selected goal reference")
        if min(selected) > ask_index:
            return CapabilityVerdict(False, "asked before reading the selected goal reference")
        questions = ask.input.get("questions")
        if not isinstance(questions, list) or not 2 <= len(questions) <= 4:
            held = len(questions) if isinstance(questions, list) else 0
            return CapabilityVerdict(False, f"asked {held} discovery questions, expected 2 to 4")
        question_words = []
        for question in questions:
            if not isinstance(question, dict):
                return CapabilityVerdict(False, "discovery included a malformed question")
            question_words.append(str(question.get("question", "")))
        words = " ".join(question_words).casefold()
        matched = sum(any(term in words for term in group) for group in goal.question_terms)
        if matched < 2:
            return CapabilityVerdict(False, "discovery was not specific to the selected goal")
        if any(call.name == APPLY_TOOL for call in output.calls):
            return CapabilityVerdict(False, "created an application before plan approval")
        return CapabilityVerdict(
            True, "asked tailored questions and returned a plan without creating an app"
        )

    return DescribedGrader(
        "reads workspace context and one goal reference, asks 2 to 4 tailored questions, and "
        "plans without creating an app",
        grade,
    )


def _answer(goal: GoalCase) -> CapabilityFollowup:
    async def answer(_output: CapabilityOutput) -> str | None:
        return goal.answer

    return answer


def _clean_memory() -> CapabilitySeed:
    async def seed(_workspace_id: UUID, _agent_id: UUID, _blob: BlobStore) -> None:
        await forget_workspace_memory()

    return seed


CASES = tuple(
    CapabilityCase(
        f"first-run-{goal.name}",
        goal.message,
        _goal_plan_scorer(goal),
        followup=_answer(goal),
        seed=_clean_memory(),
        rubric=goal.rubric,
        digest_tag=f"first-run:{goal.name}",
    )
    for goal in GOALS
)
