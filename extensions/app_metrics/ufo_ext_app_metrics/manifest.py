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
    (Path(__file__).parent / "prompts" / "agent_metrics.md")
    .read_text()
    .strip()
    .replace("{{home_skill}}", HOME_SKILL)
    .replace("{{report_task}}", REPORT_TASK)
)

METRICS_APP_SETUP_INSTRUCTIONS = (
    "Connect the accounts the sets you want are read from. Every set you leave unconnected is a "
    "band the page states rather than a number, and a product this screen does not list — GitHub "
    "for delivery among them — is one to ask the app for."
)

METRICS_APP_SCHEDULE = SetupSchedule(
    name=REPORT_TASK,
    prompt=(Path(__file__).parent / "prompts" / "task_report.md").read_text().strip(),
    cadences=(
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
