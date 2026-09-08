"""The application homepage builder: one pinned subagent profile and where its build stops.

An application's homepage is a kit page — `app.tsx` beside an `index.html` — drawn as a 360 px
wireframe, built against the deploy's own `ufo/kit`, hosted by `deploy_website` at a permanent
link, and bound to the agent by `set_homepage`. None of that is a tool of its own: the child holds
the core file builtins, the read-only connector tools, and the one site action that hosts.

What is left here is only what a skill cannot say. A skill cannot pin a model or a reasoning
effort, cannot make a tool set exact, cannot make connector calls read-only, cannot wall a child's
answer as untrusted, cannot cap rounds, and cannot type a payload `spawn` validates. Everything
else the builder needs to know is in the `application-homepage` skill this profile preloads."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ufo.sdk.manifest import SubagentProfile

APPLICATION_BUILDER_NAME = "ufo_application_builder"
APPLICATION_HOMEPAGE_SKILL = "application-homepage"
APPLICATION_BUILDER_MODEL = "google/gemini-3.7-flash"
APPLICATION_BUILDER_REASONING: Literal["medium"] = "medium"
APPLICATION_BUILDER_ROUND_LIMIT = 100
"""A runaway's backstop, not a budget to spend down.

What a build ought to cost is a question for an eval, measured over many of them; a ceiling in the
runtime answers it for every member's page at once and answers it wrong. Set at 35 it decided most
builds rather than bounding any: over 106 recorded worker turns the median ran 36 rounds and 71%
reached it. This is the ceiling the website builder beside it already runs under, and one no
correct build reaches."""
PAGE_REFUSAL_OPENING = "This page cannot be hosted yet. Repair it and deploy again:"
"""What a deploy refusal says before it lists the repairs.

The builder's next `edit` reads that list, so a child holding this text has been told what to
do and has not done it yet."""
APPLICATION_SCAFFOLD_PATH = "/workspace/ufo-app"
APPLICATION_SOURCE_PATH = f"{APPLICATION_SCAFFOLD_PATH}/app.tsx"
DESIGN_SUFFIX = ".svg"
APPLICATION_DESIGN_PATH = f"{APPLICATION_SCAFFOLD_PATH}/application-design{DESIGN_SUFFIX}"
APPLICATION_SKILL_DIR = Path(__file__).parent / "skills" / APPLICATION_HOMEPAGE_SKILL
APPLICATION_TEMPLATE_DIR = APPLICATION_SKILL_DIR / "template"
APPLICATION_AUDIT_SCRIPT_PATH = APPLICATION_SKILL_DIR / "scripts" / "audit_application.cjs"
BUILDER_CONTRACT = (
    (Path(__file__).parent / "prompts" / "subagent_application_builder.md")
    .read_text()
    .strip()
    .replace("{{homepage_skill}}", APPLICATION_HOMEPAGE_SKILL)
    .replace("{{scaffold_path}}", APPLICATION_SCAFFOLD_PATH)
)


ApplicationBuildStatus = Literal["designed", "deployed", "blocked"]
"""Where a build stopped: the wireframe it drew, the site it hosted, or what stopped it."""


class ApplicationBuildTask(BaseModel):
    """One application homepage build.

    Where the build stops is said in the objective, not carried as a field. Nothing in the code
    ever branched on such a field — only the contract the child reads did — so it was a convention
    wearing an API, and one more thing for a spawn to get wrong."""

    model_config = ConfigDict(extra="forbid")

    objective: str = Field(
        min_length=1,
        max_length=20_000,
        description="The application's own prompt and the member's request: what the page shows "
        "and who reads it. Say here if the member is to see the wireframe before the page is "
        "built; otherwise the child hosts the page. Everything the child needs to know goes here.",
    )
    preload_skills: tuple[Literal["application-homepage"], ...] = Field(
        default=("application-homepage",),
        description="Leave this out. The builder's skill is fixed and already loaded; the app's "
        "own home skill is not what it follows, and naming one refuses the spawn.",
    )


class ApplicationBuildResult(BaseModel):
    """What the build left behind: the design it drew, or the site it hosted.

    Each status has to carry its own evidence. A `deployed` with no link is a claim the parent would
    bind nothing from, and a `blocked` with no blocker is a turn that ended for no stated reason —
    both reach the child as a validation error it can still fix, rather than reaching the member as
    a page that is not there."""

    status: ApplicationBuildStatus
    design_path: str = Field(
        default="",
        description="The wireframe you wrote: the .svg itself, never the design.png the audit "
        "renders beside it. The picture is how you look at the drawing; the drawing is the file.",
    )
    site: str = ""
    site_url: str = ""
    blocker: str = Field(default="", max_length=4_000)

    @model_validator(mode="after")
    def status_carries_its_evidence(self) -> "ApplicationBuildResult":
        needed = {
            "designed": ("design_path",),
            "deployed": ("site", "site_url"),
            "blocked": ("blocker",),
        }[self.status]
        missing = tuple(field for field in needed if not getattr(self, field))
        if missing:
            raise ValueError(f"{self.status} needs {', '.join(missing)}")
        if self.status == "blocked" and PAGE_REFUSAL_OPENING in self.blocker:
            raise ValueError(
                "a deploy refusal is not a blocker: make the repairs it lists and deploy again"
            )
        if self.status == "designed" and not self.design_path.endswith(DESIGN_SUFFIX):
            raise ValueError(
                f"design_path is the wireframe you wrote, ending {DESIGN_SUFFIX} — not the "
                "picture rendered beside it"
            )
        return self


APPLICATION_BUILDER_PROFILE = SubagentProfile(
    name=APPLICATION_BUILDER_NAME,
    prompt=BUILDER_CONTRACT,
    tool_names=(
        "bash",
        "read",
        "write",
        "edit",
        "glob",
        "grep",
        "list_external_tools",
        "describe_external_tools",
        "search_connector_tools",
        "call_external_tool",
        "action:site:deploy_website",
    ),
    input_model=ApplicationBuildTask,
    output_model=ApplicationBuildResult,
    max_rounds=APPLICATION_BUILDER_ROUND_LIMIT,
    model=APPLICATION_BUILDER_MODEL,
    reasoning=APPLICATION_BUILDER_REASONING,
    untrusted_output=True,
    isolated_tools=True,
    connector_read_only=True,
)
