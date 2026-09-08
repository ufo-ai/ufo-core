import re
from pathlib import Path

import ufo_ext_app_metrics.manifest as app_metrics
from ufo_ext_keyed_connectors import KEYED_PROVIDERS
from ufo_ext_pipedream.client import CONNECTORS as BROKERED

SKILL_DIR = Path(app_metrics.__file__).parent / "skills" / "app-metrics-home"
BUILD_ENTRY = (
    Path(app_metrics.__file__).parents[2] / "web" / "frontend" / "apps" / "metrics" / "index.html"
)
MODULE_SCRIPT = re.compile(r'<script type="module" src="([^"]+)">')


def test_app_metrics_ships_one_workspace_agent_over_every_measure() -> None:
    """A member asking how the team is doing is asking one question. Six apps each holding a sixth
    of the answer is six pages to read and six accounts to wire for one weekly read."""
    manifest = app_metrics.manifest()
    assert manifest.name == "app_metrics"
    assert [provision.name for provision in manifest.agents] == ["metrics"]
    provision = manifest.agents[0]
    assert provision.spec.visibility == "workspace"
    assert provision.spec.purpose
    assert "app-metrics-home" in provision.spec.prompt
    assert {path.name for path in (spec.path for spec in manifest.skills)} == {"app-metrics-home"}


def test_the_report_offers_no_hourly_cadence() -> None:
    """The report is read at the start of a working week. Nobody reads how the quarter is
    going hourly, and a report nobody reads is a turn nobody asked for — so the cadences it offers
    start at a weekday morning and none of them is hourly."""
    schedule = app_metrics.METRICS_APP_AGENT.setup.schedule
    assert schedule is not None
    assert schedule.name == app_metrics.REPORT_TASK
    assert all(cadence.hour is not None for cadence in schedule.cadences)
    assert schedule.cadences[0].weekdays == (1,)


def test_a_measure_states_the_rule_it_was_counted_by() -> None:
    """A number whose rule nobody can state is a number nobody can act on, so the rule rides beside
    the measure: the app is asked for one where the counting happens — the standing prompt and the
    prompt the schedule arms it with — and the page gives every measure a line to draw it on."""
    assert "State the rule you counted each by" in app_metrics.METRICS_APP_PROMPT
    schedule = app_metrics.METRICS_APP_AGENT.setup.schedule
    assert schedule is not None
    assert "State the rule each measure was counted by" in schedule.prompt


def test_every_set_names_the_products_that_answer_it() -> None:
    """A set is a question and a product is what answers it, so the prompt carries both together.
    An app that named the measures alone would leave the agent to guess what to connect, and a
    member asking for revenue would be asked which account they meant."""
    prompt = app_metrics.METRICS_APP_PROMPT
    for product in (
        "GitHub",
        "Stripe",
        "Metronome",
        "Mercury",
        "QuickBooks",
        "Brex",
        "Ramp",
        "Datadog",
        "PostHog",
        "Zendesk",
        "Intercom",
        "Slack",
    ):
        assert product in prompt
    assert "so the ask is answered now rather than at the next fire" in prompt


def test_every_product_the_prompt_names_can_actually_be_obtained() -> None:
    """The prompt tells a member which products answer a set, so every one of them needs a path the
    agent can drive. A product named in the table and declared nowhere is a connect the app offers
    and then refuses.

    A product the setup screen does not offer is named in `PROMPT_ONLY_PRODUCTS` instead, so the
    agent still knows the act that reaches it — the list is the whole of what the app can read,
    wherever the member learns it from."""
    setup = app_metrics.METRICS_APP_AGENT.setup
    declared = (
        set(setup.connectors)
        | set(app_metrics.SETUP_KEY_PRODUCTS)
        | set(app_metrics.PROMPT_ONLY_PRODUCTS)
    )
    named = {
        product.strip().lower()
        for line in app_metrics.METRICS_APP_PROMPT.splitlines()
        if line.startswith("| ") and line.count("|") == 4 and "---" not in line
        for product in line.split("|")[2].split(",")
    }
    named -= {"reads"}
    assert named
    assert named <= declared, f"named but unobtainable: {sorted(named - declared)}"


def test_the_setup_screen_offers_one_row_per_set_and_asks_for_none_of_them() -> None:
    """A row earns its place by making a set readable, so the screen names one product per set and
    never a second vendor for a set already covered — a keyed product taking a row per slot its own
    read needs. Every row is optional, because the member presses Build app when they are done
    connecting, not when the list is empty."""
    setup = app_metrics.METRICS_APP_AGENT.setup
    assert setup.connectors == ("stripe", "quickbooks", "zendesk")
    assert [(credential.label, credential.slots) for credential in setup.credentials] == [
        ("Datadog API key", ("datadog_api_key",)),
        ("Datadog application key", ("datadog_application_key",)),
        ("PostHog API key", ("posthog_api_key",)),
    ]
    assert not any(credential.required for credential in setup.credentials)


def test_a_key_row_offers_every_slot_that_products_read_takes() -> None:
    """A keyed provider states what its own read needs, and a Datadog read endpoint refuses an API
    key with no application key beside it. A screen offering a hand-picked slot would take an
    admin's key once and still report no number for the set it was filled for."""
    offered = {
        slot
        for credential in app_metrics.METRICS_APP_AGENT.setup.credentials
        for slot in credential.slots
    }
    for product in app_metrics.SETUP_KEY_PRODUCTS:
        provider = next(entry for entry in KEYED_PROVIDERS if entry.provider == product)
        assert {f"{product}_{secret.key}" for secret in provider.secrets} <= offered
    assert "datadog_application_key" in offered


def test_delivery_asks_the_setup_screen_for_nothing() -> None:
    """Delivery is read through a member's own GitHub account, obtained on the ask. Neither it nor
    the workspace-wide App install carries a setup row: the app that arrives measuring nothing until
    an admin installs something is the app a member abandons on its first screen."""
    setup = app_metrics.METRICS_APP_AGENT.setup
    assert "github" not in setup.connectors
    assert "github" in app_metrics.PROMPT_ONLY_PRODUCTS
    assert not any(
        slot.startswith("github") for credential in setup.credentials for slot in credential.slots
    )
    assert "GitHub" in app_metrics.METRICS_APP_PROMPT


def test_a_set_with_no_account_reports_no_number() -> None:
    """The whole app is that a number can be checked. A set the workspace connected nothing for has
    nothing behind it, so it states the products that would answer it and reports no number —
    never one the agent composed from what it remembers."""
    prompt = app_metrics.METRICS_APP_PROMPT
    assert "You never count by hand" in prompt
    assert "reports no number at all" in prompt


def test_one_schedule_carries_every_set() -> None:
    """A set turned on joins the report the workspace already reads. A task per set would fire six
    times a week over one question, and a member would turn six of them off to stop reading it."""
    prompt = app_metrics.METRICS_APP_PROMPT
    schedule = app_metrics.METRICS_APP_AGENT.setup.schedule
    assert schedule is not None
    assert schedule.name == app_metrics.REPORT_TASK
    assert f"`{app_metrics.REPORT_TASK}`" in prompt
    assert "Report every set this workspace holds an account for" in schedule.prompt


def test_the_report_keeps_the_name_the_armed_task_already_holds() -> None:
    """A provisioning pass rewrites the row's `setup` and never a live scheduled task, and the setup
    screen reads the armed order by the name this offer carries. An offer under a second name would
    read a firing report as unarmed, offer the cadence again, and arm a second task beside it — two
    reports a week for a workspace that asked for one."""
    assert app_metrics.REPORT_TASK == "delivery-report"
    schedule = app_metrics.METRICS_APP_AGENT.setup.schedule
    assert schedule is not None
    assert schedule.name == "delivery-report"


def test_the_prompt_names_the_act_that_reaches_each_product() -> None:
    """A granted account and a workspace key are obtained by different acts, and `connect_account`
    cannot reach a keyed provider at all. An app that named the products without naming the act
    would send the agent at the wrong verb and read its refusal as the product being unavailable."""
    prompt = app_metrics.METRICS_APP_PROMPT
    assert "are accounts a member grants: reach them with `connect_account`" in prompt
    assert "`connect_account` cannot reach them at all" in prompt
    assert "`request_credentials`" in prompt
    assert "never ask for a key in chat" in prompt
    assert "ask for both slots together" in prompt


def _named(said: str) -> set[str]:
    return {product.strip() for product in re.split(r",| and ", said) if product.strip()}


def test_a_brokered_product_is_asked_for_as_a_grant() -> None:
    """`connect_account` mints the consent leg for every brokered connector the shipped packs
    bundle, so a product named as keyed-only is one the app tells a member it cannot connect while
    the verb that connects it stands beside it. The other way round is the same fault: a product
    named as keyed declares the slots `request_credentials` asks for, or the ask reaches nothing."""
    prompt = app_metrics.METRICS_APP_PROMPT
    grants = re.search(r"([\w, ]+) are accounts a member grants", prompt)
    keyed = re.search(r"([\w, ]+) authenticate with a workspace key instead", prompt)
    assert grants is not None and keyed is not None
    granted = _named(grants[1])
    named_keyed = _named(keyed[1])
    for spec in BROKERED.values():
        if spec.label in named_keyed:
            assert spec.label in granted, f"{spec.label} is brokered and named as keyed only"
    declared = {provider.label for provider in KEYED_PROVIDERS}
    assert named_keyed <= declared, f"named as keyed, declared nowhere: {named_keyed - declared}"


def test_the_built_page_is_the_apps_own_tsx() -> None:
    entry = MODULE_SCRIPT.search(BUILD_ENTRY.read_text())
    assert entry is not None
    assert (BUILD_ENTRY.parent / entry[1]).resolve() == (SKILL_DIR / "app.tsx").resolve()
    source = (SKILL_DIR / "app.tsx").read_text()
    assert "mountApp(" in source
    for subject in ("Engineering", "Revenue", "Support"):
        assert f'"{subject}"' in source
    assert "counted as pull requests merged into the default branch" in source
    assert "<AppConversations" in source
    assert "AppSetup" not in source


def test_the_home_skill_edits_builds_and_deploys_the_project() -> None:
    skill = (SKILL_DIR / "SKILL.md").read_text()
    assert "Copy this skill's `app.tsx` and `index.html`" in skill
    assert (
        "`deploy_website` action (`object_action` with kind `site`) with that directory and "
        "`site_name` `metrics-home`"
    ) in skill
    assert "do not run a build yourself" in skill
    assert "`object_get` with an empty `ref` reads this turn's own agent" in skill
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
