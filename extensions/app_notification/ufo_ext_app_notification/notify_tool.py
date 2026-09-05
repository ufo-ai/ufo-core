"""`notify`: put one message in the Notification app's inbox for the member this turn runs for.

The tool is the producer half of the back channel. It is in the member-facing set, so every agent
holds it, and it writes one row under the turn's own authority — the member it concerns is the
member the turn acts for, never a name the model supplies. Nothing is hidden because nothing is
spoken: a tool call never enters a delivered reply, so no redaction stands between this and the
member. What the model sees back is one line, plus the reason when a fence refuses, which is what
makes the fences steerable rather than silent.

Two refusals are structural. A turn carrying workspace authority names no member, and a
notification nobody is the recipient of is not one. The Notification agent's own turns cannot
post: an inbox that mails itself is the loop this whole design must be unable to enter. The inbox
is the agent this extension provisioned, found by that provision and never by name — a member's own
agent may hold `notification`, and the shipped one then lands on a free variant."""

from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.authority import authority_member_id
from ufo.sdk.context import ExtensionContext
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_app_notification.store import (
    BODY_MAX,
    SUBJECT_MAX,
    NotificationStore,
    Posted,
    Refused,
    inbox_agent_id,
)

NOTIFY_TOOL_NAME = "notify"
NOTIFICATION_AGENT_NAME = "notification"
NOTIFY_DESCRIPTION = (
    "Put one message in the notification queue for the member this turn runs for. Use it for a "
    "business or operational fact in their own data that they would want to know without asking: "
    "a churn or failed-payment spike in Stripe, a deploy or CI run that failed on main in GitHub, "
    "an email that needs a decision or is going unanswered, a customer or investor turning. It is "
    "not for ufo's own condition: a connection that stopped authenticating or a task that faults "
    "is `report_problem` and a line in your reply, never a notification. Nothing answers back "
    "here and the member may never see it: one agent reads every notification and decides. One "
    "call per subject, whatever the batch size; two different things are two calls. Do not raise "
    "what this turn already told them or a fault you can repair."
)
NOTIFY_NEEDS_A_MEMBER = (
    "this turn runs under workspace authority and names no member, so there is nobody to notify"
)
NOTIFY_NO_INBOX = "the Notification app is not live in this workspace, so there is no inbox"
NOTIFY_SELF = "the notification agent does not notify itself"
NOTIFY_QUEUED = "Queued. Nothing answers back on this conversation."
NOTIFY_FOLDED = (
    "Folded into the notification on this subject, now raised {n} times. Nothing answers "
    "back on this conversation."
)


class NotifyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    subject: str = Field(
        min_length=1,
        max_length=SUBJECT_MAX,
        description=(
            "The stable thing this is about, as a ref where one exists (`source/<name>`, "
            "`issue/1801`). Two messages naming one subject fold into one."
        ),
    )
    body: str = Field(
        min_length=1,
        max_length=BODY_MAX,
        description=(
            "What happened and why it matters, in your own words. State the fact and its "
            "consequence."
        ),
    )


def _require_ext(ext: ExtensionContext | None) -> ExtensionContext:
    if ext is None:
        raise RuntimeError("notify requires the app_notification ExtensionContext")
    return ext


def _refusal(text: str) -> ToolResult:
    return ToolResult(content=(TextContent(text=text),), is_error=True)


async def notify(ctx: ToolContext, args: NotifyInput) -> ToolResult:
    member_id = authority_member_id(ctx.authority)
    if member_id is None:
        return _refusal(NOTIFY_NEEDS_A_MEMBER)
    ext = _require_ext(ctx.ext)
    inbox = await inbox_agent_id(ext)
    if inbox is None:
        return _refusal(NOTIFY_NO_INBOX)
    if inbox == ctx.turn.agent_id:
        return _refusal(NOTIFY_SELF)
    posted = await NotificationStore(ext).post(
        to_agent_id=inbox,
        member_id=member_id,
        subject=args.subject,
        body=args.body,
        agent_id=ctx.turn.agent_id,
        agent_name=await ext.agent_name(),
        turn_id=ctx.turn.id,
        conversation_id=ctx.turn.conversation_id,
    )
    match posted:
        case Refused(reason=reason):
            return _refusal(reason)
        case Posted(occurrences=1):
            return ToolResult(content=(TextContent(text=NOTIFY_QUEUED),))
        case Posted(occurrences=n):
            return ToolResult(content=(TextContent(text=NOTIFY_FOLDED.format(n=n)),))


NOTIFY_TOOL = ToolDef(
    name=NOTIFY_TOOL_NAME,
    description=NOTIFY_DESCRIPTION,
    input_model=NotifyInput,
    handler=notify,
    side_effecting=True,
)
