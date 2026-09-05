"""What the Notification app declares: the `notify` tool every agent holds, the `notification`
object kind, and the `notification` agent whose inbox the rows are.

The app ships as an `agents` provision on the shared apps infrastructure, the way Radar does. Its
allowlist is the fence the whole design rests on: it omits `notify`, so the app can never raise a
notification, and it is the one place a member-reaching verb may later be named — an allowlist is
declared, never typed, so no member and no agent acting for one can write one. What it does name is
what the app's own work needs: the object verbs over its kind, and the file tools and two actions
its homepage skill walks through. `member_context_read` is what lets `notify` find the app's agent
by the provision that shipped it rather than by a name a member may already hold."""

from pathlib import Path

from ufo.sdk.manifest import AgentProvision, AgentSpec, Manifest, SkillSpec
from ufo_ext_app_notification.kind import NOTIFICATION_OBJECT
from ufo_ext_app_notification.notify_tool import NOTIFICATION_AGENT_NAME, NOTIFY_TOOL
from ufo_ext_app_notification.store import EXTENSION_NAME

NAME = EXTENSION_NAME
VERSION = "0.1.0"
SKILLS_ROOT = Path(__file__).parent / "skills"
HOME_SKILL = "app-notification-home"
NOTIFICATION_AGENT_PROMPT = (
    "You are the Notification app for this workspace. Every agent's turns put messages for a "
    "member in your inbox with the `notify` tool, and those messages read back as the "
    "`notification` object kind. When a member asks what has been raised for them, list the kind "
    "and answer from it; when they say one is handled or unwanted, delete it. Your homepage lists "
    "the same kind; when a member asks you to change the page, load the skill "
    f"`{HOME_SKILL}` and follow it."
)
NOTIFICATION_AGENT_PURPOSE = (
    "Decides which of the things your agents noticed are worth interrupting you for."
)
NOTIFICATION_AGENT = AgentProvision(
    name=NOTIFICATION_AGENT_NAME,
    spec=AgentSpec(
        prompt=NOTIFICATION_AGENT_PROMPT,
        purpose=NOTIFICATION_AGENT_PURPOSE,
        model="auto",
        reasoning="medium",
        internet_access_allowed=False,
        visibility="workspace",
    ),
    tools=(
        "object_list",
        "object_get",
        "object_explain",
        "object_delete",
        "load_skill",
        "bash",
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "action:site:deploy_website",
        "action:agent:set_homepage",
    ),
    icon="bell",
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(NOTIFY_TOOL,),
        objects=(NOTIFICATION_OBJECT,),
        member_context_read=True,
        agents=(NOTIFICATION_AGENT,),
        skills=(SkillSpec(path=SKILLS_ROOT / HOME_SKILL),),
    )
