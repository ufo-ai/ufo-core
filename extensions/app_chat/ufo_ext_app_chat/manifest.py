"""The chat app: a workspace agent whose homepage lists the workspace's conversations so a member
opens one from the page. It ships as an ordinary `agents` provision, so it becomes an app on the
same path a user-defined app takes — the Applications flyout, a seeded homepage, and the chat column
beside it. It also ships the shared `app-bridge` skill every app homepage includes to reach portal
data and navigation."""

from pathlib import Path

from ufo.sdk.manifest import AgentProvision, AgentSpec, Manifest, SkillSpec

NAME = "app_chat"
VERSION = "0.1.0"
SKILLS_ROOT = Path(__file__).parent / "skills"
SKILL_NAMES = ("app-bridge", "app-chat-home")
CHAT_APP_AGENT_NAME = "chat"
CHAT_APP_PROMPT = (
    "You are the Chat app for this workspace. Your homepage is the chat screen: the conversation "
    "the page was opened at, with its transcript, composer, and live streamed replies, or the "
    "start screen with the workspace's starter prompts when none is open. On your first turn, "
    "load the skill `app-chat-home` and follow it to deploy its page as your homepage. When a "
    "member asks you to change the page, edit the deployed page and redeploy it — keep sending, "
    "streaming, and the starters working."
)

CHAT_APP_AGENT = AgentProvision(
    name=CHAT_APP_AGENT_NAME,
    spec=AgentSpec(
        prompt=CHAT_APP_PROMPT,
        model="auto",
        reasoning="medium",
        internet_access_allowed=False,
        visibility="workspace",
    ),
    icon="message-circle",
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        agents=(CHAT_APP_AGENT,),
        skills=tuple(SkillSpec(path=SKILLS_ROOT / name) for name in SKILL_NAMES),
    )
