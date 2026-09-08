"""The meetings app: one workspace agent over one calendar.

It arrives briefing, and holds two more features nobody has asked for yet — the follow-ups a
meeting commits to, and the notes it decides. One subject is one app: briefing a meeting and
writing up what it agreed is one agent over one calendar, not three. An app that split its subject
would make a member wire the same account three times and read the same work on three pages.
"""

from pathlib import Path

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

NAME = "app_meetings"
VERSION = "0.1.0"
SKILLS_ROOT = Path(__file__).parent / "skills"
HOME_SKILL = "app-meetings-home"
MEETINGS_APP_AGENT_NAME = "meetings"

CALENDAR_CONNECTOR = "googlecalendar"
NOTES_CONNECTOR = "googledocs"

BRIEFS_TASK = "meeting-briefs"
FOLLOWUPS_TASK = "meeting-followups"
NOTES_TASK = "meeting-decisions"

MEETINGS_APP_PURPOSE = (
    "Briefs you before each meeting, and can turn what a meeting agreed into tracked follow-ups "
    "and workspace notes."
)

MEETINGS_APP_PROMPT = (
    (Path(__file__).parent / "prompts" / "agent_meetings.md")
    .read_text()
    .strip()
    .replace("{{home_skill}}", HOME_SKILL)
    .replace("{{followups_task}}", FOLLOWUPS_TASK)
    .replace("{{notes_task}}", NOTES_TASK)
)

MEETINGS_APP_SETUP_INSTRUCTIONS = (
    "Ask the member for the calendar this workspace runs on, and connect it."
)

MEETINGS_APP_SCHEDULE = SetupSchedule(
    name=BRIEFS_TASK,
    prompt=(Path(__file__).parent / "prompts" / "task_briefs.md").read_text().strip(),
    cadences=(
        SetupCadence(),
        SetupCadence(hour=7),
        SetupCadence(hour=7, weekdays=(1, 2, 3, 4, 5)),
    ),
)

MEETINGS_APP_AGENT = AgentProvision(
    name=MEETINGS_APP_AGENT_NAME,
    spec=AgentSpec(
        prompt=MEETINGS_APP_PROMPT,
        purpose=MEETINGS_APP_PURPOSE,
        model="auto",
        reasoning="medium",
        internet_access_allowed=False,
        visibility="workspace",
    ),
    icon="calendar",
    setup=AgentSetup(
        connectors=(CALENDAR_CONNECTOR,),
        standing=(SCHEDULE_KIND,),
        schedule=MEETINGS_APP_SCHEDULE,
        instructions=MEETINGS_APP_SETUP_INSTRUCTIONS,
    ),
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        agents=(MEETINGS_APP_AGENT,),
        skills=(SkillSpec(path=SKILLS_ROOT / HOME_SKILL),),
    )
