"""The memory extension's declared points: the two tools, the recall hook, the index job, the skill.

`memory_search` and `memory_update` are the agent's durable-memory tools; the `on_inbound` hook
auto-injects relevant memory into the turn's context before the model runs; the `memory_index`
JobSpec is the derivation that turns committed items into index chunks on an interval. Recall stays
best-effort under a gating hook: the handler owns a soft timeout below the hook deadline and
swallows every error, returning None rather than ever denying the turn.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, Field

from selfhost.sdk.context import ExtensionContext
from selfhost.sdk.index import TextChunker
from selfhost.sdk.jobs import JobSpec
from selfhost.sdk.manifest import (
    HookContext,
    HookOutcome,
    HookSpec,
    InjectContext,
    Manifest,
    OnInbound,
    SkillSpec,
)
from selfhost.sdk.sources import SHARED_SUBJECT, member_subject
from selfhost.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from selfhost_ext_memory.store import (
    FACT,
    ItemClass,
    MemoryIndexer,
    MemoryWrite,
    Recalled,
    SourceMatch,
    recall_subjects,
    store_for,
)

NAME = "memory"
VERSION = "0.1.0"
MEMORY_SEARCH_LIMIT = 8
MAX_MEMORY_QUERIES = 3
RECALL_LIMIT = 8
RECALL_SOFT_TIMEOUT_SECONDS = 4.0
RECALL_CONTEXT_PREFIX = "Relevant memory:\n"
MEMORY_INDEX_JOB = "memory_index"
MEMORY_INDEX_SCHEDULE = "0 * * * * *"
SKILL_DIR = Path(__file__).parent / "skills" / "memory"

logger = logging.getLogger(__name__)


class MemorySearchInput(BaseModel):
    queries: tuple[str, ...] = Field(min_length=1, max_length=MAX_MEMORY_QUERIES)
    start_date: str | None = None
    end_date: str | None = None


class MemoryUpdateInput(BaseModel):
    body: str
    item_class: ItemClass = FACT
    shared: bool = False
    source_ref: str | None = None


def _date_bound(value: str | None, *, end: bool) -> datetime | None:
    """Parse an ISO-8601 date or datetime to a UTC bound. A bare `end` date covers its whole day —
    the exclusive next midnight — so `[start_date, end_date]` reads inclusively; a malformed value
    raises and surfaces to the model as a recoverable tool error."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    if end and "T" not in value and " " not in value:
        parsed += timedelta(days=1)
    return parsed


async def memory_search_handler(ctx: ToolContext, args: MemorySearchInput) -> ToolResult:
    """Fan the queries out concurrently over recall and source search, then merge each kind by
    keeping every item's best score across the queries that surfaced it and bounding the merged set
    to MEMORY_SEARCH_LIMIT. start_date/end_date, when given, restrict recalled facts to a
    `created_at` window."""
    if ctx.ext is None:
        raise RuntimeError("memory_search dispatched without its ExtensionContext")
    store = store_for(ctx.ext)
    subjects = recall_subjects(ctx.member_id)
    start = _date_bound(args.start_date, end=False)
    end = _date_bound(args.end_date, end=True)
    recalled_legs, source_legs = await asyncio.gather(
        asyncio.gather(
            *(
                store.recall(query, subjects, MEMORY_SEARCH_LIMIT, start, end)
                for query in args.queries
            )
        ),
        asyncio.gather(
            *(store.search_sources(query, subjects, MEMORY_SEARCH_LIMIT) for query in args.queries)
        ),
    )
    recalled: dict[UUID, Recalled] = {}
    for recall_leg in recalled_legs:
        for item in recall_leg:
            if item.memory_id not in recalled or item.score > recalled[item.memory_id].score:
                recalled[item.memory_id] = item
    sources: dict[UUID, SourceMatch] = {}
    for source_leg in source_legs:
        for match in source_leg:
            if match.page_id not in sources or match.score > sources[match.page_id].score:
                sources[match.page_id] = match
    if not recalled and not sources:
        return ToolResult(content=(TextContent(text="No matching memory."),))
    top_recalled = sorted(recalled.values(), key=lambda item: item.score, reverse=True)
    top_sources = sorted(sources.values(), key=lambda match: match.score, reverse=True)
    lines = [f"- [{item.item_class}] {item.body}" for item in top_recalled[:MEMORY_SEARCH_LIMIT]]
    lines.extend(f"- [source] {match.text}" for match in top_sources[:MEMORY_SEARCH_LIMIT])
    return ToolResult(content=(TextContent(text="\n".join(lines)),))


async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("memory_update dispatched without its ExtensionContext")
    subject = (
        SHARED_SUBJECT
        if args.shared or ctx.member_id is None
        else member_subject(ctx.member_id)
    )
    await store_for(ctx.ext).commit(
        MemoryWrite(
            subject=subject, body=args.body, item_class=args.item_class, source_ref=args.source_ref
        )
    )
    return ToolResult(content=(TextContent(text=f"Remembered ({subject})."),))


async def recall_hook(ctx: HookContext) -> HookOutcome:
    """Auto-inject memory relevant to the inbound into the turn's system context. on_inbound is
    gating — a raising or slow handler denies the turn — so recall stays strictly best-effort: it
    runs under its own soft timeout below the hook deadline and swallows every error, returning None
    on any failure or empty result rather than ever failing the turn."""
    if not isinstance(ctx.payload, OnInbound):
        return None
    subjects = recall_subjects(ctx.member_id)
    try:
        async with asyncio.timeout(RECALL_SOFT_TIMEOUT_SECONDS):
            recalled = await store_for(ctx.ext).recall(ctx.payload.text, subjects, RECALL_LIMIT)
    except Exception:
        logger.warning("memory.recall_hook.degraded", exc_info=True)
        return None
    lines = [f"- {item.body}" for item in recalled]
    return InjectContext(RECALL_CONTEXT_PREFIX + "\n".join(lines)) if lines else None


async def index_memory(ctx: ExtensionContext) -> None:
    if ctx.index is None or ctx.embed is None:
        raise RuntimeError("memory_index requires the index and embed backends; none are wired")
    await MemoryIndexer(
        index=ctx.index, embed=ctx.embed, transaction=ctx.transaction, chunker=TextChunker()
    ).run()


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(
            ToolDef(
                name="memory_search",
                description=(
                    "Search memory for facts, notes, and synced source documents, over the current "
                    "member's memory and shared memory. Pass up to "
                    f"{MAX_MEMORY_QUERIES} distinct queries — they run in parallel and their "
                    "results are merged and deduplicated. Optionally restrict to items written "
                    "in a window with start_date/end_date (ISO-8601, e.g. 2026-01-31). Returns the "
                    "best-matching items and document snippets; use it to recall context before "
                    "answering."
                ),
                input_model=MemorySearchInput,
                handler=memory_search_handler,
            ),
            ToolDef(
                name="memory_update",
                description=(
                    "Record a durable memory item so later turns and conversations can recall it. "
                    "Writes to the current member's memory by default, or shared memory when "
                    "`shared` is true. Use proactively when learning persistent facts — name, "
                    "role, company, team, colleagues, preferences, projects, tools, key people, "
                    "communication style. Do NOT store ephemeral instructions (e.g. 'make it "
                    "shorter'); only store persistent information."
                ),
                input_model=MemoryUpdateInput,
                handler=memory_update_handler,
            ),
        ),
        hooks=(HookSpec(event="on_inbound", handler=recall_hook),),
        jobs=(
            JobSpec(name=MEMORY_INDEX_JOB, schedule=MEMORY_INDEX_SCHEDULE, handler=index_memory),
        ),
        skills=(SkillSpec(path=SKILL_DIR),),
    )
