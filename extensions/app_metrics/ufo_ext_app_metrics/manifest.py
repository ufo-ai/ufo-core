"""The metrics app: one workspace agent that reports how the team is doing.

It arrives reporting engineering delivery, and holds two more sets nobody has asked for yet —
revenue and support. One subject is one app: a member asking how the team is doing is asking one
question, and three apps each holding a third of the answer is three pages to read and three
accounts to wire for one weekly read.
"""

from pathlib import Path

from ufo_ext_coding.manifest import GITHUB_CONNECTOR, UFO_GITHUB_APP

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

NAME = "app_metrics"
VERSION = "0.1.0"
SKILLS_ROOT = Path(__file__).parent / "skills"
HOME_SKILL = "app-metrics-home"
METRICS_APP_AGENT_NAME = "metrics"

ENGINEERING_TASK = "delivery-report"
REVENUE_TASK = "revenue-report"
SUPPORT_TASK = "support-report"

METRICS_APP_PURPOSE = (
    "Reports how delivery is going on a cadence you pick, and can report revenue and support "
    "beside it."
)

METRICS_APP_PROMPT = (
    "You are the Metrics app for this workspace. You report how the team is doing, and hold three "
    "sets of measures. One is armed; the other two wait to be asked for, and you never do an "
    "unarmed set's work by hand — that is the set with no record of being armed, nothing for the "
    "member to see and nothing for them to turn off.\n"
    "\n"
    "Engineering is armed. On each fire, report four measures over the window since the last "
    "report: how many changes shipped, how long a change took from first commit to merge, how "
    "often a change had to be followed by a fix, and what is open now and for how long. State the "
    "rule you counted each by, and what you could not count. A measure whose rule you cannot state "
    "is a number nobody can act on.\n"
    "\n"
    "Revenue waits for the ask. When a member asks for it, connect the account the revenue lives "
    f"in with `connect_account`, apply a `scheduled_task` named `{REVENUE_TASK}`, then report once "
    "for the window in front of the member so the ask is answered now rather than at the next "
    "fire.\n"
    "\n"
    "Support waits for the ask in the same way, applying a `scheduled_task` named "
    f"`{SUPPORT_TASK}` over whichever account the workspace's support conversations live in.\n"
    "\n"
    "Your homepage is the metrics screen: what you are for, what the workspace still owes you, a "
    "band per set, and every conversation you hold. When a member asks you to change the page, "
    f"load the skill `{HOME_SKILL}` and follow it."
)

METRICS_APP_SETUP_INSTRUCTIONS = (
    "Ask the member which repositories to measure, and connect the GitHub account they live under."
)

METRICS_APP_SCHEDULE = SetupSchedule(
    name=ENGINEERING_TASK,
    prompt=(
        "Report the four delivery measures over the window since the last report: changes "
        "shipped, time from first commit to merge, how often a change needed a fix after it, and "
        "what is open now and for how long. State the rule each was counted by, and what could "
        "not be counted."
    ),
    cadences=(
        # A delivery report is read at the start of a working week, so the weekday-morning cadence
        # is offered first. The hourly one is absent: nobody reads how the quarter is going hourly,
        # and a report nobody reads is a turn nobody asked for.
        SetupCadence(hour=9, weekdays=(1,)),
        SetupCadence(hour=9, weekdays=(1, 2, 3, 4, 5)),
        SetupCadence(hour=9),
    ),
)

METRICS_APP_AGENT = AgentProvision(
    name=METRICS_APP_AGENT_NAME,
    spec=AgentSpec(
        prompt=METRICS_APP_PROMPT,
        purpose=METRICS_APP_PURPOSE,
        model="auto",
        reasoning="high",
        internet_access_allowed=False,
        visibility="workspace",
    ),
    icon="chart-line",
    setup=AgentSetup(
        connectors=(GITHUB_CONNECTOR,),
        credentials=(UFO_GITHUB_APP,),
        standing=(SCHEDULE_KIND,),
        schedule=METRICS_APP_SCHEDULE,
        instructions=METRICS_APP_SETUP_INSTRUCTIONS,
    ),
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        agents=(METRICS_APP_AGENT,),
        skills=(SkillSpec(path=SKILLS_ROOT / HOME_SKILL),),
    )
