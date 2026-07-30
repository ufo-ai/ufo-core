"""The memory extension's declared points: the two tools, the `memory` object kind, the recall
hook, two page-change consumers, two derivation jobs, the skill.

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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import zip_longest
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field

from ufo.sdk.context import ExtensionContext, SourceReader
from ufo.sdk.index import TextChunker
from ufo.sdk.jobs import JobSpec, owner_candidates
from ufo.sdk.manifest import (
    HookContext,
    HookOutcome,
    HookSpec,
    InjectContext,
    Manifest,
    MemorySearchProviderSpec,
    PageChangeBatch,
    SkillSpec,
    UserPromptSubmit,
)
from ufo.sdk.memory import DEFAULT_MEMORY_SEARCH_PROVIDER, MemoryMatch
from ufo.sdk.o11y import log
from ufo.sdk.objects import ObjectRef
from ufo.sdk.operator import resolve_operator_workspace
from ufo.sdk.surfaces import SurfaceSpec
from ufo.sdk.tools import TextContent, ToolContext, ToolDef, ToolResult
from ufo_ext_memory.condenser import (
    MIN_CLUSTER_FACTS,
    MIN_OLDEST_AGE,
    FactDeriver,
    MemoryConsolidator,
)
from ufo_ext_memory.events import (
    MAX_RECALL_ERROR_CLASS_CHARS,
    MAX_RECALLED_MEMORY_IDS,
    MEMORY_RECALL_EVENT,
)
from ufo_ext_memory.objects import MEMORY_KIND, MEMORY_OBJECT, PAGE_OBJECT_KIND
from ufo_ext_memory.store import (
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
    _aware,
    memory_item,
    recall_subjects,
    store_for,
)
from ufo_ext_memory.surface import ROUTES as MEMORY_ROUTES
from ufo_ext_memory.surface import SURFACE_MEMORY

NAME = "memory"
VERSION = "0.1.0"
MEMORY_SEARCH_LIMIT = 8
MAX_MEMORY_QUERIES = 3
RECALL_LIMIT = MAX_RECALLED_MEMORY_IDS
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
    user_description: str = Field(
        description="What you are trying to recall about them, in plain language for the activity "
        "timeline."
    )


class MemoryUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    source_ref: str | None = Field(
        default=None, description="Optional reference to the source this fact came from."
    )
    user_description: str = Field(
        description="What you are remembering about them, in plain language for the activity "
        "timeline."
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


@dataclass(frozen=True)
class MemorySearchService:
    """The one multi-query search workflow used by tools and dependent extensions."""

    ctx: ExtensionContext

    async def search(
        self,
        queries: tuple[str, ...],
        reader: SourceReader,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[MemoryMatch, ...]:
        if not 1 <= len(queries) <= MAX_MEMORY_QUERIES:
            raise ValueError(f"memory search requires 1-{MAX_MEMORY_QUERIES} queries")
        store = store_for(self.ctx)
        subjects = reader.subjects
        recall_batch = asyncio.gather(
            *(
                store.recall(
                    query,
                    subjects,
                    MEMORY_SEARCH_LIMIT,
                    start,
                    end,
                    source_reader=reader,
                )
                for query in queries
            )
        )
        source_batch = asyncio.gather(
            *(
                store.search_sources(
                    query,
                    subjects,
                    MEMORY_SEARCH_LIMIT,
                    start,
                    end,
                    source_reader=reader,
                )
                for query in queries
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
        matches = tuple(
            MemoryMatch(
                kind=item.item_class,
                text=item.body,
                ref=ObjectRef(kind=MEMORY_KIND, name=str(item.memory_id)),
                created_at=item.created_at,
            )
            for item in list(recalled.values())[:MEMORY_SEARCH_LIMIT]
        )
        return matches + tuple(
            MemoryMatch(
                kind="source",
                text=match.text,
                ref=ObjectRef(kind=PAGE_OBJECT_KIND, name=str(match.page_id)),
                created_at=match.created_at,
            )
            for match in list(sources.values())[:MEMORY_SEARCH_LIMIT]
        )

    async def list_recent(self, subjects: frozenset[str], limit: int) -> tuple[MemoryMatch, ...]:
        """The newest live memory items the subjects may read — the browse half of the seam: a
        bounded index-backed scan, no query, no similarity, superseded rows excluded. Source pages
        are search's alone; a listing of everything synced would be a page dump, not memory."""
        async with self.ctx.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        memory_item.c.id,
                        memory_item.c.body,
                        memory_item.c.item_class,
                        memory_item.c.created_at,
                    )
                    .where(
                        memory_item.c.workspace_id == self.ctx.store.workspace_id,
                        memory_item.c.subject.in_(subjects),
                        memory_item.c.superseded_by.is_(None),
                    )
                    .order_by(memory_item.c.created_at.desc(), memory_item.c.id)
                    .limit(limit)
                )
            ).all()
        return tuple(
            MemoryMatch(
                kind=row.item_class,
                text=row.body,
                ref=ObjectRef(kind=MEMORY_KIND, name=str(row.id)),
                created_at=_aware(row.created_at),
            )
            for row in rows
        )


def match_line(match: MemoryMatch) -> str:
    """One hit as the agent triages it: snippet first, then the durable ref and recency —
    `object_get` the ref to open the full memory or page behind the hit."""
    line = f"- [{match.kind}] {match.text}"
    if match.ref is None:
        return line
    stamp = "" if match.created_at is None else f", {match.created_at.date().isoformat()}"
    return f"{line} ({match.ref}{stamp})"


async def memory_search_handler(ctx: ToolContext, args: MemorySearchInput) -> ToolResult:
    """Fan the queries out concurrently over recall and source search, then merge each kind by
    interleaving the per-query results round-robin — each query's top hit, then each query's
    second, and so on — deduped and bounded to MEMORY_SEARCH_LIMIT, so every query gets fair
    representation rather than one high-scoring query crowding the others out. start_date/end_date,
    when given, restrict recalled facts to a `created_at` window."""
    if ctx.ext is None:
        raise RuntimeError("memory_search dispatched without its ExtensionContext")
    start = _date_bound(args.start_date, end=False)
    end = _date_bound(args.end_date, end=True)
    matches = await MemorySearchService(ctx.ext).search(
        args.queries,
        ctx.source_reader(),
        start,
        end,
    )
    if not matches:
        return ToolResult(content=(TextContent(text="No matching memory."),))
    return ToolResult(
        content=(TextContent(text="\n".join(match_line(match) for match in matches)),)
    )


async def memory_update_handler(ctx: ToolContext, args: MemoryUpdateInput) -> ToolResult:
    if ctx.ext is None:
        raise RuntimeError("memory_update dispatched without its ExtensionContext")
    subject = str(ctx.effective_audience)
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
    if ctx.turn is None:
        return None
    subjects = recall_subjects(ctx.audience)
    reader = SourceReader(
        agent_id=ctx.turn.agent_id,
        requesting_member_id=None,
        subjects=subjects,
    )
    recalled: tuple[Recalled, ...] = ()
    error_class: str | None = None
    try:
        async with asyncio.timeout(RECALL_SOFT_TIMEOUT_SECONDS):
            recalled = await store_for(ctx.ext).recall(
                ctx.payload.text,
                subjects,
                RECALL_LIMIT,
                source_reader=reader,
            )
    except Exception as error:
        error_class = type(error).__name__[:MAX_RECALL_ERROR_CLASS_CHARS]
        logger.warning("memory.recall_hook.degraded", exc_info=True)
    injected = tuple(item for item in recalled if item.recall_mode != "topic")
    if ctx.turn is not None:
        try:
            log(
                MEMORY_RECALL_EVENT,
                turn_id=str(ctx.turn.id),
                memory_ids=[str(item.memory_id) for item in injected],
                **({"error_class": error_class} if error_class is not None else {}),
            )
        except Exception:
            logger.warning("memory.recall_log_failed", exc_info=True)
    if error_class is not None:
        return None
    lines = [f"- {item.body}" for item in injected]
    return InjectContext(RECALL_CONTEXT_PREFIX + "\n".join(lines)) if lines else None


async def index_memory(ctx: ExtensionContext) -> None:
    if ctx.index is None or ctx.embed is None:
        raise RuntimeError("memory_index requires the index and embed backends; none are wired")
    await MemoryIndexer(
        index=ctx.index,
        embed=ctx.embed,
        transaction=ctx.transaction,
        chunker=TextChunker(),
        page_states=ctx.page_states,
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
        workspace_id=ctx.ext.store.workspace_id,
        page_states=ctx.ext.page_states,
    ).apply(ctx.payload.changes)
    return None


async def derive_facts(ctx: HookContext) -> HookOutcome:
    """The second `page_change` consumer: distill each replayed source-page change into durable
    `fact` memory_items with one bounded metered model pass per batch, then retire the facts each
    committed replacement replaced. Rides its own cursor, independent of the indexer's, and is the
    one writer that removes a page-derived fact — so with no model wired it fails loud and the
    cursor holds, rather than advancing past pages whose replacements were never derived."""
    if not isinstance(ctx.payload, PageChangeBatch):
        return None
    if ctx.ext.model is None:
        raise RuntimeError("fact derivation requires the model seam; none is wired")
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


def _items_awaiting_index() -> sa.Select[tuple[UUID]]:
    return (
        sa.select(memory_item.c.workspace_id)
        .where(memory_item.c.embedding_digest.is_(None))
        .distinct()
    )


def _consolidatable_workspaces() -> sa.Select[tuple[UUID]]:
    """Workspaces where a consolidation pass could actually form a cluster: at least
    MIN_CLUSTER_FACTS live facts aged past MIN_OLDEST_AGE — the consolidator's own floor, folded
    into the candidate read so a workspace whose facts are all young, superseded, or too few is
    never bound on the hourly tick. Built per tick, so the age cutoff tracks `now`."""
    cutoff = datetime.now(UTC) - MIN_OLDEST_AGE
    return (
        sa.select(memory_item.c.workspace_id)
        .where(
            memory_item.c.item_class == FACT,
            memory_item.c.created_from_page_id.is_(None),
            memory_item.c.superseded_by.is_(None),
            memory_item.c.created_at <= cutoff,
        )
        .group_by(memory_item.c.workspace_id)
        .having(sa.func.count() >= MIN_CLUSTER_FACTS)
    )


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        tools=(
            ToolDef(
                name="memory_search",
                description=(
                    "Search memory for facts, notes, and synced source documents, over the "
                    "shared audience plus the exact member making this request. An explicit "
                    "member request may search that member's private memory and sources even in a "
                    "shared conversation; never treat that access as ambient for other members. "
                    "Pass up to "
                    f"{MAX_MEMORY_QUERIES} distinct queries — they run in parallel and their "
                    "results are merged and deduplicated. Optionally restrict to items written "
                    "in a window with start_date/end_date (ISO-8601, e.g. 2026-01-31). Returns "
                    "the best-matching items and document snippets, each with its object ref "
                    "(memory/<id> or page/<id>) and date — object_get a ref to open the full "
                    "item or page and follow its provenance links. Use it to recall context "
                    "before answering."
                ),
                input_model=MemorySearchInput,
                handler=memory_search_handler,
            ),
            ToolDef(
                name="memory_update",
                description=(
                    "Record a durable memory item so later turns and conversations can recall it. "
                    "Writes only to the current conversation audience: a private conversation "
                    "writes that member's memory; a shared conversation writes shared memory. "
                    "Use proactively when learning persistent facts — name, "
                    "role, company, team, colleagues, preferences, projects, tools, key people, "
                    "communication style. Set `memory_kind` (fact/preference/decision/event/task) "
                    "so recency decay matches how fast the fact goes stale, and `confidence` "
                    "(1-10) for how sure you are. Do NOT store ephemeral instructions (e.g. 'make "
                    "it shorter'); only store persistent information. Task-execution state and "
                    "per-run/scheduled-run output are NOT durable memory — keep in-task working "
                    "state in workspace files or todo items, and let per-run output live in the "
                    "delivered post or artifact, not here."
                ),
                input_model=MemoryUpdateInput,
                handler=memory_update_handler,
            ),
        ),
        objects=(MEMORY_OBJECT,),
        hooks=(
            HookSpec(event="user_prompt_submit", handler=recall_hook),
            HookSpec(event="page_change", handler=index_pages),
            HookSpec(event="page_change", handler=derive_facts),
        ),
        jobs=(
            JobSpec(
                name=MEMORY_INDEX_JOB,
                schedule=MEMORY_INDEX_SCHEDULE,
                handler=index_memory,
                candidates=owner_candidates(_items_awaiting_index),
            ),
            JobSpec(
                name=CONSOLIDATE_JOB,
                schedule=CONSOLIDATE_SCHEDULE,
                handler=consolidate_memory,
                candidates=owner_candidates(_consolidatable_workspaces),
            ),
        ),
        skills=(SkillSpec(path=SKILL_DIR),),
        memory_search=(
            MemorySearchProviderSpec(
                name=DEFAULT_MEMORY_SEARCH_PROVIDER, build=MemorySearchService
            ),
        ),
        surfaces=(
            SurfaceSpec(
                name=SURFACE_MEMORY, routes=MEMORY_ROUTES, identify=resolve_operator_workspace
            ),
        ),
    )
