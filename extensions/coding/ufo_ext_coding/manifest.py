"""The coding pack: a software-engineering child turn over the core code builtins and the `coding`
skill that routes work to it.

`spawn("coding", {"objective": ...})` runs a child that explores a repo, edits code, runs
 tests, and reports a result. The profile names only tool names — bash/read/write/
 edit/glob/grep to work the code, load_skill to pull a workflow, js_repl to exercise Node code, and
 search_web/fetch_url for external sources — so the pack is self-contained and carries no
 cross-extension import. Nothing in it delivers a file to the member: the child leaves work in the
 workspace it shares with the parent, and the parent decides what reaches the member. Core wraps
 the prompt with the shared citation/formatting discipline and fills its skill index. The `coding`
 skill teaches the main agent to route repo work to that child.

`coding` prefers connected Claude and ChatGPT accounts in that order, then falls back to GLM.
`spawn("fable_escalation", ...)` is the last rung on one pull-request blocker: the same tools and
the same input contract on a stronger pinned model, with its own prompt for a worker that two
`coding` children already failed in front of. It reads wider than the diff, makes one attempt, and
reports.

The pack ships no agent. It is the machinery a durable one runs on — the child profiles and the
 skill — and a durable agent with work of its own is an application: it ships as its own
extension, under the slug its page is served at.

Both children work a checkout, so both read the instruction files the repositories in the
workspace carry.

The pack declares sandbox internet because repository builds install dependencies and download
their release assets. GitHub itself — private clone, push, `gh`, and the API — rides the member's
connected GitHub account: the `github` connector's CLI credential exports `GH_TOKEN` into the
sandbox as a sentinel the egress proxy swaps for the account's token."""

from pathlib import Path

from pydantic import BaseModel, Field

from ufo.sdk.manifest import Manifest, SkillSpec, SubagentProfile
from ufo_ext_coding.agents_md import repo_instruction_hooks

NAME = "coding"
VERSION = "0.1.0"
CODING_PROFILE_NAME = "coding"
SKILLS_ROOT = Path(__file__).parent / "skills"
SKILL_NAMES = ("coding", "code-review", "code-review-automation")
CODING_TOOL_NAMES = (
    "bash",
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "load_skill",
    "js_repl",
    "search_web",
    "fetch_url",
)
CODING_PROMPT = (Path(__file__).parent / "prompts" / "subagent_coding.md").read_text()
CODING_ROUND_LIMIT = 500
CODING_MODELS = ("claude-opus-5-5", "gpt-5.6-sol", "z-ai/glm-5.3")
FABLE_ESCALATION_PROFILE_NAME = "fable_escalation"
FABLE_ESCALATION_MODEL = "anthropic/claude-fable-5.1"
FABLE_ESCALATION_PROMPT = (
    Path(__file__).parent / "prompts" / "subagent_fable_escalation.md"
).read_text()
GITHUB_CONNECTOR = "github"


class CodingInput(BaseModel):
    objective: str = Field(
        description="Freeform task governed by the shared delivery register.",
    )
    extended_context: bool | None = Field(
        default=None,
        description="Run the child under the main agent's round ceiling instead of its default "
        "budget, for an unusually deep task that needs more tool-use rounds.",
    )


class CodingOutput(BaseModel):
    result: str = Field(
        description="Freeform result governed by the shared delivery register.",
    )


CODING_PROFILE = SubagentProfile(
    name=CODING_PROFILE_NAME,
    prompt=CODING_PROMPT,
    tool_names=CODING_TOOL_NAMES,
    input_model=CodingInput,
    output_model=CodingOutput,
    max_rounds=CODING_ROUND_LIMIT,
    models=CODING_MODELS,
)

FABLE_ESCALATION_PROFILE = SubagentProfile(
    name=FABLE_ESCALATION_PROFILE_NAME,
    prompt=FABLE_ESCALATION_PROMPT,
    tool_names=CODING_TOOL_NAMES,
    input_model=CodingInput,
    output_model=CodingOutput,
    max_rounds=CODING_ROUND_LIMIT,
    models=(FABLE_ESCALATION_MODEL,),
)

REPOSITORY_PROFILE_NAMES = frozenset({CODING_PROFILE_NAME, FABLE_ESCALATION_PROFILE_NAME})


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        sandbox_internet=True,
        subagents=(CODING_PROFILE, FABLE_ESCALATION_PROFILE),
        skills=tuple(SkillSpec(path=SKILLS_ROOT / name) for name in SKILL_NAMES),
        hooks=repo_instruction_hooks(REPOSITORY_PROFILE_NAMES),
    )
