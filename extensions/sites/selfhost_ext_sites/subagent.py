"""The website-building subagent profile: a child turn scoped to the site-building tools.

Its prompt is the website-building instructions; the core subagent shell wraps the shared citation
and output discipline around it, so this profile carries only the workflow that is distinct to
building and serving a site. Its tool subset is the core file builtins it edits with, the sites
tools it builds and serves through, and the Node REPL it uses to drive and verify a running page."""

from pathlib import Path

from pydantic import BaseModel

from selfhost.sdk.manifest import SubagentProfile
from selfhost_ext_sites.tools import SITES_TOOL_NAMES

WEBSITE_BUILDING_NAME = "website_building"
WEBSITE_BUILDING_PROMPT = (
    Path(__file__).parent / "prompts" / "subagent_website_building.md"
).read_text()
WEBSITE_BUILDING_ROUND_LIMIT = 100
WEBSITE_BUILDING_TOOL_NAMES = (
    "bash",
    "read",
    "write",
    "edit",
    "glob",
    "grep",
    "share_file",
    "load_skill",
    *SITES_TOOL_NAMES,
    "js_repl",
    # Web reference-gathering (source website_building set); resolves if research is installed.
    "search_web",
    "search_vertical",
    "fetch_url",
)


class WebsiteBuildingTask(BaseModel):
    objective: str
    task_name: str | None = None
    preload_skills: tuple[str, ...] | None = None
    extended_context: bool | None = None


class WebsiteBuildingResult(BaseModel):
    result: str


WEBSITE_BUILDING_PROFILE = SubagentProfile(
    name=WEBSITE_BUILDING_NAME,
    prompt=WEBSITE_BUILDING_PROMPT,
    tool_names=WEBSITE_BUILDING_TOOL_NAMES,
    input_model=WebsiteBuildingTask,
    output_model=WebsiteBuildingResult,
    max_rounds=WEBSITE_BUILDING_ROUND_LIMIT,
)
