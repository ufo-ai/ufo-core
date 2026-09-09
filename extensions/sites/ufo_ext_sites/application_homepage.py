"""The application homepage builder: one pinned subagent profile and where its build stops.

An application's homepage is a kit page — `app.tsx` beside an `index.html` — drawn as a wireframe
at the width it renders at, built against the deploy's own `ufo/kit`, hosted by `deploy_website` at
a permanent link, and bound to the agent by `set_homepage`. None of that is a tool of its own: the
child holds the core file builtins, the pair that show it its own page, the read-only connector
tools, and the one site action that hosts.

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


class ApplicationBuildTask(BaseModel):
    """One application homepage build.

    Where the build stops is said in the objective. Nothing in the code branched on a field for it,
    and the statuses that replaced the field said only what the result already shows."""

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
    """What the build left behind: a hosted page, or why there is none."""

    site: str = ""
    site_url: str = ""
    stopped: str = ""

    @model_validator(mode="after")
    def answers_with_a_page_or_a_reason(self) -> "ApplicationBuildResult":
        if bool(self.site) == bool(self.stopped):
            raise ValueError("answer with the deploy's site, or with what stopped you")
        if self.site and not self.site_url:
            raise ValueError("a hosted site answers with its site_url")
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
        "js_repl",
        "start_server",
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
