"""`notify`: put one message in the Notification app's inbox for the member speaking this turn.

The tool is the producer half of the back channel. It is in the member-facing set, so every agent
holds it, and it writes one row for the authenticated speaker — the member it concerns is never a
name the model supplies. Nothing is hidden because nothing is
spoken: a tool call never enters a delivered reply, so no redaction stands between this and the
member. What the model sees back is one line, plus the reason when a fence refuses, which is what
makes the fences steerable rather than silent.

Six refusals are structural. A turn with no speaker names no member, and a notification nobody is
the recipient of is not one. The Notification agent's own turns cannot
post: an inbox that mails itself is the loop this whole design must be unable to enter. A spawned
turn cannot post: it was started by another turn to do that turn's work, so what it finds belongs to
the turn that spawned it, which raises what is worth raising — an agent this app woke, and anything
that agent spawns in turn, therefore cannot fill the inbox that woke it, and the fact is the turn's
own rather than a record this extension keeps. And a relay turn — the one a delivery founded in the
member's own conversation — cannot post either, so a delivery can never raise a notification about
itself; that turn is not spawned but admitted into a conversation the member already reads, so the
fence there is the exact turn id the delivery recorded.

A turn whose reply reaches anyone cannot post either. The member it acts for is among the readers —
they spoke there, or set the watch there — so the reply is the telling, and a notification of the
same fact would come back to their own chat as a second copy. Admission stamps `reply_reaches` with
the surface that posts the reply, or `nobody`, so the fence reads neither the body nor the audience.

The sixth is the subject itself: one carrying an identifier minted per event is refused. The
subject is the fold key and a uuid is unique by construction, so such a subject can never fold and
every repeat opens a row of its own. Saying so in the field's description was not enough — a turn
reading pages reached for the page id it had in hand — and the fold is what stands between a member
and four hundred rows about one sync.

The inbox is the agent this extension provisioned, found by that provision and never by
name — a member's own agent may hold `notification`, and the shipped one then lands on a
free variant."""

import re

from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.context import ExtensionContext, TurnRuntimeConfig
from ufo.sdk.surfaces import REPLY_REACHES_NOBODY
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_app_notification.store import (
    BODY_MAX,
    NOTIFICATION_FLAG,
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
    "an email that needs a decision or is going unanswered, a customer or investor turning. It "
    "is also where a broken account of theirs goes, because connecting one is a thing only they "
    "can do: a revoked token, an expired grant, a slot they never filled. That is the line "
    "against `report_problem` — what a member fixes by connecting or reconnecting an account is "
    "a notification, and what only an engineer can fix is a report. Nothing answers back "
    "here and the member may never see it: one agent reads every notification and decides. One "
    "call per subject, whatever the batch size; two different things are two calls. Do not raise "
    "what this turn already told them or a fault you can repair."
)
NOTIFY_NEEDS_A_MEMBER = "this turn has no member speaker, so there is nobody to notify"
NOTIFY_NO_INBOX = "the Notification app is not live in this workspace, so there is no inbox"
NOTIFY_SELF = "the notification agent does not notify itself"
NOTIFY_INSIDE_A_SPAWN = (
    "this turn was spawned to do another turn's work; report what you found in your result, and "
    "the turn that spawned you raises what is worth raising"
)
MINTED_PER_EVENT = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\b[0-9a-f]{32}\b",
    re.IGNORECASE,
)
NOTIFY_UNSTABLE_SUBJECT = (
    "{found!r} is minted per event, so a subject holding it can never fold and every repeat opens "
    "a row of its own. Name the thing itself, as `<source>/<id>`: the pull request as "
    "`github/3132`, the thread as `gmail/<thread id>`, the source as `source/stripe`"
)
NOTIFY_INSIDE_A_DELIVERY = (
    "this turn is delivering a notification; it does not raise one about that"
)
NOTIFY_REPLY_REACHES = "this turn's reply reaches the member on {surface}; say it there"
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
            "The stable thing this is about, as `<source>/<id>` where one exists "
            "(`github/3132`, `gmail/<thread id>`, `source/stripe`). Two messages naming one "
            "subject fold into one, so an identifier minted per event — a page id, a turn id — "
            "is refused."
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
    member_id = ctx.speaker_member_id
    if member_id is None:
        return _refusal(NOTIFY_NEEDS_A_MEMBER)
    if ctx.turn.spawned:
        return _refusal(NOTIFY_INSIDE_A_SPAWN)
    minted = MINTED_PER_EVENT.search(args.subject)
    if minted is not None:
        return _refusal(NOTIFY_UNSTABLE_SUBJECT.format(found=minted.group(0)))
    ext = _require_ext(ctx.ext)
    store = NotificationStore(ext)
    if await store.is_delivery_turn(ctx.turn.id, ctx.turn.idempotency_key):
        return _refusal(NOTIFY_INSIDE_A_DELIVERY)
    inbox = await inbox_agent_id(ext)
    if inbox is None:
        return _refusal(NOTIFY_NO_INBOX)
    if inbox == ctx.turn.agent_id:
        return _refusal(NOTIFY_SELF)
    reaches = None if ctx.turn.context is None else ctx.turn.context.reply_reaches
    if reaches is not None and reaches != REPLY_REACHES_NOBODY:
        return _refusal(NOTIFY_REPLY_REACHES.format(surface=reaches))
    runtime_config = ctx.turn.runtime_config or TurnRuntimeConfig()
    posted = await store.post(
        to_agent_id=inbox,
        member_id=member_id,
        subject=args.subject,
        body=args.body,
        agent_id=ctx.turn.agent_id,
        agent_name=await ext.agent_name(),
        turn_id=ctx.turn.id,
        conversation_id=ctx.turn.conversation_id,
        runtime_config=runtime_config,
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
    flag=NOTIFICATION_FLAG,
)
