"""The memory extension's declared points: the three tools, the `memory` and `profile` object
kinds, the recall hook, two page-change consumers, seven derivation jobs.

`memory_search` and `memory_update` are the agent's durable-memory tools and `rebuild_page_facts`
sends the fact deriver back over every synced page; the `user_prompt_submit`
hook auto-injects relevant memory into the turn's context before the model runs. Two `page_change`
hooks ride independent core-runner cursors: `index_pages` turns each replayed source-page change
into index chunks + a mirror row, and `derive_facts` distills each into durable `fact`
memory_items with a bounded metered model pass. Eight JobSpecs run the interval derivations:
`memory_index` turns committed items into index chunks, `memory_consolidate` clusters aged facts
into `semantic` summaries that supersede their originals, `memory_dedup` sweeps one group of
duplicate copies per tick onto its newest copy, `memory_section` rewrites the paragraph that
opens each band of the wiki from the facts standing in it, `memory_overview` rewrites the one
paragraph the whole page opens on, `memory_people` writes each member's role and current focus into
`memory_profile`, `memory_page_pass` reads each subject's whole page on the deploy's own model —
the one job declaring `needs_deploy_model`, since seeing a page whole is what it is for — retiring
the rows that repeat one another, and `memory_drain_unindexed_pages` removes the chunks and mirror
rows of pages whose stream no longer reaches memory, in the workspaces a migration marked. Recall
is a best-effort prompt hook: the
handler owns a soft timeout below the hook deadline and records recall failures, while the hook
chain logs an outer fault at error severity and continues the turn without an injection.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import zip_longest
from typing import Literal, get_args
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field
from ufo_ext_sources.pages import PAGE_KIND

from ufo.sdk.context import ExtensionContext, SourceReader
from ufo.sdk.index import TextChunker
from ufo.sdk.jobs import PAGE_CHANGE_CURSOR_KEY, JobSpec, owner_candidates, stored_key_workspaces
from ufo.sdk.listings import ListingCursor, ListingPage, page_of, page_query
from ufo.sdk.manifest import (
    HookContext,
    HookOutcome,
    HookSpec,
    InjectContext,
    Manifest,
    MemorySearchProviderSpec,
    PageChangeBatch,
    UserPromptSubmit,
)
from ufo.sdk.memory import DEFAULT_MEMORY_SEARCH_PROVIDER, MemoryMatch
from ufo.sdk.o11y import log
from ufo.sdk.objects import ObjectRef
from ufo.sdk.operator import resolve_operator_workspace
from ufo.sdk.subjects import SHARED_SUBJECT
from ufo.sdk.surfaces import SurfaceSpec
from ufo.sdk.tools import (
    ActionPresentation,
    ObjectBinding,
    TextContent,
    ToolContext,
    ToolDef,
    ToolResult,
)
from ufo_ext_memory.condenser import (
    DEDUP_MIN_AGE,
    MIN_CLUSTER_FACTS,
    MIN_DUPLICATE_COPIES,
    MIN_OLDEST_AGE,
    MIN_OVERVIEW_FACTS,
    MIN_SECTION_FACTS,
    PAGE_PASS_MIN_ROWS,
    FactDeriver,
    MemoryConsolidator,
    MemoryDeduper,
    OverviewWriter,
    PagePass,
    ProfileWriter,
    SectionWriter,
)
from ufo_ext_memory.events import (
    MAX_RECALL_ERROR_CLASS_CHARS,
    MAX_RECALLED_MEMORY_IDS,
    MEMORY_RECALL_EVENT,
)
from ufo_ext_memory.objects import (
    MEMORY_KIND,
    MEMORY_OBJECT,
    PAGE_OBJECT_KIND,
    PROFILE_OBJECT,
)
from ufo_ext_memory.store import (
    DEFAULT_CONFIDENCE,
    FACT,
    KIND_FACT,
    MAX_CONFIDENCE,
    MEMORY_BODY_MAX_CHARS,
    OVERVIEW,
    RECALL_ITEM_MAX_CHARS,
    SECTION,
    ItemClass,
    MemoryIndexer,
    MemoryKind,
    MemoryWrite,
    PageIndexer,
    Recalled,
    SourceMatch,
    UnindexedPageDrain,
    _aware,
    body_digest,
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
RECORD_CORRECTION_ACTION = "record_correction"
RECORD_CORRECTION_LABEL = "Record edit"
CORRECTION_SOURCE_PREFIX = "corrects memory/"
RECORD_FIRST_RUN_ACTION = "record_first_run"
RECORD_FIRST_RUN_LABEL = "Continue"
FIRST_RUN_SOURCE_REF = "first run"
RECALL_LIMIT = MAX_RECALLED_MEMORY_IDS
INTERNAL_ADMISSION = "internal"
RECALL_SKIP_INTERNAL = "internal_admission"
RECALL_SOFT_TIMEOUT_SECONDS = 4.0
RECALL_CONTEXT_PREFIX = "Relevant memory:\n"
RECALL_TOTAL_MAX_CHARS = 8_000
RECALL_TRUNCATION_MARK = " …[truncated]"
MEMORY_INDEX_JOB = "memory_index"
MEMORY_INDEX_SCHEDULE = "0 * * * * *"
CONSOLIDATE_JOB = "memory_consolidate"
CONSOLIDATE_SCHEDULE = "0 0 * * * *"
DEDUP_JOB = "memory_dedup"
DEDUP_SCHEDULE = "0 30 * * * *"
SECTION_JOB = "memory_section"
SECTION_SCHEDULE = "0 15 3 * * *"
"""Daily at 03:15 UTC, half an hour after the curation pass. A band's paragraph is a re-read of
every fact standing under one heading — one metered model pass per band, so up to five per subject —
and what it says moves as slowly as the band does, where the hourly jobs each move one cluster or
one group. Writing after the curation is what makes the paragraph answer to the rows printed beneath
it. The hour keeps that spend off a working day, and minute 15 keeps it clear of the consolidator on
the hour and the deduper on the half hour, so no workspace pays two model jobs in one minute."""
OVERVIEW_JOB = "memory_overview"
OVERVIEW_SCHEDULE = "0 20 3 * * *"
"""Daily at 03:20 UTC, five minutes after the section pass. The page's opening paragraph and its
band paragraphs are the same act at two altitudes, written from the same live facts, so they belong
to one night's reading of the page rather than to two states of it a workspace apart. Minute 20
keeps the pass clear of the consolidator on the hour, the deduper on the half hour and the section
pass's own minute, so no workspace pays two model jobs in one minute."""
PROFILE_JOB = "memory_people"
PROFILE_SCHEDULE = "0 25 3 * * *"
"""Daily at 03:25 UTC, in the same nightly window as the two paragraph passes and one minute of its
own after them. The People band is written from the facts those passes just read, so the whole page
states one night; it runs last of the three because it is the only pass that also reads the roster,
which is what puts a member seated that day on the page the same night."""
PAGE_PASS_JOB = "memory_page_pass"
PAGE_PASS_SCHEDULE = "0 45 2 * * *"
"""Daily at 02:45 UTC, before the night's writing passes. This pass retires rows and writes no
prose, so it runs first: a paragraph written from rows the same night's curation then retired would
describe the page for a day as it stood before the curation, and the member reading it would find
sentences answering to nothing under them. Minute 45 stands clear of the consolidator on the hour
and the deduper on the half hour, so no workspace pays two model jobs in one minute."""
DRAIN_JOB = "memory_drain_unindexed_pages"
DRAIN_SCHEDULE = "20 * * * * *"
DRAIN_BATCH = 5000
UNINDEXED_DRAIN_KEY = "unindexed_pages_drain"
"""The store key `memory_0022` writes wherever a workspace's mirrors name a page that does not reach
memory; the drain walks from the cursor it carries and deletes it when the walk ends."""
REBUILD_QUEUED = (
    "The facts derived from synced pages are written again as the derivation pass reaches each "
    "page. The page's paragraphs and items an app recorded in a conversation are untouched."
)
REBUILD_ADMIN_ONLY = "Only a workspace admin can rebuild the facts derived from synced pages."
logger = logging.getLogger(__name__)


class MemorySearchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


RecordedClass = Literal["fact", "episodic"]
"""The classes an agent records. `overview` and `section` are the wiki's own paragraphs and
`semantic` is the consolidation job's summary of facts that agree — each has exactly one producing
pass, so a ledger an agent hand-wrote never lands under a heading a member reads as written."""


class MemoryUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(
        max_length=MEMORY_BODY_MAX_CHARS,
        description="One row of the member's wiki, in the third person: a subject they recognise, "
        "an em dash, then one sentence about it, inside 115 characters — 'Acme Corp — Moved the "
        "billing migration to 4 March.' Write the full name of every person, company, and thing "
        "you mention, because the reader sees this item alone, months later.",
    )
    item_class: RecordedClass = Field(
        default=FACT,
        description="fact for something that stays true; episodic for a breadcrumb of what "
        "happened, which recall offers as a topic to open rather than quoting back.",
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


class RecordCorrectionInput(BaseModel):
    """What a member states from the memory view: the item they are correcting and the statement
    that replaces it. Everything else a recorded item carries — its class, kind, confidence, and the
    provenance naming the corrected row — is the write's own to derive."""

    model_config = ConfigDict(extra="forbid")

    corrects: UUID = Field(description="The memory item this statement corrects.")
    body: str = Field(
        min_length=1, max_length=MEMORY_BODY_MAX_CHARS, description="The corrected statement."
    )


class RecordFirstRunInput(BaseModel):
    """What the first run states about the team: one sentence naming the tools they picked, written
    by the page from the catalog's own labels. Its class, kind, confidence, and provenance are the
    write's own."""

    model_config = ConfigDict(extra="forbid")

    body: str = Field(
        min_length=1, max_length=MEMORY_BODY_MAX_CHARS, description="What the team uses."
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
        """Every query's recall and source legs run under one gather, so a leg that raises reaches
        this caller: a gather awaited only after a prior one has already raised is abandoned with
        its exception unretrieved, and a search that loses a leg answers from a thinner index with
        no signal that it did."""
        if not 1 <= len(queries) <= MAX_MEMORY_QUERIES:
            raise ValueError(f"memory search requires 1-{MAX_MEMORY_QUERIES} queries")
        store = store_for(self.ctx)
        subjects = reader.subjects
        recalled_legs: list[tuple[Recalled, ...]]
        source_legs: list[tuple[SourceMatch, ...]]
        recalled_legs, source_legs = await asyncio.gather(
            asyncio.gather(
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
            ),
            asyncio.gather(
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
            ),
        )
        matches = tuple(
            MemoryMatch(
                kind=item.item_class,
                text=item.body,
                ref=ObjectRef(kind=MEMORY_KIND, name=str(item.memory_id)),
                created_at=item.created_at,
                subject=item.subject,
            )
            for item in self._merged(recalled_legs)
        )
        return matches + self._pages(source_legs)

    def _pages(self, legs: list[tuple[SourceMatch, ...]]) -> tuple[MemoryMatch, ...]:
        """Every query's page hits interleaved round-robin, one row per passage of a page and
        bounded to MEMORY_SEARCH_LIMIT. A page answering two queries with two passages is two rows:
        a manual's sections are what the agent came for, and keying the merge by page alone would
        hand back only the first of them."""
        sources: dict[tuple[UUID, str], SourceMatch] = {}
        for tier in zip_longest(*legs):
            for match in tier:
                if match is not None and (match.page_id, match.text) not in sources:
                    sources[(match.page_id, match.text)] = match
        return tuple(
            MemoryMatch(
                kind="source",
                text=match.text,
                ref=ObjectRef(kind=PAGE_OBJECT_KIND, name=str(match.page_id)),
                created_at=match.created_at,
                subject=match.subject,
            )
            for match in list(sources.values())[:MEMORY_SEARCH_LIMIT]
        )

    def _merged(self, legs: list[tuple[Recalled, ...]]) -> tuple[Recalled, ...]:
        """Every query's recall interleaved round-robin — each query's top hit, then each second —
        one row per memory id and then one per statement, each in the slot it first reached,
        bounded to MEMORY_SEARCH_LIMIT. The id comes first because recall rewrites an episodic hit
        to a topic pointer that carries its rank, so one item recalled at two ranks is two bodies
        and one ref. Two pages stating one body are two rows, and two queries whose candidate pools
        differ can each recall a different one of them; the bytes are identical, so which id is
        served is immaterial and a query's top hit keeps its slot rather than falling to where its
        twin ranked."""
        by_id: dict[UUID, Recalled] = {}
        for tier in zip_longest(*legs):
            for item in tier:
                if item is not None:
                    by_id.setdefault(item.memory_id, item)
        statements: dict[tuple[str, str], Recalled] = {}
        for item in by_id.values():
            statements.setdefault((item.subject, body_digest(item.body)), item)
        return tuple(statements.values())[:MEMORY_SEARCH_LIMIT]

    async def search_pages(
        self,
        queries: tuple[str, ...],
        reader: SourceReader,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[MemoryMatch, ...]:
        """The source half of `search` on its own: every query's page leg over the same index,
        merged the same way, with no recall leg — what the research extension's `page` search
        vertical answers from."""
        if not 1 <= len(queries) <= MAX_MEMORY_QUERIES:
            raise ValueError(f"page search requires 1-{MAX_MEMORY_QUERIES} queries")
        store = store_for(self.ctx)
        legs = await asyncio.gather(
            *(
                store.search_sources(
                    query,
                    reader.subjects,
                    MEMORY_SEARCH_LIMIT,
                    start,
                    end,
                    source_reader=reader,
                )
                for query in queries
            )
        )
        return self._pages(list(legs))

    def listable_kinds(self) -> tuple[str, ...]:
        """The item classes this store writes, read off `ItemClass` itself so a class added there
        reaches a consumer's filter without a second list to remember."""
        return get_args(ItemClass)

    async def list_recent(
        self,
        subjects: frozenset[str],
        limit: int,
        kinds: frozenset[str] | None = None,
        cursor: ListingCursor | None = None,
    ) -> ListingPage[MemoryMatch]:
        """One page of the live memory items the subjects may read, newest first — the browse half
        of the seam: no query, no similarity, superseded rows excluded. Source pages are search's
        alone; a listing of everything synced would be a page dump, not memory. The paging is the
        portal's shared keyset walk (`ufo.sdk.listings`), so this listing and every other page the
        same way."""
        query = sa.select(
            memory_item.c.id,
            memory_item.c.body,
            memory_item.c.item_class,
            memory_item.c.created_at,
            memory_item.c.subject,
        ).where(
            memory_item.c.workspace_id == self.ctx.store.workspace_id,
            memory_item.c.subject.in_(subjects),
            memory_item.c.superseded_by.is_(None),
            memory_item.c.retired_at.is_(None),
        )
        if kinds is not None:
            query = query.where(memory_item.c.item_class.in_(kinds))
        async with self.ctx.transaction() as connection:
            rows = (
                await connection.execute(
                    page_query(
                        query,
                        cursor,
                        limit,
                        created_at=memory_item.c.created_at,
                        ident=memory_item.c.id,
                    )
                )
            ).all()
        return page_of(
            rows,
            cursor,
            limit,
            render=lambda row: MemoryMatch(
                kind=row.item_class,
                text=row.body,
                ref=ObjectRef(kind=MEMORY_KIND, name=str(row.id)),
                created_at=_aware(row.created_at),
                subject=row.subject,
            ),
            position=lambda row: (_aware(row.created_at), str(row.id)),
        )


def match_line(match: MemoryMatch) -> str:
    """One hit as the agent triages it: snippet first, then the durable ref and recency —
    pass the ref unchanged to `object_get` to open the full memory or page behind the hit."""
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


async def record_correction_handler(ctx: ToolContext, args: RecordCorrectionInput) -> ToolResult:
    """The memory view's correction: a new item under the corrector's own audience naming the
    corrected item in `source_ref` — the row the portal's correction writes. The named item is
    neither edited nor removed — the dedup sweep retires the near-duplicate original
    toward this newer statement."""
    if ctx.ext is None:
        raise RuntimeError("record_correction dispatched without its ExtensionContext")
    subject = str(ctx.effective_audience)
    await store_for(ctx.ext).commit(
        MemoryWrite(
            subject=subject,
            body=args.body,
            item_class=FACT,
            memory_kind=KIND_FACT,
            confidence=DEFAULT_CONFIDENCE,
            source_ref=f"{CORRECTION_SOURCE_PREFIX}{args.corrects}",
        )
    )
    return ToolResult(content=(TextContent(text=f"Remembered ({subject})."),))


async def record_first_run_handler(ctx: ToolContext, args: RecordFirstRunInput) -> ToolResult:
    """The first run's one memory: what the team uses, under the picking member's own audience,
    named as the first run's so every later turn recalls it — the row the first run writes."""
    if ctx.ext is None:
        raise RuntimeError("record_first_run dispatched without its ExtensionContext")
    subject = str(ctx.effective_audience)
    await store_for(ctx.ext).commit(
        MemoryWrite(
            subject=subject,
            body=args.body,
            item_class=FACT,
            memory_kind=KIND_FACT,
            confidence=DEFAULT_CONFIDENCE,
            source_ref=FIRST_RUN_SOURCE_REF,
        )
    )
    return ToolResult(content=(TextContent(text=f"Remembered ({subject})."),))


async def recall_hook(ctx: HookContext) -> HookOutcome:
    """Auto-inject memory relevant to the inbound after the submitted message in the model context.
    Recall's user_prompt_submit spec is best effort: this handler's soft timeout records recall
    failures, and the hook chain logs and drops the injection if its outer deadline or another fault
    escapes the handler. A missing result never denies the turn.
    Injected lines are bounded per-item (RECALL_ITEM_MAX_CHARS, truncated with
    RECALL_TRUNCATION_MARK) and in total
    (RECALL_TOTAL_MAX_CHARS counted against each rendered "- " line plus its "\n" separator, so the
    joined text itself never exceeds the budget): an item past the total budget is dropped whole, so
    the recall event's memory_ids names only the items whose lines actually made it into the
    injected text. A speakerless root
    turn admitted INTERNAL_ADMISSION (a machine fold: subagent result deliveries, internal notices)
    carries no topical text to recall against, so it skips recall entirely; a child subagent turn or
    a scheduled turn keeps recall regardless of admission_source. What the skip tests is the
    arrival, not only the turn it lands on: admission folds a member's message onto whatever turn is
    live, so a member writing while an internal root turn runs fires this hook with their own text
    and their own `speaker_member_id` — a member's prompt, recalled against like any other."""
    if not isinstance(ctx.payload, UserPromptSubmit):
        return None
    if ctx.turn is None:
        return None
    if (
        ctx.speaker_member_id is None
        and ctx.turn.admission_source == INTERNAL_ADMISSION
        and ctx.turn.parent_turn_id is None
    ):
        try:
            log(
                MEMORY_RECALL_EVENT,
                turn_id=str(ctx.turn.id),
                memory_ids=[],
                skipped=RECALL_SKIP_INTERNAL,
            )
        except Exception:
            logger.warning("memory.recall_log_failed", exc_info=True)
        return None
    subjects = recall_subjects(ctx.audience, ctx.speaker_member_id)
    reader = SourceReader(
        agent_id=ctx.turn.agent_id,
        requesting_member_id=None,
        subjects=subjects,
        connections=(
            None if ctx.turn.runtime_config is None else ctx.turn.runtime_config.connections
        ),
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
    lines: list[str] = []
    kept: list[Recalled] = []
    total = 0
    for item in injected:
        body = item.body
        if len(body) > RECALL_ITEM_MAX_CHARS:
            body = body[:RECALL_ITEM_MAX_CHARS] + RECALL_TRUNCATION_MARK
        line = f"- {body}"
        separator = 1 if lines else 0
        if total + separator + len(line) > RECALL_TOTAL_MAX_CHARS:
            break
        lines.append(line)
        kept.append(item)
        total += separator + len(line)
    if ctx.turn is not None:
        try:
            log(
                MEMORY_RECALL_EVENT,
                turn_id=str(ctx.turn.id),
                memory_ids=[str(item.memory_id) for item in kept],
                **({"error_class": error_class} if error_class is not None else {}),
            )
        except Exception:
            logger.warning("memory.recall_log_failed", exc_info=True)
    if error_class is not None:
        return None
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


DERIVE_CURSOR_KEY = f"{PAGE_CHANGE_CURSOR_KEY}:{derive_facts.__name__}"
"""The cursor core drives `derive_facts` from, keyed off the handler core discriminates it by, so
the two cannot drift apart under a rename."""


class RebuildPageFactsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def rebuild_page_facts_handler(ctx: ToolContext, args: RebuildPageFactsInput) -> ToolResult:
    """Mark every synced page due for derivation again and return. Nothing is written or removed
    here: clearing the cursor sends `derive_facts` back over every page, and that pass — which owns
    this derived state — writes each page's facts and retires the reading they replace. A page the
    pass leaves without a fact keeps the one it has, so no row goes before its replacement exists;
    a page of a stream that does not reach memory is the one page whose rows go with nothing in
    their place, since nothing derives a replacement for it ever again.

    A tick already running holds the cursor value it read, so its own advance loses the
    compare-and-set against the cleared key and it stops where it stands; the next tick starts from
    the beginning."""
    if ctx.ext is None:
        raise RuntimeError("rebuild_page_facts dispatched without its ExtensionContext")
    if not await ctx.require_speaking_admin(REBUILD_ADMIN_ONLY):
        raise ValueError(REBUILD_ADMIN_ONLY)
    await ctx.ext.store.delete(DERIVE_CURSOR_KEY)
    return ToolResult(content=(TextContent(text=REBUILD_QUEUED),))


async def consolidate_memory(ctx: ExtensionContext) -> None:
    if ctx.embed is None:
        raise RuntimeError("memory_consolidate requires the embed backend; none is wired")
    await MemoryConsolidator(
        embed=ctx.embed,
        transaction=ctx.transaction,
        workspace_id=ctx.store.workspace_id,
        model=ctx.model,
    ).run()


async def dedup_memory(ctx: ExtensionContext) -> None:
    if ctx.embed is None:
        raise RuntimeError("memory_dedup requires the embed backend; none is wired")
    await MemoryDeduper(
        embed=ctx.embed,
        transaction=ctx.transaction,
        workspace_id=ctx.store.workspace_id,
        store=ctx.store,
    ).run()


async def write_memory_sections(ctx: ExtensionContext) -> None:
    await SectionWriter(
        transaction=ctx.transaction,
        workspace_id=ctx.store.workspace_id,
        model=ctx.model,
    ).run()


async def write_memory_overview(ctx: ExtensionContext) -> None:
    await OverviewWriter(
        transaction=ctx.transaction,
        workspace_id=ctx.store.workspace_id,
        model=ctx.model,
    ).run()


async def write_member_profiles(ctx: ExtensionContext) -> None:
    await ProfileWriter(
        transaction=ctx.transaction,
        workspace_id=ctx.store.workspace_id,
        model=ctx.model,
    ).run()


async def curate_memory_pages(ctx: ExtensionContext) -> None:
    await PagePass(
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


async def drain_unindexed_pages(ctx: ExtensionContext) -> None:
    if ctx.index is None:
        raise RuntimeError(f"{DRAIN_JOB} requires the index backend; none is wired")
    await UnindexedPageDrain(
        index=ctx.index,
        transaction=ctx.transaction,
        store=ctx.store,
        page_states=ctx.page_states,
        workspace_id=ctx.store.workspace_id,
        marker_key=UNINDEXED_DRAIN_KEY,
        batch=DRAIN_BATCH,
    ).run()


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
            memory_item.c.created_from_page_uid.is_(None),
            memory_item.c.superseded_by.is_(None),
            memory_item.c.retired_at.is_(None),
            memory_item.c.created_at <= cutoff,
        )
        .group_by(memory_item.c.workspace_id)
        .having(sa.func.count() >= MIN_CLUSTER_FACTS)
    )


def _dedupable_workspaces() -> sa.Select[tuple[UUID]]:
    """Workspaces holding a duplicate backlog a sweep could collapse: at least MIN_DUPLICATE_COPIES
    live tool-written rows sharing one (subject, item_class), each past DEDUP_MIN_AGE — the sweep's
    own floor, folded into the candidate read so a workspace whose copies are all fresh is never
    bound, fresh writes being the commit path's to dedup. The projection is distinct — a workspace
    with several such groups is one candidate bound once, not one per group — and a workspace whose
    repeats are all page-derived is never bound, those rows being the deriver's. Built per tick, so
    the age cutoff tracks `now`."""
    cutoff = datetime.now(UTC) - DEDUP_MIN_AGE
    return (
        sa.select(memory_item.c.workspace_id)
        .where(
            memory_item.c.created_from_page_uid.is_(None),
            memory_item.c.item_class != SECTION,
            memory_item.c.superseded_by.is_(None),
            memory_item.c.retired_at.is_(None),
            memory_item.c.created_at <= cutoff,
        )
        .group_by(memory_item.c.workspace_id, memory_item.c.subject, memory_item.c.item_class)
        .having(sa.func.count() >= MIN_DUPLICATE_COPIES)
        .distinct()
    )


def _class_count(item_class: ItemClass) -> sa.Function[int]:
    """How many of a group's rows carry one class. A paragraph pass is bound by two floors read off
    one grouped scan — the rows a paragraph would be written from, and the paragraph itself — so
    each count has to name its class rather than take the whole group."""
    return sa.func.count(sa.case((memory_item.c.item_class == item_class, 1)))


def _summarizable_workspaces() -> sa.Select[tuple[UUID]]:
    """Workspaces the section pass has work in: a (subject, memory_kind) band holding at least
    MIN_SECTION_FACTS live facts, or one still holding a paragraph. The pass's own floor is folded
    into the candidate read so a workspace whose wiki is still a handful of rows is never bound on
    the daily tick — and a band that has fallen under it is the other half of the same work, its
    paragraph now standing over rows it was not written from, which a read naming the floor alone
    would leave standing for ever. The projection is distinct, so a workspace with five such bands
    is one candidate bound once."""
    return (
        sa.select(memory_item.c.workspace_id)
        .where(
            memory_item.c.item_class.in_((FACT, SECTION)),
            memory_item.c.superseded_by.is_(None),
            memory_item.c.retired_at.is_(None),
        )
        .group_by(memory_item.c.workspace_id, memory_item.c.subject, memory_item.c.memory_kind)
        .having(
            sa.or_(
                _class_count(FACT) >= MIN_SECTION_FACTS,
                _class_count(SECTION) > 0,
            )
        )
        .distinct()
    )


def _overviewable_workspaces() -> sa.Select[tuple[UUID]]:
    """Workspaces the overview pass has work in: at least MIN_OVERVIEW_FACTS live facts on the
    workspace-shared subject, or an opening paragraph still standing there. The pass's own floor and
    its own subject are folded into the candidate read so a workspace whose wiki is still a handful
    of rows, and one whose rows are all a member's own, is never bound on the daily tick — while a
    page that has fallen under the floor is bound to lose the paragraph the floor now refuses to
    rewrite."""
    return (
        sa.select(memory_item.c.workspace_id)
        .where(
            memory_item.c.subject == SHARED_SUBJECT,
            memory_item.c.item_class.in_((FACT, OVERVIEW)),
            memory_item.c.superseded_by.is_(None),
            memory_item.c.retired_at.is_(None),
        )
        .group_by(memory_item.c.workspace_id)
        .having(
            sa.or_(
                _class_count(FACT) >= MIN_OVERVIEW_FACTS,
                _class_count(OVERVIEW) > 0,
            )
        )
    )


def _peopled_workspaces() -> sa.Select[tuple[UUID]]:
    """Workspaces the People pass could write an entry in: one holding a shared fact at all. The
    roster is never empty where a workspace has one — the pass writes from facts that name a member,
    and a workspace with no shared fact has nothing any entry could be drawn from, so binding it
    would spend a metered pass to answer that."""
    return (
        sa.select(memory_item.c.workspace_id)
        .where(
            memory_item.c.subject == SHARED_SUBJECT,
            memory_item.c.item_class == FACT,
            memory_item.c.superseded_by.is_(None),
            memory_item.c.retired_at.is_(None),
        )
        .distinct()
    )


def _curatable_workspaces() -> sa.Select[tuple[UUID]]:
    """Workspaces holding a page worth the deploy model's whole-page read: at least
    PAGE_PASS_MIN_ROWS live facts under one subject — the page pass's own floor, folded into the
    candidate read so a workspace whose wiki is still a handful of rows is never bound on the
    nightly tick. The projection is distinct, so a workspace with several such pages is one
    candidate bound once."""
    return (
        sa.select(memory_item.c.workspace_id)
        .where(
            memory_item.c.item_class == FACT,
            memory_item.c.superseded_by.is_(None),
            memory_item.c.retired_at.is_(None),
        )
        .group_by(memory_item.c.workspace_id, memory_item.c.subject)
        .having(sa.func.count() >= PAGE_PASS_MIN_ROWS)
        .distinct()
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
                    "(memory/<id> or page/<id>) and date — pass the ref unchanged as object_get's "
                    "`ref` to open the full item or page and follow its provenance links. Use it "
                    "to recall context "
                    "before answering."
                ),
                input_model=MemorySearchInput,
                handler=memory_search_handler,
                parallel_safe=True,
            ),
            ToolDef(
                name="memory_update",
                description=(
                    "Record a durable memory item so later turns and conversations can recall it, "
                    "and so it reads as one row of the member's wiki. Writes only to the current "
                    "conversation audience: a private conversation writes that member's memory; a "
                    "shared conversation writes shared memory. Use it as you learn what lasts — a "
                    "name, role, company, team, colleague, preference, project, tool, or working "
                    "style. Record what a person would act on or repeat months from now: a "
                    "decision, an owner, a commitment, a date, a standing rule. Leave to the "
                    "system the values it reports about itself and can read again on demand — a "
                    "last-updated time, a count, an identifier, a status flag. When an item you "
                    "recalled contradicts what a live source just told you, record the corrected "
                    "statement, so later recall carries the current one. Set `memory_kind` "
                    "(fact/preference/decision/event/task) so recency decay matches how fast the "
                    "item goes stale, and `confidence` (1-10) for how sure you are it is true. "
                    "A one-off instruction ('make it shorter') belongs in the turn, in-task "
                    "working state in workspace files or todo items, and per-run output in the "
                    "delivered post or artifact."
                ),
                input_model=MemoryUpdateInput,
                handler=memory_update_handler,
                binds_member_authority=False,
                side_effecting=True,
            ),
            ToolDef(
                name=RECORD_CORRECTION_ACTION,
                description=(
                    "Record the corrected statement of one memory item, as the portal's memory "
                    "view does: a new item under the speaker's own audience naming the corrected "
                    "item; the dedup sweep retires the original toward it."
                ),
                input_model=RecordCorrectionInput,
                handler=record_correction_handler,
                bound=ObjectBinding(kind=MEMORY_KIND, binding="collection"),
                side_effecting=True,
                presentation=ActionPresentation(label=RECORD_CORRECTION_LABEL),
                binds_member_authority=False,
            ),
            ToolDef(
                name=RECORD_FIRST_RUN_ACTION,
                description=(
                    "Record what the team uses, as the portal's first run states it: one item "
                    "under the speaker's own audience, named as the first run's."
                ),
                input_model=RecordFirstRunInput,
                handler=record_first_run_handler,
                bound=ObjectBinding(kind=MEMORY_KIND, binding="collection"),
                side_effecting=True,
                presentation=ActionPresentation(label=RECORD_FIRST_RUN_LABEL),
                binds_member_authority=False,
            ),
            ToolDef(
                name="rebuild_page_facts",
                description=(
                    "Write the workspace's page-derived facts again, for a workspace admin who "
                    "says the wiki rows drawn from synced documents read badly. It marks every "
                    "synced page due and returns: the derivation pass replaces each page's facts "
                    "as it reaches them, and no row goes before its replacement is written. Rows "
                    "drawn from a page the wiki derives nothing from at all — the streams a run of "
                    "the machine writes about itself — are retired outright. It "
                    "redoes nothing else — the wiki's paragraphs are the writing passes', and an "
                    "item recorded through memory_update came from a conversation that cannot be "
                    "held again. Use it for the whole workspace's page facts, never to change one "
                    "row: a single wrong statement is corrected by recording the right one."
                ),
                input_model=RebuildPageFactsInput,
                handler=rebuild_page_facts_handler,
                side_effecting=True,
                bound=ObjectBinding(kind=PAGE_KIND, binding="collection"),
                presentation=ActionPresentation(
                    label="Rebuild page facts",
                    confirm="Every synced page's derived facts are written again.",
                    frame=True,
                ),
            ),
        ),
        objects=(MEMORY_OBJECT, PROFILE_OBJECT),
        hooks=(
            HookSpec(event="user_prompt_submit", handler=recall_hook, best_effort=True),
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
            JobSpec(
                name=DEDUP_JOB,
                schedule=DEDUP_SCHEDULE,
                handler=dedup_memory,
                candidates=owner_candidates(_dedupable_workspaces),
            ),
            JobSpec(
                name=SECTION_JOB,
                schedule=SECTION_SCHEDULE,
                handler=write_memory_sections,
                candidates=owner_candidates(_summarizable_workspaces),
            ),
            JobSpec(
                name=OVERVIEW_JOB,
                schedule=OVERVIEW_SCHEDULE,
                handler=write_memory_overview,
                candidates=owner_candidates(_overviewable_workspaces),
            ),
            JobSpec(
                name=PROFILE_JOB,
                schedule=PROFILE_SCHEDULE,
                handler=write_member_profiles,
                candidates=owner_candidates(_peopled_workspaces),
            ),
            JobSpec(
                name=PAGE_PASS_JOB,
                schedule=PAGE_PASS_SCHEDULE,
                handler=curate_memory_pages,
                candidates=owner_candidates(_curatable_workspaces),
                needs_deploy_model=True,
            ),
            JobSpec(
                name=DRAIN_JOB,
                schedule=DRAIN_SCHEDULE,
                handler=drain_unindexed_pages,
                candidates=stored_key_workspaces(NAME, UNINDEXED_DRAIN_KEY),
            ),
        ),
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
