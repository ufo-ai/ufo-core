"""First-run cases: the turn the setup page hands over, and the turn that builds what was picked.

The page records what the team uses, connects Slack and GitHub, and then opens a conversation with
the picks stated in the member's own opening message. So the first turn measured here is the real
handover, not a paraphrase — and everything it must do is a fact about the trajectory, graded
deterministically without a judge.

Two failures are what these cases exist to catch. A turn that proposes from memory offers the same
four generic agents to every team, so the ideas reference must be read before a single one is
named; a turn that asks its options over several rounds spends the member's attention on the shape
of the questions rather than the choice, so the offer is one `ask_user` carrying every option.

The build itself belongs to `create-application`, which owns the interview, the name, and the
prompt. So the second case measures the handover rather than a build count: that skill is what the
answer reaches, and it interviews before it creates — a turn creating nothing yet is the flow
working. What it must never do is batch, which is what a member who picked three and reads one wall
of everything each needs grants nothing against.

The graded skill is a pack-level one, carried by the assistant bundle and the packs that compose it.
Anywhere else — an eval deploy's own `assistant_eval` among them — it cannot load, so every case
would report the harness rather than the model, which is what `FIRST_RUN_PACKS` refuses.
"""

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from evals.harness.scorers import combine, restraint_scorer, skill_scorer

FIRST_RUN_PACKS = ("assistant", "assistant_billing", "assistant_hosted")
FIRST_RUN_SKILL = "first-run"
APPLICATION_SKILL = "create-application"
ASK_TOOL = "ask_user"
APPLY_TOOL = "object_apply"
LOAD_TOOL = "load_skill"
READ_TOOL = "read"

MIN_OPTIONS = 5
MAX_OPTIONS = 15

HANDOVER = "We use Gmail, Google Calendar, Linear. What could you set up for us?"

PICKED = "Set up the meeting notes one and the issue router. Do the meeting notes first."


def _one_offer_scorer() -> Grader:
    """The offer is one question carrying every option. `ask_user` takes up to four questions and
    caps no option count, so splitting the ideas across questions costs the member a round for
    nothing, and offering four of them hides the range the reference exists to produce."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        asks = [call for call in output.calls if call.name == ASK_TOOL and call.succeeded]
        if not asks:
            return CapabilityVerdict(False, "offered nothing, so the member has nothing to pick")
        if len(asks) > 1:
            return CapabilityVerdict(False, f"asked {len(asks)} times, expected one offer")
        questions = asks[0].input.get("questions")
        if not isinstance(questions, list) or len(questions) != 1:
            held = len(questions) if isinstance(questions, list) else 0
            return CapabilityVerdict(False, f"offered {held} questions, expected one")
        question = questions[0]
        if not isinstance(question, dict):
            return CapabilityVerdict(False, "the offer carried no question")
        if not question.get("multi_select"):
            return CapabilityVerdict(False, "the offer takes one answer, so nothing can be checked")
        options = question.get("options")
        count = len(options) if isinstance(options, list) else 0
        if not MIN_OPTIONS <= count <= MAX_OPTIONS:
            return CapabilityVerdict(
                False, f"offered {count} ideas, expected {MIN_OPTIONS} to {MAX_OPTIONS}"
            )
        return CapabilityVerdict(True, f"offered {count} ideas on one multi-select question")

    return DescribedGrader(
        f"one ask_user, one multi-select question, {MIN_OPTIONS}-{MAX_OPTIONS} options", grade
    )


def _ideas_read_scorer() -> Grader:
    """The ideas reference is read before an idea is named. Proposing from memory is the failure
    the reference exists to prevent: it answers every team with the same few generic agents, and a
    team whose tools it never read cannot be offered the bridge between them."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        reads = [
            call
            for call in output.calls
            if call.name == READ_TOOL
            and "agent-ideas" in str(call.input.get("file_path", ""))
            and call.succeeded
        ]
        if not reads:
            return CapabilityVerdict(False, "named ideas without reading the ideas reference")
        offered = next(
            (index for index, call in enumerate(output.calls) if call.name == ASK_TOOL), None
        )
        first = next(
            index
            for index, call in enumerate(output.calls)
            if call.name == READ_TOOL and "agent-ideas" in str(call.input.get("file_path", ""))
        )
        if offered is not None and first > offered:
            return CapabilityVerdict(False, "offered ideas before reading the reference")
        return CapabilityVerdict(True, "read the ideas reference before offering")

    return DescribedGrader("reads references/agent-ideas.md before naming an idea", grade)


def _one_build_scorer() -> Grader:
    """The answer opens one build, through the skill that owns building. `create-application` holds
    the interview, the name, and the prompt, so first-run hands each idea to it rather than writing
    a second set of rules the create would refuse on — and it interviews before it creates, so a
    turn that creates nothing yet is the flow working, while a turn that creates several has
    batched what the member reads one at a time."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        loaded = [
            call
            for call in output.calls
            if call.name == LOAD_TOOL
            and call.input.get("name") == APPLICATION_SKILL
            and call.succeeded
        ]
        if not loaded:
            return CapabilityVerdict(
                False, f"built without {APPLICATION_SKILL}, so its rules never applied"
            )
        creates = [
            call
            for call in output.calls
            if call.name == APPLY_TOOL and call.input.get("kind") == "agent" and call.succeeded
        ]
        if len(creates) > 1:
            named = ", ".join(str(call.input.get("name")) for call in creates)
            return CapabilityVerdict(False, f"built {len(creates)} at once: {named}")
        return CapabilityVerdict(
            True, f"handed the build to {APPLICATION_SKILL} and opened {len(creates)}"
        )

    return DescribedGrader(f"hands the build to {APPLICATION_SKILL} and opens at most one", grade)


async def _picked_two(output: CapabilityOutput) -> str | None:
    """The member's answer to the offer. It names two of the ideas in their own words and an order,
    which is what makes batching visible: a turn that opens both builds has ignored the order they
    stated as much as the pace the skill sets."""
    return PICKED


CASES = (
    CapabilityCase(
        "first-run-offers-what-the-tools-bridge",
        HANDOVER,
        combine(
            skill_scorer(FIRST_RUN_SKILL, APPLICATION_SKILL),
            _ideas_read_scorer(),
            _one_offer_scorer(),
            restraint_scorer((APPLY_TOOL,)),
        ),
        digest_tag="first-run:offer",
    ),
    CapabilityCase(
        "first-run-builds-one-at-a-time",
        HANDOVER,
        _one_build_scorer(),
        followup=_picked_two,
        digest_tag="first-run:build",
    ),
)
