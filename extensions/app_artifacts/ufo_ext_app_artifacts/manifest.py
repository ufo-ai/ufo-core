"""The artifacts app: a workspace agent whose homepage lists the workspace's shared files and hosted
sites. It ships as an `agents` provision on the shared apps infrastructure; its homepage is the
static page this extension ships, edited and redeployed from the chat column beside it."""

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
    "and Open links on site cards. When a member asks you to change the page, load the skill "
    "`app-artifacts-home` and follow it — keep the shelf's controls and viewer working."
)

ARTIFACTS_APP_PURPOSE = (
    "Shelves every file and site this workspace has made, newest first, with search and a viewer."
)
ARTIFACTS_APP_AGENT = AgentProvision(
    name=ARTIFACTS_APP_AGENT_NAME,
    spec=AgentSpec(
        prompt=ARTIFACTS_APP_PROMPT,
        purpose=ARTIFACTS_APP_PURPOSE,
        model="auto",
        reasoning="medium",
        internet_access_allowed=False,
        visibility="workspace",
    ),
    icon="stack-2",
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        agents=(ARTIFACTS_APP_AGENT,),
        skills=(SkillSpec(path=SKILLS_ROOT / HOME_SKILL),),
    )
