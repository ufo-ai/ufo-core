"""The one core subagent profile — the default `spawn_subagent` dispatches when no extension names
a more specific one.

`general_purpose` is the catch-all: a focused child given a self-contained task, working in the same
workspace, that reports back a single summary. Its tool subset is the working set minus the tools a
subagent must not hold — it never asks the user (`ask_user`), never delegates further
(`spawn_subagent`), never waits on or cancels a sibling (`wait_for_subagents`, `cancel_subagent`),
and never gates a member grant (`connect_account`). Every other profile is extension-provided
through the manifest; this is the floor."""

from pydantic import BaseModel

from selfhost.loop.subagents import SubagentProfile
from selfhost.skills.runtime import CORE_SKILLS

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
)


class GeneralPurposeInput(BaseModel):
    task: str


class GeneralPurposeOutput(BaseModel):
    result: str


_AVAILABLE_SKILLS = "\n".join(f"- {skill.name}: {skill.description}" for skill in CORE_SKILLS)

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
        "Start by loading any skills relevant to the task with load_skill — they carry workflows "
        "that make you far more effective."
    ),
    f"<available_skills>\n{_AVAILABLE_SKILLS}\n</available_skills>",
    (
        "You share the /workspace directory with the parent agent and any sibling subagents. Save "
        "findings, data, and artifacts to files there with clear, unique names so they can be read "
        "back; never delete another agent's files. Passing work through workspace files is the "
        "standard way to hand off between agents."
    ),
    "Be brief and focus on results; avoid filler, and never use emojis unless asked.",
    (
        "When you are done, report a result: a short summary of what you did and where you saved "
        "anything, so the parent agent can pick it up."
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
