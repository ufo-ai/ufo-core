import re
from pathlib import Path

import ufo_ext_app_meetings.manifest as app_meetings

SKILL_DIR = Path(app_meetings.__file__).parent / "skills" / "app-meetings-home"
BUILD_ENTRY = (
    Path(app_meetings.__file__).parents[2] / "web" / "frontend" / "apps" / "meetings" / "index.html"
)
MODULE_SCRIPT = re.compile(r'<script type="module" src="([^"]+)">')


def test_app_meetings_ships_one_workspace_agent_over_one_calendar() -> None:
    """One subject is one app. Briefing a meeting and writing up what it agreed is one agent over
    one calendar — an app that split its subject would make a member wire the same account three
    times and read the same work on three pages."""
    manifest = app_meetings.manifest()
    assert manifest.name == "app_meetings"
    assert [provision.name for provision in manifest.agents] == ["meetings"]
    provision = manifest.agents[0]
    assert provision.spec.visibility == "workspace"
    assert provision.spec.purpose
    assert "app-meetings-home" in provision.spec.prompt
    assert {path.name for path in (spec.path for spec in manifest.skills)} == {"app-meetings-home"}


def test_the_account_follows_the_feature() -> None:
    """The setup names the calendar and nothing else. Google Docs is what the notes and follow-ups
    features write into, so it is asked for when a member turns one of them on — not at setup by an
    app that may only ever brief."""
    setup = app_meetings.MEETINGS_APP_AGENT.setup
    assert setup.connectors == (app_meetings.CALENDAR_CONNECTOR,)
    assert app_meetings.NOTES_CONNECTOR not in setup.connectors
    # The prompt is where the account is asked for, on the ask that needs it.
    assert app_meetings.NOTES_CONNECTOR in app_meetings.MEETINGS_APP_PROMPT


def test_the_app_arrives_armed_for_one_feature_and_offers_the_cadences_that_arm_it() -> None:
    """A clock is what wakes this app, so it says so and offers how often. The hourly cadence is
    first because a meeting is booked and moved through the day: a once-a-day pass leaves whatever
    was booked after it until tomorrow."""
    setup = app_meetings.MEETINGS_APP_AGENT.setup
    assert setup.standing == ("scheduled_task",)
    assert setup.schedule is not None
    assert setup.schedule.name == app_meetings.BRIEFS_TASK
    assert setup.schedule.cadences[0].hour is None
    assert setup.schedule.prompt


def test_the_prompt_arms_nothing_nobody_asked_for() -> None:
    """The two features that wait are named in the prompt with what turns each on — and with the
    rule that stops the app doing an unarmed feature's work by hand, which would be the feature
    with no record of being armed: nothing to see and nothing to turn off."""
    prompt = app_meetings.MEETINGS_APP_PROMPT
    assert app_meetings.FOLLOWUPS_TASK in prompt
    assert app_meetings.NOTES_TASK in prompt
    assert "never do an unarmed feature's work by hand" in prompt
    assert "so the ask is answered now rather than at the next fire" in prompt


def test_a_meeting_falls_to_exactly_one_fire() -> None:
    """A brief lands in the run's own conversation and nowhere a later run reads, so "already
    briefed" asked the model to recall every brief it had written — and a compacted transcript
    briefed the 15:30 meeting again on every fire until it started.

    The window answers it instead: each fire takes the meetings starting before the next one, so
    the clock partitions them and no meeting falls in two windows. It is the same shape as the
    issues app's stop — a fact the app's own work cannot change."""
    prompt = app_meetings.MEETINGS_APP_PROMPT
    assert "start before your next fire and have not started yet" in prompt
    assert "falls inside exactly one fire's window" in prompt
    assert "already briefed" not in prompt
    schedule = app_meetings.MEETINGS_APP_AGENT.setup.schedule
    assert schedule is not None
    assert "before this task's next fire" in schedule.prompt


def test_the_built_page_is_the_apps_own_tsx() -> None:
    entry = MODULE_SCRIPT.search(BUILD_ENTRY.read_text())
    assert entry is not None
    assert (BUILD_ENTRY.parent / entry[1]).resolve() == (SKILL_DIR / "app.tsx").resolve()
    source = (SKILL_DIR / "app.tsx").read_text()
    assert "mountApp(" in source
    # A section per feature, and every one of them filled in. The page is the shape the app
    # rebuilds against its own sources, so a page of empty bands would tell it nothing about what
    # to draw and a member nothing about what the app is for.
    for section in ("Next meetings", "Follow-ups", "Notes"):
        assert f'"{section}"' in source
    for filled in ("Northstar renewal", "$48,000", "18 September", "#2040"):
        assert filled in source
    assert "<AppConversations" in source
    # Setup is a portal screen, never a band here: the acts that wire an app — a workspace install
    # an admin makes, a model turn that authors a page — are the two a framed page cannot start.
    assert "AppSetup" not in source


def test_the_home_skill_edits_builds_and_deploys_the_project() -> None:
    skill = (SKILL_DIR / "SKILL.md").read_text()
    assert "Copy this skill's `app.tsx` and `index.html`" in skill
    assert (
        "`deploy_website` action (`object_action` with kind `site`) with that directory and "
        "`site_name` `meetings-home`"
    ) in skill
    assert "do not run a build yourself" in skill
    assert "`object_get` kind `agent` with an empty name reads this turn's own agent" in skill
    assert "`set_homepage` action's call template already carries the agent's name" in skill
    assert f"`{app_meetings.manifest().agents[0].name}`" not in skill
    assert "`object_get` the site" in skill
    # A rebuild takes the kit as it stands today, which is how a page gains what the kit has since
    # gained — and why a page nobody rebuilds keeps drawing against the kit of the day it was built.
    assert "takes the platform kit as it stands today" in skill


def test_the_page_says_what_the_app_is_for_in_the_provisions_own_words() -> None:
    """The setup screen draws the provision's purpose as its lede, and the built page draws its own
    `PURPOSE`. A member reads both, of one app, so two sentences that drift are two answers to what
    the app is — and nothing but this holds them together."""
    source = (SKILL_DIR / "app.tsx").read_text()
    stated = re.search(r"const PURPOSE =\s*(.*?);\n", source, re.DOTALL)
    assert stated is not None
    drawn = "".join(re.findall(r'"([^"]*)"', stated[1]))
    assert drawn == app_meetings.MEETINGS_APP_AGENT.spec.purpose
