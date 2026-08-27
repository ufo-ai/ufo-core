"""The chat screen on the workspace's main agent."""

from pathlib import Path

from ufo.sdk.manifest import AgentProvision, AgentSpec, Manifest, SkillSpec

NAME = "app_chat"
VERSION = "0.2.0"
SKILLS_ROOT = Path(__file__).parent / "skills"
HOME_SKILL = "app-chat-home"
CHAT_APP_AGENT_NAME = "chat"
CHAT_APP_PROMPT = (
    "You are the Chat app for this workspace. Your homepage is the chat screen: the conversation "
    "the page was opened at, with its transcript, composer, and live streamed replies, or the "
    "start screen with the workspace's starter prompts when none is open. When a member asks you "
    "to change the page, load the skill `app-chat-home` and follow it — keep sending, streaming, "
    "and the starters working."
)

CHAT_APP_PURPOSE = "Holds every conversation in this workspace, and opens a new one."
CHAT_APP_AGENT = AgentProvision(
    name=CHAT_APP_AGENT_NAME,
    spec=AgentSpec(
        prompt=CHAT_APP_PROMPT,
        purpose=CHAT_APP_PURPOSE,
        model="auto",
        reasoning="medium",
        internet_access_allowed=True,
        visibility="workspace",
    ),
    icon="message-circle",
    main=True,
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        agents=(CHAT_APP_AGENT,),
        skills=(SkillSpec(path=SKILLS_ROOT / HOME_SKILL),),
    )
