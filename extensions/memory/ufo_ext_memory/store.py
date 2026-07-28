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
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from itertools import chain
from typing import Literal, Self
from uuid import UUID, uuid5

import sqlalchemy as sa
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from ufo.sdk.audience import Audience, audience_subjects
from ufo.sdk.context import ExtensionContext, PageState
from ufo.sdk.index import (
    OWNER_KIND_MEMORY_ITEM,
    OWNER_KIND_PAGE,
    EmbedClient,
    Hit,
    IndexBackend,
    IndexScope,
    TextChunker,
    chunk_embed_upsert,
)
from ufo.sdk.sources import PageChange

RRF_K = 60
RRF_WEIGHT = 0.7
COSINE_WEIGHT = 0.3
TAIL_SCAN_MAX = 200
MEMORY_ITEM_NAMESPACE = UUID("32492d08-3cb7-59ac-8962-b2e384f024fc")
DUE_BATCH_MAX_ITEMS = 200
EMBED_CLAIM_LEASE_SECONDS = 300

TYPE_DIVERSITY_RATIO = 0.6
MAX_CONFIDENCE = 10
DEFAULT_CONFIDENCE = 5
MEMORY_INVENTORY_LIMIT = 500
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
PageStates = Callable[[tuple[UUID, ...]], Awaitable[dict[UUID, PageState]]]

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
    sa.Column("created_from_page_id", sa.Uuid, nullable=True),
    sa.Column("created_from_page_revision", sa.BigInteger, nullable=True),
    sa.Column("as_of", sa.DateTime(timezone=True), nullable=True),
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
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("subject", sa.Text, nullable=False),
    sa.Column("revision", sa.BigInteger, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
)


def recall_subjects(audience: Audience) -> frozenset[str]:
    return audience_subjects(audience)


class MemoryInventoryItem(BaseModel):
    """One stored memory as the operator explorer reads it — the whole row plus the derived recall
    signals, so both how it was ingested and how it decays are visible without re-deriving them.

    Ingestion/creation: `source_ref` is what produced it (a tool write, a synced source, a
    consolidation); `created_at` is when it was committed and `as_of` is when its source information
    was current; `embedding_digest` NULL means it is still due for the index job (not yet
    chunked/embedded), and `embedding_claimed_at` set means the indexer currently holds a lease on
    it. Recall/decay: `half_life_days` is the recency half-life for its kind (None for
    episodic/semantic, which never decay) and `decay_factor` is the live multiplier recall applies
    to its relevance (`(confidence/10)·0.5**(age_days/half_life)` for facts, else 1.0). Lifecycle:
    `superseded_by` non-NULL means consolidation replaced it; `subject` is its exact audience."""

    subject: str
    body: str
    item_class: ItemClass
    memory_kind: MemoryKind
    confidence: int
    source_ref: str | None
    created_from_page_id: UUID | None
    created_from_page_revision: int | None
    as_of: datetime | None
    embedding_digest: str | None
    embedding_claimed_at: datetime | None
    superseded_by: UUID | None
    created_at: datetime
    age_days: float
    half_life_days: float | None
    decay_factor: float


async def inventory(
    transaction: Transaction, workspace_id: UUID
) -> tuple[MemoryInventoryItem, ...]:
    """Every memory_item in the workspace, newest first — the whole durable store an operator
    explores, not a recall: no query, no similarity, no diversity cap, superseded rows kept. Each
    row carries its ingestion state and the live recall-decay signals (age, half-life, and the exact
    decay multiplier recall would apply now), computed against one `now` so the whole listing is a
    consistent snapshot. The newest `MEMORY_INVENTORY_LIMIT` rows are read over the
    `memory_item_inventory` index on `(workspace_id, created_at)`, so this is a bounded index scan —
    not a full-table scan and sort — and the `LIMIT` bounds what's serialized; one workspace's large
    store never stalls the shared request loop. Scoping is both the ambient RLS binding the opener
    carries and the explicit workspace predicate; grouping by `subject` is the reader's to do."""
    async with transaction() as connection:
        rows = (
            (
                await connection.execute(
                    sa.select(memory_item)
                    .where(memory_item.c.workspace_id == workspace_id)
                    .order_by(memory_item.c.created_at.desc(), memory_item.c.id)
                    .limit(MEMORY_INVENTORY_LIMIT)
                )
            )
            .mappings()
            .all()
        )
    now = datetime.now(UTC)
    return tuple(
        MemoryInventoryItem(
            subject=row["subject"],
            body=row["body"],
            item_class=row["item_class"],
            memory_kind=row["memory_kind"],
            confidence=row["confidence"],
            source_ref=row["source_ref"],
            created_from_page_id=row["created_from_page_id"],
            created_from_page_revision=row["created_from_page_revision"],
            as_of=row["as_of"],
            embedding_digest=row["embedding_digest"],
            embedding_claimed_at=row["embedding_claimed_at"],
            superseded_by=row["superseded_by"],
            created_at=row["created_at"],
            age_days=max(
                0.0,
                (now - _aware(row["as_of"] or row["created_at"])).total_seconds() / 86400.0,
            ),
            half_life_days=half_life_days(row["item_class"], row["memory_kind"]),
            decay_factor=decay_multiplier(
                row["item_class"],
                row["memory_kind"],
                row["confidence"],
                row["as_of"] or row["created_at"],
                now,
            ),
        )
        for row in rows
    )


def _aware(when: datetime) -> datetime:
    return when if when.tzinfo else when.replace(tzinfo=UTC)


class MemoryWrite(BaseModel):
    """What a commit records: the subject scoping visibility, the body, its class, its provenance,
    and the recall-decay inputs — `created_from_page_id` is the synced page a derivation distilled
    it from (the `created_from` link) and `created_from_page_revision` binds it to that page
    version; `source_ref` is a free-form note for tool writes, `as_of` says when the source
    information was current, `memory_kind` selects the recency half-life
    (fact/preference/decision/event/task), and `confidence` (1..10) scales a fact's decayed rank."""

    subject: str
    body: str
    item_class: ItemClass = FACT
    memory_kind: MemoryKind = KIND_FACT
    confidence: int = Field(default=DEFAULT_CONFIDENCE, ge=1, le=MAX_CONFIDENCE)
    source_ref: str | None = None
    created_from_page_id: UUID | None = None
    created_from_page_revision: int | None = None
    as_of: datetime | None = None

    @model_validator(mode="after")
    def page_origin_is_complete(self) -> Self:
        if (self.created_from_page_id is None) != (self.created_from_page_revision is None):
            raise ValueError("page-derived memory needs both page id and revision")
        return self


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
    created_from_page_id: UUID | None = None
    created_from_page_revision: int | None = None
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
    as_of: datetime | None = None
    recall_mode: str | None = None


def half_life_days(item_class: str, memory_kind: str) -> float | None:
    """The recency half-life recall decays this item by, or None when it carries none: only facts
    decay (per-`memory_kind` half-lives: fact/preference/decision/event/task); episodic/semantic
    rank on relevance alone."""
    if item_class != FACT:
        return None
    return HALFLIFE_DAYS.get(memory_kind, HALFLIFE_DAYS[KIND_FACT])


def decay_multiplier(
    item_class: str, memory_kind: str, confidence: int, as_of: datetime | None, now: datetime
) -> float:
    """The factor recall multiplies an item's relevance by — gbrain `effectiveConfidence`. A fact's
    is `(confidence / 10) * 0.5 ** (age_days / halflife)`; every other class carries no decay and
    stays 1.0. The one home for the decay math, so recall's ranking and the explorer's reported
    weight are the same number."""
    halflife = half_life_days(item_class, memory_kind)
    if halflife is None or as_of is None:
        return 1.0
    base = confidence / MAX_CONFIDENCE
    age_days = max(0.0, (now - _aware(as_of)).total_seconds() / 86400.0)
    return base * (0.5 ** (age_days / halflife))


def decay_factor(item: Recalled, now: datetime) -> float:
    return decay_multiplier(
        item.item_class, item.memory_kind, item.confidence, item.as_of or item.created_at, now
    )


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
    created_at: datetime


@dataclass(frozen=True)
class MemoryStore:
    """The memory workflow over the extension's scoped handle: commit one item, recall facts, search
    source pages. Holds the deploy index/embed backends core threaded onto the context and the
    workspace-scoped transaction opener; reads and writes only the extension's own `memory_item`."""

    index: IndexBackend
    embed: EmbedClient
    transaction: Transaction
    workspace_id: UUID
    page_states: PageStates

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
                created_from_page_id=write.created_from_page_id,
                created_from_page_revision=write.created_from_page_revision,
                as_of=write.as_of,
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
                        memory_item.c.created_from_page_id: (
                            statement.excluded.created_from_page_id
                        ),
                        memory_item.c.created_from_page_revision: (
                            statement.excluded.created_from_page_revision
                        ),
                        memory_item.c.embedding_claimed_at: sa.case(
                            (
                                sa.or_(
                                    memory_item.c.created_from_page_id.is_distinct_from(
                                        statement.excluded.created_from_page_id
                                    ),
                                    memory_item.c.created_from_page_revision.is_distinct_from(
                                        statement.excluded.created_from_page_revision
                                    ),
                                ),
                                None,
                            ),
                            else_=memory_item.c.embedding_claimed_at,
                        ),
                        memory_item.c.as_of: statement.excluded.as_of,
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
        enriched = await self._enrich(
            fuse_recall(lexical, vector, tail, limit), subjects, start, end
        )
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
        `[start, end)` `created_at` window. The mirror's subject and revision must still match the
        core page, so a changed or removed document cannot disclose stale chunks while its
        page-index job catches up."""
        fused = fuse_hits(*await self._legs(query, subjects, OWNER_KIND_PAGE, limit), limit)
        if not fused:
            return ()
        ids = [UUID(hit.owner_id) for hit in fused]
        conditions: list[ColumnElement[bool]] = [
            mem_page.c.page_id.in_(ids),
            mem_page.c.workspace_id == self.workspace_id,
            mem_page.c.subject.in_(subjects),
        ]
        if start is not None:
            conditions.append(mem_page.c.created_at >= start)
        if end is not None:
            conditions.append(mem_page.c.created_at < end)
        async with self.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(
                            mem_page.c.page_id,
                            mem_page.c.subject,
                            mem_page.c.revision,
                            mem_page.c.created_at,
                        ).where(*conditions)
                    )
                )
                .mappings()
                .all()
            )
        by_id = {row["page_id"]: row for row in rows}
        current = await self.page_states(tuple(by_id))
        return tuple(
            SourceMatch(
                page_id=UUID(hit.owner_id),
                subject=by_id[UUID(hit.owner_id)]["subject"],
                text=hit.text,
                score=hit.score,
                created_at=by_id[UUID(hit.owner_id)]["created_at"],
            )
            for hit in fused
            if UUID(hit.owner_id) in by_id
            and (state := current.get(UUID(hit.owner_id))) is not None
            and state.subject == by_id[UUID(hit.owner_id)]["subject"]
            and state.revision == by_id[UUID(hit.owner_id)]["revision"]
            and state.subject in subjects
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
        self,
        fused: tuple[Fused, ...],
        subjects: frozenset[str],
        start: datetime | None,
        end: datetime | None,
    ) -> tuple[Recalled, ...]:
        """Read the surviving (non-superseded) items back in fused order; a superseded item — or one
        outside the `[start, end)` `created_at` window — drops out here rather than being served."""
        if not fused:
            return ()
        ids = [UUID(hit.owner_id) for hit in fused]
        conditions: list[ColumnElement[bool]] = [
            memory_item.c.id.in_(ids),
            memory_item.c.workspace_id == self.workspace_id,
            memory_item.c.subject.in_(subjects),
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
                            memory_item.c.created_from_page_id,
                            memory_item.c.created_from_page_revision,
                            memory_item.c.as_of,
                            memory_item.c.created_at,
                        ).where(*conditions)
                    )
                )
                .mappings()
                .all()
            )
        by_id = {row["id"]: row for row in rows}
        page_ids = tuple(
            row["created_from_page_id"] for row in rows if row["created_from_page_id"] is not None
        )
        current = await self.page_states(page_ids)
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
                as_of=by_id[UUID(hit.owner_id)]["as_of"],
            )
            for hit in fused
            if UUID(hit.owner_id) in by_id
            and (
                by_id[UUID(hit.owner_id)]["created_from_page_id"] is None
                or (
                    (state := current.get(by_id[UUID(hit.owner_id)]["created_from_page_id"]))
                    is not None
                    and state.subject == by_id[UUID(hit.owner_id)]["subject"]
                    and state.revision == by_id[UUID(hit.owner_id)]["created_from_page_revision"]
                    and state.subject in subjects
                )
            )
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
        page_states=ext.page_states,
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
    page_states: PageStates

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
                memory_item.c.created_from_page_id,
                memory_item.c.created_from_page_revision,
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
        if item.created_from_page_id is not None:
            state = (await self.page_states((item.created_from_page_id,))).get(
                item.created_from_page_id
            )
            if (
                state is None
                or state.subject != item.subject
                or state.revision != item.created_from_page_revision
            ):
                await self._discard_stale(item)
                return
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
        current = (
            {}
            if item.created_from_page_id is None
            else await self.page_states((item.created_from_page_id,))
        )
        if item.created_from_page_id is not None and (
            (state := current.get(item.created_from_page_id)) is None
            or state.subject != item.subject
            or state.revision != item.created_from_page_revision
        ):
            await self._discard_stale(item)
            return
        async with self.transaction() as connection:
            updated = await connection.execute(
                sa.update(memory_item)
                .values(
                    embedding_digest=digest,
                    embedding_claimed_at=None,
                    updated_at=sa.func.now(),
                )
                .where(
                    memory_item.c.id == item.id,
                    memory_item.c.subject == item.subject,
                    memory_item.c.body == item.body,
                    memory_item.c.created_from_page_id == item.created_from_page_id,
                    memory_item.c.created_from_page_revision == item.created_from_page_revision,
                )
            )
        if updated.rowcount == 0:
            async with self.transaction() as connection:
                current_row = (
                    await connection.execute(
                        sa.select(memory_item.c.subject, memory_item.c.body).where(
                            memory_item.c.id == item.id
                        )
                    )
                ).one_or_none()
            if (
                current_row is None
                or current_row.subject != item.subject
                or current_row.body != item.body
            ):
                await self.index.delete(IndexScope(OWNER_KIND_MEMORY_ITEM, str(item.id)))

    async def _discard_stale(self, item: MemoryItem) -> None:
        async with self.transaction() as connection:
            deleted = await connection.execute(
                sa.delete(memory_item).where(
                    memory_item.c.id == item.id,
                    memory_item.c.subject == item.subject,
                    memory_item.c.created_from_page_id == item.created_from_page_id,
                    memory_item.c.created_from_page_revision == item.created_from_page_revision,
                )
            )
        if deleted.rowcount > 0:
            await self.index.delete(IndexScope(OWNER_KIND_MEMORY_ITEM, str(item.id)))


@dataclass(frozen=True)
class PageIndexer:
    """The page derivation the memory extension's `page_change` hook drives: turn each replayed
    source-page change into index chunks + a `mem_page` mirror row, off the write path. The core
    page-change runner owns the cursor and the batch loop and hands this one delivered batch to
    apply; the derivation stays idempotent so a replayed change re-upserts the same rows. A
    tombstoned change drops the page's chunks and mirror row; every other change removes facts
    derived from a different subject or page revision, then accepts the payload only while both
    still match the core page before and after embedding."""

    index: IndexBackend
    embed: EmbedClient
    transaction: Transaction
    chunker: TextChunker
    workspace_id: UUID
    page_states: PageStates

    async def apply(self, changes: tuple[PageChange, ...]) -> None:
        for change in changes:
            await self._apply(change)

    async def _apply(self, change: PageChange) -> None:
        current = (await self.page_states((change.page_id,))).get(change.page_id)
        await self._remove_stale_memories(change.page_id, current)
        if change.tombstone:
            if current is not None:
                return
            await self.index.delete(IndexScope(OWNER_KIND_PAGE, str(change.page_id)))
            async with self.transaction() as connection:
                await connection.execute(
                    sa.delete(mem_page).where(mem_page.c.page_id == change.page_id)
                )
            return
        if (
            current is None
            or current.subject != change.subject
            or current.revision != change.revision
        ):
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
            current.subject,
            change.body,
        )
        if (await self.page_states((change.page_id,))).get(change.page_id) != current:
            await self.index.delete(IndexScope(OWNER_KIND_PAGE, str(change.page_id)))
            async with self.transaction() as connection:
                await connection.execute(
                    sa.delete(mem_page).where(mem_page.c.page_id == change.page_id)
                )
            return
        async with self.transaction() as connection:
            updated = await connection.execute(
                sa.update(mem_page)
                .values(
                    subject=current.subject,
                    revision=current.revision,
                    created_at=change.created_at,
                )
                .where(mem_page.c.page_id == change.page_id)
            )
            if updated.rowcount == 0:
                await connection.execute(
                    sa.insert(mem_page).values(
                        page_id=change.page_id,
                        workspace_id=self.workspace_id,
                        subject=current.subject,
                        revision=current.revision,
                        created_at=change.created_at,
                    )
                )

    async def _remove_stale_memories(self, page_id: UUID, state: PageState | None) -> None:
        conditions: tuple[ColumnElement[bool], ...] = (
            memory_item.c.workspace_id == self.workspace_id,
            memory_item.c.created_from_page_id == page_id,
        )
        if state is not None:
            conditions = (
                *conditions,
                sa.or_(
                    memory_item.c.subject != state.subject,
                    memory_item.c.created_from_page_revision != state.revision,
                    memory_item.c.created_from_page_revision.is_(None),
                ),
            )
        async with self.transaction() as connection:
            stale = tuple(
                (
                    await connection.execute(
                        sa.delete(memory_item).where(*conditions).returning(memory_item.c.id)
                    )
                )
                .scalars()
                .all()
            )
        for memory_id in stale:
            await self.index.delete(IndexScope(OWNER_KIND_MEMORY_ITEM, str(memory_id)))
