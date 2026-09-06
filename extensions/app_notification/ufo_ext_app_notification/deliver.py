"""`deliver`: the one verb that reaches a member outside the conversation they are in.

It is a `profile_only` action bound on the `notification` kind, so the member-facing set, every
prepared intent, and every unnamed subagent profile withhold it; the only allowlist naming it is
the Notification app's own provision, and an allowlist is declared, never typed. The handler
re-checks that the calling agent is this extension's provision, so a second extension naming the
same id in its own allowlist fails loud rather than widening the fence in silence.

The handler picks the channel, not the model: the member's newest durable-surface conversation
they personally spoke in (`member_reach`), which is the founders' "best channel by last usage" as a
sort. Delivery is a turn invoked there under the member's own authority — admission registers the
writeback because the surface is durable, so the poller posts it and the member replies to it in the
thread they already use. A conversation whose agent was archived between the read and the invoke
is skipped for the next; a member with no durable conversation is not pushed, and the rows are
marked delivered to the portal alone, where the kind already lists them.

Two bounds hold the push rate by structure. The relay's idempotency key is the delivering turn, so a
second `deliver` in one turn admits nothing new: admission answers the relay the first call founded,
the handler finds that turn already recorded on rows and refuses, and the second call's rows stay
undelivered. And the relay turn is recorded on every row it carried, which is the loop fence:
`notify` refuses inside any turn found there, so a delivery cannot raise a notification about
itself."""

from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.authority import MemberAuthority, authority_member_id
from ufo.sdk.context import AgentArchived, ExtensionContext
from ufo.sdk.tools import ObjectBinding, TextContent, ToolContext, ToolDef, ToolResult
from ufo.sdk.untrusted import wall
from ufo_ext_app_notification.drain import DRAIN_BATCH
from ufo_ext_app_notification.store import (
    NOTIFICATION_KIND,
    NotificationStore,
    inbox_agent_id,
    notifications_enabled,
)

DELIVER_ACTION_NAME = "deliver"
DELIVER_ACTION_ID = f"action:{NOTIFICATION_KIND}:{DELIVER_ACTION_NAME}"
MESSAGE_MAX = 1200
PORTAL_ONLY = "portal"
RELAY_KEY = "notify-deliver:{turn}"
RELAY_SOURCE = "notification"
RELAY_INSTRUCTION = (
    "\nThe Notification app decided the member should hear this now. Say it to them in your own "
    "voice, in one message, and stop; do not act on it."
)
DELIVER_DESCRIPTION = (
    "Tell the member the notifications named in `refs`, as one message, on the chat surface they "
    "used most recently. Every call is a message a person reads: use it for what they would act "
    "on today, and at most once per batch. Notifications you do not name stay readable on the "
    "portal and nowhere else."
)
NOT_THE_NOTIFICATION_AGENT = (
    "deliver is the Notification app's own verb; this agent is not that app's provision"
)
NOTIFICATIONS_OFF = "notifications are switched off in this workspace, so nothing is delivered"
ONE_DELIVERY_PER_TURN = (
    "this turn already delivered one message; a member who hears from you twice in one batch "
    "stops reading you"
)
NOTHING_TO_DELIVER = (
    "none of those refs is an undelivered notification for this member; name refs from the batch "
    "you were handed"
)
NOT_DELIVERED = "the member's conversation refused the turn, so nothing was delivered"
DELIVERED_TO_PORTAL_ONLY = (
    "The member has no chat conversation on a durable surface, so nothing was pushed; the "
    "notifications stay readable on the portal and are marked delivered there."
)
DELIVERED = "Delivered on {surface}."


class DeliverInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    refs: tuple[str, ...] = Field(
        min_length=1,
        max_length=DRAIN_BATCH,
        description="The notification refs this message covers, as the batch named them.",
    )
    text: str = Field(
        min_length=1,
        max_length=MESSAGE_MAX,
        description=(
            "What the member reads, in your words: what happened and what it means for them."
        ),
    )


def _require_ext(ext: ExtensionContext | None) -> ExtensionContext:
    if ext is None:
        raise RuntimeError("deliver requires the app_notification ExtensionContext")
    return ext


def _refusal(text: str) -> ToolResult:
    return ToolResult(content=(TextContent(text=text),), is_error=True)


async def _require_notification_agent(ext: ExtensionContext, ctx: ToolContext) -> None:
    if await inbox_agent_id(ext) != ctx.turn.agent_id:
        raise RuntimeError(NOT_THE_NOTIFICATION_AGENT)


def _names(refs: tuple[str, ...]) -> tuple[str, ...]:
    prefix = f"{NOTIFICATION_KIND}/"
    return tuple(ref.removeprefix(prefix) for ref in refs)


async def deliver(ctx: ToolContext, args: DeliverInput) -> ToolResult:
    ext = _require_ext(ctx.ext)
    await _require_notification_agent(ext, ctx)
    if not await notifications_enabled():
        return _refusal(NOTIFICATIONS_OFF)
    member_id = authority_member_id(ctx.authority)
    if member_id is None:
        return _refusal(NOTHING_TO_DELIVER)
    store = NotificationStore(ext)
    rows = await store.deliverable(member_id, _names(args.refs))
    if not rows:
        return _refusal(NOTHING_TO_DELIVER)
    for chosen in await ext.member_reach(member_id):
        try:
            turn_id = await ext.invoke(
                chosen.conversation_id,
                chosen.agent_id,
                wall(RELAY_SOURCE, args.text) + RELAY_INSTRUCTION,
                RELAY_KEY.format(turn=ctx.turn.id.hex),
                authority=MemberAuthority(member_id),
                holds_work_already_done=True,
                as_scheduled=True,
            )
        except AgentArchived:
            continue
        if turn_id is None:
            return _refusal(NOT_DELIVERED)
        if await store.is_delivery_turn(turn_id):
            return _refusal(ONE_DELIVERY_PER_TURN)
        await store.mark_delivered(rows, turn_id=turn_id, surface=chosen.surface)
        return ToolResult(content=(TextContent(text=DELIVERED.format(surface=chosen.surface)),))
    await store.mark_delivered(rows, turn_id=None, surface=PORTAL_ONLY)
    return ToolResult(content=(TextContent(text=DELIVERED_TO_PORTAL_ONLY),))


DELIVER = ToolDef(
    name=DELIVER_ACTION_NAME,
    description=DELIVER_DESCRIPTION,
    input_model=DeliverInput,
    handler=deliver,
    profile_only=True,
    side_effecting=True,
    bound=ObjectBinding(kind=NOTIFICATION_KIND, binding="collection"),
)
