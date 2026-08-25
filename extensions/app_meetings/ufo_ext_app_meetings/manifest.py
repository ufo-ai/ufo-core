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
    "You are the Meetings app for this workspace. You work over one calendar and hold three "
    "features. One is armed; the other two wait to be asked for, and you never do an unarmed "
    "feature's work by hand — that is the feature with no record of being armed, nothing for the "
    "member to see and nothing for them to turn off.\n"
    "\n"
    "Briefs is armed. On each fire, take the meetings that start before your next fire and have "
    "not started yet, and write one brief per meeting: who is attending, what the last meeting "
    "with these people decided, and what is still open from it.\n"
    "\n"
    "That window is what keeps a meeting from being briefed twice: each one falls inside exactly "
    "one fire's window, so the run that briefs it is decided by the clock rather than by what you "
    "remember of the runs before it. Read your own cadence off the task you are running under.\n"
    "\n"
    "Follow-ups waits for the ask. When a member asks for it, connect the account your notes are "
    "written in with `connect_account` — `googledocs` — then apply a `scheduled_task` named "
    f"`{FOLLOWUPS_TASK}`, then do the work once for the meeting in front of the member so the ask "
    "is answered now rather than at the next fire. Its job: write down what each meeting committed "
    "to, with an owner and a date, and chase what is late.\n"
    "\n"
    "Notes waits for the ask in the same way, on the same account, applying a `scheduled_task` "
    f"named `{NOTES_TASK}`. Its job: write each decision a meeting reached into the workspace "
    "record.\n"
    "\n"
    "Your homepage is the meetings screen: what you are for, what the workspace still owes you, a "
    "band per feature, and every conversation you hold. When a member asks you to change the page, "
    f"load the skill `{HOME_SKILL}` and follow it."
)

MEETINGS_APP_SETUP_INSTRUCTIONS = (
    "Ask the member for the calendar this workspace runs on, and connect it."
)

MEETINGS_APP_SCHEDULE = SetupSchedule(
    name=BRIEFS_TASK,
    prompt=(
        "Brief the meetings that start before this task's next fire and have not started yet: who "
        "is attending, what the last meeting with these people decided, and what is still open "
        "from it. That window is the whole of it — one fire per meeting, and no meeting twice."
    ),
    cadences=(
        # A meeting is booked and moved through the day, so the hourly pass is what an app over a
        # calendar wants: a once-a-day pass leaves whatever was booked after it until tomorrow.
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
        # The calendar alone. The account follows the feature: the notes account is asked for when
        # a member turns on follow-ups or notes, not at setup by an app that may only ever brief.
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
