"""The hosted onboarding corpus states product facts to paying customers, and it drifts silently:
the code that makes a claim true lives in another package, changes without touching `packs/`, and
nothing about a stale sentence looks wrong to a reader. So every claim a mechanism can pin is pinned
from both ends — the corpus still says it, and the source that makes it true still says it. Deleting
the mechanism fails here and names the claim to rewrite; rewording the corpus past its anchor fails
here too, so an edit cannot quietly orphan the evidence.

This gate covers what a pattern can hold. The prose it cannot pin — customer-facing wording, the
troubleshooting remedies, what "shortly" means — is what the scheduled corpus-drift sweep reads."""

from dataclasses import dataclass
from pathlib import Path
from re import IGNORECASE, MULTILINE, findall, search

import yaml

from ufo.tools.builtins import BUILTIN_TOOLS

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "packs/assistant_hosted/skills/customer-onboarding-help"
SLACK_SETUP_SKILL_MD = "extensions/slack/ufo_ext_slack/skills/slack-app-setup/SKILL.md"
SLACK_TOOLS = "extensions/slack/ufo_ext_slack/tools.py"
METRONOME = "extensions/metronome/ufo_ext_metronome.py"
GATEWAY = "control/src/ufo_control/gateway.py"
GATEWAY_WEB = "control/src/ufo_control/gateway_web.py"
GATEWAY_SHARED = "control/src/ufo_control/gateway_shared.py"
INVITES = "control/src/ufo_control/gateway_invite.py"
SLACK_CONNECT = "control/src/ufo_control/gateway_slack_connect.py"
AUDIENCE = "core/src/ufo/audience.py"
MEMBERS = "core/src/ufo/members.py"
SEATS = "core/src/ufo/seats.py"
TABLES = "core/src/ufo/schema/tables.py"
WORKSPACE_KIND = "core/src/ufo/workspace_kind.py"
SCHEDULED_TASKS = "extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py"
SCHEDULING = "core/src/ufo/scheduling.py"
SLACK_SURFACE = "extensions/slack/ufo_ext_slack/surface.py"
BUILTIN_TOOLS_SOURCE = "core/src/ufo/tools/builtins.py"
SITES_TOOLS = "extensions/sites/ufo_ext_sites/tools.py"
TOOLS_CONTEXT = "core/src/ufo/tools/context.py"


@dataclass(frozen=True)
class Claim:
    """One corpus claim, the phrase carrying it, and the source pattern that makes it true."""

    claim: str
    corpus: str
    phrase: str
    source: str
    pattern: str


CLAIMS = (
    Claim(
        claim="the signed-in card carries an 'Open your workspace' button",
        corpus="references/getting-started.md",
        phrase='signed-in card carries "Open your workspace"',
        source=GATEWAY_WEB,
        pattern=r"Open your workspace",
    ),
    Claim(
        claim="the card's button opens the web portal",
        corpus="references/capabilities.md",
        phrase='"Open your workspace" button opens the web portal',
        source=GATEWAY_WEB,
        pattern=r"portal\.action = workspace \+ '/surface/web'",
    ),
    Claim(
        claim="signup asks for nothing but an email and a verification code",
        corpus="references/getting-started.md",
        phrase="nothing in it to retype",
        source=INVITES,
        pattern=r"async def redeem\(\s*self, email_domain: str",
    ),
    Claim(
        claim="an invitation works once per email domain",
        corpus="references/getting-started.md",
        phrase="works once per domain",
        source=INVITES,
        pattern=r"LIVE_DOMAIN_INDEX.*\n.*where consumed_at is null",
    ),
    Claim(
        claim="an unused invitation lapses after a couple of weeks",
        corpus="references/getting-started.md",
        phrase="lapses if it goes unused for a couple",
        source=INVITES,
        pattern=r"INVITE_TTL = timedelta\(days=14\)",
    ),
    Claim(
        claim="a refusal ends the session instead of asking again",
        corpus="references/getting-started.md",
        phrase="ends the session with the reason rather than asking again",
        source=GATEWAY,
        pattern=r'directive\("exit", "0"\)',
    ),
    Claim(
        claim="one workspace per email domain",
        corpus="references/not-yet.md",
        phrase="One workspace exists per email domain",
        source=GATEWAY,
        pattern=r"workspaces\.create\(claim\.email_domain",
    ),
    Claim(
        claim="a sign-in offers every workspace the verified address can enter",
        corpus="references/getting-started.md",
        phrase="asks them to choose when an exact",
        source=GATEWAY_SHARED,
        pattern=r"where matching\.email = \$1",
    ),
    Claim(
        claim="an exact membership bypasses the new-workspace invite gate",
        corpus="references/getting-started.md",
        phrase="An exact membership needs no invite",
        source=GATEWAY,
        pattern=r"if not choices:",
    ),
    Claim(
        claim="an admin is offered billing setup at the end of signup",
        corpus="references/getting-started.md",
        phrase="offered billing setup at the end",
        source=GATEWAY,
        pattern=r'directive\("choose", FIRST_MOVE_PROMPT, BILLING_CHOICE',
    ),
    Claim(
        claim="the shared Slack channel's invitation is a deploy switch away, so never promise it",
        corpus="references/slack-install.md",
        phrase="never promise them an automatic email",
        source=SLACK_CONNECT,
        pattern=(
            r'match os\.environ\.get\(ENABLED_ENV, "false"\)\.strip\(\)\.lower\(\):\n'
            r'\s+case "false" \| "0":\n'
            r"\s+return None"
        ),
    ),
    Claim(
        claim="pending is tied to the current Slack signing secret",
        corpus="references/slack-install.md",
        phrase="A new install, a manifest setup, or a signing-secret rotation can all land here",
        source=SLACK_TOOLS,
        pattern=(
            r'marker\.get\("fingerprint"\) == signing_secret_fingerprint\(\n'
            r"\s+secret\n"
            r"\s+\)"
        ),
    ),
    Claim(
        claim="pending follows the verified-event guard and gives the first-contact remedy",
        corpus="references/slack-install.md",
        phrase="Invite the bot to a channel and @mention it, or send it a DM",
        source=SLACK_TOOLS,
        pattern=(
            r"if await _verified\(ctx\):\n"
            r"(?:.*\n)*?"
            r"\s+return _state\(\n"
            r'\s+"pending",'
        ),
    ),
    Claim(
        claim="the mounted Slack setup skill treats rotated credentials as pending",
        corpus="references/slack-install.md",
        phrase="A new install, a manifest setup, or a signing-secret rotation can all land here",
        source=SLACK_SETUP_SKILL_MD,
        pattern=(
            r"`pending`\s+means\s+an\s+app\s+identity\s+exists,\s+but\s+this\s+deploy\s+has\s+not\s+"
            r"verified\s+a\s+Slack\s+event\s+with\s+the\s+current\s+app\s+credentials;\s+a\s+new\s+"
            r"install,\s+manifest\s+setup,\s+or\s+signing-secret\s+rotation\s+can\s+all\s+land\s+"
            r"here\s+\(skip\s+to\s+Step\s+5\)"
        ),
    ),
    Claim(
        claim="the overview's close is driven by the slack-app-setup skill, which must still ship",
        corpus="references/capabilities.md",
        phrase="load the `slack-app-setup` skill and let it drive the install",
        source=SLACK_SETUP_SKILL_MD,
        pattern=r"^name: slack-app-setup$",
    ),
    Claim(
        claim="that skill owns the install end to end, so the corpus assembles no step of it",
        corpus="references/capabilities.md",
        phrase="never assemble an install step, a link, or a request for a token here",
        source=SLACK_SETUP_SKILL_MD,
        pattern=r"Secrets\s+never\s+enter\s+this\s+chat",
    ),
    Claim(
        claim="a new member is answered straight away, with no approval step",
        corpus="references/billing-and-seats.md",
        phrase="answered by the agent straight away",
        source=TABLES,
        pattern=(r'sa\.Column\(\s*\n?\s*"seated_at".*server_default=sa\.func\.now\(\)'),
    ),
    Claim(
        claim="members are unlimited and the count is only ever reported",
        corpus="references/billing-and-seats.md",
        phrase="members are unlimited",
        source=METRONOME,
        pattern=r'"properties": \{"seat_count": str\(len\(snapshot\.members\)\)\}',
    ),
    Claim(
        claim="plan activation runs on a schedule rather than instantly",
        corpus="references/billing-and-seats.md",
        phrase="activation runs on a schedule",
        source=METRONOME,
        pattern=r"BILLING_JOB_SCHEDULE = ",
    ),
    Claim(
        claim="only an admin can reach billing",
        corpus="references/billing-and-seats.md",
        phrase="Only a workspace admin can set up billing",
        source=METRONOME,
        pattern=r"async def _admin_billing\(ctx: ToolContext\)",
    ),
    Claim(
        claim="any member can list who is in the workspace and which of them are admins",
        corpus="references/billing-and-seats.md",
        phrase="Any member can list who is in the workspace",
        source=WORKSPACE_KIND,
        pattern=(
            r"async def status\(\n"
            r"(?:.*\n)*?"
            r'\s+"roster": \[\n'
            r'\s+\{"email": entry\.email, "seated": entry\.seated, "admin": entry\.admin\}'
        ),
    ),
    Claim(
        claim="an externally shared channel reads and writes only itself",
        corpus="references/capabilities.md",
        phrase="reads and\nwrites only itself",
        source=AUDIENCE,
        pattern=r"def audience_subjects\(.*\n(?:.*\n)*?.*return frozenset\(\{parsed\}\)",
    ),
    Claim(
        claim=(
            "a member speaking in a sealed foreign channel still reaches their own private "
            "memory, and what they say there is not written into it"
        ),
        corpus="references/capabilities.md",
        phrase="A member speaking there still reaches their own private memory",
        source=TOOLS_CONTEXT,
        pattern=r"return subjects \| \{member_subject\(acting\)\}",
    ),
    Claim(
        claim="the last seated admin cannot be demoted",
        corpus="references/capabilities.md",
        phrase="keeps at least one seated admin",
        source=MEMBERS,
        pattern=r'raise ValueError\("a workspace must have a seated admin"\)',
    ),
    Claim(
        claim="only an admin, through the main agent, can change an admin role",
        corpus="references/capabilities.md",
        phrase="never a subagent",
        source=MEMBERS,
        pattern=r"if not await ctx\.agent_is_main\(\) or ctx\.speaker_member_id is None",
    ),
    Claim(
        claim="an admin can add a teammate before that teammate ever signs in",
        corpus="references/getting-started.md",
        phrase="add someone ahead of their first",
        source=MEMBERS,
        pattern=r'ADD_MEMBER_TOOL = "add_member"',
    ),
    Claim(
        claim="an added address may be at any email domain",
        corpus="references/getting-started.md",
        phrase="at any email domain",
        source=MEMBERS,
        pattern=r"have ever contacted the agent, at any email domain",
    ),
    Claim(
        claim="an admin removes a person's access by unseating them, and can restore it",
        corpus="references/billing-and-seats.md",
        phrase="an admin unseats them in chat",
        source=SEATS,
        pattern=r"async def revoke\(.*\n(?:.*\n)*?\s+\.values\(seated_at=None",
    ),
    Claim(
        claim="a member an admin adds is answered without any further step",
        corpus="references/billing-and-seats.md",
        phrase="one an admin adds by email",
        source=MEMBERS,
        pattern=r"They can speak to the agent now\.",
    ),
    Claim(
        claim="a private channel carries a room scope of its own",
        corpus="references/capabilities.md",
        phrase="Room-scoped memory",
        source=AUDIENCE,
        pattern=r'ROOM_AUDIENCE_PREFIX = "room:"',
    ),
    Claim(
        claim="there is no one-time reminder — every scheduled task repeats",
        corpus="references/not-yet.md",
        phrase="there is no one-shot scheduling",
        source=SCHEDULED_TASKS,
        pattern=(
            r"if validated_schedule is None or spec\.prompt is None:\n"
            r'\s+raise ValueError\("creating a scheduled task requires schedule and prompt"\)'
        ),
    ),
    Claim(
        claim="there is no one-time reminder",
        corpus="references/capabilities.md",
        phrase="there is no one-time reminder",
        source=SCHEDULED_TASKS,
        pattern=(
            r"if validated_schedule is None or spec\.prompt is None:\n"
            r'\s+raise ValueError\("creating a scheduled task requires schedule and prompt"\)'
        ),
    ),
    Claim(
        claim="the agent answers a channel message only when addressed, never passing top-level"
        " traffic",
        corpus="references/capabilities.md",
        phrase="only when @-mentioned; once\n  a mention starts a thread, every reply in that"
        " thread reaches it too",
        source=SLACK_SURFACE,
        pattern=(
            r"an un-addressed\n"
            r"\s+channel message is admitted only as a reply in a thread the agent already"
            r" converses in"
        ),
    ),
    Claim(
        claim="a document artifact is shared back as a downloadable file",
        corpus="references/capabilities.md",
        phrase="shares them back as\ndownloadable files",
        source=BUILTIN_TOOLS_SOURCE,
        pattern=r'name="share_file"',
    ),
    Claim(
        claim="a website is hosted and shared as a permanent link, not a downloadable file",
        corpus="references/capabilities.md",
        phrase="the agent hosts it and shares a permanent link instead of a file to\ndownload",
        source=SITES_TOOLS,
        pattern=r"register that port as a hosted site and return its\n`site_url`",
    ),
    Claim(
        claim="an expiry stops a scheduled task from running again",
        corpus="references/not-yet.md",
        phrase="A schedule that should stop can carry an expiry instead",
        source=SCHEDULING,
        pattern=(
            r"if task\.expires_at is None or task\.expires_at > now:\n"
            r"\s+return False\n"
            r"\s+async with workspace_tx\(\) as connection:\n"
            r"\s+await connection\.execute\(\n"
            r"\s+sa\.delete\(tables\.scheduled_task\)"
        ),
    ),
)


def _flowed(text: str) -> str:
    """Markdown prose is hard-wrapped, so a phrase spans lines wherever the paragraph happens to
    break. Compare with runs of whitespace collapsed: a reflow is not a claim change."""
    return " ".join(text.split())


def test_every_claim_holds_at_both_ends() -> None:
    for claim in CLAIMS:
        corpus = _flowed((CORPUS / claim.corpus).read_text())
        assert _flowed(claim.phrase) in corpus, (
            f"{claim.corpus} no longer says {claim.phrase!r} — the anchor for "
            f"{claim.claim!r} is orphaned"
        )
        source = (REPO_ROOT / claim.source).read_text()
        assert search(claim.pattern, source, MULTILINE) is not None, (
            f"{claim.source} no longer matches {claim.pattern!r}, so the corpus claim "
            f"{claim.claim!r} in {claim.corpus} may be stale"
        )


def test_the_corpus_names_exactly_the_install_states_slack_reports() -> None:
    """The install-state table is translated for customers state by state, so a fifth state leaves a
    customer with no row and a renamed one leaves a row that can never appear. Both sides are
    discovered, never filtered against a list this file keeps: the reported set is every state
    `_state()` is called with, the documented set is every underscored token the table backquotes
    (`slack-app-setup` and `not-yet.md` carry hyphens and dots, so they cannot collide). A list
    would make the comparison a tautology — a new fifth state is exactly what it could not see."""
    reported = set(findall(r'_state\(\s*"([a-z_]+)"', (REPO_ROOT / SLACK_TOOLS).read_text()))
    documented = set(findall(r"`([a-z_]+)`", (CORPUS / "references/slack-install.md").read_text()))

    assert reported, "found no _state() calls — the extraction broke, not the states"
    assert documented, "found no backquoted states in the table — the extraction broke"
    assert reported == documented, (
        f"the corpus table and the Slack extension disagree: "
        f"undocumented {reported - documented or '{}'}, "
        f"no longer reported {documented - reported or '{}'}"
    )


def test_no_member_facing_cancel_contradicts_the_corpus() -> None:
    """The corpus tells a customer a running turn cannot be cancelled and will finish. The only
    cancel a turn can reach is a parent cancelling its own subagent; a member-facing one would make
    that a lie."""
    cancels = {tool.name for tool in BUILTIN_TOOLS if "cancel" in tool.name}

    assert cancels == {"cancel_subagent"}, f"unexpected cancel tool(s): {cancels}"


BANNED_COPY = (
    "beam",
    "transmit",
    "signal",
    "saucer",
    "mothership",
    "identification",
    "identified",
    "unidentified",
)


def test_the_corpus_carries_none_of_the_banned_copy() -> None:
    """CLAUDE.md's user-facing copy rule bans the UFO metaphors from every word a member reads, and
    this corpus is the vocabulary the agent answers a customer in. Pinned because the words were
    removed by hand: `identification` and `identified` were throughout it until they were caught in
    review, and nothing would have caught them coming back."""
    found = {
        f"{path.name}:{word}"
        for path in sorted(CORPUS.rglob("*.md"))
        for word in BANNED_COPY
        if search(rf"\b{word}", path.read_text(), IGNORECASE)
    }

    assert not found, f"banned copy in the corpus: {', '.join(sorted(found))}"


def test_the_drift_sweep_points_at_the_corpus_and_this_gate() -> None:
    """The sweep is the half of the coverage a pattern cannot hold, so it must read the corpus this
    gate pins and be able to extend it: a prompt that named neither would sweep nothing."""
    workflow = yaml.load(
        (REPO_ROOT / ".github/workflows/corpus-drift.yml").read_text(), Loader=yaml.BaseLoader
    )
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    verify = jobs["verify"]
    assert isinstance(verify, dict)
    steps = [step for step in verify["steps"] if isinstance(step, dict)]
    sweep = next(step for step in steps if str(step.get("uses", "")).startswith("anthropics/"))
    prompt = str(sweep["with"]["prompt"])

    assert workflow["on"]["schedule"]
    assert CORPUS.relative_to(REPO_ROOT).as_posix() in prompt
    assert Path(__file__).name in prompt
