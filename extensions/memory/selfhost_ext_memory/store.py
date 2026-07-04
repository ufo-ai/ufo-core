"""The memory domain: the `memory_item` table the extension owns, recall's fusion, and the indexer.

`commit` persists one `memory_item` and derives nothing — chunking and embedding are the index
job's work, never inline on a write. `recall` embeds the query once, asks the deploy index backend
for its lexical and vector hits under the caller's subject filter, fuses them with reciprocal-rank
fusion (K=60), and reads the surviving items back. `search_sources` fuses the same legs over
source-page chunks and reads the matched snippet straight off the index (a tombstoned page's chunks
are already gone). `MemoryIndexer` is the derivation job: it atomically claims memory items whose
`embedding_digest` is NULL, chunks and embeds each body, and stamps the digest so the row is no
longer due. Everything reaches the database through the extension's workspace-scoped
`transaction()` and the deploy index/embed backends core threads onto its context — never a core
internal.
"""

import hashlib
import logging
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from itertools import chain
from typing import Literal
from uuid import UUID, uuid5

import sqlalchemy as sa
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from selfhost.sdk.context import ExtensionContext, ScopedStore
from selfhost.sdk.index import (
    OWNER_KIND_MEMORY_ITEM,
    OWNER_KIND_PAGE,
    EmbedClient,
    Hit,
    IndexBackend,
    IndexScope,
    TextChunker,
    chunk_embed_upsert,
)
from selfhost.sdk.sources import SHARED_SUBJECT, PageChange, PageFeed, member_subject

RRF_K = 60
RRF_WEIGHT = 0.7
COSINE_WEIGHT = 0.3
TAIL_SCAN_MAX = 200
MEMORY_ITEM_NAMESPACE = UUID("32492d08-3cb7-59ac-8962-b2e384f024fc")
DUE_BATCH_MAX_ITEMS = 200
EMBED_CLAIM_LEASE_SECONDS = 300
PAGE_INDEX_BATCH = 50
PAGE_CURSOR_KEY = "page_index_cursor"

TYPE_DIVERSITY_RATIO = 0.6
MAX_CONFIDENCE = 10
DEFAULT_CONFIDENCE = 5
HALFLIFE_DAYS: dict[str, float] = {
    "fact": 365.0,
    "preference": 180.0,
    "decision": 120.0,
    "event": 30.0,
    "task": 14.0,
}

logger = logging.getLogger(__name__)

ItemClass = Literal["fact", "episodic", "semantic"]
FACT: ItemClass = "fact"
EPISODIC: ItemClass = "episodic"
SEMANTIC: ItemClass = "semantic"

MemoryKind = Literal["fact", "preference", "decision", "event", "task"]
KIND_FACT: MemoryKind = "fact"

Transaction = Callable[[], AbstractAsyncContextManager[AsyncConnection]]

_metadata = sa.MetaData()
memory_item = sa.Table(
    "memory_item",
    _metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("subject", sa.Text, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("item_class", sa.Text, nullable=False),
    sa.Column("memory_kind", sa.Text, nullable=False),
    sa.Column("confidence", sa.Integer, nullable=False),
    sa.Column("source_ref", sa.Text, nullable=True),
    sa.Column("embedding_digest", sa.Text, nullable=True),
    sa.Column("embedding_claimed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("superseded_by", sa.Uuid, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

mem_page = sa.Table(
    "mem_page",
    _metadata,
    sa.Column("page_id", sa.Uuid, primary_key=True),
    sa.Column("subject", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)


def recall_subjects(member_id: UUID | None) -> frozenset[str]:
    """The subjects a turn recalls under: the member's own space plus the shared space, or shared
    alone when the conversation has no linked member."""
    if member_id is None:
        return frozenset({SHARED_SUBJECT})
    return frozenset({member_subject(member_id), SHARED_SUBJECT})


class MemoryWrite(BaseModel):
    """What a commit records: the subject scoping visibility, the body, its class, the ref back to
    what produced it, and the recall-decay inputs — `memory_kind` selects the recency half-life
    (fact/preference/decision/event/task) and `confidence` (1..10) scales a fact's decayed rank."""

    subject: str
    body: str
    item_class: ItemClass = FACT
    memory_kind: MemoryKind = KIND_FACT
    confidence: int = Field(default=DEFAULT_CONFIDENCE, ge=1, le=MAX_CONFIDENCE)
    source_ref: str | None = None


class MemoryItem(BaseModel):
    """A stored memory row as the derivation job loads it. `embedding_digest` NULL means the item is
    due for indexing; `superseded_by` points at the item that replaced it."""

    id: UUID
    subject: str
    body: str
    item_class: ItemClass
    memory_kind: MemoryKind = KIND_FACT
    confidence: int = DEFAULT_CONFIDENCE
    source_ref: str | None = None
    embedding_digest: str | None = None
    superseded_by: UUID | None = None


@dataclass(frozen=True)
class Fused:
    owner_id: str
    score: float
    text: str


def _fuse(
    legs: tuple[tuple[Hit, ...], ...], cosine_leg: tuple[Hit, ...]
) -> dict[str, tuple[float, float, str]]:
    """Per owning row: its reciprocal-rank-fusion score (K=60) with the matched snippet, and its raw
    vector-leg cosine. Each leg ranks its own chunk hits, a chunk's RRF sums 1/(K+rank) across the
    legs it placed in, and a row takes its best-scoring chunk (that chunk's text rides along); the
    cosine is the row's largest score in `cosine_leg` — the continuous semantic-closeness signal
    recall blends into its rank."""
    ranks = tuple(
        {hit.chunk_digest: rank for rank, hit in enumerate(leg, start=1) if hit.score > 0}
        for leg in legs
    )
    cosine: dict[str, float] = {}
    for hit in cosine_leg:
        cosine[hit.owner_id] = max(cosine.get(hit.owner_id, 0.0), hit.score)
    best: dict[str, tuple[float, str]] = {}
    for hit in chain.from_iterable(legs):
        rrf = sum(1.0 / (RRF_K + leg[hit.chunk_digest]) for leg in ranks if hit.chunk_digest in leg)
        current = best.get(hit.owner_id)
        if current is None or rrf > current[0]:
            best[hit.owner_id] = (rrf, hit.text)
    return {
        owner_id: (rrf, cosine.get(owner_id, 0.0), text) for owner_id, (rrf, text) in best.items()
    }


def fuse_hits(lexical: tuple[Hit, ...], vector: tuple[Hit, ...], limit: int) -> tuple[Fused, ...]:
    """Pure reciprocal-rank fusion collapsed to one score per owning row — source-page search's
    ranking, where the fused rank across the lexical and vector legs is the whole signal."""
    fused = _fuse((lexical, vector), vector)
    ranked = sorted(fused.items(), key=lambda item: item[1][0], reverse=True)[:limit]
    return tuple(Fused(owner_id, rrf, text) for owner_id, (rrf, _cosine, text) in ranked)


def fuse_recall(
    lexical: tuple[Hit, ...], vector: tuple[Hit, ...], tail: tuple[Hit, ...], limit: int
) -> tuple[Fused, ...]:
    """gbrain cosine re-score blend: `RRF_WEIGHT·(normalized RRF) + COSINE_WEIGHT·(raw query-chunk
    cosine)`, so a semantically closer row is promoted by a continuous signal, not only its fused
    rank — the score recall ranks by before recency decay. The RRF is normalized by the top fused
    score across rows; the cosine is the row's best vector-leg score (0 when the query never
    embedded, degrading the blend to normalized RRF alone). `tail` is a third, lexical-only leg over
    the un-embedded rows the index has not chunked yet — it carries no cosine, so a just-committed
    fact ranks on normalized RRF alone until the index job serves it."""
    fused = _fuse((lexical, vector, tail), vector)
    top_rrf = max((rrf for rrf, _cosine, _text in fused.values()), default=0.0) or 1.0
    scored = [
        (owner_id, RRF_WEIGHT * (rrf / top_rrf) + COSINE_WEIGHT * cosine, text)
        for owner_id, (rrf, cosine, text) in fused.items()
    ]
    ranked = sorted(scored, key=lambda item: item[1], reverse=True)[:limit]
    return tuple(Fused(owner_id, score, text) for owner_id, score, text in ranked)


@dataclass(frozen=True)
class Recalled:
    memory_id: UUID
    subject: str
    item_class: str
    body: str
    source_ref: str | None
    score: float
    memory_kind: str = KIND_FACT
    confidence: int = DEFAULT_CONFIDENCE
    created_at: datetime | None = None
    recall_mode: str | None = None


def decay_factor(item: Recalled, now: datetime) -> float:
    """Recency decay applies to fact items only (per-`memory_kind` half-lives: fact/preference/
    decision/event/task); episodic/semantic carry no half-life and rank on relevance alone. A
    fact's factor is `(confidence / 10) * 0.5 ** (age_days / halflife)` — gbrain
    `effectiveConfidence`."""
    if item.item_class != FACT or item.created_at is None:
        return 1.0
    base = item.confidence / MAX_CONFIDENCE
    halflife = HALFLIFE_DAYS.get(item.memory_kind, HALFLIFE_DAYS[KIND_FACT])
    created = item.created_at if item.created_at.tzinfo else item.created_at.replace(tzinfo=UTC)
    age_days = max(0.0, (now - created).total_seconds() / 86400.0)
    return base * (0.5 ** (age_days / halflife))


def enforce_type_diversity(rows: tuple[Recalled, ...], limit: int) -> tuple[Recalled, ...]:
    """gbrain `enforceTypeDiversity`: take rows in rank order, admitting at most
    TYPE_DIVERSITY_RATIO of `limit` per item class, then backfill from the deferred remainder only
    if needed to reach `limit`, so no single class crowds out the rest."""
    if limit <= 0:
        return ()
    cap = max(1, int(limit * TYPE_DIVERSITY_RATIO))
    counts: dict[str, int] = {}
    kept: list[Recalled] = []
    overflow: list[Recalled] = []
    for row in rows:
        if counts.get(row.item_class, 0) < cap:
            counts[row.item_class] = counts.get(row.item_class, 0) + 1
            kept.append(row)
        else:
            overflow.append(row)
    if len(kept) < limit:
        kept.extend(overflow[: limit - len(kept)])
    return tuple(kept[:limit])


def as_topic_pointer(item: Recalled, index: int) -> Recalled:
    """An episodic hit is rewritten to a topic pointer excluded from auto-injection (recall_mode
    `topic`): episodic memory is a breadcrumb to browse, never verbatim context — gbrain
    `episodicPointer`."""
    if item.item_class != EPISODIC:
        return item
    return replace(
        item,
        body=f"Memory topic {index + 1} (item {item.memory_id})",
        recall_mode="topic",
    )


@dataclass(frozen=True)
class SourceMatch:
    page_id: UUID
    subject: str
    text: str
    score: float


@dataclass(frozen=True)
class MemoryStore:
    """The memory workflow over the extension's scoped handle: commit one item, recall facts, search
    source pages. Holds the deploy index/embed backends core threaded onto the context and the
    workspace-scoped transaction opener; reads and writes only the extension's own `memory_item`."""

    index: IndexBackend
    embed: EmbedClient
    transaction: Transaction
    workspace_id: UUID

    async def commit(self, write: MemoryWrite) -> None:
        """Persist one memory_item with no derived state: embedding_digest stays NULL, marking the
        row due for the index job — the sole producer of chunks and embeddings. The id is
        content-addressed over `(workspace, subject, item_class, body)`, so re-committing the same
        fact upserts its decay inputs in place rather than accumulating a duplicate recallable row;
        the identical body leaves the existing chunks (and their digest) untouched."""
        async with self.transaction() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            statement = insert(memory_item).values(
                id=uuid5(
                    MEMORY_ITEM_NAMESPACE,
                    "\x00".join(
                        (str(self.workspace_id), write.subject, write.item_class, write.body)
                    ),
                ),
                workspace_id=self.workspace_id,
                subject=write.subject,
                body=write.body,
                item_class=write.item_class,
                memory_kind=write.memory_kind,
                confidence=write.confidence,
                source_ref=write.source_ref,
                superseded_by=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[memory_item.c.id],
                    set_={
                        memory_item.c.memory_kind: statement.excluded.memory_kind,
                        memory_item.c.confidence: statement.excluded.confidence,
                        memory_item.c.source_ref: statement.excluded.source_ref,
                        memory_item.c.updated_at: sa.func.now(),
                    },
                )
            )

    async def recall(
        self,
        query: str,
        subjects: frozenset[str],
        limit: int,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[Recalled, ...]:
        """Fuse the index legs with the cosine re-score blend and a lexical leg over the un-embedded
        tail, read the surviving items back, then rank by recency decay (fact half-lives), cap
        per-class diversity, and rewrite episodic hits to topic pointers. The tail leg makes a
        just-committed fact recallable before the index job derives its chunks. An optional
        half-open `[start, end)` bound on `created_at` restricts recall to a window; the index never
        sees the bound, so the filter lands in the row read-back alongside the superseded drop."""
        lexical, vector = await self._legs(query, subjects, OWNER_KIND_MEMORY_ITEM, limit)
        tail = await self._untail_leg(query, subjects, limit)
        enriched = await self._enrich(fuse_recall(lexical, vector, tail, limit), start, end)
        now = datetime.now(UTC)
        ranked = tuple(
            sorted(
                (replace(item, score=item.score * decay_factor(item, now)) for item in enriched),
                key=lambda item: item.score,
                reverse=True,
            )
        )
        diversified = enforce_type_diversity(ranked, limit)
        return tuple(as_topic_pointer(item, index) for index, item in enumerate(diversified))

    async def search_sources(
        self,
        query: str,
        subjects: frozenset[str],
        limit: int,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[SourceMatch, ...]:
        """Search synced source pages the same way recall searches facts: fuse the two index legs
        under the subject filter over the page owner kind, then read each surviving page back from
        the `mem_page` mirror — carrying its subject and dropping any outside the optional
        `[start, end)` `created_at` window. A tombstoned page's mirror row (and chunks) are dropped
        by the page-index job, so a removed document never surfaces here."""
        fused = fuse_hits(*await self._legs(query, subjects, OWNER_KIND_PAGE, limit), limit)
        if not fused:
            return ()
        ids = [UUID(hit.owner_id) for hit in fused]
        conditions = [mem_page.c.page_id.in_(ids)]
        if start is not None:
            conditions.append(mem_page.c.created_at >= start)
        if end is not None:
            conditions.append(mem_page.c.created_at < end)
        async with self.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(mem_page.c.page_id, mem_page.c.subject).where(*conditions)
                    )
                )
                .mappings()
                .all()
            )
        by_id = {row["page_id"]: row for row in rows}
        return tuple(
            SourceMatch(
                page_id=UUID(hit.owner_id),
                subject=by_id[UUID(hit.owner_id)]["subject"],
                text=hit.text,
                score=hit.score,
            )
            for hit in fused
            if UUID(hit.owner_id) in by_id
        )

    async def _legs(
        self, query: str, subjects: frozenset[str], owner_kind: str, limit: int
    ) -> tuple[tuple[Hit, ...], tuple[Hit, ...]]:
        embedding = await self._embed_query(query)
        lexical = await self.index.lexical(query, subjects, owner_kind, limit)
        vector = (
            await self.index.vector(embedding, subjects, owner_kind, limit) if embedding else ()
        )
        return lexical, vector

    async def _untail_leg(
        self, query: str, subjects: frozenset[str], limit: int
    ) -> tuple[Hit, ...]:
        """A lexical leg over the un-embedded tail — memory_item rows the index job has not chunked
        yet (`embedding_digest` NULL) — so a just-committed fact is recallable within the indexer's
        tick rather than only after it. Scored in-process by query-term count over the body (gbrain
        `lexical_score`); the scan is bounded to the newest TAIL_SCAN_MAX rows so a backlogged
        indexer cannot unbound it. Once a row is indexed it leaves this set and the index legs serve
        it, so the tail never double-counts an indexed row."""
        terms = [term for term in re.split(r"\W+", query.lower()) if term]
        if not terms or not subjects:
            return ()
        async with self.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(memory_item.c.id, memory_item.c.subject, memory_item.c.body)
                        .where(
                            memory_item.c.workspace_id == self.workspace_id,
                            memory_item.c.subject.in_(subjects),
                            memory_item.c.embedding_digest.is_(None),
                            memory_item.c.superseded_by.is_(None),
                        )
                        .order_by(memory_item.c.created_at.desc())
                        .limit(TAIL_SCAN_MAX)
                    )
                )
                .mappings()
                .all()
            )
        scored = tuple(
            Hit(
                chunk_digest=f"tail:{row['id']}",
                owner_kind=OWNER_KIND_MEMORY_ITEM,
                owner_id=str(row["id"]),
                subject=row["subject"],
                ordinal=0,
                text=row["body"],
                score=float(matches),
            )
            for row in rows
            if (matches := sum(row["body"].lower().count(term) for term in terms)) > 0
        )
        return tuple(sorted(scored, key=lambda hit: hit.score, reverse=True)[:limit])

    async def _embed_query(self, query: str) -> tuple[float, ...]:
        if not query.strip():
            return ()
        try:
            vectors = await self.embed.embed((query,))
        except Exception:
            logger.warning("memory.recall.embed_query_failed", exc_info=True)
            return ()
        return vectors[0] if vectors else ()

    async def _enrich(
        self, fused: tuple[Fused, ...], start: datetime | None, end: datetime | None
    ) -> tuple[Recalled, ...]:
        """Read the surviving (non-superseded) items back in fused order; a superseded item — or one
        outside the `[start, end)` `created_at` window — drops out here rather than being served."""
        if not fused:
            return ()
        ids = [UUID(hit.owner_id) for hit in fused]
        conditions = [
            memory_item.c.id.in_(ids),
            memory_item.c.superseded_by.is_(None),
        ]
        if start is not None:
            conditions.append(memory_item.c.created_at >= start)
        if end is not None:
            conditions.append(memory_item.c.created_at < end)
        async with self.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(
                            memory_item.c.id,
                            memory_item.c.subject,
                            memory_item.c.item_class,
                            memory_item.c.memory_kind,
                            memory_item.c.confidence,
                            memory_item.c.body,
                            memory_item.c.source_ref,
                            memory_item.c.created_at,
                        ).where(*conditions)
                    )
                )
                .mappings()
                .all()
            )
        by_id = {row["id"]: row for row in rows}
        return tuple(
            Recalled(
                memory_id=UUID(hit.owner_id),
                subject=by_id[UUID(hit.owner_id)]["subject"],
                item_class=by_id[UUID(hit.owner_id)]["item_class"],
                body=by_id[UUID(hit.owner_id)]["body"],
                source_ref=by_id[UUID(hit.owner_id)]["source_ref"],
                score=hit.score,
                memory_kind=by_id[UUID(hit.owner_id)]["memory_kind"],
                confidence=by_id[UUID(hit.owner_id)]["confidence"],
                created_at=by_id[UUID(hit.owner_id)]["created_at"],
            )
            for hit in fused
            if UUID(hit.owner_id) in by_id
        )


def store_for(ext: ExtensionContext) -> MemoryStore:
    """Build the memory workflow over a scoped context, failing loud when the deploy index/embed
    backends are not threaded onto it."""
    if ext.index is None or ext.embed is None:
        raise RuntimeError("memory requires the index and embed backends; none are wired")
    return MemoryStore(
        index=ext.index,
        embed=ext.embed,
        transaction=ext.transaction,
        workspace_id=ext.store.workspace_id,
    )


@dataclass(frozen=True)
class MemoryIndexer:
    """The derivation job: turn memory items due for indexing into chunks, off the write path. A run
    atomically claims a batch of rows whose `embedding_digest` is NULL and whose claim is unset or
    lease-expired — stamping `embedding_claimed_at` (Postgres `FOR UPDATE SKIP LOCKED`, SQLite the
    single writer) so an overlapping tick skips them and never double-embeds — chunks and embeds
    each body, then writes the content digest and clears the claim so the row is no longer due."""

    index: IndexBackend
    embed: EmbedClient
    transaction: Transaction
    chunker: TextChunker

    async def run(self) -> None:
        for item in await self._claim_due():
            await self._index_item(item)

    async def _claim_due(self) -> tuple[MemoryItem, ...]:
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=EMBED_CLAIM_LEASE_SECONDS)
        due = (
            sa.select(
                memory_item.c.id,
                memory_item.c.subject,
                memory_item.c.body,
                memory_item.c.item_class,
                memory_item.c.source_ref,
                memory_item.c.embedding_digest,
                memory_item.c.superseded_by,
            )
            .where(
                memory_item.c.embedding_digest.is_(None),
                sa.or_(
                    memory_item.c.embedding_claimed_at.is_(None),
                    memory_item.c.embedding_claimed_at < cutoff,
                ),
            )
            .limit(DUE_BATCH_MAX_ITEMS)
        )
        async with self.transaction() as connection:
            if connection.dialect.name == "postgresql":
                due = due.with_for_update(skip_locked=True)
            rows = (await connection.execute(due)).mappings().all()
            if rows:
                await connection.execute(
                    sa.update(memory_item)
                    .values(embedding_claimed_at=now, updated_at=sa.func.now())
                    .where(memory_item.c.id.in_([row["id"] for row in rows]))
                )
        return tuple(MemoryItem.model_validate(dict(row)) for row in rows)

    async def _index_item(self, item: MemoryItem) -> None:
        await chunk_embed_upsert(
            self.index,
            self.embed,
            self.chunker,
            OWNER_KIND_MEMORY_ITEM,
            str(item.id),
            item.subject,
            item.body,
        )
        digest = "sha256:" + hashlib.sha256(item.body.encode()).hexdigest()
        async with self.transaction() as connection:
            await connection.execute(
                sa.update(memory_item)
                .values(
                    embedding_digest=digest,
                    embedding_claimed_at=None,
                    updated_at=sa.func.now(),
                )
                .where(memory_item.c.id == item.id)
            )


@dataclass(frozen=True)
class PageIndexer:
    """The page derivation job: replay source-page changes off the core `PageFeed` and turn each
    into index chunks + a `mem_page` mirror row, off the write path. A single-owner `(changed_at,
    id)` cursor lives in the extension's ScopedStore — no lease, no double-embed: the cursor only
    advances, so a restart resumes exactly where it left off and a page re-appears only when its
    `updated_at` bumps. A tombstoned change drops the page's chunks and mirror row; every other
    change chunks and embeds the inlined body and upserts the mirror (subject + created_at for
    search's date window)."""

    pages: PageFeed
    index: IndexBackend
    embed: EmbedClient
    transaction: Transaction
    chunker: TextChunker
    cursor_store: ScopedStore

    async def run(self) -> None:
        stored = await self.cursor_store.get(PAGE_CURSOR_KEY)
        cursor = stored if isinstance(stored, str) else None
        while True:
            batch = await self.pages.pages_changed_since(cursor, PAGE_INDEX_BATCH)
            if not batch.changes:
                return
            for change in batch.changes:
                await self._apply(change)
            cursor = batch.next_cursor
            await self.cursor_store.put(PAGE_CURSOR_KEY, cursor)
            if len(batch.changes) < PAGE_INDEX_BATCH:
                return

    async def _apply(self, change: PageChange) -> None:
        if change.tombstone:
            await self.index.delete(IndexScope(OWNER_KIND_PAGE, str(change.page_id)))
            async with self.transaction() as connection:
                await connection.execute(
                    sa.delete(mem_page).where(mem_page.c.page_id == change.page_id)
                )
            return
        await chunk_embed_upsert(
            self.index,
            self.embed,
            self.chunker,
            OWNER_KIND_PAGE,
            str(change.page_id),
            change.subject,
            change.body,
        )
        async with self.transaction() as connection:
            updated = await connection.execute(
                sa.update(mem_page)
                .values(subject=change.subject, created_at=change.created_at)
                .where(mem_page.c.page_id == change.page_id)
            )
            if updated.rowcount == 0:
                await connection.execute(
                    sa.insert(mem_page).values(
                        page_id=change.page_id,
                        subject=change.subject,
                        created_at=change.created_at,
                    )
                )
