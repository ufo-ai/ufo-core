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
SLACK_TOOLS = "extensions/slack/ufo_ext_slack/tools.py"
METRONOME = "extensions/metronome/ufo_ext_metronome.py"
GATEWAY = "control/src/ufo_control/gateway.py"
INVITES = "control/src/ufo_control/gateway_invite.py"
SLACK_CONNECT = "control/src/ufo_control/gateway_slack_connect.py"
AUDIENCE = "core/src/ufo/audience.py"
MEMBERS = "core/src/ufo/members.py"


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
        pattern=r"workspaces\.ensure\(claim\.email_domain",
    ),
    Claim(
        claim="an admin is offered billing setup at the end of signup",
        corpus="references/getting-started.md",
        phrase="offered billing setup at the end",
        source=GATEWAY,
        pattern=r'directive\("choose", FIRST_MOVE_PROMPT, BILLING_CHOICE',
    ),
    Claim(
        claim="the team sets the shared Slack channel up by hand, so never promise the email",
        corpus="references/slack-install.md",
        phrase="never promise them an automatic email",
        source=SLACK_CONNECT,
        pattern=r'os\.environ\.get\(ENABLED_ENV, "false"\)',
    ),
    Claim(
        claim="a new member is seated automatically while an included seat is open",
        corpus="references/billing-and-seats.md",
        phrase="seated automatically while an included seat is open",
        source=METRONOME,
        pattern=r"INCLUDED_SEATS_DEFAULT = \d+",
    ),
    Claim(
        claim="seats stop at a hard cap",
        corpus="references/billing-and-seats.md",
        phrase="hard cap on seats",
        source=METRONOME,
        pattern=r"SEAT_LIMIT_DEFAULT = \d+",
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
        claim="an externally shared channel reads and writes only itself",
        corpus="references/capabilities.md",
        phrase="reads and\nwrites only itself",
        source=AUDIENCE,
        pattern=r"def audience_subjects\(.*\n(?:.*\n)*?.*return frozenset\(\{parsed\}\)",
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
        claim="a private channel carries a room scope of its own",
        corpus="references/capabilities.md",
        phrase="Room-scoped memory",
        source=AUDIENCE,
        pattern=r'ROOM_AUDIENCE_PREFIX = "room:"',
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
