"""The radar app: a workspace agent whose homepage shows the digest of recent scheduled runs. It
ships as an `agents` provision on the shared apps infrastructure; its homepage is the static page
this extension ships, edited and redeployed from the chat column beside it."""

from pathlib import Path

from ufo.sdk.manifest import AgentProvision, AgentSpec, Manifest, SkillSpec

NAME = "app_radar"
VERSION = "0.1.0"
SKILLS_ROOT = Path(__file__).parent / "skills"
HOME_SKILL = "app-radar-home"
RADAR_APP_AGENT_NAME = "radar"
RADAR_APP_PROMPT = (
    "You are the Radar app for this workspace. Your homepage is the radar feed: each scheduled "
    "run as an entry on a rail — its digest title, summary, and points, the task and moment it "
    "fired, and a cover picture — opening into the full story with its files, its report, its "
    "conversation, and the reports to read next. The header carries the Rebuild entries control. "
    "When a member asks you to change the page, load the skill `app-radar-home` and follow it — "
    "keep the feed, the story view, and the rebuild control working."
)

RADAR_APP_AGENT = AgentProvision(
    name=RADAR_APP_AGENT_NAME,
    spec=AgentSpec(
        prompt=RADAR_APP_PROMPT,
        model="auto",
        reasoning="medium",
        internet_access_allowed=False,
        visibility="workspace",
    ),
    icon="radar",
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        agents=(RADAR_APP_AGENT,),
        skills=(SkillSpec(path=SKILLS_ROOT / HOME_SKILL),),
    )
