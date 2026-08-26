"""`report_problem`: a workspace condition no turn can repair, pushed to the operators who run this
deploy.

The handler emits one warning record through the fleet's telemetry pipeline and nothing else. An
operator reads those records on the turns board, which counts them and lists them; what makes one
actionable is the debugger link it carries — this extension's own route, scoped to the workspace,
conversation, and turn the report came from, so opening it lands on the transcript. The link
is composed from `SURFACE_DEBUG`, the same name the manifest mounts the surface under, so it cannot
drift from the route it addresses. A deploy that publishes no base URL has no such route to name,
and the record carries the three ids alone.

The tool belongs to this extension because the link is this extension's surface: the reader of a
report is the operator reading sessions here, and a report that named a path nothing serves would be
a link to nowhere.

The agent's own failed step is not a report — a command it can run again, a path it named wrong, a
provider error that clears on retry is the turn's work. What this carries is the condition a member
cannot fix and neither can we from inside a turn, plus whatever a member asks us to be told, which
is why the origin rides the wire rather than being inferred from the symptom.

One call is one event, and nothing pages on it: a report is a line on a board an operator reads,
not an alert. So the tool keeps no window, no store, and no dedup of its own — how long a condition
has lasted is answered by its own reports standing in that list, and a second answer to it would be
worse than none.

What crosses is an error's *class*, never its text. A message is output this process never wrote: a
sandbox command's stderr echoes the environment it ran under, and that environment carries the
turn's signed run token in `HTTP_PROXY` — which is why `formatted_stack` exports frames and classes
and no message at all. Two bounds hold the same line here, both in code rather than in the field's
description: a class is shorter than any token this deploy signs, and a value carrying a scheme is
refused, because a URL is never the name of an error. The text itself is one click away, in the
transcript the link opens."""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from ufo.sdk.o11y import warn
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_debugger.surface import SURFACE_DEBUG

REPORT_PROBLEM_TOOL = "report_problem"
PROBLEM_REPORTED_EVENT = "problem.reported"
SYMPTOM_MAX_CHARS = 300
OBJECT_REF_MAX_CHARS = 120
ERROR_CLASS_MAX_CHARS = 120
NEXT_ACTION_MAX_CHARS = 300
URL_SCHEME = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://")
REPORT_DESCRIPTION = (
    "Report a broken condition in this workspace to the operators who run this deploy. Use it for "
    "a fault no turn can repair — a connection that no longer authenticates, a credential slot "
    "with no value, a scheduled task that faults on every run, a source that has stopped syncing, "
    "a site or sandbox that stopped answering — and whenever a member asks for a problem to be "
    "reported to the team, whatever the problem is. Not for a step you can retry: a command that "
    "failed, a path you named wrong, or a provider error that clears on the next attempt is this "
    "turn's work. One call per condition. Nothing answers back on this conversation, so tell the "
    "member what you reported and carry on with whatever still works."
)
REPORTED = (
    "Reported to the operators of this deploy, with the symptom, the object, the error class, and "
    "a link to this turn. No answer arrives on this conversation."
)


class ReportProblemInput(BaseModel):
    origin: Literal["fault", "member_request"] = Field(
        description=(
            "`fault` when you found the condition yourself, `member_request` when a member asked "
            "for it to be reported."
        )
    )
    symptom: str = Field(
        max_length=SYMPTOM_MAX_CHARS,
        description="The observable failure in one sentence.",
    )
    next_action: str = Field(
        max_length=NEXT_ACTION_MAX_CHARS,
        description="What an operator should check or do next.",
    )
    object_ref: str | None = Field(
        default=None,
        max_length=OBJECT_REF_MAX_CHARS,
        description=(
            "The object the fault is in as `kind/name` — `connection/gmail`, "
            "`scheduled_task/daily-brief` — where one is known."
        ),
    )
    error_class: str | None = Field(
        default=None,
        max_length=ERROR_CLASS_MAX_CHARS,
        description=(
            "What the failure came back as, named rather than quoted — `HTTPStatusError 401`, "
            "`invalid_grant`, `exit 137`. Never command output, a stack, or a URL."
        ),
    )

    @field_validator("error_class")
    @classmethod
    def _names_an_error(cls, value: str | None) -> str | None:
        if value is not None and URL_SCHEME.search(value):
            raise ValueError("error_class names an error and never carries a URL")
        return value


async def report_problem(ctx: ToolContext, args: ReportProblemInput) -> ToolResult:
    base = ctx.public_base_url
    link = (
        None
        if base is None
        else f"{base.rstrip('/')}/surface/{SURFACE_DEBUG}"
        f"?ws={ctx.turn.workspace_id}&c={ctx.turn.conversation_id}&t={ctx.turn.id}"
    )
    warn(
        PROBLEM_REPORTED_EVENT,
        origin=args.origin,
        symptom=args.symptom,
        next_action=args.next_action,
        object_ref=args.object_ref,
        error_class=args.error_class,
        conversation_id=str(ctx.turn.conversation_id),
        turn_id=str(ctx.turn.id),
        agent_id=str(ctx.turn.agent_id),
        member_id=None if ctx.acting_member_id is None else str(ctx.acting_member_id),
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
