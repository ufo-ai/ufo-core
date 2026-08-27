"""The wiki app: a private agent whose homepage reads the workspace's shared memory as a document
and the member roster. It ships as an `agents` provision on the shared apps infrastructure; its
homepage is the static page this extension ships, edited and redeployed from the chat column beside
it.

Every workspace is provisioned the agent, and the provision is private: it reaches the workspace's
admins and the members granted web access in its own conversation, and nobody else — a
workspace-visible row stands in every member's portal whatever the lists withhold, since the boot
payload carries it and its address opens the page. Whether a member is shown it is then the
portal's read of `enable-wiki-app` — the one flag there that reads closed, because this app has
never been offered and a deploy that cannot answer must not be the one that offers it."""

from pathlib import Path

from ufo.sdk.manifest import AgentProvision, AgentSpec, Manifest, SkillSpec

NAME = "app_wiki"
VERSION = "0.1.0"
SKILLS_ROOT = Path(__file__).parent / "skills"
HOME_SKILL = "app-wiki-home"
WIKI_APP_AGENT_NAME = "wiki"
WIKI_APP_PROMPT = (
    "You are the Wiki app for this workspace. Your homepage is a document: an overview written "
    "from the workspace's consolidated memory, the People roster as the way into one member's own "
    "page, the shared memory set out under topic sections (how the team works, decisions, open "
    "work, history, facts), a details group, and a page-actions menu whose Rebuild page facts act "
    "queues the derivation job through the main agent. When a member asks you to change the "
    "page, load the skill `app-wiki-home` and follow it — keep the document shape and the rebuild "
    "act."
)

WIKI_APP_PURPOSE = (
    "Writes what this workspace knows — how the team works, what it decided, who is here — "
    "into one page you can read."
)
WIKI_APP_AGENT = AgentProvision(
    name=WIKI_APP_AGENT_NAME,
    spec=AgentSpec(
        prompt=WIKI_APP_PROMPT,
        purpose=WIKI_APP_PURPOSE,
        model="auto",
        reasoning="medium",
        internet_access_allowed=False,
        visibility="private",
    ),
    icon="book",
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        agents=(WIKI_APP_AGENT,),
        skills=(SkillSpec(path=SKILLS_ROOT / HOME_SKILL),),
    )
