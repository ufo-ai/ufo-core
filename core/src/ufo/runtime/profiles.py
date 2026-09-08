"""The one core subagent profile — the default `spawn` dispatches when no extension names
a more specific one.

`general_purpose` is the catch-all: a focused child given a self-contained task, working in the same
workspace. Its tool subset is the working set minus the tools a
subagent must not hold — it never asks the user (`ask_user`), never delegates further
(`spawn`), never messages or cancels a sibling (`message_spawn`, `cancel_spawn`),
and never gates a member grant (`connect_account`). Its input is the default task contract; its
output is the concise profile result contract. Every
other profile is extension-provided through the manifest; this is the floor."""

from pathlib import Path

from ufo.runtime.subagents import SubagentProfile
from ufo.runtime.turns.contracts import ResultOutput, TaskInput

GENERAL_PURPOSE = "general_purpose"

GENERAL_PURPOSE_TOOLS = (
    "bash",
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "load_skill",
    "share_file",
    "search_web",
    "search_vertical",
    "fetch_url",
    "list_external_tools",
    "describe_external_tools",
    "call_external_tool",
    "xlsx_repl",
)


GENERAL_PURPOSE_PROMPT = (
    (Path(__file__).parent / "prompts" / "subagent_general_purpose.md").read_text().strip()
)


GENERAL_PURPOSE_PROFILE = SubagentProfile(
    name=GENERAL_PURPOSE,
    prompt=GENERAL_PURPOSE_PROMPT,
    tool_names=GENERAL_PURPOSE_TOOLS,
    input_model=TaskInput,
    output_model=ResultOutput,
    concise_parent_handoff=True,
)

CORE_SUBAGENT_PROFILES: tuple[SubagentProfile, ...] = (GENERAL_PURPOSE_PROFILE,)
