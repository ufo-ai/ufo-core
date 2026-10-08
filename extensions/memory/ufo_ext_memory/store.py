"""The memory domain: the `memory_item` table the extension owns, recall's fusion, and the indexer.

`commit` persists one `memory_item` and derives nothing — chunking and embedding are the index
job's work, never inline on a write. One fact from one page is one row: a body lands on the row its
page already holds for it, and a conversation-written body on the one pageless row of its subject,
so two pages stating identical bytes are two rows and every read here serves the statement once
(`one_row_per_statement`). `recall` embeds the query once, asks the deploy index backend
for its lexical and vector hits under the caller's subject filter, fuses them with reciprocal-rank
fusion (K=60), and reads the surviving items back. `search_sources` fuses the same legs over
source-page chunks and reads the matched snippet straight off the index (a tombstoned page's chunks
are already gone). `MemoryIndexer` is the derivation job: it atomically claims memory items whose
`embedding_digest` is NULL, chunks and embeds each body whose chunks the index does not already
hold, and stamps the digest so the row is no longer due. A claimed publishable body the index still
holds — an identical re-commit that only rebound the row's page revision, or a lease-expired retry —
settles without paying the embed again (a row's body never changes under its id, so held chunks
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
from collections.abc import Awaitable, Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol, Self
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from ufo.sdk.audience import Audience, audience_subjects
from ufo.sdk.context import ExtensionContext, PageState, ScopedStore, SourceReader
from ufo.sdk.ids import uuid7
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
from ufo.sdk.subjects import member_subject
from ufo_ext_memory.client import ItemClass, MemoryKind
from ufo_ext_memory.heads import HEAD_WALK_MAX, topic_pointer
from ufo_ext_memory.pages import (
    RECALL_CANDIDATE_POOL,
    RECALL_COSINE_FLOOR,
    Fused,
    fuse_hits,
    fuse_legs,
)

RRF_WEIGHT = 0.7
COSINE_WEIGHT = 0.3
TAIL_SCAN_MAX = 200
DUE_BATCH_MAX_ITEMS = 200
SWEEP_MAX_ROWS = 2_000
"""How many live rows one declared name may reach, set between the widest real correction and the
narrowest name that is not one. On the largest workspace measured `metalcraftai/ufo` named 1,653
rows and `Datadog` 664, while `Alex` named 2,833, `ufo` 3,798 and `Slack` 4,332 — a member, a
product and a surface, none of which one correction makes out of date."""
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

FACT: Literal["fact"] = "fact"
EPISODIC: ItemClass = "episodic"
SEMANTIC: ItemClass = "semantic"
SECTION: ItemClass = "section"
OVERVIEW: ItemClass = "overview"

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
    sa.Column("body_digest", sa.Text, nullable=True),
    sa.Column("item_class", sa.Text, nullable=False),
    sa.Column("memory_kind", sa.Text, nullable=False, server_default=KIND_FACT),
    sa.Column("confidence", sa.Integer, nullable=False, server_default="5"),
    sa.Column("source_ref", sa.Text, nullable=True),
    sa.Column("created_from_page_uid", sa.Uuid, nullable=True),
    sa.Column("created_from_page_revision", sa.BigInteger, nullable=True),
    sa.Column("source_uid", sa.Uuid, nullable=True),
    sa.Column("created_from_conversation_id", sa.Uuid, nullable=True),
    sa.Column("as_of", sa.DateTime(timezone=True), nullable=True),
    sa.Column("embedding_digest", sa.Text, nullable=True),
    sa.Column("embedding_claimed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("superseded_by", sa.Uuid, nullable=True),
    sa.Column("overtaken_by", sa.Uuid, nullable=True),
    sa.Column("deprecates", sa.JSON(none_as_null=True), nullable=True),
    sa.Column("swept_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "(created_from_page_uid is null and created_from_page_revision is null "
        "and source_uid is null) or (created_from_page_uid is not null "
        "and created_from_page_revision is not null and source_uid is not null)",
        name="memory_item_page_source",
    ),
    sa.CheckConstraint(
        "superseded_by is null or overtaken_by is null", name="memory_item_one_pointer"
    ),
    sa.CheckConstraint(
        "item_class in ('fact', 'episodic', 'semantic', 'section', 'overview')",
        name="memory_item_class",
    ),
    sa.CheckConstraint(
        "subject = 'shared' or subject like 'member:%' or subject like 'room:%:%' "
        "or subject like 'foreign:%:%'",
        name="memory_item_subject",
    ),
    sa.ForeignKeyConstraint(
        ["workspace_id"],
        ["workspace.id"],
        ondelete="CASCADE",
        name="memory_item_workspace_id_fkey",
    ),
    sa.ForeignKeyConstraint(
        ["superseded_by"],
        ["memory_item.id"],
        ondelete="SET NULL",
        name="memory_item_superseded_by_fkey",
    ),
    sa.ForeignKeyConstraint(
        ["overtaken_by"],
        ["memory_item.id"],
        ondelete="SET NULL",
        name="memory_item_overtaken_by_fkey",
    ),
    sa.Index("memory_item_due", "embedding_digest"),
    sa.Index(
        "memory_item_overtaken",
        "overtaken_by",
        postgresql_where=sa.text("overtaken_by is not null"),
        sqlite_where=sa.text("overtaken_by is not null"),
    ),
    sa.Index(
        "memory_item_superseded",
        "superseded_by",
        postgresql_where=sa.text("superseded_by is not null"),
        sqlite_where=sa.text("superseded_by is not null"),
    ),
    sa.Index(
        "memory_item_unswept",
        "workspace_id",
        postgresql_where=sa.text("deprecates is not null and swept_at is null"),
        sqlite_where=sa.text("deprecates is not null and swept_at is null"),
    ),
    sa.Index(
        "memory_item_page",
        "workspace_id",
        "created_from_page_uid",
        postgresql_where=sa.text("created_from_page_uid is not null"),
        sqlite_where=sa.text("created_from_page_uid is not null"),
    ),
    sa.Index("memory_item_inventory", "workspace_id", "created_at"),
    sa.Index(
        "memory_item_consolidate",
        "workspace_id",
        "created_at",
        postgresql_where=sa.text("item_class = 'fact' and superseded_by is null"),
        sqlite_where=sa.text("item_class = 'fact' and superseded_by is null"),
    ),
    sa.Index(
        "memory_item_page_body",
        "workspace_id",
        "subject",
        "item_class",
        "body_digest",
        "created_from_page_uid",
        unique=True,
        postgresql_where=sa.text("created_from_page_uid is not null"),
        sqlite_where=sa.text("created_from_page_uid is not null"),
    ),
    sa.Index(
        "memory_item_written_body",
        "workspace_id",
        "subject",
        "item_class",
        "body_digest",
        unique=True,
        postgresql_where=sa.text("created_from_page_uid is null"),
        sqlite_where=sa.text("created_from_page_uid is null"),
    ),
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


def recall_subjects(audience: Audience, member_id: UUID | None = None) -> frozenset[str]:
    subjects = audience_subjects(audience)
    if member_id is None:
        return subjects
    return subjects | {member_subject(member_id)}


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


def body_digest(body: str) -> str:
    """The sha256 hex `memory_item.body_digest` holds: the key under which one body is one row."""
    return hashlib.sha256(body.encode()).hexdigest()


class Stated(Protocol):
    """A row stating one body under one subject, and when the statement was current."""

    @property
    def subject(self) -> str: ...

    @property
    def body(self) -> str: ...

    @property
    def as_of(self) -> datetime | None: ...

    @property
    def created_at(self) -> datetime: ...


def one_row_per_statement[T: Stated](rows: Iterable[T]) -> tuple[T, ...]:
    """`rows` with every restatement dropped: of the rows stating one body under one subject, the
    one with the newest `as_of` (then `created_at`) stays, in the order the rows came. Two pages
    stating identical bytes are two rows, so every read that serves a member or writes what one
    reads goes through here and serves the statement once."""
    ordered = tuple(rows)
    newest: dict[tuple[str, str], tuple[tuple[datetime, datetime], int]] = {}
    for index, row in enumerate(ordered):
        key = (row.subject, body_digest(row.body))
        stated = (_aware(row.as_of or row.created_at), _aware(row.created_at))
        held = newest.get(key)
        if held is None or stated > held[0]:
            newest[key] = (stated, index)
    kept = {index for _stated, index in newest.values()}
    return tuple(row for index, row in enumerate(ordered) if index in kept)


def _readable_source(source_ids: frozenset[UUID]) -> ColumnElement[bool]:
    return memory_item.c.source_uid.in_(source_ids)


class MemoryInventoryItem(BaseModel):
    """One stored memory as the operator explorer reads it — the whole row plus the derived recall
    signals, so both how it was ingested and how it decays are visible without re-deriving them.

    Ingestion/creation: `source_ref` is what produced it (a tool write, a synced source, a
    consolidation); `created_at` is when it was committed and `as_of` is when its source information
    was current; `embedding_digest` NULL means it is still due for the index job, and set means the
    job settled that body — published as chunks, or withheld because its page moved on — while
    `embedding_claimed_at` set means the indexer currently holds a lease on it. Recall/decay:
    `half_life_days` is the recency half-life for its kind (None for episodic, section, and
    overview rows, which never decay) and `decay_factor` is the live multiplier recall applies to
    its relevance (`(confidence/10)·0.5**(age_days/half_life)` for facts and summaries, else 1.0).
    Lifecycle: `superseded_by` non-NULL means a newer row stands in its place; `subject` is its
    exact audience. `source_id` is the source of the page it was derived from."""

    subject: str
    body: str
    item_class: ItemClass
    memory_kind: MemoryKind
    confidence: int
    source_ref: str | None
    created_from_page_id: UUID | None
    created_from_page_revision: int | None
    source_id: UUID | None
    as_of: datetime | None
    embedding_digest: str | None
    embedding_claimed_at: datetime | None
    superseded_by: UUID | None
    overtaken_by: UUID | None
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
    store never stalls the shared request loop. Scoping is the explicit workspace predicate beside
    the workspace the opener pins; grouping by `subject` is the reader's to do."""
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
            created_from_page_id=row["created_from_page_uid"],
            created_from_page_revision=row["created_from_page_revision"],
            source_id=row["source_uid"],
            as_of=row["as_of"],
            embedding_digest=row["embedding_digest"],
            embedding_claimed_at=row["embedding_claimed_at"],
            superseded_by=row["superseded_by"],
            overtaken_by=row["overtaken_by"],
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
    version; `source_ref` is a free-form note for tool writes, `created_from_conversation_id` is
    the conversation a tool write happened in, `as_of` says when the source
    information was current, `memory_kind` selects the recency half-life
    (fact/preference/decision/event/task), and `confidence` (1..10) scales a fact's decayed rank.
    `deprecates` names the things whose earlier memories this row makes out of date; the sweep
    stamps every older row carrying one of them and marks this row swept.

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
    created_from_conversation_id: UUID | None = None
    as_of: datetime | None = None
    deprecates: tuple[str, ...] = ()

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
    """A stored memory row as the derivation job loads it. `embedding_digest` NULL means the item
    is due for indexing; `superseded_by` points at the item that replaced it, `overtaken_by` at
    the item that says what is true now, and `retired_at` says the page pass judged the wiki reads
    better without it."""

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
    fused = fuse_legs((lexical, vector, tail), vector)
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
class Declared:
    """One correction the sweep has yet to apply: its row, the audience it was written into, and
    the names it declared out of date."""

    id: UUID
    subject: str
    names: tuple[str, ...]


@dataclass(frozen=True)
class Recalled:
    """One served memory. `head` is the body of the live row at the end of an overtaken row's
    pointer chain — what is true now about the thing the row names — and None for a row nothing
    has overtaken or whose chain leads to a row that has since left."""

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
    head: str | None = None
    created_from_page_id: UUID | None = None
    created_from_conversation_id: UUID | None = None


async def repoint(
    connection: AsyncConnection, workspace_id: UUID, doomed: Sequence[sa.Row]
) -> None:
    """Move every pointer aimed at a row about to leave: onto that row's own live chain head
    where it has one, otherwise cleared with the pointing row made due again, so the next index
    tick publishes it afresh rather than reviving it with chunks nobody withdrew. Done before the
    delete, since the foreign key would otherwise clear the pointer and lose the head."""
    if not doomed:
        return
    gone = {row.id for row in doomed}
    heads: dict[UUID, UUID | None] = {}
    for row in doomed:
        onward = row.superseded_by or row.overtaken_by
        for _ in range(HEAD_WALK_MAX):
            if onward is None or onward in gone:
                onward = None
                break
            target = (
                await connection.execute(
                    sa.select(
                        memory_item.c.superseded_by,
                        memory_item.c.overtaken_by,
                        memory_item.c.retired_at,
                    ).where(memory_item.c.id == onward)
                )
            ).one_or_none()
            if target is None or target.retired_at is not None:
                onward = None
                break
            if target.superseded_by is None and target.overtaken_by is None:
                break
            onward = target.superseded_by or target.overtaken_by
        heads[row.id] = onward
    for pointer in (memory_item.c.superseded_by, memory_item.c.overtaken_by):
        for gone_id, head in heads.items():
            aimed = (memory_item.c.workspace_id == workspace_id, pointer == gone_id)
            if head is not None:
                await connection.execute(
                    sa.update(memory_item)
                    .values({pointer: head, memory_item.c.updated_at: sa.func.now()})
                    .where(*aimed)
                )
            else:
                await connection.execute(
                    sa.update(memory_item)
                    .values(
                        {
                            pointer: None,
                            memory_item.c.embedding_digest: None,
                            memory_item.c.embedding_claimed_at: None,
                            memory_item.c.updated_at: sa.func.now(),
                        }
                    )
                    .where(*aimed)
                )


def superseding(by: UUID) -> dict[str, object]:
    """The pointer values every writer of `superseded_by` sets. The stronger stamp clears the
    weaker one: `memory_item_one_pointer` admits one pointer per row, and a hidden row's quoted
    head is moot. Without the clear, superseding a row the sweep had dated raises IntegrityError
    and takes its whole job transaction with it."""
    return {
        "superseded_by": by,
        "overtaken_by": None,
        "updated_at": sa.func.now(),
    }


def half_life_days(item_class: str, memory_kind: str) -> float | None:
    """The recency half-life recall decays this item by, or None when it carries none. A fact
    decays on its `memory_kind`'s half-life (fact/preference/decision/event/task), and a summary on
    the kind its sources shared, since a summary of current-state facts is itself a current-state
    claim. A section or overview is rewritten nightly from live rows and an episodic row is a topic
    pointer, so those rank on relevance alone."""
    if item_class not in (FACT, SEMANTIC):
        return None
    return HALFLIFE_DAYS.get(memory_kind, HALFLIFE_DAYS[KIND_FACT])


def decay_multiplier(
    item_class: str, memory_kind: str, confidence: int, as_of: datetime | None, now: datetime
) -> float:
    """The factor recall multiplies an item's relevance by — gbrain `effectiveConfidence`. A fact's
    or a summary's is `(confidence / 10) * 0.5 ** (age_days / halflife)`; every other class carries
    no decay and stays 1.0. The one home for the decay math, so recall's ranking and the explorer's
    reported weight are the same number."""
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
        body=topic_pointer(index + 1, item.memory_id),
        recall_mode="topic",
    )


@dataclass(frozen=True)
class SourceMatch:
    page_id: UUID
    subject: str
    text: str
    score: float
    created_at: datetime
    provider: str | None = None
    title: str | None = None


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
        chunks and embeddings. One fact from one page is one row: a page-derived body lands on the
        row of `(workspace, subject, item_class, body_digest, page)`, a conversation-written body on
        the one pageless row of `(workspace, subject, item_class, body_digest)`, each held by a
        partial unique index, and the id is opaque. The id comes back because a caller deriving a
        whole page's facts is the only thing that knows which rows that page still stands behind,
        and `supersede_page_facts` retires the rest by exactly that answer.

        Re-committing upserts the decay inputs in place rather than accumulating a duplicate
        recallable row; a re-commit that binds it to another page revision makes it due again, since
        whether that revision may be published is the index job's question to answer, while an
        identical re-commit at the same binding leaves the existing chunks and their digest
        untouched.

        A reworded restatement is a fresh row: both bodies land live, and retiring one onto the
        other is dedup — derived state, which this write path never produces — so `MemoryDeduper`
        is the sole superseder of a tool-written row, and near-duplicate copies accrete until the
        sweep's next eligible rotation collapses them onto the newest one. The sweep's own age floor
        is the second span they stand through: a row younger than DEDUP_MIN_AGE is invisible to it,
        so a burst of restatements is recallable in full until it ages into history.

        A member re-asserting a superseded body brings it back: a tool-written re-commit clears
        `superseded_by` and `overtaken_by`, so a member restating what a correction or a stamp put
        behind makes it live and current again, and `created_at` moves to now — a revival is a fresh
        assertion, so it carries fresh recency for recall's decay and for the sweep, whose winner is
        the newest copy. Only a revival moves it: an identical re-commit of a live row keeps the
        row's original `created_at`, since nothing about it was restated. Without the move, the copy
        that retired it would still be the newer row, and the next sweep would retire the member's
        restatement right back. A revival is due again, because the index job withdrew the chunks of
        the row while it stood superseded and settled its digest over an empty scope: a revival that
        kept that digest would be claimed by no tick and served by no leg.

        A re-commit carrying `deprecates` is a fresh declaration: the names land on the row and
        `swept_at` clears, so the next sweep stamps what has appeared since. One without leaves the
        row's earlier declaration and its sweep as they stand.

        A page-derived re-commit clears nothing: a page still synced re-commits its identical body
        on every derivation, so a judgement the upsert cleared would stand until the next tick and
        no longer. `retired_at` and both pointers survive re-derivation on a page row, and
        `created_at` stays where it was. The page pass retires a row by stamping `retired_at`, a
        correction hides one by stamping `superseded_by`, and the sweep dates one by stamping
        `overtaken_by`; each stands through every re-derivation of the body behind it. A
        page-derived row this write inserts is born retired when a retired row already states its
        body under its subject: the judgement was about the statement, and a page that never held it
        before derives the same statement. A row a member wrote carries no page and is never fenced
        by what the pass took — the pass promises never to take what a member wrote."""
        digest = body_digest(write.body)
        page_local = write.created_from_page_id is not None
        retired_before = (
            sa.select(sa.func.max(memory_item.c.retired_at))
            .where(
                memory_item.c.workspace_id == self.workspace_id,
                memory_item.c.subject == write.subject,
                memory_item.c.item_class == write.item_class,
                memory_item.c.body_digest == digest,
                memory_item.c.created_from_page_uid.is_not(None),
                memory_item.c.retired_at.is_not(None),
            )
            .scalar_subquery()
        )
        async with self.transaction() as connection:
            insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
            statement = insert(memory_item).values(
                id=uuid7(),
                workspace_id=self.workspace_id,
                subject=write.subject,
                body=write.body,
                body_digest=digest,
                item_class=write.item_class,
                memory_kind=write.memory_kind,
                confidence=write.confidence,
                source_ref=write.source_ref,
                created_from_page_uid=write.created_from_page_id,
                created_from_page_revision=write.created_from_page_revision,
                source_uid=write.source_id,
                created_from_conversation_id=write.created_from_conversation_id,
                as_of=write.as_of,
                superseded_by=None,
                deprecates=list(write.deprecates) or None,
                swept_at=None,
                retired_at=retired_before if page_local else None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
            rebound = memory_item.c.created_from_page_revision.is_distinct_from(
                statement.excluded.created_from_page_revision
            )
            revived = (
                sa.false()
                if page_local
                else sa.or_(
                    memory_item.c.superseded_by.is_not(None),
                    memory_item.c.overtaken_by.is_not(None),
                )
            )
            landed = await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[
                        memory_item.c.workspace_id,
                        memory_item.c.subject,
                        memory_item.c.item_class,
                        memory_item.c.body_digest,
                        *((memory_item.c.created_from_page_uid,) if page_local else ()),
                    ],
                    index_where=(
                        memory_item.c.created_from_page_uid.is_not(None)
                        if page_local
                        else memory_item.c.created_from_page_uid.is_(None)
                    ),
                    set_={
                        memory_item.c.memory_kind: statement.excluded.memory_kind,
                        memory_item.c.confidence: statement.excluded.confidence,
                        memory_item.c.source_ref: statement.excluded.source_ref,
                        memory_item.c.created_from_page_revision: (
                            statement.excluded.created_from_page_revision
                        ),
                        memory_item.c.source_uid: statement.excluded.source_uid,
                        memory_item.c.created_from_conversation_id: sa.func.coalesce(
                            statement.excluded.created_from_conversation_id,
                            memory_item.c.created_from_conversation_id,
                        ),
                        memory_item.c.embedding_digest: sa.case(
                            (sa.or_(rebound, revived), None),
                            else_=memory_item.c.embedding_digest,
                        ),
                        memory_item.c.embedding_claimed_at: sa.case(
                            (sa.or_(rebound, revived), None),
                            else_=memory_item.c.embedding_claimed_at,
                        ),
                        memory_item.c.as_of: statement.excluded.as_of,
                        memory_item.c.created_at: sa.case(
                            (revived, sa.func.now()), else_=memory_item.c.created_at
                        ),
                        memory_item.c.superseded_by: (
                            memory_item.c.superseded_by if page_local else None
                        ),
                        memory_item.c.overtaken_by: (
                            memory_item.c.overtaken_by if page_local else None
                        ),
                        memory_item.c.deprecates: sa.func.coalesce(
                            statement.excluded.deprecates, memory_item.c.deprecates
                        ),
                        memory_item.c.swept_at: sa.case(
                            (statement.excluded.deprecates.is_not(None), None),
                            else_=memory_item.c.swept_at,
                        ),
                        memory_item.c.updated_at: sa.func.now(),
                    },
                ).returning(memory_item.c.id)
            )
            return landed.scalar_one()

    async def supersede(self, item_id: UUID, by: UUID, subjects: frozenset[str]) -> bool:
        """Stamp `item_id` as superseded by `by` and make it due again, so the index job's next tick
        withdraws its chunks, and answer whether a row was stamped. Only a live row under one of
        `subjects` takes the stamp: a correction written into one audience never hides a row in
        another, and a row that is already superseded, retired, or the superseder itself is left as
        it stands."""
        async with self.transaction() as connection:
            stamped = await connection.execute(
                sa.update(memory_item)
                .values(
                    **superseding(by),
                    embedding_digest=None,
                    embedding_claimed_at=None,
                )
                .where(
                    memory_item.c.workspace_id == self.workspace_id,
                    memory_item.c.id == item_id,
                    memory_item.c.id != by,
                    memory_item.c.subject.in_(subjects),
                    memory_item.c.superseded_by.is_(None),
                    memory_item.c.retired_at.is_(None),
                )
            )
            return stamped.rowcount == 1

    async def subjects_present(self) -> frozenset[str]:
        """Every audience the workspace's memory holds a row under: the reach of a shared
        correction, since a shared statement bears on what every member remembers."""
        async with self.transaction() as connection:
            rows = await connection.execute(
                sa.select(memory_item.c.subject)
                .where(memory_item.c.workspace_id == self.workspace_id)
                .distinct()
            )
            return frozenset(rows.scalars().all())

    def _naming(self, name: str, subjects: frozenset[str]) -> tuple[ColumnElement[bool], ...]:
        return (
            memory_item.c.workspace_id == self.workspace_id,
            memory_item.c.subject.in_(subjects),
            memory_item.c.item_class.in_((FACT, SEMANTIC)),
            memory_item.c.superseded_by.is_(None),
            memory_item.c.overtaken_by.is_(None),
            memory_item.c.retired_at.is_(None),
            memory_item.c.body.icontains(name, autoescape=True),
        )

    async def naming_count(self, name: str, subjects: frozenset[str]) -> int:
        """How many live rows under `subjects` carry `name` anywhere in their body: the bound the
        declaration gate reads, over the same prefilter the sweep pages."""
        async with self.transaction() as connection:
            return (
                await connection.execute(
                    sa.select(sa.func.count()).where(*self._naming(name, subjects))
                )
            ).scalar_one()

    async def overtake_naming(
        self, name: str, by: UUID, subjects: frozenset[str], boundary: re.Pattern[str]
    ) -> int:
        """Stamp every live row under `subjects` strictly older than `by` whose body carries `name`
        as a whole name as overtaken by `by`, and answer how many took the stamp. LIKE pages the
        candidates on either database and the whole-name rule is read in Python, one rule for both.
        A row is older by `as_of`, or `created_at` where it has none, the date recall decays it
        from; `by` itself and a row already carrying a pointer never take the stamp. The stamp
        leaves `updated_at` where it stands: core's member-context read pages on that column and
        serves the body with no head, so moving it would fill a member's digest with the very rows
        this stamp dates."""
        since = (
            sa.select(sa.func.coalesce(memory_item.c.as_of, memory_item.c.created_at))
            .where(memory_item.c.id == by)
            .scalar_subquery()
        )
        async with self.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(memory_item.c.id, memory_item.c.body).where(
                        *self._naming(name, subjects),
                        memory_item.c.id != by,
                        sa.func.coalesce(memory_item.c.as_of, memory_item.c.created_at) < since,
                    )
                )
            ).all()
            ids = [row.id for row in rows if boundary.search(row.body)]
            if ids:
                await connection.execute(
                    sa.update(memory_item)
                    .values(overtaken_by=by)
                    .where(
                        memory_item.c.id.in_(ids),
                        memory_item.c.superseded_by.is_(None),
                        memory_item.c.overtaken_by.is_(None),
                    )
                )
        return len(ids)

    async def unswept(self) -> tuple[Declared, ...]:
        """Every row that declared names and has not been swept, oldest first: the sweep's work
        list, read off the rows themselves."""
        async with self.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(memory_item.c.id, memory_item.c.subject, memory_item.c.deprecates)
                    .where(
                        memory_item.c.workspace_id == self.workspace_id,
                        memory_item.c.deprecates.is_not(None),
                        memory_item.c.swept_at.is_(None),
                    )
                    .order_by(memory_item.c.created_at, memory_item.c.id)
                )
            ).all()
        return tuple(
            Declared(id=row.id, subject=row.subject, names=tuple(row.deprecates)) for row in rows
        )

    async def swept(self, item_id: UUID) -> None:
        async with self.transaction() as connection:
            await connection.execute(
                sa.update(memory_item)
                .values(swept_at=sa.func.now())
                .where(memory_item.c.workspace_id == self.workspace_id, memory_item.c.id == item_id)
            )

    async def supersede_page_facts(self, page_id: UUID, kept: frozenset[UUID] | None) -> None:
        """Retire what an earlier state of one page derived: delete the page's rows, index scope
        and all. A gone page (`kept` None) retires every row it derived. Otherwise `kept` is the
        rows the derivation just committed for this page, and every other row of the page goes —
        the page's own latest reading is the whole of what it stands behind, whether the page moved
        revision or was read a second time at the revision it already had. A rebuild is that second
        reading, and a test on the revision could not see it: the statements it replaces sit at the
        very revision it settles on. The fact deriver is its only caller, and a replay commits the
        same rows, so it names the same `kept` and finds nothing left. A row the page stranded under
        a subject it has left is already gone — the page indexer takes those, since no derivation
        keyed on the page's own subject would ever name them."""
        if kept is not None and not kept:
            raise ValueError("a page that settled no fact retires nothing")
        stale: tuple[ColumnElement[bool], ...] = (
            memory_item.c.workspace_id == self.workspace_id,
            memory_item.c.created_from_page_uid == page_id,
        )
        if kept is not None:
            stale = (*stale, memory_item.c.id.not_in(kept))
        async with self.transaction() as connection:
            doomed = (
                await connection.execute(
                    sa.select(
                        memory_item.c.id, memory_item.c.superseded_by, memory_item.c.overtaken_by
                    ).where(*stale)
                )
            ).all()
            await repoint(connection, self.workspace_id, doomed)
            deleted = (
                (
                    await connection.execute(
                        sa.delete(memory_item).where(*stale).returning(memory_item.c.id)
                    )
                )
                .scalars()
                .all()
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
        the source-reach fence — a page-derived row survives only for a reader who may read its
        source. The index legs fetch a bounded candidate pool rather than just `limit`, so
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
                provider=state.backend,
                title=state.title,
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
        terms = [term for term in re.split(r"\W+", query.lower()) if term]
        if not terms or not subjects:
            return ()
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
                            sa.or_(
                                memory_item.c.source_uid.is_(None),
                                _readable_source(source_ids),
                            ),
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
        if not fused:
            return ()
        ids = [UUID(hit.owner_id) for hit in fused]
        conditions: list[ColumnElement[bool]] = [
            memory_item.c.id.in_(ids),
            memory_item.c.workspace_id == self.workspace_id,
            memory_item.c.subject.in_(subjects),
            memory_item.c.superseded_by.is_(None),
            memory_item.c.retired_at.is_(None),
            sa.or_(memory_item.c.source_uid.is_(None), _readable_source(source_ids)),
        ]
        if start is not None:
            conditions.append(memory_item.c.created_at >= start)
        if end is not None:
            conditions.append(memory_item.c.created_at < end)
        async with self.transaction() as connection:
            rows = (
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
                        memory_item.c.created_from_conversation_id,
                        memory_item.c.as_of,
                        memory_item.c.created_at,
                        memory_item.c.overtaken_by,
                    ).where(*conditions)
                )
            ).all()
        current = await self.page_states(
            tuple(row.created_from_page_id for row in rows if row.created_from_page_id is not None)
        )
        servable = one_row_per_statement(
            row
            for row in rows
            if row.created_from_page_id is None
            or (
                (state := current.get(row.created_from_page_id)) is not None
                and state.subject == row.subject
                and state.revision == row.created_from_page_revision
                and state.subject in subjects
            )
        )
        by_id = {row.id: row for row in servable}
        heads = await self.heads(
            {row.id: row.overtaken_by for row in servable if row.overtaken_by is not None},
            subjects,
            sa.or_(memory_item.c.source_uid.is_(None), _readable_source(source_ids)),
        )
        return tuple(
            Recalled(
                memory_id=row.id,
                subject=row.subject,
                item_class=row.item_class,
                body=row.body,
                source_ref=row.source_ref,
                score=hit.score,
                memory_kind=row.memory_kind,
                confidence=row.confidence,
                created_at=_aware(row.created_at),
                as_of=row.as_of,
                head=heads.get(row.id),
                created_from_page_id=row.created_from_page_id,
                created_from_conversation_id=row.created_from_conversation_id,
            )
            for hit in fused
            if (row := by_id.get(UUID(hit.owner_id))) is not None
        )

    async def heads(
        self, pointers: dict[UUID, UUID], subjects: frozenset[str], reach: ColumnElement[bool]
    ) -> dict[UUID, str]:
        """The body of the live row each overtaken row's chain ends at, followed through either
        pointer for at most HEAD_WALK_MAX hops. A quoted head is served text, so every hop answers
        to the fence the served row answered to: `reach`, the caller's source fence, the reader's
        subjects, and a page still standing at the revision the row was read from. A chain that
        reaches a retired row, a row the reader may not read, a row whose page has moved, a row that
        has left, or the walk's cap yields no head: the row is served bare rather than under a
        quote the reader was never to see."""
        heads: dict[UUID, str] = {}
        pending = dict(pointers)
        for _ in range(HEAD_WALK_MAX):
            if not pending:
                break
            async with self.transaction() as connection:
                rows = (
                    await connection.execute(
                        sa.select(
                            memory_item.c.id,
                            memory_item.c.subject,
                            memory_item.c.body,
                            memory_item.c.superseded_by,
                            memory_item.c.overtaken_by,
                            memory_item.c.retired_at,
                            memory_item.c.created_from_page_uid.label("created_from_page_id"),
                            memory_item.c.created_from_page_revision,
                        ).where(
                            memory_item.c.workspace_id == self.workspace_id,
                            memory_item.c.id.in_(list(set(pending.values()))),
                            memory_item.c.subject.in_(subjects),
                            memory_item.c.retired_at.is_(None),
                            reach,
                        )
                    )
                ).all()
            current = await self.page_states(
                tuple(row.created_from_page_id for row in rows if row.created_from_page_id)
            )
            targets = {
                row.id: row
                for row in rows
                if row.created_from_page_id is None
                or (
                    (state := current.get(row.created_from_page_id)) is not None
                    and state.subject == row.subject
                    and state.revision == row.created_from_page_revision
                )
            }
            following: dict[UUID, UUID] = {}
            for origin, target_id in pending.items():
                target = targets.get(target_id)
                if target is None:
                    continue
                onward = target.superseded_by or target.overtaken_by
                if onward is None:
                    heads[origin] = target.body
                else:
                    following[origin] = onward
            pending = following
        return heads

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
    workspace_id: UUID
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
                memory_item.c.memory_kind,
                memory_item.c.confidence,
                memory_item.c.source_ref,
                memory_item.c.created_from_page_uid.label("created_from_page_id"),
                memory_item.c.created_from_page_revision,
                memory_item.c.source_uid.label("source_id"),
                memory_item.c.embedding_digest,
                memory_item.c.superseded_by,
                memory_item.c.retired_at,
            )
            .where(
                memory_item.c.workspace_id == self.workspace_id,
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
        if (
            item.retired_at is not None
            or item.superseded_by is not None
            or not await self._publishable(
                item.subject,
                item.created_from_page_id,
                item.created_from_page_revision,
            )
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
                "",
            )
        async with self.transaction() as connection:
            binding = (
                await connection.execute(
                    sa.select(
                        memory_item.c.created_from_page_uid.label("created_from_page_id"),
                        memory_item.c.created_from_page_revision,
                    ).where(
                        memory_item.c.workspace_id == self.workspace_id,
                        memory_item.c.id == item.id,
                    )
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
        if page_id is None:
            return True
        state = (await self.page_states((page_id,))).get(page_id)
        return state is not None and state.subject == subject and state.revision == revision

    async def _settle(self, item: MemoryItem) -> None:
        async with self.transaction() as connection:
            await connection.execute(
                sa.update(memory_item)
                .values(
                    embedding_digest="sha256:" + hashlib.sha256(item.body.encode()).hexdigest(),
                    embedding_claimed_at=None,
                    updated_at=sa.func.now(),
                )
                .where(
                    memory_item.c.workspace_id == self.workspace_id,
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
    and revision still match the core page before and after embedding. It settles what each page
    has left behind on a cursor no model can hold: a row stranded under a subject the page no
    longer carries goes, and one still under that subject at an earlier revision keeps its place
    and loses its chunks."""

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
        await self._settle_left_behind_facts(change.page_id, current)
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
            current.digest,
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

    async def _settle_left_behind_facts(self, page_id: UUID, state: PageState | None) -> None:
        mine: tuple[ColumnElement[bool], ...] = (
            memory_item.c.workspace_id == self.workspace_id,
            memory_item.c.created_from_page_uid == page_id,
        )
        withdrawn = sa.update(memory_item).values(
            embedding_digest=None, embedding_claimed_at=None, updated_at=sa.func.now()
        )
        if state is None:
            async with self.transaction() as connection:
                await connection.execute(withdrawn.where(*mine))
            return
        moved: tuple[ColumnElement[bool], ...] = (*mine, memory_item.c.subject != state.subject)
        async with self.transaction() as connection:
            doomed = (
                await connection.execute(
                    sa.select(
                        memory_item.c.id, memory_item.c.superseded_by, memory_item.c.overtaken_by
                    ).where(*moved)
                )
            ).all()
            await repoint(connection, self.workspace_id, doomed)
            stranded = (
                (
                    await connection.execute(
                        sa.delete(memory_item).where(*moved).returning(memory_item.c.id)
                    )
                )
                .scalars()
                .all()
            )
            await connection.execute(
                withdrawn.where(
                    *mine,
                    memory_item.c.created_from_page_revision.is_distinct_from(state.revision),
                )
            )
        for memory_id in stranded:
            await self.index.delete(IndexScope(OWNER_KIND_MEMORY_ITEM, str(memory_id)))


DRAIN_CONCURRENCY = 16


class DrainCursor(BaseModel):
    """Where a workspace's drain of unindexed pages stands: the last mirror `page_uid` walked, None
    before the first batch. Kept in the extension store under the drain's marker key, whose presence
    is what makes the workspace a candidate."""

    after: UUID | None = None


@dataclass(frozen=True)
class SupersededChunkDrain:
    """Remove the chunks of rows superseded before the indexer learned to withdraw them: a memory
    migration marks each workspace holding one, and this walks `batch` such rows past the marker's
    cursor in id order, deletes their scopes, and deletes the marker when the walk ends. The rows'
    digests stay settled, so an outgoing image's indexer never claims them and never puts the
    chunks back; a row superseded from here on is made due by `supersede` and withdrawn by the
    incoming indexer's own tick."""

    index: IndexBackend
    transaction: Transaction
    store: ScopedStore
    workspace_id: UUID
    marker_key: str
    batch: int

    async def run(self) -> int:
        marker = await self.store.get(self.marker_key)
        if marker is None:
            return 0
        walked = await self._superseded_after(DrainCursor.model_validate(marker).after)
        gate = asyncio.Semaphore(DRAIN_CONCURRENCY)

        async def delete_scope(item_id: UUID) -> None:
            async with gate:
                await self.index.delete(IndexScope(OWNER_KIND_MEMORY_ITEM, str(item_id)))

        outcomes = await asyncio.gather(
            *(delete_scope(item_id) for item_id in walked), return_exceptions=True
        )
        failed = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        if failed:
            raise failed[0]
        if len(walked) < self.batch:
            await self.store.delete(self.marker_key)
        else:
            await self.store.put(
                self.marker_key, DrainCursor(after=walked[-1]).model_dump(mode="json")
            )
        return len(walked)

    async def _superseded_after(self, after: UUID | None) -> tuple[UUID, ...]:
        query = (
            sa.select(memory_item.c.id)
            .where(
                memory_item.c.workspace_id == self.workspace_id,
                memory_item.c.superseded_by.is_not(None),
                memory_item.c.embedding_digest.is_not(None),
            )
            .order_by(memory_item.c.id)
            .limit(self.batch)
        )
        if after is not None:
            query = query.where(memory_item.c.id > after)
        async with self.transaction() as connection:
            return tuple((await connection.execute(query)).scalars())


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
