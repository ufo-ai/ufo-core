"""The research subagent profiles: a focused `research` child and a long-budget `deep_research` one.

Both run scoped to the research tool set — the web tools plus the browser, external-tool, file, and
memory tools a research child reaches for — and their prompts are the ported instructions verbatim.
`deep_research` lifts the round budget to the main ceiling for multi-source work; `wide_research`
(in delegation.py) fans the same `research` profile over a list of entities."""

from pathlib import Path

from pydantic import BaseModel

from ufo.sdk.manifest import SubagentProfile
from ufo_ext_research.tools import FETCH_URL_TOOL, SEARCH_VERTICAL_TOOL, SEARCH_WEB_TOOL

RESEARCH_PROFILE_NAME = "research"
DEEP_RESEARCH_PROFILE_NAME = "deep_research"
DEEP_RESEARCH_ROUND_LIMIT = 200
RESEARCH_TOOL_NAMES = (
    SEARCH_WEB_TOOL,
    FETCH_URL_TOOL,
    SEARCH_VERTICAL_TOOL,
    "navigate",
    "read_page",
    "get_page_text",
    "find",
    "tabs_context",
    "tabs_create",
    "tabs_close",
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
    "list_skills",
    "share_file",
    "memory_search",
    "xlsx_repl",
)
RESEARCH_PROMPT = (Path(__file__).parent / "prompts" / "subagent_research.md").read_text()
DEEP_RESEARCH_PROMPT = (Path(__file__).parent / "prompts" / "subagent_deep_research.md").read_text()


class ResearchInput(BaseModel):
    objective: str


class ResearchOutput(BaseModel):
    result: str


RESEARCH_PROFILE = SubagentProfile(
    name=RESEARCH_PROFILE_NAME,
    prompt=RESEARCH_PROMPT,
    tool_names=RESEARCH_TOOL_NAMES,
    input_model=ResearchInput,
    output_model=ResearchOutput,
)
DEEP_RESEARCH_PROFILE = SubagentProfile(
    name=DEEP_RESEARCH_PROFILE_NAME,
    prompt=DEEP_RESEARCH_PROMPT,
    tool_names=RESEARCH_TOOL_NAMES,
    input_model=ResearchInput,
    output_model=ResearchOutput,
    max_rounds=DEEP_RESEARCH_ROUND_LIMIT,
)
