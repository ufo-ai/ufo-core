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
GATEWAY = "servers/control/src/gateway.rs"
GATEWAY_WEB = "servers/control/src/login.html"
WORKOS = "servers/control/src/workos.rs"
ONBOARD_CONTROL = "core/src/ufo/onboard/onboard_control.py"
INVITES = "servers/control/src/invite.rs"
INVITE_DELIVERY = "servers/control/src/invite_delivery.rs"
SLACK_CONNECT = "servers/control/src/slack_connect.rs"
AUDIENCE = "core/src/ufo/turns/audience.py"
MEMBERS = "core/src/ufo/kinds/members.py"
BALANCE = "core/src/ufo/billing/balance.py"
SEATS = "core/src/ufo/seats.py"
TABLES = "core/src/ufo/schema/tables.py"
WORKSPACE_KIND = "core/src/ufo/kinds/workspace_kind.py"
SCHEDULED_TASKS = "extensions/scheduled_tasks/ufo_ext_scheduled_tasks/tools.py"
SCHEDULING = "extensions/scheduled_tasks/ufo_ext_scheduled_tasks/schedules.py"
SLACK_SURFACE = "extensions/slack/ufo_ext_slack/surface.py"
BUILTIN_TOOLS_SOURCE = "core/src/ufo/tools/builtins.py"
SITES_TOOLS = "extensions/sites/ufo_ext_sites/tools.py"
TOOLS_CONTEXT = "core/src/ufo/tools/context.py"
STOP = "core/src/ufo/surfaces/stop.py"
WEB_PANELS = "extensions/web/ufo_ext_web/panels.py"
WEB_SURFACE = "extensions/web/ufo_ext_web/surface.py"
WEB_AUDIENCE = "extensions/web/ufo_ext_web/audience.py"
WEB_BILLING_VIEW = "extensions/web/frontend/src/views/Billing.tsx"
TERMINAL_SURFACE = "extensions/ufo/ufo_ext_ufo/surface.py"
SPEC = "spec.md"
TASK_SCHEDULING_SKILL_MD = (
    "extensions/scheduled_tasks/ufo_ext_scheduled_tasks/skills/task-scheduling/SKILL.md"
)
CREATE_APPLICATION_SKILL_MD = "core/src/ufo/skills/create-application/SKILL.md"
RADAR_HOME = "extensions/app_radar/ufo_ext_app_radar/skills/app-radar-home/app.tsx"
ARTIFACTS_HOME = "extensions/app_artifacts/ufo_ext_app_artifacts/skills/app-artifacts-home/app.tsx"
TASKS_VIEW = "extensions/web/frontend/src/views/Tasks.tsx"
WEB_OBJECTS = "extensions/web/frontend/src/kernel/objects.tsx"
MEMORY_MANIFEST = "extensions/memory/ufo_ext_memory/manifest.py"
WEB_MEMORY_VIEW = "extensions/web/frontend/src/views/Memory.tsx"
WEB_APP = "extensions/web/frontend/src/App.tsx"
FIRST_RUN_VIEW = "extensions/web/frontend/src/views/FirstRun.tsx"


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
        claim="the invitation opens first run and sign-in continues there",
        corpus="references/getting-started.md",
        phrase="invitation opens the workspace's first-run page",
        source=GATEWAY_WEB,
        pattern=r"founding \|\| firstRun \? '\?first=1' : ''",
    ),
    Claim(
        claim="web sign-in opens the portal without another action",
        corpus="references/capabilities.md",
        phrase="sign-in opens the web portal automatically",
        source=GATEWAY_WEB,
        pattern=r"portal\.requestSubmit\(\)",
    ),
    Claim(
        claim="signup asks for nothing but an email and a verification code",
        corpus="references/getting-started.md",
        phrase="nothing in it to retype",
        source=INVITES,
        pattern=r"pub async fn redeem\(",
    ),
    Claim(
        claim="an invitation works once per signup subject, a work address's own domain",
        corpus="references/getting-started.md",
        phrase="works once per domain",
        source=INVITES,
        pattern=r'refuse_standing\(\n\s+&transaction,\n\s+"signup_subject",\n',
    ),
    Claim(
        claim="an unused invitation lapses after a couple of weeks",
        corpus="references/getting-started.md",
        phrase="lapses if it goes unused for a couple",
        source=INVITES,
        pattern=r"INVITE_TTL_DAYS: i64 = 14",
    ),
    Claim(
        claim="a refusal ends the session instead of asking again",
        corpus="references/getting-started.md",
        phrase="ends the session with the reason rather than asking again",
        source=GATEWAY,
        pattern=r'directive\("exit", &\["0"\]\)',
    ),
    Claim(
        claim="one workspace per signup subject, a work address's own domain",
        corpus="references/not-yet.md",
        phrase="One workspace exists per email domain",
        source=GATEWAY,
        pattern=r"\.create\(\s*&claim\.signup_subject,",
    ),
    Claim(
        claim="a sign-in offers every workspace the verified address can enter",
        corpus="references/getting-started.md",
        phrase="asks them to choose when an exact",
        source=ONBOARD_CONTROL,
        pattern=r"where matching\.email = :member",
    ),
    Claim(
        claim="an exact membership bypasses the new-workspace invite gate",
        corpus="references/getting-started.md",
        phrase="An exact membership needs no invite",
        source=GATEWAY,
        pattern=r"if choices\.is_empty\(\)",
    ),
    Claim(
        claim="the browser sign-in page offers a Google account as an alternative to the emailed"
        " code",
        corpus="references/getting-started.md",
        phrase="they choose Continue with Google and verify through their Google account",
        source=WORKOS,
        pattern=r'pub const GOOGLE_PROVIDER: &str = "GoogleOAuth";',
    ),
    Claim(
        claim="an admin is offered connecting Slack, billing setup, or a tour, at the end of"
        " signup",
        corpus="references/getting-started.md",
        phrase="offered connecting Slack, billing setup, or a tour, at the end",
        source=GATEWAY,
        pattern=(r"\[FIRST_MOVE_PROMPT, SLACK_CHOICE, BILLING_CHOICE, TOUR_CHOICE\]"),
    ),
    Claim(
        claim="the shared Slack channel's invitation is a deploy switch away, so never promise it",
        corpus="references/slack-install.md",
        phrase="never promise them an automatic email",
        source=SLACK_CONNECT,
        pattern=r'"false" \| "0" => Ok\(None\)',
    ),
    Claim(
        claim=(
            "the Slack Connect invitation goes to whoever the grant named, not necessarily "
            "whoever ends up creating the workspace"
        ),
        corpus="references/slack-install.md",
        phrase="not necessarily whoever ends up creating the workspace",
        source=SLACK_CONNECT,
        pattern=(
            r"pub fn earned_sql\(\) -> String \{\n"
            r"\s+format!\(\n"
            r'\s+"select email_domain, email, created_at from \{INVITE_TABLE\} \\\n'
            r"\s+where \(consumed_at is not null or expires_at > now\(\)\) \\\n"
            r"\s+and signup_subject <> email \\\n"
            r"\s+union all \\\n"
            r"\s+select email_domain, email, created_at from \{CLAIM_TABLE\} \\\n"
            r"\s+where created_workspace \\\n"
            r'\s+and signup_subject <> email"'
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
        claim="what a workspace is billed for is what it spent, never how many people it holds",
        corpus="references/billing-and-seats.md",
        phrase="members are unlimited",
        source=METRONOME,
        pattern=r'"priced_micro_usd": str\(export\.priced_micro_usd\)',
    ),
    Claim(
        claim="no count of members is shipped, rated, or enforced anywhere",
        corpus="references/billing-and-seats.md",
        phrase="No count of members is shipped, rated, or enforced anywhere",
        source=SPEC,
        pattern=r"so no count of\nmembers is shipped, rated, or enforced anywhere",
    ),
    Claim(
        claim="a spent balance refuses the turn rather than queueing it",
        corpus="references/billing-and-seats.md",
        phrase="a turn is refused once the balance reaches",
        source=BALANCE,
        pattern=r"def balance_refusal_message\(billing_url: str \| None\) -> str:",
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
        claim="adding someone writes to them unless the admin asks for no message",
        corpus="references/getting-started.md",
        phrase="An admin asking the agent can ask for no message",
        source=MEMBERS,
        pattern=r"notify: bool = Field\(\n\s+default=True,",
    ),
    Claim(
        claim="that message carries a link to the sign-in page, and no invite code",
        corpus="references/getting-started.md",
        phrase="emailed that they were added, with a link to the ordinary sign-in page",
        source=INVITE_DELIVERY,
        pattern=r"Sign in as \{email\} at https://\{apex_host\}\{sign_in_path\}",
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
        claim="a scheduled run posts only when it finds something worth reporting",
        corpus="references/capabilities.md",
        phrase="a run posts only when it finds something worth reporting",
        source=TASK_SCHEDULING_SKILL_MD,
        pattern=r"Nothing new happened since the last check — end the run without posting",
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
        pattern=r"register that port as a hosted site, returning its\s+`site_url`",
    ),
    Claim(
        claim="Radar opens a scheduled run with its report, files, and conversation",
        corpus="references/capabilities.md",
        phrase="Radar opens each scheduled run as a full report with its files and\n  conversation",
        source=RADAR_HOME,
        pattern=(
            r"Each scheduled run reports here: the reply it closed with and the files it shared"
        ),
    ),
    Claim(
        claim="Artifacts lists shared files and hosted sites",
        corpus="references/capabilities.md",
        phrase="Artifacts lists shared files and hosted sites",
        source=ARTIFACTS_HOME,
        pattern=r"A file or site an app makes in a conversation is listed here",
    ),
    Claim(
        claim="Tasks lists scheduled work and source triggers",
        corpus="references/capabilities.md",
        phrase="Tasks lists recurring tasks and source triggers",
        source=TASKS_VIEW,
        pattern=(
            r'\{ kind: "scheduled_task", label: "Scheduled" \},\n'
            r'\s+\{ kind: "source_trigger", label: "Triggers" \}'
        ),
    ),
    Claim(
        claim="Tasks can pause or resume a scheduled task",
        corpus="references/capabilities.md",
        phrase="Tasks can also pause or resume a scheduled task",
        source=TASKS_VIEW,
        pattern=r'\{paused \? "Resume" : "Pause"\}',
    ),
    Claim(
        claim="Tasks uses the editable object panel for its rows",
        corpus="references/capabilities.md",
        phrase="edit\nor delete a row",
        source=TASKS_VIEW,
        pattern=r"<ObjectDetail",
    ),
    Claim(
        claim="the object panel can edit or delete a row",
        corpus="references/capabilities.md",
        phrase="edit\nor delete a row",
        source=WEB_OBJECTS,
        pattern=(
            r"<Button variant=\"send\" onClick=\{\(\) => setEditing\(record\)\}>\n"
            r"\s+Edit\n(?:.*\n){0,3}\s+<ConfirmButton verb=\"Delete\""
        ),
    ),
    Claim(
        claim="Radar can rebuild its entries",
        corpus="references/capabilities.md",
        phrase="Radar can rebuild its entries",
        source=RADAR_HOME,
        pattern=r'<RebuildDialog title="Rebuild Entries" action="Rebuild entries"',
    ),
    Claim(
        claim="the portal memory view reads saved facts and records corrections",
        corpus="references/capabilities.md",
        phrase="In Memory in the web portal, a member can read saved facts and record a correction",
        source=WEB_MEMORY_VIEW,
        pattern=(
            r"usePanelRead<MemoryPayload>\((?:.*\n)*?\s+"
            r"<DialogTitle>Correct Memory</DialogTitle>"
        ),
    ),
    Claim(
        claim="a portal memory correction supersedes instead of editing the earlier statement",
        corpus="references/capabilities.md",
        phrase="a correction that\n  supersedes the earlier statement",
        source=WEB_PANELS,
        pattern=r"The named item is never edited or removed",
    ),
    Claim(
        claim="the agent records a correction through the memory update tool",
        corpus="references/capabilities.md",
        phrase="The agent can also correct a fact when the member states the correction in chat",
        source=MEMORY_MANIFEST,
        pattern=r'name="memory_update"',
    ),
    Claim(
        claim="an actual app build routes to the create-application skill",
        corpus="references/capabilities.md",
        phrase="load `create-application` and do it instead of answering with this overview",
        source=CREATE_APPLICATION_SKILL_MD,
        pattern=r"^name: create-application$",
    ),
    Claim(
        claim="a member who names the job goes straight to the application interview",
        corpus="references/capabilities.md",
        phrase="A member who names the job goes straight to the interview",
        source=CREATE_APPLICATION_SKILL_MD,
        pattern=r"A member who named the job gets the interview straight away",
    ),
    Claim(
        claim="the application interview asks for the job and its audience",
        corpus="references/capabilities.md",
        phrase="It asks what job the application is for\nand who can use it",
        source=CREATE_APPLICATION_SKILL_MD,
        pattern=(
            r"\| What job is it for\? \| the prompt \|\n"
            r"\| Who else uses it\? `Just me` / `Everyone in the workspace` \|"
        ),
    ),
    Claim(
        claim="a member who does not name the job gets the guided application build",
        corpus="references/capabilities.md",
        phrase="the job gets a guided build: the same run with a proposal in front of it",
        source=CREATE_APPLICATION_SKILL_MD,
        pattern=(r"gets a guided build: the same run\nwith a proposal in front of it"),
    ),
    Claim(
        claim="both ways into an application design its homepage before anything is created",
        corpus="references/capabilities.md",
        phrase="Either way the member is then shown the homepage design",
        source=CREATE_APPLICATION_SKILL_MD,
        pattern=(
            r"A member who named the job gets the interview straight away, then the design of the "
            r"app's\nhomepage, then the create\."
        ),
    ),
    Claim(
        claim="an application is created only after the member accepts its design",
        corpus="references/capabilities.md",
        phrase="creation follows only once they accept it",
        source=CREATE_APPLICATION_SKILL_MD,
        pattern=r"`Build it` on the design is the member saying go\.",
    ),
    Claim(
        claim="a member reaches every workspace-visible agent and their own, not only shared ones",
        corpus="references/capabilities.md",
        phrase="the agents open to everyone in the workspace, any agent they created themselves",
        source=WEB_AUDIENCE,
        pattern=(
            r'if a\.visibility == "workspace"\s+or a\.id in granted\s+'
            r"or \(member_id is not None and a\.owner_member_id == member_id\)"
        ),
    ),
    Claim(
        claim="the billing screen turns automatic refills on or off, no chat needed",
        corpus="references/billing-and-seats.md",
        phrase="turn\n  automatic refills on or off",
        source=WEB_PANELS,
        pattern=r'verb: Literal\["refill"\]',
    ),
    Claim(
        claim="the screen takes any whole-dollar refill amount and balance line, not a fixed pair",
        corpus="references/billing-and-seats.md",
        phrase="at any whole-dollar amount and any balance to refill below",
        source=WEB_BILLING_VIEW,
        pattern=r"refill\(figures\.amount, figures\.below\)",
    ),
    Claim(
        claim="the billing screen asks for the portal link, to save a first card or change one",
        corpus="references/billing-and-seats.md",
        phrase="the billing screen offers a button that returns the same link — to save a\nfirst"
        " card, or to change the one on file",
        source=WEB_PANELS,
        pattern=r'verb: Literal\["save_card"\]',
    ),
    Claim(
        claim="no route adds credit once without leaving a standing rule behind",
        corpus="references/not-yet.md",
        phrase="Adding credit once, without leaving a standing rule behind, is not something the"
        " screen or\n  the agent can do",
        source=METRONOME,
        pattern=r'action: Literal\["status", "portal", "autopay"\]',
    ),
    Claim(
        claim="the screen names the card it will charge by brand and last four",
        corpus="references/billing-and-seats.md",
        phrase="names the card it will charge, by brand and last four digits",
        source=WEB_BILLING_VIEW,
        pattern=r'card\.brand \+ " •••• " \+ card\.last4',
    ),
    Claim(
        claim="an expiry stops a scheduled task from running again",
        corpus="references/not-yet.md",
        phrase="A schedule that should stop can carry an expiry instead",
        source=SCHEDULING,
        pattern=(
            r"if task\.expires_at is None or task\.expires_at > now:\n"
            r"\s+return False\n"
            r"\s+async with self\.ctx\.transaction\(\) as connection:\n"
            r"\s+await connection\.execute\(\n"
            r"\s+sa\.delete\(scheduled_task\)"
        ),
    ),
    Claim(
        claim="the web portal's stop button ends a running turn",
        corpus="references/capabilities.md",
        phrase="The web portal's stop button and the terminal client's Esc key both end a running"
        " turn",
        source=WEB_SURFACE,
        pattern=r'STOP_TURN_HEADER = "x-ufo-stop-turn"',
    ),
    Claim(
        claim="the terminal client's Esc key ends a running turn",
        corpus="references/capabilities.md",
        phrase="The web portal's stop button and the terminal client's Esc key both end a running"
        " turn",
        source=TERMINAL_SURFACE,
        pattern=r"A stop \(`x-ufo-stop`, the member's Esc\) admits nothing",
    ),
    Claim(
        claim="a stopped turn does not resume, and a message already sent before the stop founds"
        " a new turn",
        corpus="references/capabilities.md",
        phrase="it does not resume. A message the member already sent before stopping starts a"
        " new turn instead",
        source=STOP,
        pattern=r"founded = await self\.admission\.redispatch\(workspace_id, conversation_id\)",
    ),
    Claim(
        claim="sign-in from the invitation reaches a first-run setup before the agent answers,"
        " not a chat directly",
        corpus="references/getting-started.md",
        phrase="a short setup asks what they want help with, the tools their team uses, and who"
        " else\nto invite",
        source=WEB_APP,
        pattern=r'if \(route\.kind === "first-run"\) \{\n(?:.*\n)*?\s+<FirstRun',
    ),
    Claim(
        claim="the first-run setup asks the goal, then the tools used, then who to invite,"
        " in that order",
        corpus="references/getting-started.md",
        phrase="a short setup asks what they want help with, the tools their team uses, and who"
        " else\nto invite",
        source=FIRST_RUN_VIEW,
        pattern=(
            r"\[GOAL_STEP\]: \{\n(?:.*\n)*?\s+\[TOOLS_STEP\]: \{\n(?:.*\n)*?\s+\[TEAM_STEP\]: \{"
        ),
    ),
    Claim(
        claim="the first-run setup can offer connecting iMessage, gated by the deploy and a flag"
        " that defaults on",
        corpus="references/getting-started.md",
        phrase="then, where the deploy offers it, connecting iMessage",
        source=WEB_SURFACE,
        pattern=r"and await flag_enabled\(IMESSAGE_STEP_FLAG, default=True\)",
    ),
    Claim(
        claim="the terminal's concluding choice has a third option that talks instead of"
        " returning a link",
        corpus="references/getting-started.md",
        phrase="the first two return a link,\n   the third talks through what the agent can do"
        " instead",
        source=GATEWAY,
        pattern=r'TOUR_CHOICE: &str = "Show me what you can do";',
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


def test_no_agent_tool_cancels_a_turn() -> None:
    """The corpus tells a customer a running turn is stopped through the surface — a button, an Esc
    key — never by asking the agent to do it. The only cancel a turn can reach through a tool is a
    parent cancelling its own subagent; a member-facing one would make that a lie."""
    cancels = {tool.name for tool in BUILTIN_TOOLS if "cancel" in tool.name}

    assert cancels == {"cancel_spawn"}, f"unexpected cancel tool(s): {cancels}"


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
    """AGENTS.md's user-facing copy rule bans the UFO metaphors from every word a member reads, and
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
