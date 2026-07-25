"""The hosted assistant pack: the same assistant config backed by managed infrastructure.

Activating it (config `[pack] name = "assistant_hosted"`) brings up the assistant capabilities —
memory and recall, Exa research, brokered connectors (Composio's open namespace plus the Pipedream
allowlist) and MCP,
the browser/computer-use tools,
website building and the code REPL, document generation, todos, scheduled tasks, member-authored
skills, the ufo terminal
surface and the operator session debugger, the Bedrock and OpenRouter model providers, Metronome
usage and seat metering, and the coding
subagent — but over managed backends instead
of core's own: the Turbopuffer index (in place of the local index), the Slack surface, the Redis
live-frame hub, the E2B sandbox carrier, and Chrome driven inside each conversation's sandbox (the
sandbox_chrome cdp provider, in place of a static `BROWSER_CDP_URL`). Memory still retrieves through
OpenAI embeddings
(Turbopuffer is the index seam, embeddings are separate). A workspace owner connects Slack in chat
— the slack extension's setup tools drive it. It bundles only extensions and adds no
pack-level skills or onboarding of its own — each capability rides its own extension's manifest."""

from ufo.sdk.manifest import Pack

NAME = "assistant_hosted"
VERSION = "0.1.0"
EXTENSIONS = (
    "turbopuffer",
    "exa",
    "todos",
    "ufo",
    "debugger",
    "slack",
    "sites",
    "scheduled_tasks",
    "research",
    "repl",
    "redis_hub",
    "bedrock",
    "openrouter",
    "memory",
    "mcp",
    "e2b",
    "documents",
    "connectors",
    "composio",
    "pipedream",
    "sources",
    "coding",
    "browser",
    "sandbox_chrome",
    "skill_create",
    "embed_openai",
    "knowledge_graph",
    "metronome",
)


def pack() -> Pack:
    return Pack(name=NAME, version=VERSION, extensions=EXTENSIONS)
