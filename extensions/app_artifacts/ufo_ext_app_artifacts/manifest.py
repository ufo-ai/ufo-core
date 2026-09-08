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
    (Path(__file__).parent / "prompts" / "agent_artifacts.md")
    .read_text()
    .strip()
    .replace("{{home_skill}}", HOME_SKILL)
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
