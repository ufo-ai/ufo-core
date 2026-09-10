"""The memory domain: the `memory_item` table the extension owns, recall's fusion, and the indexer.

`commit` persists one `memory_item` and derives nothing — chunking and embedding are the index
job's work, never inline on a write. `recall` embeds the query once, asks the deploy index backend
for its lexical and vector hits under the caller's subject filter, fuses them with reciprocal-rank
fusion (K=60), and reads the surviving items back. `search_sources` fuses the same legs over
source-page chunks and reads the matched snippet straight off the index (a tombstoned page's chunks
are already gone). `MemoryIndexer` is the derivation job: it atomically claims memory items whose
`embedding_digest` is NULL, chunks and embeds each body whose chunks the index does not already
hold, and stamps the digest so the row is no longer due. A claimed publishable body the index still
holds — an identical re-commit that only rebound the row's page revision, or a lease-expired retry —
settles without paying the embed again (the id is content-addressed over the body, so held chunks
are that body's); a body whose chunks were withdrawn is not held and is re-embedded. A row the page
pass retired carries `retired_at`, the one column `commit` never clears and every read here fences
on: a page still synced re-commits its identical body each derivation, so a judgement kept in
`superseded_by` would be undone by the next tick, and one kept here stands.
Everything reaches the database through the extension's workspace-scoped
`transaction()` and the deploy index/embed backends core threads onto its context — never a core
internal.
"""

import asyncio
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
from ufo.sdk.context import ExtensionContext, PageState, ScopedStore, SourceReader
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
"""How near something must be for a query the lexical legs matched nowhere to recall anything. A
vector search answers every query with its closest chunks however far away they are, so without a
floor a meaningless string recalls whatever it happens to sit nearest.

The bar judges the query, never a row inside a wordful query's pool. A cosine height means
something only within one corpus on one embedding model — measured across three corpora, correct
answers sit at 0.44 where garbage tops 0.16, and garbage reaches 0.50 where a terse question's
answer sits at 0.34 — so a constant held against each row cuts real answers wherever the corpus
runs cool, and the rows it cuts first are the ones worded unlike their question: a memory filed
under a full name, asked for by handle. What a constant can judge is total lexical silence, because
across those same corpora every real question matched some word and no random string matched any.
The value sits just above the highest garbage measured: 200 random strings in five shapes against
the largest live corpus (29k items, `text-embedding-3-large`) reached 0.5038 at the very top."""
RECALL_COSINE_FLOOR = 0.52
TAIL_SCAN_MAX = 200
RECALL_CANDIDATE_POOL = 200
MEMORY_ITEM_NAMESPACE = UUID("32492d08-3cb7-59ac-8962-b2e384f024fc")
DUE_BATCH_MAX_ITEMS = 200
EMBED_CLAIM_LEASE_SECONDS = 300

TYPE_DIVERSITY_RATIO = 0.6
MAX_CONFIDENCE = 10
DEFAULT_CONFIDENCE = 5
MEMORY_INVENTORY_LIMIT = 500
MEMORY_BODY_MAX_CHARS = 2_000
"""How long a committed body runs. A memory is one self-contained statement a reader meets alone,
months later; 2,000 characters holds the longest statement still worth keeping whole, and a body
past it is a document, which lives as a page. Shape is the writer's brief, never this bound's: the
extraction prompt holds a fact to a scannable row, the consolidator's word and sentence budgets
hold a paragraph."""
HALFLIFE_DAYS: dict[str, float] = {
    "fact": 365.0,
    "preference": 180.0,
    "decision": 120.0,
    "event": 30.0,
    "task": 14.0,
}

logger = logging.getLogger(__name__)

ItemClass = Literal["fact", "episodic", "semantic", "section", "overview"]
FACT: Literal["fact"] = "fact"
EPISODIC: ItemClass = "episodic"
SEMANTIC: ItemClass = "semantic"
SECTION: ItemClass = "section"
OVERVIEW: ItemClass = "overview"

MemoryKind = Literal["fact", "preference", "decision", "event", "task"]
KIND_FACT: MemoryKind = "fact"

Transaction = Callable[[], AbstractAsyncContextManager[AsyncConnection]]
PageStates = Callable[[tuple[UUID, ...]], Awaitable[dict[UUID, PageState]]]
ReadablePageStates = Callable[
    [tuple[UUID, ...], SourceReader],
    Awaitable[dict[UUID, PageState]],
]
ReadableSourceIds = Callable[[SourceReader], Awaitable[frozenset[UUID]]]

_metadata = sa.MetaData()
memory_item = sa.Table(
    "memory_item",
    _metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("subject", sa.Text, nullable=False),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("item_class", sa.Text, nullable=False),
    sa.Column("memory_kind", sa.Text, nullable=False, server_default=KIND_FACT),
    sa.Column("confidence", sa.Integer, nullable=False, server_default="5"),
    sa.Column("source_ref", sa.Text, nullable=True),
    sa.Column("created_from_page_uid", sa.Uuid, nullable=True),
    sa.Column("created_from_page_revision", sa.BigInteger, nullable=True),
    sa.Column("source_uid", sa.Uuid, nullable=True),
    sa.Column("as_of", sa.DateTime(timezone=True), nullable=True),
    sa.Column("embedding_digest", sa.Text, nullable=True),
    sa.Column("embedding_claimed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("superseded_by", sa.Uuid, nullable=True),
    sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "(created_from_page_uid is null and created_from_page_revision is null "
        "and source_uid is null) or (created_from_page_uid is not null "
        "and created_from_page_revision is not null and source_uid is not null)",
        name="memory_item_page_source",
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id"],
        ["workspace.id"],
        ondelete="CASCADE",
        name="memory_item_workspace_id_fkey",
    ),
    sa.Index("memory_item_due", "embedding_digest"),
    sa.Index("memory_item_inventory", "workspace_id", "created_at"),
    sa.Index(
        "memory_item_consolidate",
        "workspace_id",
        "created_at",
        postgresql_where=sa.text("item_class = 'fact' and superseded_by is null"),
        sqlite_where=sa.text("item_class = 'fact' and superseded_by is null"),
    ),
)

memory_source = sa.Table(
    "memory_source",
    _metadata,
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("memory_item_id", sa.Uuid, nullable=False),
    sa.Column("source_uid", sa.Uuid, nullable=False),
    sa.Column("page_uid", sa.Uuid, nullable=False),
    sa.Column("revision", sa.BigInteger, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(
        ["memory_item_id"],
        ["memory_item.id"],
        ondelete="CASCADE",
        name="memory_source_memory_item_id_fkey",
    ),
    sa.PrimaryKeyConstraint("memory_item_id", "page_uid", name="memory_source_pkey"),
)

mem_page = sa.Table(
    "mem_page",
    _metadata,
    sa.Column("page_uid", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("subject", sa.Text, nullable=False),
    sa.Column("revision", sa.BigInteger, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(
        ["workspace_id"],
        ["workspace.id"],
        ondelete="CASCADE",
        name="mem_page_workspace_id_fkey",
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id", "page_uid"],
        ["page.workspace_id", "page.uid"],
        ondelete="CASCADE",
        name="mem_page_page_uid_fkey",
    ),
)


def recall_subjects(audience: Audience) -> frozenset[str]:
    return audience_subjects(audience)


def clip_to_word(text: str, limit: int) -> str:
    """`text` cut back to the last whole word that fits `limit`, with the ellipsis inside the count.
    Text already inside the limit stands exactly as written. Every writer on this path is told its
    budget, so a cut is what a model that overran leaves a member: half a word tells them less than
    the word before it and costs the same line."""
    if len(text) <= limit:
        return text
    kept = text[: limit - 1]
    cut = kept.rfind(" ")
    return (kept if cut < 0 else kept[:cut]).rstrip(" ,;:—-") + "…"


def _readable_link(source_ids: frozenset[UUID]) -> ColumnElement[bool]:
    """The reach fence over the source link set: a page-derived row is readable when the reader may
    read any one of the sources that derived it, not only the source of its current primary binding
    — a fact learned from two feeds is reached through either. Which sources a reader may read is
    core's one answer, read through `readable_source_ids`: a connector grant on the source's
    connection, or a connection the workspace shares read by its main agent. Correlates to
    `memory_item.c.id`, so it composes into a read as an `EXISTS` predicate."""
    return sa.exists().where(
        memory_source.c.memory_item_id == memory_item.c.id,
        memory_source.c.source_uid.in_(source_ids),
    )


class MemoryInventoryItem(BaseModel):
    """One stored memory as the operator explorer reads it — the whole row plus the derived recall
    signals, so both how it was ingested and how it decays are visible without re-deriving them.

    Ingestion/creation: `source_ref` is what produced it (a tool write, a synced source, a
    consolidation); `created_at` is when it was committed and `as_of` is when its source information
    was current; `embedding_digest` NULL means it is still due for the index job, and set means the
    job settled that body — published as chunks, or withheld because its page moved on — while
    `embedding_claimed_at` set means the indexer currently holds a lease on it. Recall/decay:
    `half_life_days` is the recency half-life for its kind (None for episodic/semantic, which never
    decay) and `decay_factor` is the live multiplier recall applies to its relevance
    (`(confidence/10)·0.5**(age_days/half_life)` for facts, else 1.0). Lifecycle: `superseded_by`
    non-NULL means consolidation replaced it; `subject` is its exact audience. `source_id` is the
    source of the derivation it currently binds to (the last to write it); `source_ids` is the full
    set of sources that derived this one fact — the same fact learned from two feeds is one row
    reachable through either, so the operator sees every feed it came from, not only the last."""

    subject: str
    body: str
    item_class: ItemClass
    memory_kind: MemoryKind
    confidence: int
    source_ref: str | None
    created_from_page_id: UUID | None
    created_from_page_revision: int | None
    source_id: UUID | None
    source_ids: tuple[UUID, ...]
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
        links: dict[UUID, list[UUID]] = {}
        for link in (
            await connection.execute(
                sa.select(memory_source.c.memory_item_id, memory_source.c.source_uid)
                .where(
                    memory_source.c.workspace_id == workspace_id,
                    memory_source.c.memory_item_id.in_([row["id"] for row in rows]),
                )
                .order_by(memory_source.c.created_at, memory_source.c.source_uid)
            )
        ).all():
            links.setdefault(link.memory_item_id, []).append(link.source_uid)
    now = datetime.now(UTC)
    return tuple(
        MemoryInventoryItem(
            subject=row["subject"],
            body=row["body"],
            item_class=row["item_class"],
            memory_kind=row["memory_kind"],
            confidence=row["confidence"],
            source_ref=row["source_ref"],
            created_from_page_id=row["created_from_page_uid"],
            created_from_page_revision=row["created_from_page_revision"],
            source_id=row["source_uid"],
            source_ids=tuple(links.get(row["id"], ())),
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
    and the recall-decay inputs — `created_from_page_id` and `source_id` identify the synced page
    a derivation distilled it from, while `created_from_page_revision` binds it to that page
    version; `source_ref` is a free-form note for tool writes, `as_of` says when the source
    information was current, `memory_kind` selects the recency half-life
    (fact/preference/decision/event/task), and `confidence` (1..10) scales a fact's decayed rank.

    The body is held to `MEMORY_BODY_MAX_CHARS`. It is a bound and never a cut: every writer on
    this path is told its budget, so a body that ran past it is a writer that ignored one, and
    `clip_to_word` is where a caller decides to trim."""

    subject: str
    body: str
    item_class: ItemClass = FACT
    memory_kind: MemoryKind = KIND_FACT
    confidence: int = Field(default=DEFAULT_CONFIDENCE, ge=1, le=MAX_CONFIDENCE)
    source_ref: str | None = None
    created_from_page_id: UUID | None = None
    created_from_page_revision: int | None = None
    source_id: UUID | None = None
    as_of: datetime | None = None

    @model_validator(mode="after")
    def body_is_within_budget(self) -> Self:
        if len(self.body) > MEMORY_BODY_MAX_CHARS:
            raise ValueError(
                f"a {self.item_class} body runs to {MEMORY_BODY_MAX_CHARS} characters,"
                f" not {len(self.body)}"
            )
        return self

    @model_validator(mode="after")
    def page_origin_is_complete(self) -> Self:
        page_origin = (
            self.created_from_page_id is not None,
            self.created_from_page_revision is not None,
            self.source_id is not None,
        )
        if any(page_origin) and not all(page_origin):
            raise ValueError("page-derived memory needs page id, revision, and source id")
        return self


class MemoryItem(BaseModel):
    """A stored memory row as the derivation job loads it. `embedding_digest` NULL means the item is
    due for indexing; `superseded_by` points at the item that replaced it, and `retired_at` says the
    page pass judged the wiki reads better without it."""

    id: UUID
    subject: str
    body: str
    item_class: ItemClass
    memory_kind: MemoryKind = KIND_FACT
    confidence: int = DEFAULT_CONFIDENCE
    source_ref: str | None = None
    created_from_page_id: UUID | None = None
    created_from_page_revision: int | None = None
    source_id: UUID | None = None
    embedding_digest: str | None = None
    superseded_by: UUID | None = None
    retired_at: datetime | None = None


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
    ranking, where the fused rank across the lexical and vector legs is the whole signal.

    A query whose lexical leg matched nothing is held to `RECALL_COSINE_FLOOR`, as recall is: this
    search answers the same box and the same tool, so a query with no meaning must come back empty
    here too. Fused rank cannot carry that bar, being relative to whatever the legs returned; and
    once any page is worded the query is a real question, so the fusion ranks everything — the page
    a member wants is not always the page carrying their words."""
    fused = _fuse((lexical, vector), vector)
    floor = 0.0 if lexical else RECALL_COSINE_FLOOR
    kept = {owner_id: held for owner_id, held in fused.items() if held[1] >= floor}
    ranked = sorted(kept.items(), key=lambda item: item[1][0], reverse=True)[:limit]
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
    fact ranks on normalized RRF alone until the index job serves it.

    A query neither lexical leg matched anywhere stands only on cosine, every row held to
    `RECALL_COSINE_FLOOR`: the vector leg answers every query with its nearest chunks, so without
    that floor a string with no meaning recalls whatever it sits closest to. The normalized RRF
    cannot carry the floor itself — it is relative to the top row of whatever came back, so the
    best of a set of far rows still scores 1.0. Once either leg worded anything the floor lifts
    for the whole pool: the query is one a member means, and the row it wants is not always a row
    carrying its words — a memory filed under a full name, asked for by handle, sits under any bar
    high enough to stop garbage."""
    fused = _fuse((lexical, vector, tail), vector)
    floor = 0.0 if lexical or tail else RECALL_COSINE_FLOOR
    top_rrf = max((rrf for rrf, _cosine, _text in fused.values()), default=0.0) or 1.0
    scored = [
        (owner_id, RRF_WEIGHT * (rrf / top_rrf) + COSINE_WEIGHT * cosine, text)
        for owner_id, (rrf, cosine, text) in fused.items()
        if cosine >= floor
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
    decay (per-`memory_kind` half-lives: fact/preference/decision/event/task); every other class
    ranks on relevance alone."""
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


RECALL_OVERLAP_JACCARD = 0.6
RECALL_ITEM_MAX_CHARS = 2_000
RECALL_DEDUP_OVERSAMPLE = 4


def _body_shingles(body: str) -> frozenset[str]:
    words = [word for word in re.split(r"\W+", body.lower()) if word]
    return frozenset(" ".join(words[i : i + 3]) for i in range(max(1, len(words) - 2)))


def drop_near_duplicates(items: tuple[Recalled, ...], keep: int) -> tuple[Recalled, ...]:
    """Two live copies of one fact rank together and burn injection slots restating it — the
    audited store held facts in 3-27 live copies. Word-trigram Jaccard is deterministic and cheap
    enough for the recall soft timeout; the dedup sweep is the real fix, this guards the slots
    against what it has not healed yet. Shingled over each body's injectable prefix only
    (RECALL_ITEM_MAX_CHARS) — recall truncates every line there anyway, so prefix-equality is
    injected-content-equality.

    It walks only as far as `keep` distinct items, and shingles nothing past them: the caller reads
    a candidate pool far larger than the slots it can fill, and a body ranked below the last usable
    slot costs a turn nothing to skip. Even bounded, the pass is arithmetic over trigram sets of up
    to RECALL_ITEM_MAX_CHARS — its caller runs it off the event loop."""
    kept: list[Recalled] = []
    shingles: list[frozenset[str]] = []
    for item in items:
        if len(kept) >= keep:
            break
        own = _body_shingles(item.body[:RECALL_ITEM_MAX_CHARS])
        if any(
            len(own & seen) / len(own | seen) >= RECALL_OVERLAP_JACCARD
            for seen in shingles
            if own | seen
        ):
            continue
        kept.append(item)
        shingles.append(own)
    return tuple(kept)


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
    """The memory workflow over the extension's scoped handle: commit one item, retire what a page's
    settled revision replaced, recall facts, search source pages. Holds the deploy index/embed
    backends core threaded onto the context and the workspace-scoped transaction opener; reads and
    writes only the extension's own `memory_item`."""

    index: IndexBackend
    embed: EmbedClient
    transaction: Transaction
    workspace_id: UUID
    page_states: PageStates
    readable_page_states: ReadablePageStates | None = None
    readable_source_ids: ReadableSourceIds | None = None

    async def commit(self, write: MemoryWrite) -> UUID:
        """Persist one memory_item with no derived state, and answer the row it landed on:
        embedding_digest stays NULL, marking the row due for the index job — the sole producer of
        chunks and embeddings. The id is content-addressed over
        `(workspace, subject, item_class, body)`, so the same fact learned from two sources is one
        row, not two; each page that derived it is recorded as an additive `memory_source` link, one
        per page, and the row's own `(page, revision, source)` records the derivation it currently
        binds to. The id comes back because a caller deriving a whole page's facts is the only thing
        that knows which rows that page still stands behind, and `supersede_page_facts` retires the
        rest by exactly that answer.

        Re-committing upserts the decay inputs in place rather
        than accumulating a duplicate recallable row; a re-commit that binds it to another page
        revision makes it due again, since whether that revision may be published is the index job's
        question to answer, while an identical re-commit at the same binding leaves the existing
        chunks and their digest untouched.

        A reworded restatement hashes to a fresh id, so the content address cannot upsert it and
        both bodies land live. Retiring one onto the other is dedup — derived state, which this
        write path never produces — so `MemoryDeduper` is the sole superseder of a tool-written
        row, and near-duplicate copies accrete until the sweep's next eligible rotation collapses
        them onto the newest one. The sweep's own age floor is the second span they stand through:
        a row younger than DEDUP_MIN_AGE is invisible to it, so a burst of restatements is
        recallable in full until it ages into history.

        Re-asserting a retired body brings it back: the upsert clears `superseded_by`, so a member
        restating what the sweep retired makes it live again, and `created_at` moves to now — a
        revival is a fresh assertion, so it carries fresh recency for recall's decay and for the
        sweep, whose winner is the newest copy. Only a revival moves it: an identical re-commit of
        a live row keeps the row's original `created_at`, since nothing about it was restated.
        Without the move, the copy that retired it would still be the newer row, and the next sweep
        would retire the member's restatement right back. Its chunks and digest stand — the stamp
        is a read-time fence that never withdrew them, and the id is content-addressed over the
        body, so what the index holds is still exactly this body's. Without that clear, the
        restatement would land back under the fence the sweep set and stay invisible to every
        reader.

        `retired_at` is the one column this upsert leaves exactly as it found it. A page still
        synced re-commits its identical body on every derivation, so a judgement written where the
        upsert reaches would stand until the next tick and no longer; the page pass retires a row by
        stamping that column, and the row it retired stays retired through every re-derivation of
        the body behind it."""
        item_id = uuid5(
            MEMORY_ITEM_NAMESPACE,
            "\x00".join((str(self.workspace_id), write.subject, write.item_class, write.body)),
        )
        async with self.transaction() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            statement = insert(memory_item).values(
                id=item_id,
                workspace_id=self.workspace_id,
                subject=write.subject,
                body=write.body,
                item_class=write.item_class,
                memory_kind=write.memory_kind,
                confidence=write.confidence,
                source_ref=write.source_ref,
                created_from_page_uid=write.created_from_page_id,
                created_from_page_revision=write.created_from_page_revision,
                source_uid=write.source_id,
                as_of=write.as_of,
                superseded_by=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
            rebound = sa.or_(
                memory_item.c.created_from_page_uid.is_distinct_from(
                    statement.excluded.created_from_page_uid
                ),
                memory_item.c.created_from_page_revision.is_distinct_from(
                    statement.excluded.created_from_page_revision
                ),
            )
            revived = memory_item.c.superseded_by.is_not(None)
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[memory_item.c.id],
                    set_={
                        memory_item.c.memory_kind: statement.excluded.memory_kind,
                        memory_item.c.confidence: statement.excluded.confidence,
                        memory_item.c.source_ref: statement.excluded.source_ref,
                        memory_item.c.created_from_page_uid: (
                            statement.excluded.created_from_page_uid
                        ),
                        memory_item.c.created_from_page_revision: (
                            statement.excluded.created_from_page_revision
                        ),
                        memory_item.c.source_uid: statement.excluded.source_uid,
                        memory_item.c.embedding_digest: sa.case(
                            (rebound, None), else_=memory_item.c.embedding_digest
                        ),
                        memory_item.c.embedding_claimed_at: sa.case(
                            (rebound, None), else_=memory_item.c.embedding_claimed_at
                        ),
                        memory_item.c.as_of: statement.excluded.as_of,
                        memory_item.c.created_at: sa.case(
                            (revived, sa.func.now()), else_=memory_item.c.created_at
                        ),
                        memory_item.c.superseded_by: None,
                        memory_item.c.updated_at: sa.func.now(),
                    },
                )
            )
            if write.source_id is not None:
                link = insert(memory_source).values(
                    workspace_id=self.workspace_id,
                    memory_item_id=item_id,
                    source_uid=write.source_id,
                    page_uid=write.created_from_page_id,
                    revision=write.created_from_page_revision,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                await connection.execute(
                    link.on_conflict_do_update(
                        index_elements=[
                            memory_source.c.memory_item_id,
                            memory_source.c.page_uid,
                        ],
                        set_={
                            memory_source.c.source_uid: link.excluded.source_uid,
                            memory_source.c.revision: link.excluded.revision,
                            memory_source.c.updated_at: sa.func.now(),
                        },
                    )
                )
        return item_id

    async def supersede_page_facts(self, page_id: UUID, kept: frozenset[UUID] | None) -> None:
        """Retire what an earlier state of one page derived, by the link that page left — not by the
        fact's row. A fact learned from several feeds is one row a reader reaches through any of its
        `memory_source` links, keyed one per page it was derived from, so retiring one page drops
        only that page's link and keeps the row while another page still links it; the row is
        deleted, index scope and all, only when its last link is gone. When the dropped link was the
        row's own primary origin — `commit` writes a row's binding and that page's link together, so
        the page it binds to names the link — the primary re-points to a surviving link the page
        mirror still shows live at that link's revision, falling back to the oldest surviving link
        when none is; either way the primary lands on a link that exists, and clears its digest so
        the index job re-checks the binding. When no surviving link is yet live in the mirror — the
        page indexer runs on a cursor independent of this one, so its `mem_page` write can lag — the
        primary lands on the oldest surviving link anyway, and recall fences that binding by
        revision until the mirror catches up rather than serving a stale one.

        A gone page (`kept` None) retires every link from it. Otherwise `kept` is the rows the
        derivation just committed for this page, and every other link from it goes — the page's own
        latest reading is the whole of what it stands behind, whether the page moved revision or was
        read a second time at the revision it already had. A rebuild is that second reading, and a
        test on the revision could not see it: the statements it replaces sit at the very revision
        it settles on. This is the only path that removes a page-derived memory, and the fact
        deriver its only caller. Affected rows are locked in id order so concurrent retirements over
        different pages of the same fact serialize on the row rather than racing its re-point; a
        replay commits the same rows, so it names the same `kept` and finds no stale link."""
        if kept is not None and not kept:
            raise ValueError("a page that settled no fact retires nothing")
        stale: tuple[ColumnElement[bool], ...] = (
            memory_source.c.workspace_id == self.workspace_id,
            memory_source.c.page_uid == page_id,
        )
        if kept is not None:
            stale = (*stale, memory_source.c.memory_item_id.not_in(kept))
        deleted: list[UUID] = []
        async with self.transaction() as connection:
            affected = sorted(
                row.memory_item_id
                for row in (
                    await connection.execute(
                        sa.select(memory_source.c.memory_item_id).where(*stale).distinct()
                    )
                ).all()
            )
            if not affected:
                return
            locked = (
                sa.select(
                    memory_item.c.id,
                    memory_item.c.subject,
                    memory_item.c.created_from_page_uid,
                    memory_item.c.created_from_page_revision,
                )
                .where(memory_item.c.id.in_(affected))
                .order_by(memory_item.c.id)
            )
            if connection.dialect.name == "postgresql":
                locked = locked.with_for_update()
            rows = (await connection.execute(locked)).all()
            await connection.execute(sa.delete(memory_source).where(*stale))
            survivors = (
                await connection.execute(
                    sa.select(
                        memory_source.c.memory_item_id,
                        memory_source.c.source_uid,
                        memory_source.c.page_uid,
                        memory_source.c.revision,
                    )
                    .where(memory_source.c.memory_item_id.in_(affected))
                    .order_by(memory_source.c.created_at, memory_source.c.source_uid)
                )
            ).all()
            links_by_item: dict[UUID, list[sa.Row]] = {}
            for link in survivors:
                links_by_item.setdefault(link.memory_item_id, []).append(link)
            mirror = (
                {
                    page.page_uid: page
                    for page in (
                        await connection.execute(
                            sa.select(
                                mem_page.c.page_uid, mem_page.c.subject, mem_page.c.revision
                            ).where(
                                mem_page.c.workspace_id == self.workspace_id,
                                mem_page.c.page_uid.in_({link.page_uid for link in survivors}),
                            )
                        )
                    ).all()
                }
                if survivors
                else {}
            )
            for row in rows:
                links = links_by_item.get(row.id, [])
                if not links:
                    await connection.execute(
                        sa.delete(memory_item).where(memory_item.c.id == row.id)
                    )
                    deleted.append(row.id)
                    continue
                if any(link.page_uid == row.created_from_page_uid for link in links):
                    continue
                current = [
                    link
                    for link in links
                    if (page := mirror.get(link.page_uid)) is not None
                    and page.subject == row.subject
                    and page.revision == link.revision
                ]
                survivor = current[0] if current else links[0]
                await connection.execute(
                    sa.update(memory_item)
                    .where(memory_item.c.id == row.id)
                    .values(
                        created_from_page_uid=survivor.page_uid,
                        created_from_page_revision=survivor.revision,
                        source_uid=survivor.source_uid,
                        embedding_digest=None,
                        embedding_claimed_at=None,
                    )
                )
        for memory_id in deleted:
            await self.index.delete(IndexScope(OWNER_KIND_MEMORY_ITEM, str(memory_id)))

    async def recall(
        self,
        query: str,
        subjects: frozenset[str],
        limit: int,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        source_reader: SourceReader,
    ) -> tuple[Recalled, ...]:
        """Fuse the index legs with the cosine re-score blend and a lexical leg over the un-embedded
        tail, read the surviving items back, then rank by recency decay (fact half-lives), drop
        near-duplicate bodies, cap per-class diversity, and rewrite episodic hits to topic
        pointers. The tail leg makes a just-committed fact recallable before the index job derives
        its chunks. An optional
        half-open `[start, end)` bound on `created_at` restricts recall to a window; the index never
        sees the bound, so the filter lands in the row read-back alongside the superseded drop and
        the source-reach fence — a page-derived row survives only for a reader who may read one of
        its source links. The index legs fetch a bounded candidate pool rather than just `limit`, so
        the fence has higher-ranked-but-unreachable rows to discard without starving the `limit`
        rows a reader may see; it is a row filter, not an index partition.

        Ranking that pool down to the slots is `_shortlist`, in a worker thread: this runs from the
        recall hook on every member turn, and its arithmetic holds no await for the turn's soft
        timeout to interrupt. The near-duplicate guard there reads the pool but stops at `limit *
        RECALL_DEDUP_OVERSAMPLE` distinct items — enough material for the type-diversity cap to
        choose from, and nothing spent shingling rows no slot could reach."""
        source_ids = await self._source_ids(source_reader)
        pool = max(limit, RECALL_CANDIDATE_POOL)
        lexical, vector = await self._legs(query, subjects, OWNER_KIND_MEMORY_ITEM, pool)
        tail = await self._untail_leg(query, subjects, pool, source_ids)
        enriched = await self._enrich(
            fuse_recall(lexical, vector, tail, pool),
            subjects,
            source_ids,
            start,
            end,
        )
        shortlist = await asyncio.to_thread(self._shortlist, enriched, limit, datetime.now(UTC))
        return tuple(as_topic_pointer(item, index) for index, item in enumerate(shortlist))

    def _shortlist(
        self, enriched: tuple[Recalled, ...], limit: int, now: datetime
    ) -> tuple[Recalled, ...]:
        """The candidate pool narrowed to the slots the caller asked for: rank by recency decay,
        drop the near-duplicate bodies, then cap per-class diversity. Every step is arithmetic over
        the whole pool, which is the one GIL-bound stretch of a recall — so the caller runs it in a
        worker thread rather than holding the process the hook fires in."""
        ranked = tuple(
            sorted(
                (replace(item, score=item.score * decay_factor(item, now)) for item in enriched),
                key=lambda item: item.score,
                reverse=True,
            )
        )
        guarded = drop_near_duplicates(ranked, limit * RECALL_DEDUP_OVERSAMPLE)
        return enforce_type_diversity(guarded, limit)

    async def search_sources(
        self,
        query: str,
        subjects: frozenset[str],
        limit: int,
        start: datetime | None = None,
        end: datetime | None = None,
        *,
        source_reader: SourceReader,
    ) -> tuple[SourceMatch, ...]:
        """Search synced source pages the same way recall searches facts: fuse the two index legs
        under the subject filter over the page owner kind, then read each surviving page back from
        the `mem_page` mirror — fenced on the reader's grant for the page's source, carrying its
        subject, and dropping any outside the optional
        `[start, end)` `created_at` window. The mirror's subject and revision must still match the
        core page, so a changed or removed document cannot disclose stale chunks while its
        page-index job catches up."""
        pool = max(limit, RECALL_CANDIDATE_POOL)
        fused = fuse_hits(
            *await self._legs(query, subjects, OWNER_KIND_PAGE, pool),
            pool,
        )
        if not fused:
            return ()
        ids = [UUID(hit.owner_id) for hit in fused]
        conditions: list[ColumnElement[bool]] = [
            mem_page.c.page_uid.in_(ids),
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
                            mem_page.c.page_uid,
                            mem_page.c.subject,
                            mem_page.c.revision,
                            mem_page.c.created_at,
                        ).where(*conditions)
                    )
                )
                .mappings()
                .all()
            )
        by_owner = {row["page_uid"]: row for row in rows}
        current = await self._readable_states(tuple(by_owner), source_reader)
        matches = tuple(
            SourceMatch(
                page_id=page["page_uid"],
                subject=page["subject"],
                text=hit.text,
                score=hit.score,
                created_at=_aware(page["created_at"]),
            )
            for hit in fused
            if (page := by_owner.get(UUID(hit.owner_id))) is not None
            and (state := current.get(page["page_uid"])) is not None
            and state.subject == page["subject"]
            and state.revision == page["revision"]
            and state.subject in subjects
        )
        return matches[:limit]

    async def _source_ids(
        self,
        source_reader: SourceReader,
    ) -> frozenset[UUID]:
        if self.readable_source_ids is None:
            raise RuntimeError("source-derived memory reads require core's source-reach authority")
        return await self.readable_source_ids(source_reader)

    async def _legs(
        self,
        query: str,
        subjects: frozenset[str],
        owner_kind: str,
        limit: int,
    ) -> tuple[tuple[Hit, ...], tuple[Hit, ...]]:
        embedding = await self._embed_query(query)
        lexical = await self.index.lexical(query, subjects, owner_kind, limit)
        vector = (
            await self.index.vector(embedding, subjects, owner_kind, limit) if embedding else ()
        )
        return lexical, vector

    async def _untail_leg(
        self,
        query: str,
        subjects: frozenset[str],
        limit: int,
        source_ids: frozenset[UUID],
    ) -> tuple[Hit, ...]:
        """A lexical leg over the un-embedded tail — memory_item rows the index job has not settled
        yet (`embedding_digest` NULL) — so a just-committed fact is recallable within the indexer's
        tick rather than only after it. Scored in-process by query-term count over the body (gbrain
        `lexical_score`); the scan is bounded to the newest TAIL_SCAN_MAX rows so a backlogged
        indexer cannot unbound it. Once a row is settled it leaves this set and the index legs serve
        it, so the tail never double-counts an indexed row."""
        terms = [term for term in re.split(r"\W+", query.lower()) if term]
        if not terms or not subjects:
            return ()
        authority: ColumnElement[bool] = memory_item.c.source_uid.is_(None)
        if source_ids:
            authority = sa.or_(authority, _readable_link(source_ids))
        async with self.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(
                            memory_item.c.id,
                            memory_item.c.subject,
                            memory_item.c.body,
                        )
                        .where(
                            memory_item.c.workspace_id == self.workspace_id,
                            memory_item.c.subject.in_(subjects),
                            memory_item.c.embedding_digest.is_(None),
                            memory_item.c.superseded_by.is_(None),
                            memory_item.c.retired_at.is_(None),
                            authority,
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
        source_ids: frozenset[UUID],
        start: datetime | None,
        end: datetime | None,
    ) -> tuple[Recalled, ...]:
        """Read the surviving (non-superseded) items back in fused order, fenced on source reach: a
        page-derived row survives only when the reader may read one of its source links, a
        member-written row (no source) always. A superseded item — or one outside the
        `[start, end)` `created_at` window, or one whose page has moved off its bound revision, or
        one the page pass retired — drops out here rather than being served."""
        if not fused:
            return ()
        ids = [UUID(hit.owner_id) for hit in fused]
        conditions: list[ColumnElement[bool]] = [
            memory_item.c.id.in_(ids),
            memory_item.c.workspace_id == self.workspace_id,
            memory_item.c.subject.in_(subjects),
            memory_item.c.superseded_by.is_(None),
            memory_item.c.retired_at.is_(None),
            sa.or_(memory_item.c.source_uid.is_(None), _readable_link(source_ids)),
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
                            memory_item.c.created_from_page_uid.label("created_from_page_id"),
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
                created_at=_aware(by_id[UUID(hit.owner_id)]["created_at"]),
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

    async def _readable_states(
        self, page_ids: tuple[UUID, ...], source_reader: SourceReader
    ) -> dict[UUID, PageState]:
        if not page_ids:
            return {}
        if self.readable_page_states is None:
            raise RuntimeError("source-derived memory reads require core's source-reach authority")
        return await self.readable_page_states(page_ids, source_reader)


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
        readable_page_states=ext.readable_page_states,
        readable_source_ids=ext.readable_source_ids,
    )


@dataclass(frozen=True)
class MemoryIndexer:
    """The derivation job: turn memory items due for indexing into chunks, off the write path. A run
    atomically claims a batch of rows whose `embedding_digest` is NULL and whose claim is unset or
    lease-expired — stamping `embedding_claimed_at` (Postgres `FOR UPDATE SKIP LOCKED`, SQLite the
    single writer) so an overlapping tick skips them and never double-embeds — chunks and embeds
    each body whose chunks the index does not already hold, then writes the content digest and
    clears the claim so the row is no longer due. It owns a row's chunks, never the row: a body it
    may not publish is withheld from the index and its
    row left intact for the fact deriver, the one writer that retires a page-derived memory. A row
    the page pass retired is withheld the same way, and the pass clears its digest to make it due,
    so the chunks it had published leave the index on the tick that follows the curation. Every
    claimed row reaches a terminal digest either way, so a row nobody may read can never hold the
    claim slots a newly committed fact needs."""

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
                memory_item.c.created_from_page_uid.label("created_from_page_id"),
                memory_item.c.created_from_page_revision,
                memory_item.c.source_uid.label("source_id"),
                memory_item.c.embedding_digest,
                memory_item.c.superseded_by,
                memory_item.c.retired_at,
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
        if item.retired_at is not None or not await self._publishable(
            item.subject,
            item.created_from_page_id,
            item.created_from_page_revision,
        ):
            await self.index.delete(IndexScope(OWNER_KIND_MEMORY_ITEM, str(item.id)))
            await self._settle(item)
            return
        if not await self.index.has_chunks(IndexScope(OWNER_KIND_MEMORY_ITEM, str(item.id))):
            await chunk_embed_upsert(
                self.index,
                self.embed,
                self.chunker,
                OWNER_KIND_MEMORY_ITEM,
                str(item.id),
                item.subject,
                item.body,
            )
        async with self.transaction() as connection:
            binding = (
                await connection.execute(
                    sa.select(
                        memory_item.c.created_from_page_uid.label("created_from_page_id"),
                        memory_item.c.created_from_page_revision,
                    ).where(memory_item.c.id == item.id)
                )
            ).one_or_none()
        if binding is None or not await self._publishable(
            item.subject,
            binding.created_from_page_id,
            binding.created_from_page_revision,
        ):
            await self.index.delete(IndexScope(OWNER_KIND_MEMORY_ITEM, str(item.id)))
            return
        if binding.created_from_page_revision != item.created_from_page_revision:
            return
        await self._settle(item)

    async def _publishable(self, subject: str, page_id: UUID | None, revision: int | None) -> bool:
        """Whether this body may be published to the index under `subject`: a page-derived row only
        while its page is live and still carries exactly that subject and revision. Recall asks the
        index for its candidate window first and fences page-derived rows afterwards, so a row bound
        to a superseded revision must never occupy a candidate slot a reader could have spent on the
        fact that replaced it; the row itself is the fact deriver's to retire."""
        if page_id is None:
            return True
        state = (await self.page_states((page_id,))).get(page_id)
        return state is not None and state.subject == subject and state.revision == revision

    async def _settle(self, item: MemoryItem) -> None:
        """Stamp the body digest and release the claim, so the row leaves the due set this job reads
        — the terminal state of a row this run decided, whether it published the body or withheld
        one bound to a superseded revision. Guarded by the binding the run claimed and by the claim
        itself: a row the deriver rebound, or one whose page moved on and released the claim, stays
        due for the run that reads it next, so a decision the page has already invalidated can never
        be the row's terminal state."""
        async with self.transaction() as connection:
            await connection.execute(
                sa.update(memory_item)
                .values(
                    embedding_digest="sha256:" + hashlib.sha256(item.body.encode()).hexdigest(),
                    embedding_claimed_at=None,
                    updated_at=sa.func.now(),
                )
                .where(
                    memory_item.c.id == item.id,
                    memory_item.c.subject == item.subject,
                    memory_item.c.body == item.body,
                    memory_item.c.created_from_page_uid == item.created_from_page_id,
                    memory_item.c.created_from_page_revision == item.created_from_page_revision,
                    memory_item.c.source_uid == item.source_id,
                    memory_item.c.embedding_claimed_at.is_not(None),
                )
            )


@dataclass(frozen=True)
class PageIndexer:
    """The page derivation the memory extension's `page_change` hook drives: turn each replayed
    source-page change into index chunks + a `mem_page` mirror row, off the write path. The core
    page-change runner owns the cursor and the batch loop and hands this one delivered batch to
    apply; the derivation stays idempotent so a replayed change re-upserts the same rows. A
    tombstoned change, or one on a stream whose pages do not reach memory, drops the page's chunks
    and mirror row and embeds nothing; every other change accepts the payload only while subject
    and revision still match the core page before and after embedding. It never retires a
    `memory_item` — facts derived from a page are the fact deriver's to replace and retire, on its
    own cursor — but it does make the facts of the revisions a page has left due for the index job
    again, so their chunks leave recall's candidate window on a cursor no model can hold."""

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
        await self._unsettle_left_behind_facts(change.page_id, current)
        if not change.indexed:
            await self._drop(change.page_id)
            return
        if change.tombstone:
            if current is None:
                await self._drop(change.page_id)
            return
        if (
            current is None
            or current.subject != change.subject
            or current.revision != change.revision
        ):
            await self._drop(change.page_id)
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
            await self._drop(change.page_id)
            return
        async with self.transaction() as connection:
            updated = await connection.execute(
                sa.update(mem_page)
                .values(
                    subject=current.subject,
                    revision=current.revision,
                    created_at=change.created_at,
                )
                .where(mem_page.c.page_uid == change.page_id)
            )
            if updated.rowcount == 0:
                await connection.execute(
                    sa.insert(mem_page).values(
                        page_uid=change.page_id,
                        workspace_id=self.workspace_id,
                        subject=current.subject,
                        revision=current.revision,
                        created_at=change.created_at,
                    )
                )

    async def _drop(self, page_id: UUID) -> None:
        await self.index.delete(IndexScope(OWNER_KIND_PAGE, str(page_id)))
        async with self.transaction() as connection:
            await connection.execute(sa.delete(mem_page).where(mem_page.c.page_uid == page_id))

    async def _unsettle_left_behind_facts(self, page_id: UUID, state: PageState | None) -> None:
        """Make every fact of a revision this page has left due for the index job again — each row
        created from `page_id` that no longer carries the page's live subject and revision, and all
        of them once the page is gone. The index job is the one writer of a memory row's chunks, so
        withdrawing them is its decision too; clearing the digest and the claim is how a page that
        moved on asks for that decision again, and it withholds exactly the bodies whose page no
        longer carries their revision. Withdrawal cannot wait on a derivation: a revision that
        derives nothing derives no replacement, and the chunks it never replaced would otherwise
        spend recall's candidate window for as long as the rows live. The rows themselves are
        untouched — retiring a page-derived fact stays the fact deriver's, and only where a
        replacement committed."""
        left_behind: tuple[ColumnElement[bool], ...] = (
            memory_item.c.workspace_id == self.workspace_id,
            memory_item.c.created_from_page_uid == page_id,
        )
        if state is not None:
            left_behind = (
                *left_behind,
                sa.or_(
                    memory_item.c.subject != state.subject,
                    memory_item.c.created_from_page_revision.is_distinct_from(state.revision),
                ),
            )
        async with self.transaction() as connection:
            await connection.execute(
                sa.update(memory_item)
                .values(embedding_digest=None, embedding_claimed_at=None, updated_at=sa.func.now())
                .where(*left_behind)
            )


DRAIN_CONCURRENCY = 16


class DrainCursor(BaseModel):
    """Where a workspace's drain of unindexed pages stands: the last mirror `page_uid` walked, None
    before the first batch. Kept in the extension store under the drain's marker key, whose presence
    is what makes the workspace a candidate."""

    after: UUID | None = None


@dataclass(frozen=True)
class UnindexedPageDrain:
    """Remove the chunks and mirror rows of pages whose stream stopped reaching memory before the
    page indexer saw them: the core backfill marked them before `indexed` joined the revision
    trigger, so the feed never replays those pages, and a memory migration marks each workspace
    holding some instead. One pass walks `batch` mirror rows past the marker's cursor in `page_uid`
    order, asks core which of those pages are live and not indexed, and drops them — every scope
    first, then the mirror rows of the scopes that went, so a pass that dies midway leaves rows the
    next pass finds again rather than chunks nothing names. The scope deletes are HTTP calls that
    hold no connection, so they run `DRAIN_CONCURRENCY` at a time; the mirror rows go in one
    statement, so the pool is touched once per batch. Every failure is re-raised once the batch
    settles; a short batch ends the walk and deletes the marker."""

    index: IndexBackend
    transaction: Transaction
    store: ScopedStore
    page_states: PageStates
    workspace_id: UUID
    marker_key: str
    batch: int

    async def run(self) -> int:
        marker = await self.store.get(self.marker_key)
        if marker is None:
            return 0
        walked = await self._mirrors_after(DrainCursor.model_validate(marker).after)
        states = await self.page_states(walked)
        unindexed = tuple(
            page_id
            for page_id in walked
            if (state := states.get(page_id)) is not None and not state.indexed
        )
        await self._drop_all(unindexed)
        if len(walked) < self.batch:
            await self.store.delete(self.marker_key)
        else:
            await self.store.put(
                self.marker_key, DrainCursor(after=walked[-1]).model_dump(mode="json")
            )
        return len(unindexed)

    async def _mirrors_after(self, after: UUID | None) -> tuple[UUID, ...]:
        query = (
            sa.select(mem_page.c.page_uid)
            .where(mem_page.c.workspace_id == self.workspace_id)
            .order_by(mem_page.c.page_uid)
            .limit(self.batch)
        )
        if after is not None:
            query = query.where(mem_page.c.page_uid > after)
        async with self.transaction() as connection:
            return tuple((await connection.execute(query)).scalars())

    async def _drop_all(self, page_ids: tuple[UUID, ...]) -> None:
        gate = asyncio.Semaphore(DRAIN_CONCURRENCY)

        async def delete_scope(page_id: UUID) -> None:
            async with gate:
                await self.index.delete(IndexScope(OWNER_KIND_PAGE, str(page_id)))

        outcomes = await asyncio.gather(
            *(delete_scope(page_id) for page_id in page_ids), return_exceptions=True
        )
        gone = [
            page_id
            for page_id, outcome in zip(page_ids, outcomes, strict=True)
            if not isinstance(outcome, BaseException)
        ]
        if gone:
            async with self.transaction() as connection:
                await connection.execute(sa.delete(mem_page).where(mem_page.c.page_uid.in_(gone)))
        for outcome in outcomes:
            if isinstance(outcome, BaseException):
                raise outcome
