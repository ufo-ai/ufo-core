"""Website delegation tool: hand a build objective to the website-building subagent.

`build_website` spawns one `website_building` child turn for a build-and-validate session and
returns its summary. The child runs in this conversation's sandbox, so what it builds is here when
the turn ends and the site it hosts is registered against this conversation — the child has its own
conversation and transcript, not its own filesystem. It reaches the child through `ctx.spawn` — the
same Spawn seam
the `spawn` tool uses — so a delegated build is scoped to the profile's tools, never a raw sites
handle. Its typed input carries the two per-build knobs the generic `spawn` payload can't
describe to the spawning agent: `preload_skills`, which seeds the child's context with a skill's
instructions before its first round, and `extended_context`, which lifts its round ceiling for a
large build. The tool is `side_effecting` and keys its child on the call's `idempotency_key`, so
a dispatch step re-executed on crash recovery reconnects to the build it already spawned."""

from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.tools import ObjectBinding, TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_sites.objects import SITE_KIND
from ufo_ext_sites.subagent import WEBSITE_BUILDING_NAME

BUILD_WEBSITE_TOOL = "build_website"
BUILD_WEBSITE_DESCRIPTION = (
    'Delegates a website, dashboard, or web game (never an "app") build to a focused subagent '
    "that builds it, brings it up, validates it against a real browser, and deploys it. The child "
    "works in this conversation's sandbox, so what it builds is here afterwards and the link it "
    "registers is this conversation's; a build needing a backend, or a site that would replace "
    "the one already up here under a new name, comes back for you to host. Include ALL context "
    "in the objective — the child has no history."
)


class BuildWebsiteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
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
    result = await ctx.spawn(
        f"profile:{WEBSITE_BUILDING_NAME}",
        args.model_dump(exclude_none=True),
        dedup_key=ctx.idempotency_key,
    )
    text = "" if result.output is None else result.output.model_dump_json()
    return ToolResult(content=(TextContent(text=text),))


DELEGATION_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        name=BUILD_WEBSITE_TOOL,
        description=BUILD_WEBSITE_DESCRIPTION,
        input_model=BuildWebsiteInput,
        handler=_build_website,
        side_effecting=True,
        bound=ObjectBinding(kind=SITE_KIND, binding="collection"),
        binds_member_authority=False,
    ),
)
