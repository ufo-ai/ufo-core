"""The assistant pack: a full self-contained assistant config activated as one named pack.

Activating it (config `[pack] name = "assistant"`) narrows the deploy to exactly the extensions it
names — durable memory and recall over the base-pinned local index and OpenAI embeddings, web
research (the research tools over the Perplexity search backend), brokered connectors
(Composio's open namespace plus the Pipedream allowlist), keyed connectors (a workspace API key
injected at the egress proxy) and MCP,
the sandbox browser/computer-use tools with Chrome driven inside each conversation's sandbox (the
sandbox-chrome cdp provider), website building and the code REPL, document generation,
todos, durable objectives, scheduled tasks, member-authored skills, the member apps (chat,
radar, tasks, wiki, artifacts, meetings, issues, metrics — each a shipped agent with an editable
homepage), the member web portal, the
operator session debugger (and, riding the memory extension, the memory explorer), an extra
OpenRouter model provider, and the coding subagent. It runs on core's own local carrier and index
with no managed infrastructure
— that is what distinguishes it from `assistant_hosted`. It bundles only extensions and adds no
pack-level skills or onboarding of its own: each capability's tools, skills, and onboarding ride
that extension's own manifest, so the pack is nothing but the set that comes up together."""

from pathlib import Path

from ufo.sdk.manifest import Pack, SkillSpec

NAME = "assistant"
VERSION = "0.1.0"
EXTENSIONS = (
    "app_artifacts",
    "app_chat",
    "app_code",
    "app_issues",
    "app_meetings",
    "app_metrics",
    "app_radar",
    "app_tasks",
    "app_wiki",
    "perplexity",
    "todos",
    "objectives",
    "sites",
    "scheduled_tasks",
    "report_digest",
    "monitors",
    "research",
    "repl",
    "memory",
    "mcp",
    "documents",
    "connectors",
    "composio",
    "keyed_connectors",
    "pipedream",
    "sources",
    "coding",
    "browser",
    "sandbox_chrome",
    "skill_create",
    "index_default",
    "embed_openai",
    "openrouter",
    "ufo",
    "web",
    "debugger",
)


SKILLS_DIR = Path(__file__).parent / "skills"
SKILL_NAMES = ("first-run",)


def pack() -> Pack:
    return Pack(
        name=NAME,
        version=VERSION,
        extensions=EXTENSIONS,
        skills=tuple(SkillSpec(path=SKILLS_DIR / name) for name in SKILL_NAMES),
    )
