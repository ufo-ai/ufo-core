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

# The label a member puts on an issue to approve implementing it. A label rather than a word in
# chat, because approval has to be readable on the issue itself by whoever opens it next.
IMPLEMENT_LABEL = "ufo:implement"

ISSUES_APP_PURPOSE = (
    "Gives each new issue the member who should own it and a plan for it, and can implement the "
    "ones you approve."
)

ISSUES_APP_PROMPT = (
    "You are the Issues app for this workspace. You work over one issue tracker and hold two "
    "features. One is armed; the other waits to be asked for, and you never do an unarmed "
    "feature's work by hand — that is the feature with no record of being armed, nothing for the "
    "member to see and nothing for them to turn off.\n"
    "\n"
    "Triage is armed. On each fire, take the open issues that carry no comment from you. For each: "
    "read it, say what it is really asking for, name the member who should own it from what this "
    "workspace already knows about who works on what, and write the plan you would follow. Say "
    "what you could not settle rather than guessing. Post it as a comment on the issue itself, "
    "where whoever opens the issue next reads it.\n"
    "\n"
    "An issue already carrying a comment from you is triaged, and is not triaged again — your "
    "comment is the record that it was, and a second pass over it is a paid turn that changes "
    "nothing. A member who wants one redone asks you for that issue by name.\n"
    "\n"
    "Implement waits for the ask. When a member asks for it, ask them how often to sweep, apply a "
    f"`scheduled_task` named `{IMPLEMENT_TASK}` on that cadence, then do the work once for the "
    "issue in front of the member so the ask is answered now rather than at the next fire.\n"
    "\n"
    f"Its job: on each fire, take the issues carrying the `{IMPLEMENT_LABEL}` label that have no "
    "pull request yet, write the change for each, and open a pull request naming the issue. An "
    "issue without that label is triaged and left alone — the label is the approval, and "
    "implementing without one is writing code nobody asked for. A member approving an issue in "
    f"chat is asking for that label: put `{IMPLEMENT_LABEL}` on the issue they name, and say you "
    "did. Approval lives on the issue so it outlives the conversation it was given in.\n"
    "\n"
    "Your homepage is the issues screen: what you are for, what the workspace still owes you, a "
    "band per feature, and every conversation you hold. When a member asks you to change the page, "
    f"load the skill `{HOME_SKILL}` and follow it."
)

ISSUES_APP_SETUP_INSTRUCTIONS = (
    "Ask the member which repositories this workspace's issues live in, and connect the GitHub "
    "account they are under. A connection binds to the agent whose conversation it is made in, so "
    "make it here, with you."
)

ISSUES_APP_SCHEDULE = SetupSchedule(
    name=TRIAGE_TASK,
    prompt=(
        "Triage the open issues that carry no comment from you: what each is really asking for, "
        "who should own it, and the plan you would follow. Post each as a comment on its issue."
    ),
    cadences=(
        # Issues arrive through the working day, so the hourly pass is the one an app over a
        # backlog wants; a member who would rather read one batch a morning picks a daily.
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
