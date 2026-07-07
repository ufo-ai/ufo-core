"""Chat-bound watches over the workspace's synced pages.

A member asks for a watch in conversation; the tool binds it to that conversation and agent. The
page_change hook then classifies every changed page against every watch with one bounded, metered
off-turn completion, and a match invokes an alerting turn into the bound conversation — delivered
by the surface's own path (a Slack watch alerts in Slack). The hook is fed only by the source
pipeline, so an alert turn can never re-fire it; the page digest keys idempotency, so a replayed
batch never double-alerts."""

import re
from uuid import UUID

from pydantic import BaseModel

from ufo.sdk.manifest import HookContext, HookOutcome, PageChangeBatch
from ufo.sdk.models import Message, ModelRequest
from ufo.sdk.tools import TextContent, ToolContext, ToolResult

WATCH_PREFIX = "watch:"
PAGE_EXCERPT_CHARS = 2000
CLASSIFY_MAX_TOKENS = 16
MATCH = "MATCH"


class WatchPagesInput(BaseModel):
    topic: str
    name: str = ""


class ListPageWatchesInput(BaseModel):
    pass


class CancelPageWatchInput(BaseModel):
    name: str


def _slug(raw: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    if not slug:
        raise ValueError("a watch needs a name with at least one letter or digit")
    return slug


async def watch_pages(ctx: ToolContext, args: WatchPagesInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("watch_pages dispatched without its ExtensionContext")
    name = _slug(args.name or args.topic)
    await ctx.ext.store.put(
        f"{WATCH_PREFIX}{name}",
        {
            "topic": args.topic,
            "conversation_id": str(ctx.turn.conversation_id),
            "agent_id": str(ctx.turn.agent_id),
        },
    )
    return ToolResult(
        content=(
            TextContent(
                text=(
                    f"Watching synced pages for {args.topic!r} as {name!r} — a matching change "
                    "alerts this conversation."
                )
            ),
        )
    )


async def list_page_watches(ctx: ToolContext, args: ListPageWatchesInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("list_page_watches dispatched without its ExtensionContext")
    watches = await ctx.ext.store.list(WATCH_PREFIX)
    if not watches:
        return ToolResult(content=(TextContent(text="No page watches."),))
    lines = [
        f"{key.removeprefix(WATCH_PREFIX)}: {_watch_fields(key, value)[0]}"
        for key, value in watches
    ]
    return ToolResult(content=(TextContent(text="\n".join(lines)),))


async def cancel_page_watch(ctx: ToolContext, args: CancelPageWatchInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("cancel_page_watch dispatched without its ExtensionContext")
    key = f"{WATCH_PREFIX}{_slug(args.name)}"
    if await ctx.ext.store.get(key) is None:
        raise ValueError(f"no page watch named {args.name!r}")
    await ctx.ext.store.delete(key)
    return ToolResult(content=(TextContent(text=f"Cancelled page watch {args.name!r}."),))


async def on_page_change(ctx: HookContext) -> HookOutcome:
    """Classify each changed page against each watch and invoke an alert turn per match. One
    bounded completion per (page, watch) pair — the batch is feed-capped and the excerpt is
    clipped next to the call."""
    match ctx.payload:
        case PageChangeBatch(changes=changes):
            pass
        case _:
            raise RuntimeError("page_alerts hook fired on a non-page_change payload")
    watches = await ctx.ext.store.list(WATCH_PREFIX)
    if not watches:
        return None
    model = ctx.ext.model
    if model is None:
        raise RuntimeError("page_alerts needs the off-turn model; none is wired")
    for change in changes:
        if change.tombstone:
            continue
        excerpt = change.body[:PAGE_EXCERPT_CHARS]
        for key, value in watches:
            topic, conversation_id, agent_id = _watch_fields(key, value)
            verdict = await model.complete(
                ModelRequest(
                    model="",
                    system=(
                        "You decide whether a changed document concerns a watch topic. "
                        "Answer with exactly MATCH or NO."
                    ),
                    messages=(
                        Message(
                            role="user",
                            content=(
                                f"Watch topic: {topic}\n"
                                f"Document {change.subject!r} begins:\n{excerpt}"
                            ),
                        ),
                    ),
                    max_tokens=CLASSIFY_MAX_TOKENS,
                    reasoning="off",
                )
            )
            if MATCH not in verdict.strip().upper():
                continue
            await ctx.ext.invoke(
                UUID(conversation_id),
                UUID(agent_id),
                (
                    f"A synced page changed and matches your watch {topic!r}: "
                    f"{change.subject!r}. It begins:\n{excerpt}\n\n"
                    "Alert the member in this conversation: say what changed and why it "
                    "matters to the watch."
                ),
                idempotency_key=f"page-alert:{key}:{change.digest}",
            )
    return None


def _watch_fields(key: str, value: object) -> tuple[str, str, str]:
    match value:
        case {"topic": str(topic), "conversation_id": str(conversation), "agent_id": str(agent)}:
            return topic, conversation, agent
        case _:
            raise RuntimeError(f"malformed page watch {key!r}")
