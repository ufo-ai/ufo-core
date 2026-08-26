"""The writing subagent profile: a child turn that drafts and edits prose.

It lives in this pack because the pack owns `writing-drafts`, the workflow the profile runs on: the
skill and the child that always has it in hand ship and version together. Its payload defaults
`preload_skills` to that skill, so every spawn starts with the workflow and its checklist already in
the child's context and loaded under `$UFO_HOME/skills/writing-drafts/` instead of spending a round
loading it; a caller naming other skills replaces the default and must include `writing-drafts`.

Its tool subset is the file builtins it reads a draft and saves one with, plus `load_skill` for the
skill that owns the artifact's shape. No `bash` and no REPL: a prose child that could run a script
would be a second coding subagent, and the office skills that build a file format are the parent's
to route. No web tools either, so nothing it writes rests on content it fetched itself. No
`share_file` — the child has no member to deliver to, and the workspace is the handoff the parent
reads back. The profile pins `gpt-5.6-terra`, which the core catalog calls on the Responses surface,
so the pin is a deliberate choice of writer rather than whatever model the parent happens to run.
"""

from pathlib import Path

from pydantic import BaseModel, Field

from ufo.sdk.manifest import SubagentProfile

WRITING_PROFILE_NAME = "writing"
WRITING_MODEL = "gpt-5.6-terra"
WRITING_DRAFTS_SKILL = "writing-drafts"
WRITING_TOOL_NAMES = (
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "load_skill",
)
WRITING_PROMPT = (Path(__file__).parent / "prompts" / "subagent_writing.md").read_text()


class WritingTask(BaseModel):
    objective: str = Field(
        description="Freeform task governed by the shared delivery register.",
    )
    preload_skills: tuple[str, ...] = Field(
        default=(WRITING_DRAFTS_SKILL,),
        description="Skill names to preload into the child's context before its first round. "
        'Defaults to ("writing-drafts",), the workflow this child writes under; naming others '
        "replaces that default, so keep it in the tuple.",
    )


class WritingResult(BaseModel):
    result: str = Field(
        description="Freeform result governed by the shared delivery register.",
    )


WRITING_PROFILE = SubagentProfile(
    name=WRITING_PROFILE_NAME,
    prompt=WRITING_PROMPT,
    tool_names=WRITING_TOOL_NAMES,
    input_model=WritingTask,
    output_model=WritingResult,
    model=WRITING_MODEL,
)
