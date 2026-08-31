"""Wizard cases: the first turn of the portal's `New application` act, and the turn a member's
whole-brief message tries to skip the interview with.

The portal opens the run by sending the member's message for them, then draws a progress bar over
the todo board `create-application` keeps for a guided build. So a first turn that opens no board
leaves the member watching an empty bar through work that is happening, and a turn that creates the
app straight away has skipped the interview the member's go-ahead lives in. Both are facts about
the trajectory, so both are graded deterministically and no judge is needed.

`Build me a new app.` is the exact message the portal sends, so the opening case measures the real
query rather than a paraphrase — a message that names no job, which is what makes the build guided.
The second case is the known failure: a member who states the whole app in one sentence — name,
prompt, and `Go ahead` — invites the model to reach for `object_apply` in the first round, and the
interview requirement is what holds it back; their message named the job, so no board is owed.

The neighbour that shares the word is measured where the harness can decide it. An explicit
website routes to `website-building`; an explicit app routes to `create-application`.
"""

from ufo_ext_todos import UPDATE_TODO_LIST_TOOL

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from evals.harness.scorers import (
    attempted_tools_scorer,
    combine,
    required_tools_scorer,
    restraint_scorer,
    skill_scorer,
)

APPLICATION_SKILL = "create-application"
SITE_SKILL = "website-building"
ASK_TOOL = "ask_user"
CREATE_TOOL = "object_apply"
APPLICATION_PREVIEW_TOOL = "action:site:design_ufo_application"
WEBSITE_BUILD_TOOL = "action:site:build_website"
LOAD_TOOL = "load_skill"
GUIDED_PHASES = 4


def _phase_board_scorer() -> Grader:
    """The board the progress bar reads: one task per phase this run will take, and the first write
    of it is what the bar's length comes from. A guided build takes four — propose, interview,
    homepage design, create — so a board of another length is a bar that misstates the run rather
    than a matter of taste."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        boards = [
            call for call in output.calls if call.name == UPDATE_TODO_LIST_TOOL and call.succeeded
        ]
        opened = boards[0] if boards else None
        if opened is None:
            return CapabilityVerdict(False, "opened no phase board, so the progress bar reads none")
        tasks = opened.input.get("tasks")
        held = len(tasks) if isinstance(tasks, list) else 0
        if held != GUIDED_PHASES:
            return CapabilityVerdict(False, f"opened {held} phases, expected {GUIDED_PHASES}")
        return CapabilityVerdict(True, f"opened a {held}-phase board")

    return DescribedGrader("the first todo board holds one task per phase of the run", grade)


CASES = (
    CapabilityCase(
        "wizard-opening-turn",
        "Build me a new app.",
        combine(
            skill_scorer(APPLICATION_SKILL, SITE_SKILL),
            _phase_board_scorer(),
            required_tools_scorer(
                (LOAD_TOOL, UPDATE_TODO_LIST_TOOL, ASK_TOOL),
                ((UPDATE_TODO_LIST_TOOL, ASK_TOOL),),
            ),
            restraint_scorer((CREATE_TOOL,)),
        ),
        digest_tag="app-builder:opening",
    ),
    CapabilityCase(
        "named-app-holds-the-interview",
        "lets build an app that displays the current time across pacific, eastern, and utc time.",
        combine(
            attempted_tools_scorer(
                ((LOAD_TOOL, {"name": APPLICATION_SKILL}),),
                (),
                (),
            ),
            required_tools_scorer((ASK_TOOL,)),
            restraint_scorer((CREATE_TOOL, APPLICATION_PREVIEW_TOOL, WEBSITE_BUILD_TOOL)),
        ),
        digest_tag="app-builder:named-app",
    ),
    CapabilityCase(
        "wizard-holds-the-interview",
        "Set up an app called finance-dash that reads our weekly revenue export and answers "
        "questions about it. Its prompt should tell it to cite the row it read. Go ahead.",
        combine(
            skill_scorer(APPLICATION_SKILL, SITE_SKILL),
            required_tools_scorer((ASK_TOOL,)),
            restraint_scorer((CREATE_TOOL,)),
        ),
        digest_tag="app-builder:interview",
    ),
)
