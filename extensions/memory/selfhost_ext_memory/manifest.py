"""The memory extension's declared points: the two tools, the recall hook, two page-change
consumers, two derivation jobs, the skill.

`memory_search` and `memory_update` are the agent's durable-memory tools; the `user_prompt_submit`
hook auto-injects relevant memory into the turn's context before the model runs. Two `page_change`
hooks ride independent core-runner cursors: `index_pages` turns each replayed source-page change
into index chunks + a mirror row, and `derive_facts` distills each into durable `fact`
memory_items with a bounded metered model pass. Two JobSpecs run the interval derivations:
`memory_index` turns committed items into index chunks, and `memory_consolidate` clusters aged
facts into `semantic` summaries that supersede their originals. Recall stays best-effort under a
gating hook: the handler owns a soft timeout below the hook deadline and swallows every error,
returning None rather than ever denying the turn.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from itertools import zip_longest
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
    PageChangeBatch,
    SkillSpec,
    UserPromptSubmit,
)
from selfhost.sdk.sources import SHARED_SUBJECT, member_subject
from selfhost.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from selfhost_ext_memory.condenser import FactDeriver, MemoryConsolidator
from selfhost_ext_memory.store import (
    DEFAULT_CONFIDENCE,
    FACT,
    KIND_FACT,
    MAX_CONFIDENCE,
    ItemClass,
    MemoryIndexer,
    MemoryKind,
    MemoryWrite,
    PageIndexer,
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
CONSOLIDATE_JOB = "memory_consolidate"
CONSOLIDATE_SCHEDULE = "0 0 * * * *"
SKILL_DIR = Path(__file__).parent / "skills" / "memory"

logger = logging.getLogger(__name__)


class MemorySearchInput(BaseModel):
    queries: tuple[str, ...] = Field(
        min_length=1,
        max_length=MAX_MEMORY_QUERIES,
        description="Up to 3 distinct queries, each targeting ONE topic — multiple focused queries "
        "beat one vague catch-all. They run in parallel and their results are merged.",
    )
    start_date: str | None = Field(
        default=None,
        description="Optional ISO-8601 start of a created-at window to restrict results, e.g. "
        "'2026-01-31'.",
    )
    end_date: str | None = Field(
        default=None,
        description="Optional ISO-8601 end of the window; a bare date covers its whole day.",
    )
    user_description: str | None = Field(
        default=None,
        description="Brief plain-language description shown in the activity timeline.",
    )


class MemoryUpdateInput(BaseModel):
    body: str = Field(
        description="A durable fact to remember about the user, written from their perspective "
        "(e.g. 'I prefer concise summaries'). Store persistent facts — role, company, team, "
        "preferences, projects, key people — never ephemeral instructions like 'make it shorter'."
    )
    item_class: ItemClass = Field(
        default=FACT, description="The memory item class; defaults to a fact."
    )
    memory_kind: MemoryKind = Field(
        default=KIND_FACT,
        description="Memory kind (fact/preference/decision/event/task), which sets the "
        "recency-decay half-life; defaults to fact.",
    )
    confidence: int = Field(
        default=DEFAULT_CONFIDENCE,
        ge=1,
        le=MAX_CONFIDENCE,
        description="Confidence 1-10 in the fact; scales its recall score.",
    )
    shared: bool = Field(
        default=False,
        description="Store as shared workspace memory rather than the speaking member's private "
        "memory.",
    )
    source_ref: str | None = Field(
        default=None, description="Optional reference to the source this fact came from."
    )
    user_description: str | None = Field(
        default=None,
        description="Brief plain-language description shown in the activity timeline.",
    )


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
    interleaving the per-query results round-robin — each query's top hit, then each query's
    second, and so on — deduped and bounded to MEMORY_SEARCH_LIMIT, so every query gets fair
    representation rather than one high-scoring query crowding the others out. start_date/end_date,
    when given, restrict recalled facts to a `created_at` window."""
    if ctx.ext is None:
        raise RuntimeError("memory_search dispatched without its ExtensionContext")
    store = store_for(ctx.ext)
    subjects = recall_subjects(ctx.member_id)
    start = _date_bound(args.start_date, end=False)
    end = _date_bound(args.end_date, end=True)
    recall_batch = asyncio.gather(
        *(store.recall(query, subjects, MEMORY_SEARCH_LIMIT, start, end) for query in args.queries)
    )
    source_batch = asyncio.gather(
        *(
            store.search_sources(query, subjects, MEMORY_SEARCH_LIMIT, start, end)
            for query in args.queries
        )
    )
    recalled_legs: list[tuple[Recalled, ...]] = await recall_batch
    source_legs: list[tuple[SourceMatch, ...]] = await source_batch
    recalled: dict[UUID, Recalled] = {}
    for recall_tier in zip_longest(*recalled_legs):
        for item in recall_tier:
            if item is not None and item.memory_id not in recalled:
                recalled[item.memory_id] = item
    sources: dict[UUID, SourceMatch] = {}
    for source_tier in zip_longest(*source_legs):
        for match in source_tier:
            if match is not None and match.page_id not in sources:
                sources[match.page_id] = match
    if not recalled and not sources:
        return ToolResult(content=(TextContent(text="No matching memory."),))
    lines = [
        f"- [{item.item_class}] {item.body}"
        for item in list(recalled.values())[:MEMORY_SEARCH_LIMIT]
    ]
    lines.extend(
        f"- [source] {match.text}" for match in list(sources.values())[:MEMORY_SEARCH_LIMIT]
    )
    return ToolResult(content=(TextContent(text="\n".join(lines)),))


async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("memory_update dispatched without its ExtensionContext")
    subject = (
        SHARED_SUBJECT if args.shared or ctx.member_id is None else member_subject(ctx.member_id)
    )
    await store_for(ctx.ext).commit(
        MemoryWrite(
            subject=subject,
            body=args.body,
            item_class=args.item_class,
            memory_kind=args.memory_kind,
            confidence=args.confidence,
            source_ref=args.source_ref,
        )
    )
    return ToolResult(content=(TextContent(text=f"Remembered ({subject})."),))


async def recall_hook(ctx: HookContext) -> HookOutcome:
    """Auto-inject memory relevant to the inbound into the turn's system context. user_prompt_submit
    is gating — a raising or slow handler denies the turn — so recall stays strictly best-effort: it
    runs under its own soft timeout below the hook deadline and swallows every error, returning None
    on any failure or empty result rather than ever failing the turn."""
    if not isinstance(ctx.payload, UserPromptSubmit):
        return None
    subjects = recall_subjects(ctx.member_id)
    try:
        async with asyncio.timeout(RECALL_SOFT_TIMEOUT_SECONDS):
            recalled = await store_for(ctx.ext).recall(ctx.payload.text, subjects, RECALL_LIMIT)
    except Exception:
        logger.warning("memory.recall_hook.degraded", exc_info=True)
        return None
    lines = [f"- {item.body}" for item in recalled if item.recall_mode != "topic"]
    return InjectContext(RECALL_CONTEXT_PREFIX + "\n".join(lines)) if lines else None


async def index_memory(ctx: ExtensionContext) -> None:
    if ctx.index is None or ctx.embed is None:
        raise RuntimeError("memory_index requires the index and embed backends; none are wired")
    await MemoryIndexer(
        index=ctx.index, embed=ctx.embed, transaction=ctx.transaction, chunker=TextChunker()
    ).run()


async def index_pages(ctx: HookContext) -> HookOutcome:
    """The `page_change` consumer: derive index chunks + a `mem_page` mirror from each replayed
    source-page change the core runner delivers. The runner owns the cursor and batch loop; this
    applies one delivered batch through the extension's jobs-way context (index + embed wired)."""
    if not isinstance(ctx.payload, PageChangeBatch):
        return None
    if ctx.ext.index is None or ctx.ext.embed is None:
        raise RuntimeError("page_change indexing requires the index and embed backends; none wired")
    await PageIndexer(
        index=ctx.ext.index,
        embed=ctx.ext.embed,
        transaction=ctx.ext.transaction,
        chunker=TextChunker(),
    ).apply(ctx.payload.changes)
    return None


async def derive_facts(ctx: HookContext) -> HookOutcome:
    """The second `page_change` consumer: distill each replayed source-page change into durable
    `fact` memory_items with one bounded metered model pass per batch. Rides its own cursor,
    independent of the indexer's; fail-soft when no model is wired (the batch is skipped, the
    cursor still advances)."""
    if not isinstance(ctx.payload, PageChangeBatch):
        return None
    await FactDeriver(store=store_for(ctx.ext), model=ctx.ext.model).apply(ctx.payload.changes)
    return None


async def consolidate_memory(ctx: ExtensionContext) -> None:
    if ctx.embed is None:
        raise RuntimeError("memory_consolidate requires the embed backend; none is wired")
    await MemoryConsolidator(
        embed=ctx.embed,
        transaction=ctx.transaction,
        workspace_id=ctx.store.workspace_id,
        model=ctx.model,
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
                    "communication style. Set `memory_kind` (fact/preference/decision/event/task) "
                    "so recency decay matches how fast the fact goes stale, and `confidence` "
                    "(1-10) for how sure you are. Do NOT store ephemeral instructions (e.g. 'make "
                    "it shorter'); only store persistent information."
                ),
                input_model=MemoryUpdateInput,
                handler=memory_update_handler,
            ),
        ),
        hooks=(
            HookSpec(event="user_prompt_submit", handler=recall_hook),
            HookSpec(event="page_change", handler=index_pages),
            HookSpec(event="page_change", handler=derive_facts),
        ),
        jobs=(
            JobSpec(name=MEMORY_INDEX_JOB, schedule=MEMORY_INDEX_SCHEDULE, handler=index_memory),
            JobSpec(
                name=CONSOLIDATE_JOB, schedule=CONSOLIDATE_SCHEDULE, handler=consolidate_memory
            ),
        ),
        skills=(SkillSpec(path=SKILL_DIR),),
    )
