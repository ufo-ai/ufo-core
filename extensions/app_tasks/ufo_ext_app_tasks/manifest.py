"""The tasks app: a workspace agent whose homepage lists the workspace's scheduled tasks and source
triggers and can pause or resume a task. It ships as an `agents` provision on the shared apps
infrastructure; its homepage is a static page it deploys and edits from the chat column beside it.
The page reads and writes through the `app-bridge` client."""

from pathlib import Path

from ufo.sdk.manifest import AgentProvision, AgentSpec, Manifest, SkillSpec

NAME = "app_tasks"
VERSION = "0.1.0"
SKILLS_ROOT = Path(__file__).parent / "skills"
HOME_SKILL = "app-tasks-home"
TASKS_APP_AGENT_NAME = "tasks"
TASKS_APP_PROMPT = (
    "You are the Tasks app for this workspace. Your homepage is the tasks screen: two listings — "
    "Scheduled and Triggers — with a filter, sortable columns, and a record panel that opens a row "
    "for editing (pause or resume rides the edit form's Paused field) or deleting. On your first "
    "turn, load the skill `app-tasks-home` and follow it to deploy its page as your homepage. When "
    "a member asks you to change the page, edit the deployed page and redeploy it — keep the two "
    "listings, the record panel, and its edit and delete acts."
)

TASKS_APP_AGENT = AgentProvision(
    name=TASKS_APP_AGENT_NAME,
    spec=AgentSpec(
        prompt=TASKS_APP_PROMPT,
        model="auto",
        reasoning="medium",
        internet_access_allowed=False,
        visibility="workspace",
    ),
    icon="clock-play",
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        agents=(TASKS_APP_AGENT,),
        skills=(SkillSpec(path=SKILLS_ROOT / HOME_SKILL),),
    )
