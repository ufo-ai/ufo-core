"""The research subagent profiles: a focused `research` child and a long-budget `deep_research` one.

Both run scoped to the research tool set — the web tools plus `browser_task` (the raw browser
surface belongs to the browser subagent alone, and `wide_browse` stays with the main agent, whose
ask_user gates a 20+ entity fan-out), the external-tool, file, and memory tools a research child
reaches for — and their prompts are the ported instructions verbatim.
`deep_research` lifts the round budget to the main ceiling for multi-source work; `wide_research`
(in delegation.py) fans the same `research` profile over a list of entities.

`research` runs on `gpt-5.6-terra` at high reasoning: the Artificial Analysis Intelligence Index
scores it 57 at $0.34 per task against `claude-sonnet-5`'s 48 at $1.22, so the focused child gets
the better answer for a third of the cost. `deep_research` runs on `claude-sonnet-5-5`."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from ufo.sdk.delivery_register import SUBAGENT_RESULT_DESCRIPTION
from ufo.sdk.manifest import SubagentProfile
from ufo_ext_research.tools import FETCH_URL_TOOL, SEARCH_VERTICAL_TOOL, SEARCH_WEB_TOOL

RESEARCH_PROFILE_NAME = "research"
DEEP_RESEARCH_PROFILE_NAME = "deep_research"
DEEP_RESEARCH_ROUND_LIMIT = 200
RESEARCH_MODEL = "gpt-5.6-terra"
RESEARCH_REASONING: Literal["high"] = "high"
DEEP_RESEARCH_MODEL = "claude-sonnet-5-5"
RESEARCH_TOOL_NAMES = (
    SEARCH_WEB_TOOL,
    FETCH_URL_TOOL,
    SEARCH_VERTICAL_TOOL,
    "browser_task",
    "list_external_tools",
    "describe_external_tools",
    "call_external_tool",
    "bash",
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "load_skill",
    "share_file",
    "memory_search",
    "xlsx_repl",
)
RESEARCH_PROMPT = (Path(__file__).parent / "prompts" / "subagent_research.md").read_text()
DEEP_RESEARCH_PROMPT = (Path(__file__).parent / "prompts" / "subagent_deep_research.md").read_text()


class ResearchInput(BaseModel):
    objective: str = Field(
        description="Freeform task governed by the shared delivery register.",
    )


class ResearchOutput(BaseModel):
    result: str = Field(
        description=SUBAGENT_RESULT_DESCRIPTION,
    )


RESEARCH_PROFILE = SubagentProfile(
    name=RESEARCH_PROFILE_NAME,
    prompt=RESEARCH_PROMPT,
    tool_names=RESEARCH_TOOL_NAMES,
    input_model=ResearchInput,
    output_model=ResearchOutput,
    models=(RESEARCH_MODEL,),
    reasoning=RESEARCH_REASONING,
    concise_parent_handoff=True,
)
DEEP_RESEARCH_PROFILE = SubagentProfile(
    name=DEEP_RESEARCH_PROFILE_NAME,
    prompt=DEEP_RESEARCH_PROMPT,
    tool_names=RESEARCH_TOOL_NAMES,
    input_model=ResearchInput,
    output_model=ResearchOutput,
    max_rounds=DEEP_RESEARCH_ROUND_LIMIT,
    models=(DEEP_RESEARCH_MODEL,),
    concise_parent_handoff=True,
)
