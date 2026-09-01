"""`report_problem`: a condition in this workspace that no turn can repair, pushed to the engineers
who run this deploy.

The handler emits one warning record through the fleet's telemetry pipeline and nothing else. An
engineer reads those records on the turns board, which counts them by category and lists them; what
makes one actionable is the debugger link it carries — this extension's own route, scoped to the
workspace, conversation, and turn the report came from, so opening it lands on the transcript. The
link is composed from `SURFACE_DEBUG`, the same name the manifest mounts the surface under, so it
cannot drift from the route it addresses. A deploy that publishes no base URL has no such route to
name, and the record carries the three ids alone.

The tool belongs to this extension because the link is this extension's surface: the reader of a
report is the engineer reading sessions here, and a report that named a path nothing serves would be
a link to nowhere.

`category` is what the record is read by. Every log field is queryable by its own name, so the
board's count groups on this one — the enum exists because a free-text subsystem name cannot be
grouped, and each member names the subsystem a triager would route the report to. `impact` is what
the condition costs the member, and it is required: a default reached by silence measures nothing.
It is not named `severity`, because the record already carries one at WARN.

A single failed step is not a report — a command the agent can run again, a path it named wrong, a
provider error that clears on retry is the turn's work. `a fault no turn can repair` is the whole of
what says so: a description that also spelled the retry out held four restraint cases no better over
two ablations, and its one measured effect was to make a refused token read as the provider's
problem rather than the task's. What this carries is the condition a member cannot fix and neither
can we from inside a turn, plus whatever a member asks us to be told, which is why the origin rides
the wire rather than being inferred from the problem. Its two names are the whole of its
documentation: the description above says when a member's ask is the reason, and an ablation that
reduced the field's own hint to nothing routed every member request correctly.

One call is one event, and nothing pages on it: a report is a line on a board an engineer reads, not
an alert. So the tool keeps no window, no store, and no dedup of its own — how long a condition has
lasted is answered by its own reports standing in that list, and a second answer to it would be
worse than none.

`problem` is the agent's own account, and never output it copied. A message this process did not
write echoes the environment the failing command ran under, and that environment carries the turn's
signed run token in `HTTP_PROXY` — which is why `formatted_stack` exports frames and classes and no
message at all. Two bounds hold that line here, both in code rather than in the field's description:
the field is too short to hold a dump, and a URL carrying userinfo is refused, because that is the
shape a copied proxy variable takes. The text of any error is one click away, in the transcript the
link opens."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ufo.sdk.authority import authority_member_id
from ufo.sdk.o11y import warn
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_debugger.surface import SURFACE_DEBUG

REPORT_PROBLEM_TOOL = "report_problem"
PROBLEM_REPORTED_EVENT = "problem.reported"
PROBLEM_MAX_CHARS = 600
CREDENTIALED_URL = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@]*@")
REPORT_DESCRIPTION = (
    "Report a problem in this workspace to the engineers who run this deploy. Use it for a fault "
    "no turn can repair — a connection that stopped authenticating, an empty credential slot, a "
    "task that faults on every run, a sandbox or site that stopped answering — and whenever a "
    "member asks for a problem to be reported, whatever the problem is. Say what you attempted, "
    "what happened, and what you expected, in your own words; never paste command output. An "
    "engineer opens the transcript of this turn from the record, so the detail is already there. "
    "One call per problem. Nothing "
    "answers back on this conversation, so tell the member what you reported and carry on with "
    "whatever still works."
)
REPORTED = (
    "Reported to the engineers of this deploy, with the problem, its category, its impact, and a "
    "link to this turn. No answer arrives on this conversation."
)


class ReportProblemInput(BaseModel):
    problem: str = Field(
        max_length=PROBLEM_MAX_CHARS,
        description=(
            "What went wrong, in your own words: what was attempted, what happened, and what was "
            "expected. Never command output, a stack, or a credentialed URL."
        ),
    )
    category: Literal[
        "browser_task",
        "coding_subagent",
        "cron_task",
        "asset_pptx",
        "asset_docx",
        "asset_xlsx",
        "asset_pdf",
        "asset_image",
        "asset_site",
        "asset_other",
        "external_connector",
        "sandbox_runtime",
        "memory",
        "conversation_quality",
        "other",
    ] = Field(description="The subsystem the problem is in. Pick the most specific match.")
    impact: Literal["critical", "major", "minor"] = Field(
        description=(
            "`critical` when this conversation cannot continue, `major` when a member loses "
            "something they asked for, `minor` when the work goes on around it."
        )
    )
    origin: Literal["fault", "member_request"]

    @field_validator("problem")
    @classmethod
    def _is_the_agents_own_account(cls, value: str) -> str:
        if CREDENTIALED_URL.search(value):
            raise ValueError("problem is your own account of the fault and carries no credential")
        return value


async def report_problem(ctx: ToolContext, args: ReportProblemInput) -> ToolResult:
    base = ctx.public_base_url
    link = (
        None
        if base is None
        else f"{base.rstrip('/')}/surface/{SURFACE_DEBUG}"
        f"?ws={ctx.turn.workspace_id}&c={ctx.turn.conversation_id}&t={ctx.turn.id}"
    )
    member_id = authority_member_id(ctx.authority)
    warn(
        PROBLEM_REPORTED_EVENT,
        problem=args.problem,
        category=args.category,
        impact=args.impact,
        origin=args.origin,
        conversation_id=str(ctx.turn.conversation_id),
        turn_id=str(ctx.turn.id),
        agent_id=str(ctx.turn.agent_id),
        member_id=None if member_id is None else str(member_id),
        debug_url=link,
    )
    return ToolResult(content=(TextContent(text=REPORTED),))


REPORT_PROBLEM_TOOL_DEF = ToolDef(
    name=REPORT_PROBLEM_TOOL,
    description=REPORT_DESCRIPTION,
    input_model=ReportProblemInput,
    handler=report_problem,
    parallel_safe=True,
)
