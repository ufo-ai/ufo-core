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


def test_the_page_says_what_the_app_is_for_in_the_provisions_own_words() -> None:
    """The setup screen draws the provision's purpose as its lede, and the built page draws its own
    `PURPOSE`. A member reads both, of one app, so two sentences that drift are two answers to what
    the app is — and nothing but this holds them together."""
    source = (SKILL_DIR / "app.tsx").read_text()
    stated = re.search(r"const PURPOSE =\s*(.*?);\n", source, re.DOTALL)
    assert stated is not None
    drawn = "".join(re.findall(r'"([^"]*)"', stated[1]))
    assert drawn == app_meetings.MEETINGS_APP_AGENT.spec.purpose
