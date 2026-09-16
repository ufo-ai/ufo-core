"""`deliver`: the one verb that reaches a member outside the conversation they are in.

It is a `profile_only` action bound on the `notification` kind, so the member-facing set, every
prepared intent, and every unnamed subagent profile withhold it; the only allowlist naming it is
the Notification app's own provision, and an allowlist is declared, never typed. The handler
re-checks that the calling agent is this extension's provision, so a second extension naming the
same id in its own allowlist fails loud rather than widening the fence in silence.

The handler picks the channel, not the model: the member's own direct chat, Slack first and iMessage
after it (`REACH_SURFACES`), never a channel thread they happen to have spoken in — a notification
is addressed to one member, and a room their colleagues read is the wrong place for it however
recently they typed there. Delivery is an automatic turn invoked in that chat with the
notification's exact capabilities — admission registers the writeback because the surface is
durable, so the poller posts it and the member replies to it in the chat they already use. A
conversation whose agent was archived between the read and the invoke is skipped for the next; a
member with neither chat is not pushed, and the rows are marked delivered to the portal alone,
where the kind already lists them.

The relay turn holds the skip the app cannot: the member's own agent reads their standing orders and
what it already said to them, so the instruction gives it the silence sentinel as a whole reply and
the surface posts nothing. The rows still count as delivered, because the app spent the batch on
them and the portal keeps them readable.

Two bounds hold the push rate by structure. An append-only delivery row reserves one exact request
and destination under the delivering turn's idempotency key before relay admission: a different
second request is refused, and a matching replay uses that destination and resolves the same relay.
The relay id is bound there independently of the folded inbox rows, while those rows are marked only
at the occurrences the request read. `notify` refuses inside a turn whose key or id is that delivery
identity, so a fold cannot erase the loop fence and the relay cannot race ahead of it."""

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.audience import audience_member
from ufo.sdk.context import AgentArchived, ExtensionContext
from ufo.sdk.surfaces import SILENCE_SENTINEL
from ufo.sdk.tools import ObjectBinding, TextContent, ToolContext, ToolDef, ToolResult
from ufo.sdk.untrusted import wall
from ufo_ext_app_notification.drain import DRAIN_BATCH
from ufo_ext_app_notification.store import (
    NOTIFICATION_FLAG,
    NOTIFICATION_KIND,
    DeliveryDestination,
    DeliveryRequestConflict,
    Notification,
    NotificationStore,
    inbox_agent_id,
)

DELIVER_ACTION_NAME = "deliver"
DELIVER_ACTION_ID = f"action:{NOTIFICATION_KIND}:{DELIVER_ACTION_NAME}"
MESSAGE_MAX = 1200
PORTAL_ONLY = "portal"
RELAY_KEY = "notify-deliver:{turn}"
RELAY_SOURCE = "notification"
REACH_SURFACES = ("slack", "imessage")
RELAY_INSTRUCTION = (
    "\nThe Notification app decided the member should hear this now. Say it to them in your own "
    "voice, in one message: what happened, what it means for them, and the one thing you could do "
    "about it if they want it. Do nothing else until they answer."
    "\nSay nothing at all when a standing order of theirs covers this, or when you already told "
    f"them the same thing: write {SILENCE_SENTINEL} as your whole reply and the member reads "
    "nothing. A member who said to stop reporting CI failures on ufo while it is broken gets no "
    "CI failure from you, and no message about staying quiet either: a message that says you are "
    "silent is not silence."
)
DELIVER_DESCRIPTION = (
    "Brief the member's own agent on the notifications named in `refs`, in the member's own "
    "direct chat with it. `text` is what that agent is told, not what the member reads: it says "
    "the message in its own voice, in the conversation it already has with them. Say what "
    "happened and what it means for them, and leave the wording to it. That agent says nothing "
    "at all when a standing order of the member's covers what you sent. Every call costs the "
    "member a message: use it for what they would act on today, and at most once per batch. "
    "Notifications you do not name stay readable on the portal and nowhere else."
)
NOT_THE_NOTIFICATION_AGENT = (
    "deliver is the Notification app's own verb; this agent is not that app's provision"
)
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
    "The member has no direct chat with the agent, so nothing was pushed; the notifications stay "
    "readable on the portal and are marked delivered there."
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
            "What the member's own agent is told, in your words: what happened and what it "
            "means for them. That agent says it to them in its own voice; this is not the "
            "message they read."
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


def _delivery_request_digest(rows: tuple[Notification, ...], text: str) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "rows": sorted((row.id.hex, row.occurrences) for row in rows),
                "text": text,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).hexdigest()


async def deliver(ctx: ToolContext, args: DeliverInput) -> ToolResult:
    ext = _require_ext(ctx.ext)
    await _require_notification_agent(ext, ctx)
    member_id = audience_member(ctx.audience)
    if member_id is None:
        return _refusal(NOTHING_TO_DELIVER)
    store = NotificationStore(ext)
    rows = await store.deliverable(member_id, _names(args.refs))
    rows = tuple(row for row in rows if row.runtime_config == ctx.turn.runtime_config)
    if not rows:
        return _refusal(NOTHING_TO_DELIVER)
    delivery_key = RELAY_KEY.format(turn=ctx.turn.id.hex)
    request_digest = _delivery_request_digest(rows, args.text)
    try:
        destination = await store.delivery_destination(delivery_key, request_digest)
    except DeliveryRequestConflict:
        return _refusal(ONE_DELIVERY_PER_TURN)
    if destination is None:
        reaches = await ext.member_reach(member_id, REACH_SURFACES)
        if not reaches:
            await store.mark_delivered(rows, turn_id=None, surface=PORTAL_ONLY)
            return ToolResult(content=(TextContent(text=DELIVERED_TO_PORTAL_ONLY),))
        selected = reaches[0]
        try:
            destination = await store.prepare_delivery(
                delivery_key,
                request_digest,
                DeliveryDestination(
                    conversation_id=selected.conversation_id,
                    agent_id=selected.agent_id,
                    surface=selected.surface,
                ),
            )
        except DeliveryRequestConflict:
            return _refusal(ONE_DELIVERY_PER_TURN)
    attempted: set[DeliveryDestination] = set()
    while destination not in attempted:
        attempted.add(destination)
        try:
            turn_id = await ext.invoke(
                destination.conversation_id,
                destination.agent_id,
                wall(RELAY_SOURCE, args.text) + RELAY_INSTRUCTION,
                delivery_key,
                holds_work_already_done=True,
                as_scheduled=True,
                runtime_config=ctx.turn.runtime_config,
                acting_member_id=member_id,
            )
        except AgentArchived:
            remaining = tuple(
                reach
                for reach in await ext.member_reach(member_id, REACH_SURFACES)
                if DeliveryDestination(
                    conversation_id=reach.conversation_id,
                    agent_id=reach.agent_id,
                    surface=reach.surface,
                )
                not in attempted
            )
            if not remaining:
                break
            replacement = remaining[0]
            moved = await store.move_delivery_destination(
                delivery_key,
                destination,
                DeliveryDestination(
                    conversation_id=replacement.conversation_id,
                    agent_id=replacement.agent_id,
                    surface=replacement.surface,
                ),
            )
            if moved is None:
                return _refusal(NOT_DELIVERED)
            destination = moved
            continue
        if turn_id is None:
            return _refusal(NOT_DELIVERED)
        await store.bind_delivery_turn(delivery_key, turn_id)
        await store.mark_delivered(rows, turn_id=turn_id, surface=destination.surface)
        return ToolResult(
            content=(TextContent(text=DELIVERED.format(surface=destination.surface)),)
        )
    await store.mark_delivered(rows, turn_id=None, surface=PORTAL_ONLY)
    return ToolResult(content=(TextContent(text=DELIVERED_TO_PORTAL_ONLY),))


DELIVER = ToolDef(
    name=DELIVER_ACTION_NAME,
    description=DELIVER_DESCRIPTION,
    input_model=DeliverInput,
    handler=deliver,
    profile_only=True,
    binds_member_authority=False,
    side_effecting=True,
    bound=ObjectBinding(kind=NOTIFICATION_KIND, binding="collection"),
    flag=NOTIFICATION_FLAG,
)
