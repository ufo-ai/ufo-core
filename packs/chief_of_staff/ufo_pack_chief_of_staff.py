"""The chief-of-staff pack: a manager's operating manual running on this deploy.

One member's working world — Google Meet transcripts and Gemini smart notes, the Slack channels
that matter, a markdown state repo of people files, the org chart, and daily logs — syncs into
memory and the knowledge graph, and the member drives everything from one Slack front door. A
scheduled `sync` run reviews what accumulated and proposes a routed fan-out (observations, 1:1
agenda items, todos, decisions, kudos, follow-through watches) the member approves in chat before
anything writes; `prep` assembles a 1:1 brief from the same state; `triage` is the written-down
judgment both consult, iterated as the member corrects it. Everything rides bundled extensions —
brokered connector grants (Composio) plus feed sync, the Slack surface, chat-native scheduling,
page watches for loops that close weeks later, the conversation todo board, workspace-authored
skills, and the governed self-improvement loop. Four pack-level skills carry the workflows; setup
is a conversation (`chief-of-staff-setup`), never a coded step, so every granting act stays with
the speaker."""

from pathlib import Path

from ufo.sdk.manifest import Pack, SkillSpec

NAME = "chief_of_staff"
VERSION = "0.1.0"
EXTENSIONS = (
    "connectors",
    "composio",
    "sources",
    "memory",
    "index_default",
    "embed_openai",
    "knowledge_graph",
    "slack",
    "ufo",
    "scheduled_tasks",
    "page_alerts",
    "todos",
    "skill_create",
    "self_improvement",
)
SKILLS_DIR = Path(__file__).parent / "skills"
SKILL_NAMES = ("chief-of-staff-setup", "sync", "prep", "triage")


def pack() -> Pack:
    return Pack(
        name=NAME,
        version=VERSION,
        extensions=EXTENSIONS,
        skills=tuple(SkillSpec(path=SKILLS_DIR / name) for name in SKILL_NAMES),
    )
