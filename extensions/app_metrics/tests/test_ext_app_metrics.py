import re
from pathlib import Path

import ufo_ext_app_metrics.manifest as app_metrics

SKILL_DIR = Path(app_metrics.__file__).parent / "skills" / "app-metrics-home"
BUILD_ENTRY = (
    Path(app_metrics.__file__).parents[2] / "web" / "frontend" / "apps" / "metrics" / "index.html"
)
MODULE_SCRIPT = re.compile(r'<script type="module" src="([^"]+)">')


def test_app_metrics_ships_one_workspace_agent_over_every_measure() -> None:
    """A member asking how the team is doing is asking one question. Three apps each holding a
    third of the answer is three pages to read and three accounts to wire for one weekly read."""
    manifest = app_metrics.manifest()
    assert manifest.name == "app_metrics"
    assert [provision.name for provision in manifest.agents] == ["metrics"]
    provision = manifest.agents[0]
    assert provision.spec.visibility == "workspace"
    assert provision.spec.purpose
    assert "app-metrics-home" in provision.spec.prompt
    assert {path.name for path in (spec.path for spec in manifest.skills)} == {"app-metrics-home"}


def test_the_report_offers_no_hourly_cadence() -> None:
    """A delivery report is read at the start of a working week. Nobody reads how the quarter is
    going hourly, and a report nobody reads is a turn nobody asked for — so the cadences it offers
    start at a weekday morning and none of them is hourly."""
    schedule = app_metrics.METRICS_APP_AGENT.setup.schedule
    assert schedule is not None
    assert schedule.name == app_metrics.ENGINEERING_TASK
    assert all(cadence.hour is not None for cadence in schedule.cadences)
    assert schedule.cadences[0].weekdays == (1,)


def test_a_measure_states_the_rule_it_was_counted_by() -> None:
    """A number whose rule nobody can state is a number nobody can act on, so the rule rides beside
    the measure: the app is asked for one where the counting happens — the standing prompt and the
    prompt the schedule arms it with — and the page gives every measure a line to draw it on."""
    assert "State the rule you counted each by" in app_metrics.METRICS_APP_PROMPT
    schedule = app_metrics.METRICS_APP_AGENT.setup.schedule
    assert schedule is not None
    assert "State the rule each was counted by" in schedule.prompt


def test_two_sets_wait_for_the_ask() -> None:
    prompt = app_metrics.METRICS_APP_PROMPT
    assert app_metrics.REVENUE_TASK in prompt
    assert app_metrics.SUPPORT_TASK in prompt
    assert "never do an unarmed set's work by hand" in prompt
    assert "so the ask is answered now rather than at the next fire" in prompt


def test_the_built_page_is_the_apps_own_tsx() -> None:
    entry = MODULE_SCRIPT.search(BUILD_ENTRY.read_text())
    assert entry is not None
    assert (BUILD_ENTRY.parent / entry[1]).resolve() == (SKILL_DIR / "app.tsx").resolve()
    source = (SKILL_DIR / "app.tsx").read_text()
    assert "mountApp(" in source
    # A set per subject, every one of them filled in — the page is the shape the app rebuilds
    # against its own sources.
    for subject in ("Engineering", "Revenue", "Support"):
        assert f'"{subject}"' in source
    # And every measure states the rule it was counted by, on the page as in the prompt.
    assert "counted as pull requests merged into the default branch" in source
    assert "<AppConversations" in source
    # Setup is a portal screen, never a band here: the acts that wire an app — a workspace install
    # an admin makes, a model turn that authors a page — are the two a framed page cannot start.
    assert "AppSetup" not in source


def test_the_home_skill_edits_builds_and_deploys_the_project() -> None:
    skill = (SKILL_DIR / "SKILL.md").read_text()
    assert "Copy this skill's `app.tsx` and `index.html`" in skill
    assert (
        "`deploy_website` action (`object_action` with kind `site`) with that directory and "
        "`site_name` `metrics-home`"
    ) in skill
    assert "do not run a build yourself" in skill
    assert "`object_get` kind `agent` with an empty name reads this turn's own agent" in skill
    assert "`set_homepage` action's call template already carries the agent's name" in skill
    assert f"`{app_metrics.manifest().agents[0].name}`" not in skill
    assert "takes the platform kit as it stands today" in skill


def test_the_page_says_what_the_app_is_for_in_the_provisions_own_words() -> None:
    """The setup screen draws the provision's purpose as its lede, and the built page draws its own
    `PURPOSE`. A member reads both, of one app, so two sentences that drift are two answers to what
    the app is — and nothing but this holds them together."""
    source = (SKILL_DIR / "app.tsx").read_text()
    stated = re.search(r"const PURPOSE =\s*(.*?);\n", source, re.DOTALL)
    assert stated is not None
    drawn = "".join(re.findall(r'"([^"]*)"', stated[1]))
    assert drawn == app_metrics.METRICS_APP_AGENT.spec.purpose
