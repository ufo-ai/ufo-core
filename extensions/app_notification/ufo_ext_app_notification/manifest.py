"""What the Notification app declares: the `notify` tool every agent holds, the `notification`
object kind, the `notification` agent whose inbox the rows are, the per-minute drain that folds
each member's open rows into one turn on that agent's lane, and the `deliver` action that agent
alone holds. Work the batch names goes to the agent whose job it is through `spawn`, which core
already makes idempotent per parent turn and key, so this app declares no verb of its own for it.

The app ships as an `agents` provision on the shared apps infrastructure, the way Radar does. Its
allowlist is the fence the whole design rests on: it omits `notify`, so the app can never raise a
notification, and it is the one place the member-reaching `deliver` is named — an allowlist is
declared, never typed, so no member and no agent acting for one can write one. What else it names is
what the app's own work needs: the object verbs over its kind, and the file tools and two actions
its homepage skill walks through. `member_context_read` is what lets every writer and reader find
the app's agent by the provision that shipped it rather than by a name a member may already hold,
and lets `deliver` read the member's conversations it delivers through."""

from pathlib import Path

from ufo.sdk.context import ExtensionContext
from ufo.sdk.jobs import JobSpec
from ufo.sdk.manifest import AgentProvision, AgentSpec, Manifest, SkillSpec
from ufo_ext_app_notification.deliver import DELIVER, DELIVER_ACTION_ID
from ufo_ext_app_notification.drain import InboxDrain
from ufo_ext_app_notification.kind import NOTIFICATION_OBJECT
from ufo_ext_app_notification.notify_tool import NOTIFICATION_AGENT_NAME, NOTIFY_TOOL
from ufo_ext_app_notification.store import EXTENSION_NAME, untriaged_workspaces

NAME = EXTENSION_NAME
VERSION = "0.6.0"
SKILLS_ROOT = Path(__file__).parent / "skills"
HOME_SKILL = "app-notification-home"
DRAIN_JOB = "notification_drain"
DRAIN_SCHEDULE = "0 * * * * *"
NOTIFICATION_AGENT_PROMPT = (
    (Path(__file__).parent / "prompts" / "agent_notification.md")
    .read_text()
    .strip()
    .replace("{{home_skill}}", HOME_SKILL)
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
        "spawn",
        DELIVER_ACTION_ID,
    ),
    icon="bell",
)


async def _drain(ctx: ExtensionContext) -> None:
    await InboxDrain(ctx=ctx).run()


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(NOTIFY_TOOL, DELIVER),
        objects=(NOTIFICATION_OBJECT,),
        member_context_read=True,
        jobs=(
            JobSpec(
                name=DRAIN_JOB,
                schedule=DRAIN_SCHEDULE,
                handler=_drain,
                candidates=untriaged_workspaces(),
            ),
        ),
        agents=(NOTIFICATION_AGENT,),
        skills=(SkillSpec(path=SKILLS_ROOT / HOME_SKILL),),
    )
