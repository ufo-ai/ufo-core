"""Website delegation tool: hand a build objective to the website-building subagent.

`build_website` spawns one `website_building` child turn for a full build-and-serve session and
returns its summary. It reaches the child through `ctx.spawn` — the same Spawn seam
`spawn_subagent` uses — so a delegated build is scoped to the profile's tools, never a raw sites
handle. Its typed input carries the two per-build knobs the generic `spawn_subagent` payload can't
describe to the spawning agent: `preload_skills`, which seeds the child's context with a skill's
instructions before its first round, and `extended_context`, which lifts its round ceiling for a
large build. Each call wants a fresh session, so it passes no `dedup_key`."""

from pydantic import BaseModel, Field

from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_sites.subagent import WEBSITE_BUILDING_NAME

BUILD_WEBSITE_TOOL = "build_website"
BUILD_WEBSITE_DESCRIPTION = (
    "Delegates a full website, web app, dashboard, or web game build to a focused subagent that "
    "builds, serves, and validates the site in the sandbox and returns a summary. Include ALL "
    "context in the objective — the child has no conversation history."
)


class BuildWebsiteInput(BaseModel):
    objective: str = Field(
        description="Self-contained description of the site to build: purpose, pages/sections, "
        "content, and any design or behavior requirements. The child has no conversation history."
    )
    task_name: str | None = Field(
        default=None,
        description="Short, user-friendly name for this build, e.g. 'Landing page' or 'Pricing "
        "dashboard'.",
    )
    preload_skills: tuple[str, ...] | None = Field(
        default=None,
        description="Skill names to preload into the child's context before its first round, so "
        "it starts with their instructions already in hand instead of spending a round calling "
        'load_skill itself — e.g. ("website-building",) for any web build.',
    )
    extended_context: bool | None = Field(
        default=None,
        description="Run the child under the main agent's round ceiling instead of its default "
        "budget, for unusually large or multi-page builds that need more tool-use rounds.",
    )


async def _build_website(ctx: ToolContext, args: BuildWebsiteInput) -> ToolResult:
    result = await ctx.spawn(WEBSITE_BUILDING_NAME, args.model_dump(exclude_none=True))
    text = "" if result.output is None else result.output.model_dump_json()
    return ToolResult(content=(TextContent(text=text),))


DELEGATION_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name=BUILD_WEBSITE_TOOL,
        description=BUILD_WEBSITE_DESCRIPTION,
        input_model=BuildWebsiteInput,
        handler=_build_website,
    ),
)
