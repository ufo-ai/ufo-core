"""The one core subagent profile — the default `spawn_subagent` dispatches when no extension names
a more specific one.

`general_purpose` is the catch-all: a focused child given a self-contained task, working in the same
workspace. Its tool subset is the working set minus the tools a
subagent must not hold — it never asks the user (`ask_user`), never delegates further
(`spawn_subagent`), never messages or cancels a sibling (`message_subagent`, `cancel_subagent`),
and never gates a member grant (`connect_account`). Every
other profile is extension-provided through the manifest; this is the floor."""

from pydantic import BaseModel, Field

from ufo.loop.prompts.render import SKILL_INDEX_SLOT
from ufo.loop.subagents import SubagentProfile

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
    # Cross-extension tools matching the source general_purpose's set: web search/fetch, the
    # connector trio, and the spreadsheet REPL. Names resolve only if the owning extension is
    # installed (the strict allow-list filters against the live tool set), so an absent extension
    # leaves the tool silently unavailable rather than erroring.
    "search_web",
    "search_vertical",
    "fetch_url",
    "list_external_tools",
    "describe_external_tools",
    "call_external_tool",
    "xlsx_repl",
)


class GeneralPurposeInput(BaseModel):
    task: str = Field(
        description="Freeform task governed by the shared delivery register.",
    )


class GeneralPurposeOutput(BaseModel):
    result: str = Field(
        description="Freeform result governed by the shared delivery register.",
    )


_PARAGRAPHS = (
    "You are a focused subagent working on a specific task delegated by a parent agent.",
    (
        "Solve as much as you can on your own. Use your tools to answer your own questions and "
        "make progress. Never ask clarifying questions — make reasonable assumptions and proceed."
    ),
    (
        "If an approach is blocked, do not brute-force it or retry the same failing action in a "
        "loop. Try another approach, or end your turn with what you have so the parent agent can "
        "decide the next step."
    ),
    (
        "Start by loading any skills relevant to the task from <available_skills> with load_skill "
        "— they carry workflows that make you far more effective. The index below is complete "
        "for this turn."
    ),
    (
        "A formal document deliverable must use its Office format — .docx, .pptx, or .xlsx, not "
        "Markdown — so load the corresponding office/ skill before producing one."
    ),
    SKILL_INDEX_SLOT,
    (
        "You share the /workspace directory with the parent agent and any sibling subagents. Save "
        "findings, data, and artifacts to files there with clear, unique names so they can be read "
        "back."
    ),
)

GENERAL_PURPOSE_PROMPT = "\n\n".join(_PARAGRAPHS)


GENERAL_PURPOSE_PROFILE = SubagentProfile(
    name=GENERAL_PURPOSE,
    prompt=GENERAL_PURPOSE_PROMPT,
    tool_names=GENERAL_PURPOSE_TOOLS,
    input_model=GeneralPurposeInput,
    output_model=GeneralPurposeOutput,
)

CORE_SUBAGENT_PROFILES: tuple[SubagentProfile, ...] = (GENERAL_PURPOSE_PROFILE,)
