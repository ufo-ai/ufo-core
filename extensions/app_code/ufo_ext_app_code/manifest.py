"""The Code app: one workspace agent over one set of pull requests.

It is named for what it works over, like every other default app. It arrives reviewing, and holds
one more feature nobody has asked for yet: babysitting the pull requests it reviews until they
merge or close. The review procedure it runs is `prompts/agent_code.md`; the babysitting procedure
is a skill, for the reason `BABYSIT_SKILL` gives.

The agent itself is not new. The `coding` extension shipped it as `code-review`, and this extension
adopts that row rather than shipping a second one — the member's grants, its conversations and its
own edits stand, and a migration moves the extension half of the provision identity here.

**A second feature reaches an adopted row only as a skill.** Provisioning writes a prompt when it
creates a row and never again, because a prompt is a member's to edit — so a procedure added to
the prompt would reach new workspaces alone, and every workspace that already holds the reviewer
would draw a control with no instructions behind it. A skill is a file this extension ships, so it
arrives on the next deploy wherever the app is. What the prompt carries is the one line that sends
the agent there, and that line is the only thing a member has to add by hand.
"""

from pathlib import Path

from ufo_ext_coding.manifest import GITHUB_CONNECTOR

from ufo.sdk.manifest import (
    AgentProvision,
    AgentSetup,
    AgentSpec,
    Manifest,
    SkillSpec,
)

NAME = "app_code"
VERSION = "0.1.0"
SKILLS_ROOT = Path(__file__).parent / "skills"
HOME_SKILL = "app-code-home"
BABYSIT_TASK = "pr-babysit"
BABYSIT_SKILL = "app-code-babysit"
"""Where the babysitting procedure lives.

It is a skill and not prompt text, because a prompt is written into an agent row once and never
again. A workspace that already holds this app would never meet a procedure added to the prompt
later, and would draw a feature with no instructions behind it. A skill is a file this extension
ships, so it reaches every workspace on the next deploy and stays one document.

Babysitting is armed by a `scheduled_task`, not by the source trigger the review runs on. The
`pull_requests` stream syncs `/repos/{owner}/{repo}/pulls`, so a finished check run and a commit
status move no record and wake nothing — and a failing check is the first thing babysitting exists
to clear. A clock is the only thing that sees it."""
CODE_APP_AGENT_NAME = "code"
"""What this app's agent is called: the work it operates over, which is what every default app is
named for.

It is not `coding`, and that is not cosmetic: the `coding` extension registers a subagent profile
under that name, and a spawn target that names both a profile and a workspace agent is refused as
ambiguous before the permission check. Every reviewer this app spawns would stop at that call.

The release that shipped the reviewer called it `code-review`, so the adopting migration moves the
declared name with the rest of the identity — and the row's own name with it, where the workspace
never renamed it and holds nothing else by this one."""

TRIGGER_KIND = "source_trigger"
CODE_APP_DELIVERY = "current"
CODE_APP_STREAMS = ("pull_requests",)
"""Without them the trigger also takes the connection's workflow runs, comments and issues, and
wakes the reviewer for changes outside the pull requests it reviews."""

CODE_APP_PURPOSE = (
    "Reviews each pull request as it changes, and says what would break and what is missing."
)

CODE_APP_PROMPT = (Path(__file__).parent / "prompts" / "agent_code.md").read_text()

CODE_APP_SETUP_INSTRUCTIONS = (
    "Connect the GitHub account this workspace reviews under. A connection binds to the agent "
    "whose conversation it is made in, so make it here, with you; the same account fetches the "
    "commits the reviewers read. Then ask the member to register the pull-request source for that "
    "account and share it — only a shared source carries a trigger — and apply a source trigger "
    f"naming it with `delivery: {CODE_APP_DELIVERY}` and `streams: {list(CODE_APP_STREAMS)}`, so "
    "changed pull requests wake this conversation and workflow runs or comments on the same "
    "connection do not."
)

CODE_APP_AGENT = AgentProvision(
    name=CODE_APP_AGENT_NAME,
    spec=AgentSpec(
        prompt=CODE_APP_PROMPT,
        purpose=CODE_APP_PURPOSE,
        model="z-ai/glm-5.3",
        reasoning="high",
        internet_access_allowed=False,
        sandbox_size="large",
        visibility="workspace",
    ),
    icon="git-pull-request",
    setup=AgentSetup(
        connectors=(GITHUB_CONNECTOR,),
        standing=(TRIGGER_KIND,),
        instructions=CODE_APP_SETUP_INSTRUCTIONS,
    ),
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        agents=(CODE_APP_AGENT,),
        skills=(
            SkillSpec(path=SKILLS_ROOT / HOME_SKILL),
            SkillSpec(path=SKILLS_ROOT / BABYSIT_SKILL),
        ),
    )
