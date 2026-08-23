"""The artifacts app: a workspace agent whose homepage lists the workspace's shared files and hosted
sites. It ships as an `agents` provision on the shared apps infrastructure; its homepage is a static
page it deploys and edits from the chat column beside it. The page reads through the `app-bridge`
client."""

from pathlib import Path

from ufo.sdk.manifest import AgentProvision, AgentSpec, Manifest, SkillSpec

NAME = "app_artifacts"
VERSION = "0.1.0"
SKILLS_ROOT = Path(__file__).parent / "skills"
HOME_SKILL = "app-artifacts-home"
ARTIFACTS_APP_AGENT_NAME = "artifacts"
ARTIFACTS_APP_PROMPT = (
    "You are the Artifacts app for this workspace. Your homepage is the artifacts shelf: hosted "
    "sites and shared files in one grid newest first, read as tiles or a table, narrowed by "
    "search, scope, and a Sites/Images/Documents/Other filter, paged Newer/Older, with a viewer "
    "for an opened file (image inline, download as its own act, the way out to its conversation) "
    "and Open links on site cards. On your first turn, load the skill `app-artifacts-home` and "
    "follow it to deploy its page as your homepage. When a member asks you to change the page, "
    "edit the deployed page and redeploy it — keep the shelf's controls and viewer working."
)

ARTIFACTS_APP_AGENT = AgentProvision(
    name=ARTIFACTS_APP_AGENT_NAME,
    spec=AgentSpec(
        prompt=ARTIFACTS_APP_PROMPT,
        model="auto",
        reasoning="medium",
        internet_access_allowed=False,
        visibility="workspace",
    ),
    icon="books",
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        agents=(ARTIFACTS_APP_AGENT,),
        skills=(SkillSpec(path=SKILLS_ROOT / HOME_SKILL),),
    )
