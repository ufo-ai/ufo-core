"""The Code app: one workspace agent over one set of pull requests.

It is named for what it works over, like every other default app. It arrives reviewing, and
reviewing is the whole of what it does: the procedure it runs is `prompts/agent_code.md`,
unchanged from the release that shipped it.

The agent itself is not new. The `coding` extension shipped it as `code-review`, and this extension
adopts that row rather than shipping a second one — the member's grants, its conversations and its
own edits stand, and a migration moves the extension half of the provision identity here.

**This app ships one feature, and that is a consequence of adopting a live row.** Provisioning
writes a prompt when it creates a row and never again, because a prompt is a member's to edit — so
an adopted agent keeps the instructions it already has. A second feature would be a band on the
page, a control under it, and no instructions behind it in any workspace that already holds the
reviewer, which is every workspace that has one. A feature this app can only offer to workspaces
that do not exist yet is a dead control, and a dead control is worse than an absent one.
"""

from pathlib import Path

from ufo_ext_coding.manifest import GITHUB_CONNECTOR, UFO_GITHUB_APP

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

CODE_APP_PURPOSE = (
    "Reviews each pull request as it changes, and says what would break and what is missing."
)

CODE_APP_PROMPT = (Path(__file__).parent / "prompts" / "agent_code.md").read_text()

CODE_APP_SETUP_INSTRUCTIONS = (
    "Connect the GitHub account this workspace reviews under. A connection binds to the agent "
    "whose conversation it is made in, so make it here, with you. Then ask the member to register "
    "the pull-request source for that account and share it — only a shared source carries a "
    "trigger — and apply a source trigger naming it, so a changed pull request wakes this "
    "conversation. The reviewers also need the ufo GitHub App installed to fetch commits: "
    "`connect_github` hands an admin that link once for the whole workspace."
)

CODE_APP_AGENT = AgentProvision(
    name=CODE_APP_AGENT_NAME,
    spec=AgentSpec(
        prompt=CODE_APP_PROMPT,
        purpose=CODE_APP_PURPOSE,
        model="auto",
        reasoning="high",
        internet_access_allowed=False,
        sandbox_size="large",
        visibility="workspace",
    ),
    icon="git-pull-request",
    setup=AgentSetup(
        connectors=(GITHUB_CONNECTOR,),
        credentials=(UFO_GITHUB_APP,),
        # A feed wakes this app, so it declares the standing order a feed arms and offers no
        # cadence: a pull request changes when it changes.
        standing=(TRIGGER_KIND,),
        instructions=CODE_APP_SETUP_INSTRUCTIONS,
    ),
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        agents=(CODE_APP_AGENT,),
        skills=(SkillSpec(path=SKILLS_ROOT / HOME_SKILL),),
    )
