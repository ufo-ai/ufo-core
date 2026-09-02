"""The metrics app: one workspace agent that reports how the team is doing.

It holds six sets of measures and privileges none of them. One subject is one app: a member asking
how the team is doing is asking one question, and six apps each holding a sixth of the answer is
six pages to read and six accounts to wire for one weekly read. What it reports follows from the
accounts the workspace connected, so a member connects what they have, builds the app, and reads
the sets those accounts answer.
"""

from pathlib import Path

from ufo_ext_keyed_connectors import KEYED_PROVIDERS

from ufo.sdk.manifest import (
    SCHEDULE_KIND,
    AgentProvision,
    AgentSetup,
    AgentSpec,
    Manifest,
    SetupCadence,
    SetupCredential,
    SetupSchedule,
    SkillSpec,
)

NAME = "app_metrics"
VERSION = "0.1.0"
SKILLS_ROOT = Path(__file__).parent / "skills"
HOME_SKILL = "app-metrics-home"
METRICS_APP_AGENT_NAME = "metrics"

REPORT_TASK = "delivery-report"
"""The one scheduled task that carries every set.

It keeps the name the first release shipped, when the report carried delivery alone, because the
name is the identity of a row a workspace already holds: a provisioning pass rewrites `setup` and
never a live task, so an offer under a second name would read the armed report as unarmed, offer
the cadence again, and arm a second task beside the one already firing."""

SETUP_PRODUCTS = ("stripe", "quickbooks", "zendesk")
"""The products the setup screen offers, one per set that a member can settle in a single act.

Every one is an offer and none is a gate: `Build app` stands beside the list, so a member connects
the sets they want measured and builds the page from those. A row therefore earns its place by
being the product that makes a set readable for most workspaces — a second vendor for a set already
covered is a row a member reads past, and the prompt carries it instead."""

SETUP_KEY_PRODUCTS = ("datadog", "posthog")
"""The products the setup screen asks for a workspace key for, for the two sets no grant can reach.

A key is filled once for the whole workspace and by an admin, so a product earns a row only where
the set has no granted account at all — which is why Mercury is absent while its set is covered by
QuickBooks. Every secret the provider declares takes a row of its own, because what a read needs is
the provider's own statement rather than this app's: a Datadog read endpoint refuses an API key with
no application key beside it, so a screen offering one of the two asks the admin and still reports
no number."""

PROMPT_ONLY_PRODUCTS = (
    "github",
    "metronome",
    "mercury",
    "brex",
    "ramp",
    "intercom",
    "slack",
)
"""The products the prompt names and the setup screen does not.

The long tail of a set already covered, so the agent obtains one on the ask rather than the screen
carrying a row nobody presses. Slack is here because the workspace's own Slack arrives with the
surface, so support conversations there need no second grant. Promoting one costs a line in
`SETUP_PRODUCTS`, and a workspace reads the list it was provisioned with either way."""

KEY_WORDS = {"api": "API"}

SETUP_CREDENTIALS = tuple(
    SetupCredential(
        label=" ".join(
            (provider.label, *(KEY_WORDS.get(word, word) for word in secret.key.split("_")))
        ),
        slots=(f"{provider.provider}_{secret.key}",),
    )
    for product in SETUP_KEY_PRODUCTS
    for provider in KEYED_PROVIDERS
    if provider.provider == product
    for secret in provider.secrets
)

METRICS_APP_PURPOSE = (
    "Reports delivery, revenue, runway, reliability, product and support on a cadence you pick, "
    "from the accounts you connect."
)

METRICS_APP_PROMPT = (
    "You are the Metrics app for this workspace. You report how the team is doing, over six sets "
    "of measures. No set is more yours than another. You report the sets this workspace has "
    "connected an account for, and a set with no account is a band that names the products that "
    "would answer it.\n"
    "\n"
    "| Set | Reads | Measures |\n"
    "|---|---|---|\n"
    "| Delivery | GitHub | changes shipped; first commit to merge; changes reverted inside seven "
    "days; open past a week |\n"
    "| Revenue | Stripe, Metronome | revenue in the window; revenue from new accounts; revenue "
    "lost to churn; net change |\n"
    "| Runway | Mercury, QuickBooks, Brex, Ramp | cash on hand; net burn over the window; months "
    "of runway at that burn |\n"
    "| Reliability | Datadog | alerts raised; longest alert open; the usage measure the workspace "
    "names |\n"
    "| Product | PostHog | active accounts; accounts that reached first value; accounts held from "
    "the window before |\n"
    "| Support | Zendesk, Intercom, Slack | conversations opened; time to first reply; open now "
    "and for how long |\n"
    "\n"
    "On each fire, report every connected set over the window since the last report. State the "
    "rule you counted each by, and what you could not count. A measure whose rule you cannot state "
    "is a number nobody can act on.\n"
    "\n"
    "You never count by hand. A number you composed from what you remember is a number no member "
    "can check, so a set whose account you do not hold reports no number at all.\n"
    "\n"
    "GitHub, Stripe, QuickBooks, Ramp, Brex, Zendesk and Intercom are accounts a member grants: "
    "reach them with `connect_account`. Datadog, PostHog, Mercury and Metronome authenticate with "
    "a workspace key instead, so `connect_account` cannot reach them at all — run the `credential` "
    "collection's `request_credentials` action for their slots, and never ask for a key in chat. "
    "Datadog refuses a read that carries its API key alone: every measure in the reliability set "
    "needs the application key beside it, so ask for both slots together.\n"
    "\n"
    "When a member asks for a set you hold nothing for, obtain it the way that product takes, then "
    "report once for the window in front of the member so the ask is answered now rather than at "
    "the next fire. A member who names a product outside the table is asking for a set you cannot "
    f"count — say which products answer that measure. One `scheduled_task` named `{REPORT_TASK}` "
    "carries every set, so a set turned on joins the report the workspace already reads instead of "
    "arming a second one.\n"
    "\n"
    "Your homepage is the metrics screen: what you are for, what the workspace still owes you, a "
    "band per set, and every conversation you hold. When a member asks you to change the page, "
    f"load the skill `{HOME_SKILL}` and follow it."
)

METRICS_APP_SETUP_INSTRUCTIONS = (
    "Connect the accounts the sets you want are read from. Every set you leave unconnected is a "
    "band the page states rather than a number, and a product this screen does not list — GitHub "
    "for delivery among them — is one to ask the app for."
)

METRICS_APP_SCHEDULE = SetupSchedule(
    name=REPORT_TASK,
    prompt=(
        "Report every set this workspace holds an account for, over the window since the last "
        "report. State the rule each measure was counted by, and what could not be counted."
    ),
    cadences=(
        # The report is read at the start of a working week, so the weekday-morning cadence
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
        connectors=SETUP_PRODUCTS,
        credentials=SETUP_CREDENTIALS,
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
