"""The hosted assistant pack: the same assistant config backed by managed infrastructure.

Activating it (config `[pack] name = "assistant_hosted"`) brings up the assistant capabilities —
memory and recall, Perplexity research, brokered connectors (Composio's open namespace plus the
Pipedream allowlist), keyed connectors (a workspace API key injected at the egress proxy) and MCP,
the browser/computer-use tools,
website building and the code REPL, document generation, todos, durable objectives, scheduled
tasks, member-authored skills, markdown content sources over a GitHub repository or a serve-local
directory, the ufo terminal
surface, the member web portal, and the operator session debugger, the Bedrock and OpenRouter
model providers, Metronome plan provisioning, usage and seat metering, and the coding
subagent — but over managed backends instead
of core's own: the Turbopuffer index (in place of the local index), Slack and iMessage surfaces,
the Redis live-frame hub, the E2B sandbox carrier, and a Browserbase-hosted Chrome per browser run
(the browserbase cdp provider, in place of Chrome inside the conversation's own sandbox). Memory
still retrieves through OpenAI embeddings
(Turbopuffer is the index seam, embeddings are separate). A workspace admin connects Slack in chat
— the slack extension's setup tools drive it. Beyond its extensions it carries two pack-level
skills. `customer-onboarding-help` is a curated, read-only corpus of this deploy's own onboarding
facts (signup and invitations, the Slack install, billing and seats, what is not available yet, and
what must never be disclosed) so a hosted workspace can be answered from shipped content rather than
from memory a customer's workspace does not have. `first-run` drives the opening conversation of a
new workspace — what the company uses, the Slack install, and one piece of real work — so the member
watches the product work instead of reading about it."""

from pathlib import Path

import ufo_pack_assistant

from ufo.sdk.manifest import Pack, SkillSpec

NAME = "assistant_hosted"
VERSION = "0.1.0"
EXTENSIONS = (
    "turbopuffer",
    "perplexity",
    "todos",
    "objectives",
    "ufo",
    "web",
    "debugger",
    "slack",
    "imessage",
    "sites",
    "scheduled_tasks",
    "sweep",
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
    "keyed_connectors",
    "pipedream",
    "sources",
    "gbrain",
    "coding",
    "browser",
    "browserbase",
    "skill_create",
    "embed_openai",
    "metronome",
)
SKILLS_DIR = Path(__file__).parent / "skills"
SKILL_NAMES = ("customer-onboarding-help",)


def pack() -> Pack:
    return Pack(
        name=NAME,
        version=VERSION,
        extensions=EXTENSIONS,
        skills=(
            *(SkillSpec(path=SKILLS_DIR / name) for name in SKILL_NAMES),
            *(
                SkillSpec(path=ufo_pack_assistant.SKILLS_DIR / name)
                for name in ufo_pack_assistant.SKILL_NAMES
            ),
        ),
    )
