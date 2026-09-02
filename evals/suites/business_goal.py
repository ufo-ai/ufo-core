"""Goal-thread cases: what a thread the first run opened on one picked goal has to do.

The thread carries its own instructions — the run knows which goal was picked, so `goalOpening` in
`FirstRun.tsx` says what taking the first step means and no skill is routed to say it. Both shells
draw that one component, so there is one copy of these words;
`test_every_case_says_the_words_the_first_run_actually_sends` holds this suite against it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilitySeed,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from evals.harness.memory_fence import forget_workspace_memory
from ufo.blob import BlobStore

BUSINESS_GOAL_PACKS = ("assistant", "assistant_billing", "assistant_hosted")
ASK_TOOL = "ask_user"
APPLY_TOOL = "object_apply"

WORK_TOOLS = frozenset(
    {
        "web_search",
        "fetch_url",
        "search_vertical",
        "memory_search",
        "object_list",
        "object_get",
        "read",
        "bash",
    }
)


@dataclass(frozen=True)
class GoalCase:
    name: str
    business: str
    role: str
    said: str
    asks: str
    rubric: tuple[str, ...]

    @property
    def opening(self) -> str:
        return (
            f"I just set up this workspace. My business: {self.business}. My role: {self.role}. "
            f"My goal: {self.said}. {self.asks}"
        )


GOALS = (
    GoalCase(
        name="more-revenue",
        business=(
            "Ledgerloop, a $12,000-a-year subscription that reconciles books for support-heavy "
            "SaaS teams"
        ),
        role="Founder",
        said="more revenue",
        asks=(
            "Start by working out my funnel as it stands from whatever CRM, billing, analytics or "
            "spreadsheet access I have granted: volume at each stage, conversion between them, "
            "and average deal size. Name the single stage losing the most, and one experiment on "
            "it with a metric. Do not guess a number I have not given you — say what you could "
            "not read. Ask me at most one thing."
        ),
        rubric=(
            "The reply states a funnel baseline read from the workspace, or states plainly that "
            "the access to read one was not granted.",
            "The reply names one constrained stage — lead volume, qualification, conversion, deal "
            "size, retention, or expansion — rather than listing every stage.",
            "The reply names one experiment on that stage, with the metric it moves.",
            "The reply invents no conversion rate, deal count, or revenue figure the member never "
            "gave and no read returned.",
        ),
    ),
    GoalCase(
        name="faster-product-development",
        business="Hookdeck, a B2B web product two engineers ship twice a week",
        role="Engineer",
        said="faster product development",
        asks=(
            "Start by measuring how long a change takes from opened to shipped, from whatever "
            "repository and issue tracker I have granted, and name the stage that holds it "
            "longest. Use what I already have rather than proposing a replacement for it. Do not "
            "invent a cycle time you could not read — say what you could not read. Ask me at most "
            "one thing."
        ),
        rubric=(
            "The reply measures the delivery path from what the workspace can reach, or states "
            "plainly that no repository or tracker was granted to measure it.",
            "The reply names one stage as the bottleneck and one change to that stage.",
            "The reply uses the coding capability, the Code application, or a granted tracker "
            "rather than proposing a replacement for them.",
            "The reply invents no cycle-time figure that no read returned.",
        ),
    ),
    GoalCase(
        name="automate-operations",
        business="Northsale, a 12-person agency that onboards signed customers by hand",
        role="Operations",
        said="automating operations",
        asks=(
            "Start by naming the recurring workflow that costs us the most time and mapping it as "
            "it runs today: trigger, frequency, inputs, decisions, outputs, and owner. Split it "
            "into the steps an agent can complete and the steps a person must approve, and keep "
            "the approval wherever money, contracts, access or an outside commitment changes. Ask "
            "me at most one thing."
        ),
        rubric=(
            "The reply maps one current workflow — trigger, inputs, decisions, outputs, owner — "
            "before it proposes any future one.",
            "The reply splits the workflow into steps an agent can complete and steps a person "
            "must approve.",
            "The reply keeps approval wherever money, contracts, access, or an outside commitment "
            "changes.",
            "The reply asks at most one question, and offers no menu of options.",
        ),
    ),
    GoalCase(
        name="find-product-market-fit",
        business=(
            "Ticketlens, a ticket-theme analyser sold to support leaders at 50-to-200-person SaaS "
            "companies"
        ),
        role="Founder",
        said="finding product-market fit",
        asks=(
            "Start by stating the hypothesis my business implies — which user, which problem — "
            "and count the candidates I can already reach in whatever CRM, support or calendar "
            "access I have granted. Tell me the gap between what we claim and what has been "
            "tested. Send nothing and book nobody. Ask me at most one thing."
        ),
        rubric=(
            "The reply states the hypothesis the business implies — which user, which problem.",
            "The reply states the evidence gap: what is claimed and what has never been tested.",
            "The reply counts reachable candidates, or states that no access to count them was "
            "granted.",
            "The reply sends no outreach and books no meeting.",
        ),
    ),
)


def _goal_thread_scorer() -> Grader:
    """The contract every goal thread holds to, whichever goal it opened on: work before any
    question, one bare question at most, and nothing durable applied on a turn nobody is reading."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        worked = [
            index
            for index, call in enumerate(output.calls)
            if call.name in WORK_TOOLS and call.succeeded
        ]
        if not worked:
            return CapabilityVerdict(False, "took no first step")
        asks = [
            (index, call)
            for index, call in enumerate(output.calls)
            if call.name == ASK_TOOL and call.succeeded
        ]
        if asks and asks[0][0] < min(worked):
            return CapabilityVerdict(False, "asked before taking the first step")
        for _, ask in asks:
            questions = ask.input.get("questions")
            if not isinstance(questions, list) or len(questions) > 1:
                held = len(questions) if isinstance(questions, list) else 0
                return CapabilityVerdict(False, f"asked {held} questions, expected at most one")
            if any(
                isinstance(question, dict) and question.get("options") for question in questions
            ):
                return CapabilityVerdict(False, "offered the member a menu")
        if any(call.name == APPLY_TOOL and call.succeeded for call in output.calls):
            return CapabilityVerdict(False, "applied an object on a thread nobody is reading")
        return CapabilityVerdict(True, "worked first, then asked at most one bare question")

    return DescribedGrader(
        "does the first step before asking, and asks at most one question without a menu",
        grade,
    )


def _clean_memory() -> CapabilitySeed:
    async def seed(_workspace_id: UUID, _agent_id: UUID, _blob: BlobStore) -> None:
        await forget_workspace_memory()

    return seed


CASES = tuple(
    CapabilityCase(
        f"business-goal-{goal.name}",
        goal.opening,
        _goal_thread_scorer(),
        seed=_clean_memory(),
        rubric=goal.rubric,
        digest_tag=f"business-goal:{goal.name}",
    )
    for goal in GOALS
)
