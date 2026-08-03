"""The website-building subagent profile: a child turn scoped to the site-building tools.

Its prompt is the website-building instructions; the core subagent shell wraps the shared citation
and output discipline around it, so this profile carries only the workflow that is distinct to
building and validating a site. Its tool subset is the core file builtins it edits with, the build
and local-serve tools, the Node REPL it uses to drive and verify a running page, and the hosting
tools, minus `publish_website`: a subagent runs in the sandbox of the turn that spawned it, so a
port it brings up is served by the member's own sandbox and the `deploy_website` link it registers
belongs to the member's conversation, outliving this turn.

Two tools it does not hold. `publish_website` stands up an app with a backend and is the parent's to
call — the child reports what it built and control returns to the turn the member is talking to.
`share_file` delivers a copy to that member, and the child has no member to deliver to: the
workspace is the handoff, read back by the parent with `glob` and `read` in the same filesystem."""

from pathlib import Path

from pydantic import BaseModel, Field

from ufo.sdk.manifest import SubagentProfile
from ufo_ext_sites.tools import PUBLISH_WEBSITE_TOOL, SITES_TOOL_NAMES

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
    "load_skill",
    *(name for name in SITES_TOOL_NAMES if name != PUBLISH_WEBSITE_TOOL),
    "js_repl",
    "xlsx_repl",
    # Web reference-gathering (source website_building set); resolves if research is installed.
    "search_web",
    "search_vertical",
    "fetch_url",
)


class WebsiteBuildingTask(BaseModel):
    objective: str = Field(
        description="Freeform task governed by the shared delivery register.",
    )
    task_name: str | None = None
    preload_skills: tuple[str, ...] | None = None
    extended_context: bool | None = None


class WebsiteBuildingResult(BaseModel):
    result: str = Field(
        description="Freeform result governed by the shared delivery register.",
    )


WEBSITE_BUILDING_PROFILE = SubagentProfile(
    name=WEBSITE_BUILDING_NAME,
    prompt=WEBSITE_BUILDING_PROMPT,
    tool_names=WEBSITE_BUILDING_TOOL_NAMES,
    input_model=WebsiteBuildingTask,
    output_model=WebsiteBuildingResult,
    max_rounds=WEBSITE_BUILDING_ROUND_LIMIT,
)
