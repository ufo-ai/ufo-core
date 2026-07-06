"""The hosted assistant pack: the same assistant config backed by managed infrastructure.

Activating it (config `[pack] name = "assistant_hosted"`) brings up the assistant capabilities —
memory and recall, Exa research, Composio connectors and MCP, the browser/computer-use tools,
website building and the code REPL, document generation, todos, scheduled tasks, the web chat
surface, the OpenRouter model provider, and the coding subagent — but over managed backends instead
of core's own: the Turbopuffer index (in place of the local index), the Slack surface, the Redis
live-frame hub, and the E2B sandbox carrier. Memory still retrieves through OpenAI embeddings
(Turbopuffer is the index seam, embeddings are separate). It bundles only extensions and adds no
pack-level skills or onboarding of its own — each capability rides its own extension's manifest."""

from selfhost.sdk.manifest import Pack

NAME = "assistant_hosted"
VERSION = "0.1.0"
EXTENSIONS = (
    "turbopuffer",
    "exa",
    "todos",
    "web",
    "slack",
    "sites",
    "scheduled_tasks",
    "research",
    "repl",
    "redis_hub",
    "openrouter",
    "memory",
    "mcp",
    "e2b",
    "documents",
    "connectors",
    "coding",
    "browser",
    "embed-openai",
)


def pack() -> Pack:
    return Pack(name=NAME, version=VERSION, extensions=EXTENSIONS)
