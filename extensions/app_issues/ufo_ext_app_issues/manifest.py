"""The issues app: one workspace agent over one issue tracker.

It arrives triaging — giving each new issue an owner and a plan — and holds one more feature nobody
has asked for yet: implementing the ones a member approves. Triaging an issue and writing the code
that closes it is one agent over one backlog, not two: an app that split them would read the same
issue twice and make a member connect the same account for each half.

Both features sweep on a clock. Triage answers on the issue itself, and a comment moves the issue
it is posted on — so an app woken by a changed issue is woken by its own answer. A sweep asks what
its own work cannot change: whether the issue already carries a comment from this app.
"""

from pathlib import Path

from ufo_ext_coding.manifest import GITHUB_CONNECTOR

from ufo.sdk.manifest import (
    SCHEDULE_KIND,
    AgentProvision,
    AgentSetup,
    AgentSpec,
    Manifest,
    SetupCadence,
    SetupSchedule,
    SkillSpec,
)

NAME = "app_issues"
VERSION = "0.1.0"
SKILLS_ROOT = Path(__file__).parent / "skills"
HOME_SKILL = "app-issues-home"
ISSUES_APP_AGENT_NAME = "issues"

TRIAGE_TASK = "issue-triage"
IMPLEMENT_TASK = "issue-implement"
"""What each feature runs off: one named task apiece, so a member sees each, pauses it, or deletes
it like any other.

Both sweep, and triage sweeps for a reason. Triage answers on the issue itself, and on GitHub a
comment moves the issue it is posted on — so an app woken by a changed issue is woken by its own
answer, and comments on one issue for ever, a paid turn at a time. A sweep asks a question its own
work cannot change: an issue carrying a comment from this app has been triaged, and the comment is
that record, readable by whoever opens the issue next and by the next sweep alike."""

IMPLEMENT_LABEL = "ufo:implement"

ISSUES_APP_PURPOSE = (
    "Gives each new issue the member who should own it and a plan for it, and can implement the "
    "ones you approve."
)

ISSUES_APP_PROMPT = (
    (Path(__file__).parent / "prompts" / "agent_issues.md")
    .read_text()
    .strip()
    .replace("{{home_skill}}", HOME_SKILL)
    .replace("{{implement_task}}", IMPLEMENT_TASK)
    .replace("{{implement_label}}", IMPLEMENT_LABEL)
)

ISSUES_APP_SETUP_INSTRUCTIONS = (
    "Ask the member which repositories this workspace's issues live in, and connect the GitHub "
    "account they are under. A connection binds to the agent whose conversation it is made in, so "
    "make it here, with you."
)

ISSUES_APP_SCHEDULE = SetupSchedule(
    name=TRIAGE_TASK,
    prompt=(Path(__file__).parent / "prompts" / "task_triage.md").read_text().strip(),
    cadences=(
        SetupCadence(),
        SetupCadence(hour=9),
        SetupCadence(hour=9, weekdays=(1, 2, 3, 4, 5)),
    ),
)

ISSUES_APP_AGENT = AgentProvision(
    name=ISSUES_APP_AGENT_NAME,
    spec=AgentSpec(
        prompt=ISSUES_APP_PROMPT,
        purpose=ISSUES_APP_PURPOSE,
        model="auto",
        reasoning="high",
        internet_access_allowed=False,
        sandbox_size="large",
        visibility="workspace",
    ),
    icon="list-check",
    setup=AgentSetup(
        connectors=(GITHUB_CONNECTOR,),
        standing=(SCHEDULE_KIND,),
        schedule=ISSUES_APP_SCHEDULE,
        instructions=ISSUES_APP_SETUP_INSTRUCTIONS,
    ),
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        agents=(ISSUES_APP_AGENT,),
        skills=(SkillSpec(path=SKILLS_ROOT / HOME_SKILL),),
    )
