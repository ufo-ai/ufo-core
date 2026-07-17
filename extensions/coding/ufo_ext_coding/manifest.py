"""The coding subagent pack: a software-engineering child turn over the core code builtins, plus the
`coding` and `code-review` skills the agent loads on demand.

`spawn_subagent("coding", {"objective": ...})` runs a child that explores a repo, edits code, runs
tests, and reports a self-contained result. The profile names only tool names — bash/read/write/
edit/glob/grep to work the code, load_skill to pull a workflow, share_file to hand back
an artifact, and js_repl to exercise Node code — so the pack is self-contained and carries no
cross-extension import. Core wraps the prompt with the shared citation/formatting discipline and
fills its skill index. The `coding` skill teaches the main agent to route repo work to that child;
`code-review` teaches reviewing a PR through the GitHub connector and reporting findings."""

from pathlib import Path

from pydantic import BaseModel, Field

from ufo.sdk.manifest import Manifest, SkillSpec, SubagentProfile

NAME = "coding"
VERSION = "0.1.0"
CODING_PROFILE_NAME = "coding"
SKILLS_ROOT = Path(__file__).parent / "skills"
SKILL_NAMES = ("code-review", "coding")
CODING_TOOL_NAMES = (
    "bash",
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "load_skill",
    "share_file",
    "js_repl",
    "search_web",
    "fetch_url",
)
CODING_PROMPT = (Path(__file__).parent / "prompts" / "subagent_coding.md").read_text()
CODING_ROUND_LIMIT = 100


class CodingInput(BaseModel):
    objective: str
    extended_context: bool | None = Field(
        default=None,
        description="Run the child under the main agent's round ceiling instead of its default "
        "budget, for an unusually deep task that needs more tool-use rounds.",
    )


class CodingOutput(BaseModel):
    result: str


CODING_PROFILE = SubagentProfile(
    name=CODING_PROFILE_NAME,
    prompt=CODING_PROMPT,
    tool_names=CODING_TOOL_NAMES,
    input_model=CodingInput,
    output_model=CodingOutput,
    max_rounds=CODING_ROUND_LIMIT,
)


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        subagents=(CODING_PROFILE,),
        skills=tuple(SkillSpec(path=SKILLS_ROOT / name) for name in SKILL_NAMES),
    )
