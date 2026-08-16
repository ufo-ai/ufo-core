"""The memory condenser: the producers that feed recall's otherwise-dead machinery.

`FactDeriver` is the memory extension's second `page_change` consumer — it distills each replayed
source page into durable `fact` memory_items with one bounded metered model pass per batch, so a
synced document becomes recallable facts, not only RAG chunks, and it is the one writer that retires
a page-derived fact — for exactly the pages whose replacement it just committed.
`MemoryConsolidator` is the periodic job that clusters aged `fact` items by embedding cosine and
collapses each cluster into one `semantic` summary through a bounded metered model pass, stamping
`superseded_by` on the clustered originals — the producer of recall's decay-exempt `semantic`
handling, and one of the two writers that make its `superseded_by IS NULL` drop fire.
`MemoryDeduper` is the other: the periodic sweep that collapses each group of accreted restatements
onto its newest copy with no model pass at all, and the only superseder of a tool-written row —
`commit` derives nothing, so every restatement it takes lands live. The deriver and the consolidator
meter through `ctx.model`; the consolidator writes nothing without one (gbrain Tier-B), while the
deriver requires one, since a batch it cannot derive is a batch whose replacements do not exist.
Every model and embed payload is bounded next to its call, each model call runs before the write
transaction, never holding it open, and the clustering both periodic jobs do is arithmetic a worker
thread carries, never the loop."""

import asyncio
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import batched
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.sql.elements import ColumnElement

from ufo.sdk.context import JsonValue, ModelAccess, ScopedStore
from ufo.sdk.index import EmbedClient
from ufo.sdk.models import Message, ModelRequest, ToolSchema, ToolUseBlock
from ufo.sdk.sources import PageChange
from ufo_ext_memory.store import (
    DEFAULT_CONFIDENCE,
    FACT,
    KIND_FACT,
    MAX_CONFIDENCE,
    MEMORY_BODY_MAX_CHARS,
    SEMANTIC,
    MemoryKind,
    MemoryStore,
    MemoryWrite,
    Transaction,
    memory_item,
)

MAX_PAGE_BODY_CHARS = 8_000
MIN_PAGE_BODY_CHARS = 40
EXTRACT_PAGE_BATCH = 10
FACT_EXTRACT_MAX_TOKENS = 16_384
EXTRACT_KEEP_NOTABILITY = frozenset({"high", "medium"})
FACT_EXTRACT_TOOL = "record_facts"
FACT_EXTRACT_TOOL_DESCRIPTION = (
    "Record every fact worth keeping from the source pages, one entry per fact."
)
FACT_EXTRACT_SYSTEM = (
    "Distill durable, standalone facts from the source pages the user sends as JSON "
    '({"pages":[{"page_id":"...","body":"..."}]}). For each candidate fact judge its notability '
    "high, medium, or low and emit only high and medium ones. Write each fact as a concise "
    "third-person claim that stands alone without the page, carrying its source page_id, a "
    "memory_kind (one of fact, preference, decision, event, task), and a confidence 1-10. Record "
    f"them with the {FACT_EXTRACT_TOOL} tool."
)

DEDUP_GROUP_MAX = 2_000
DEDUP_EMBED_BATCH = 100
SUPERSEDE_COSINE = 0.90
MIN_DUPLICATE_COPIES = 2
DEDUP_MIN_AGE = timedelta(hours=1)
DEDUP_CURSOR_KEY = "dedup_cursor"
DEDUP_FINGERPRINT_PREFIX = "dedup_fingerprint:"

CLUSTER_THRESHOLD = 0.85
MIN_CLUSTER_FACTS = 3
MIN_CLUSTER_SIZE = 2
MIN_OLDEST_AGE = timedelta(hours=24)
MAX_BUCKET_FACTS = 100
CONSOLIDATION_SCAN_MAX = 500
CONSOLIDATE_EMBED_CHARS = 2_000
CONSOLIDATE_FACT_CHARS = 1_000
MAX_SUMMARY_CHARS = 2_000
CONSOLIDATE_MAX_TOKENS = 1_024
CONSOLIDATE_REASONING: Literal["low"] = "low"
CONSOLIDATE_SYSTEM = (
    "You are consolidating several related memory facts into one durable summary. The user sends "
    'the facts as JSON ({"facts":["...","..."]}). Write a single concise standalone statement, in '
    "the third person, that captures their combined and most current meaning — resolving overlap "
    "or contradiction in favor of the more specific or more recent claim. Return only the summary "
    "text, no preamble and no JSON."
)


class ExtractedFact(BaseModel):
    """One fact the extraction model read out of a source page — untrusted model output validated
    at this boundary before it reaches `memory_item`. `page_id` maps the fact back to the page that
    scopes its subject; a fact whose notability is below the keep set is dropped by the caller."""

    page_id: str
    notability: str
    body: str
    memory_kind: MemoryKind = KIND_FACT
    confidence: int = Field(default=DEFAULT_CONFIDENCE, ge=1, le=MAX_CONFIDENCE)


class ExtractedFacts(BaseModel):
    """The arguments of the extraction's `record_facts` call — the shape whose JSON schema is the
    tool contract the model records against, so the facts arrive as arguments the provider decoded
    rather than as JSON this code reads out of prose."""

    facts: tuple[ExtractedFact, ...]


@dataclass(frozen=True)
class FactDeriver:
    """Distill each replayed source-page change into durable `fact` memory_items and retire what
    they replace, off the write path. The core page-change runner owns the cursor and batch loop (a
    cursor independent of the memory indexer's) and hands one delivered batch to `apply`: a page no
    longer there is retired outright, since nothing will ever replace its facts, and every other
    page goes through one bounded metered model pass per bounded group of substantial live pages —
    whose committed facts are the only thing that authorizes retiring the revisions they replace.
    Removal is conditional on the replacement's committed result, not merely later than it: a page
    the pass leaves without a fact (a body too thin to send, an extraction carrying none) keeps
    every fact it has, fenced out of recall by its revision until a derivation supersedes it. An
    extraction the pass cannot read is no settlement either, and settles nothing by raising: like a
    batch with no model wired, it holds the cursor where it stands so the next tick replays those
    pages, rather than advancing past facts nothing will ever derive again. Once-delivery is the
    cursor's guarantee — each changed page reaches this handler once; the content-addressed commit
    dedups an identical re-derivation onto the same row, and the retirement finds nothing left on a
    replay, so a replayed batch settles on the same rows."""

    store: MemoryStore
    model: ModelAccess

    async def apply(self, changes: tuple[PageChange, ...]) -> None:
        live = await self.store.page_states(tuple(change.page_id for change in changes))
        for change in changes:
            if change.page_id not in live:
                await self.store.supersede_page_facts(change.page_id, None)
        eligible = tuple(
            change
            for change in changes
            if change.page_id in live
            and not change.tombstone
            and len(change.body) >= MIN_PAGE_BODY_CHARS
        )
        for group in batched(eligible, EXTRACT_PAGE_BATCH):
            for settled in await self._derive(group):
                await self.store.supersede_page_facts(settled.page_id, settled.revision)

    async def _derive(self, pages: tuple[PageChange, ...]) -> tuple[PageChange, ...]:
        """Commit the kept facts of one bounded model pass over the pages still exactly where the
        change found them, and return the pages a fact actually landed for — the only pages whose
        other revisions now have a replacement to retire."""
        current = await self.store.page_states(tuple(page.page_id for page in pages))
        authorized = tuple(
            page
            for page in pages
            if (state := current.get(page.page_id)) is not None
            and state.subject == page.subject
            and state.revision == page.revision
        )
        if not authorized:
            return ()
        extracted = await self._extract(authorized)
        by_id = {str(page.page_id): page for page in authorized}
        settled: dict[UUID, PageChange] = {}
        for fact in extracted:
            page = by_id.get(fact.page_id)
            if page is None or fact.notability.lower() not in EXTRACT_KEEP_NOTABILITY:
                continue
            latest = (await self.store.page_states((page.page_id,))).get(page.page_id)
            if latest is None or latest.subject != page.subject or latest.revision != page.revision:
                continue
            await self.store.commit(
                MemoryWrite(
                    subject=page.subject,
                    body=fact.body[:MEMORY_BODY_MAX_CHARS],
                    item_class=FACT,
                    memory_kind=fact.memory_kind,
                    confidence=fact.confidence,
                    created_from_page_id=page.page_id,
                    created_from_page_revision=page.revision,
                    source_id=page.source_id,
                    as_of=page.as_of,
                )
            )
            settled[page.page_id] = page
        return tuple(settled.values())

    async def _extract(self, pages: tuple[PageChange, ...]) -> tuple[ExtractedFact, ...]:
        """The one bounded metered model pass over a group, returning the facts it recorded. The
        pass compels one `record_facts` call whose input schema is `ExtractedFacts`, so the model's
        answer is arguments the provider decoded — never structured data sliced out of a completion,
        where one character the model failed to escape costs the whole group its facts. Each entry
        is validated on its own, so an entry the contract does not satisfy drops without taking the
        rest with it, while a reply carrying no recorded facts at all raises: it is not the same
        answer as "these pages hold nothing", and the caller must settle nothing for the group.
        A forced tool choice runs with reasoning off — the provider rejects it under extended
        thinking."""
        payload = {
            "pages": [
                {"page_id": str(page.page_id), "body": page.body[:MAX_PAGE_BODY_CHARS]}
                for page in pages
            ]
        }
        request = ModelRequest(
            model=self.model.model,
            system=FACT_EXTRACT_SYSTEM,
            messages=(Message(role="user", content=json.dumps(payload, separators=(",", ":"))),),
            max_tokens=FACT_EXTRACT_MAX_TOKENS,
            tools=(
                ToolSchema(
                    name=FACT_EXTRACT_TOOL,
                    description=FACT_EXTRACT_TOOL_DESCRIPTION,
                    input_schema=ExtractedFacts.model_json_schema(),
                ),
            ),
            tool_choice=FACT_EXTRACT_TOOL,
            reasoning="off",
        )
        reply = await self.model.turn(request)
        blocks = () if isinstance(reply.content, str) else reply.content
        recorded = next(
            (
                block
                for block in blocks
                if isinstance(block, ToolUseBlock) and block.name == FACT_EXTRACT_TOOL
            ),
            None,
        )
        if recorded is None:
            raise ValueError(f"fact extraction recorded no {FACT_EXTRACT_TOOL} call")
        entries = recorded.input.get("facts")
        if not isinstance(entries, list):
            raise ValueError(f"{FACT_EXTRACT_TOOL} arguments carry no facts list")
        facts: list[ExtractedFact] = []
        for entry in entries:
            try:
                facts.append(ExtractedFact.model_validate(entry))
            except ValidationError:
                continue
        return tuple(facts)


def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """Semantic closeness of two embeddings, 0.0 when either carries no magnitude — the decision
    both clustering passes below are made of. Summed through `math.sumprod` rather than a generator
    expression: the dedup sweep spends one call per (copy, cluster head) pair across a whole group,
    and at 3072 dimensions the generator form costs 159us against this one's 26us."""
    denom = math.sqrt(math.sumprod(left, left)) * math.sqrt(math.sumprod(right, right))
    if denom == 0:
        return 0.0
    return math.sumprod(left, right) / denom


@dataclass(frozen=True)
class _AgedFact:
    id: UUID
    subject: str
    body: str
    confidence: int
    created_at: datetime


@dataclass(frozen=True)
class MemoryConsolidator:
    """Collapse aged, related facts into semantic summaries. A periodic job fed only by its own
    interval — never a page change or memory write — so it can never fire on the facts a derivation
    just produced. `run` reads the bounded candidate set (non-superseded `fact` items at least
    MIN_OLDEST_AGE old), buckets it by subject, and within each bucket holding at least
    MIN_CLUSTER_FACTS greedily clusters by embedding cosine at CLUSTER_THRESHOLD; each cluster of at
    least MIN_CLUSTER_SIZE is collapsed by one bounded metered model pass into a single `semantic`
    memory_item and the clustered originals are stamped `superseded_by` that summary in the same
    transaction. Idempotent: superseded originals leave the candidate set and the summary is
    `semantic`, not `fact`, so a re-run re-consolidates nothing. Fail-soft: with no model wired
    nothing is consolidated."""

    embed: EmbedClient
    transaction: Transaction
    workspace_id: UUID
    model: ModelAccess | None = None

    async def run(self) -> None:
        if self.model is None:
            return
        for _subject, facts in self._buckets(await self._aged_facts()):
            if len(facts) < MIN_CLUSTER_FACTS:
                continue
            embeddings = await self._embed(facts)
            for cluster in await asyncio.to_thread(self._clusters, facts, embeddings):
                if len(cluster) >= MIN_CLUSTER_SIZE:
                    await self._consolidate(self.model, cluster)

    async def _aged_facts(self) -> tuple[_AgedFact, ...]:
        cutoff = datetime.now(UTC) - MIN_OLDEST_AGE
        async with self.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(
                            memory_item.c.id,
                            memory_item.c.subject,
                            memory_item.c.body,
                            memory_item.c.confidence,
                            memory_item.c.created_at,
                        )
                        .where(
                            memory_item.c.workspace_id == self.workspace_id,
                            memory_item.c.item_class == FACT,
                            memory_item.c.created_from_page_id.is_(None),
                            memory_item.c.superseded_by.is_(None),
                            memory_item.c.created_at <= cutoff,
                        )
                        .order_by(memory_item.c.created_at.desc())
                        .limit(CONSOLIDATION_SCAN_MAX)
                    )
                )
                .mappings()
                .all()
            )
        return tuple(
            _AgedFact(row["id"], row["subject"], row["body"], row["confidence"], row["created_at"])
            for row in rows
        )

    def _buckets(
        self, facts: tuple[_AgedFact, ...]
    ) -> tuple[tuple[str, tuple[_AgedFact, ...]], ...]:
        groups: dict[str, list[_AgedFact]] = {}
        for fact in facts:
            groups.setdefault(fact.subject, []).append(fact)
        return tuple(
            (subject, tuple(sorted(members, key=_recency, reverse=True)[:MAX_BUCKET_FACTS]))
            for subject, members in sorted(groups.items())
        )

    async def _embed(self, facts: tuple[_AgedFact, ...]) -> dict[UUID, tuple[float, ...]]:
        vectors = await self.embed.embed(
            tuple(fact.body[:CONSOLIDATE_EMBED_CHARS] for fact in facts)
        )
        return {fact.id: vectors[index] for index, fact in enumerate(facts)}

    def _clusters(
        self, facts: tuple[_AgedFact, ...], embeddings: dict[UUID, tuple[float, ...]]
    ) -> tuple[tuple[_AgedFact, ...], ...]:
        """Greedy embedding-cosine clustering (gbrain `clusterFacts`): newest-first, each fact joins
        the first cluster whose head is within CLUSTER_THRESHOLD; a fact with no embedding starts a
        singleton the MIN_CLUSTER_SIZE gate then drops. Pure and synchronous so its caller runs it
        under `asyncio.to_thread`: MAX_BUCKET_FACTS bounds the bucket, but the cosines over even
        that many facts are GIL-bound work this job has no business doing on the loop."""
        clusters: list[list[_AgedFact]] = []
        for fact in sorted(facts, key=_recency, reverse=True):
            vector = embeddings.get(fact.id)
            joined = False
            if vector:
                for cluster in clusters:
                    head = embeddings.get(cluster[0].id)
                    if head and cosine(vector, head) >= CLUSTER_THRESHOLD:
                        cluster.append(fact)
                        joined = True
                        break
            if not joined:
                clusters.append([fact])
        return tuple(tuple(cluster) for cluster in clusters)

    async def _consolidate(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> None:
        summary = await self._summarize(model, cluster)
        if not summary:
            return
        summary_id = uuid4()
        ids = [fact.id for fact in cluster]
        async with self.transaction() as connection:
            donors = (
                sa.select(
                    memory_item.c.id,
                    memory_item.c.body,
                    memory_item.c.confidence,
                )
                .where(
                    memory_item.c.id.in_(ids),
                    memory_item.c.workspace_id == self.workspace_id,
                    memory_item.c.subject == cluster[0].subject,
                    memory_item.c.item_class == FACT,
                    memory_item.c.created_from_page_id.is_(None),
                    memory_item.c.superseded_by.is_(None),
                )
                .order_by(memory_item.c.id)
            )
            if connection.dialect.name == "postgresql":
                donors = donors.with_for_update()
            present = {
                row.id: (row.body, row.confidence)
                for row in (await connection.execute(donors)).all()
            }
            expected = {fact.id: (fact.body, fact.confidence) for fact in cluster}
            if present != expected:
                return
            await connection.execute(
                sa.insert(memory_item).values(
                    id=summary_id,
                    workspace_id=self.workspace_id,
                    subject=cluster[0].subject,
                    body=summary,
                    item_class=SEMANTIC,
                    memory_kind=KIND_FACT,
                    confidence=max(fact.confidence for fact in cluster),
                    source_ref=None,
                    created_from_page_id=None,
                    embedding_digest=None,
                    superseded_by=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            updated = await connection.execute(
                sa.update(memory_item)
                .values(superseded_by=summary_id, updated_at=sa.func.now())
                .where(
                    memory_item.c.id.in_(ids),
                    memory_item.c.workspace_id == self.workspace_id,
                    memory_item.c.subject == cluster[0].subject,
                    memory_item.c.item_class == FACT,
                    memory_item.c.created_from_page_id.is_(None),
                    memory_item.c.superseded_by.is_(None),
                )
            )
            if updated.rowcount != len(ids):
                raise RuntimeError("memory consolidation donors changed while locked")

    async def _summarize(self, model: ModelAccess, cluster: tuple[_AgedFact, ...]) -> str:
        payload = {"facts": [fact.body[:CONSOLIDATE_FACT_CHARS] for fact in cluster]}
        request = ModelRequest(
            model=model.model,
            system=CONSOLIDATE_SYSTEM,
            messages=(Message(role="user", content=json.dumps(payload, separators=(",", ":"))),),
            max_tokens=CONSOLIDATE_MAX_TOKENS,
            reasoning=CONSOLIDATE_REASONING,
        )
        return (await model.complete(request)).strip()[:MAX_SUMMARY_CHARS]


def _recency(fact: _AgedFact) -> tuple[datetime, UUID]:
    return (fact.created_at, fact.id)


@dataclass(frozen=True)
class _LiveCopy:
    id: UUID
    body: str


@dataclass(frozen=True)
class _Group:
    """One (subject, item_class) group of live tool-written rows: the identity the walk orders by,
    and the fingerprint that says whether the group has moved since the sweep last read it."""

    subject: str
    item_class: str
    copies: int
    latest: datetime

    @property
    def key(self) -> tuple[str, str]:
        return (self.subject, self.item_class)

    @property
    def fingerprint(self) -> list[JsonValue]:
        return [self.copies, self.latest.isoformat()]


@dataclass(frozen=True)
class MemoryDeduper:
    """Collapse the live near-duplicate copies of one tool-written statement onto the newest of
    them. No model pass and no summary row: the newest copy already IS the current statement, where
    the consolidator's job is merging facts that are related rather than restatements — so this
    sweep runs over every item class (the copies that accreted are `semantic` ledgers) and gates on
    no age, newest-wins being safe on a row of any age.

    This sweep is the sole superseder of a tool-written row: `MemoryStore.commit` produces no
    derived state, so every restatement lands live and accretes until a tick collapses it.
    DEDUP_MIN_AGE is what keeps that collapse off a member's own writing — a row younger than the
    floor is invisible to every read and stamp here, so a statement made minutes ago is recallable
    in full while the member is still working, and a tick that happens to land mid-thread never
    retires what they just said. Page-derived rows are never read and never stamped — their
    lifecycle is `supersede_page_facts` — and only live rows are touched, since a stamped row is
    one a member brings back by restating it, which re-establishes its recency and so wins the next
    sweep it is read in.

    One group per tick, walked through two KV keys. `DEDUP_CURSOR_KEY` holds the last group read as
    `[subject, item_class]`, and the next tick takes the first group ordering strictly after it,
    wrapping — so the walk advances whatever appeared or vanished in between, where restarting at
    the front on a vanished cursor would re-walk the prefix after every collapse. It is written
    before the group is swept, so a group that raises costs one retry per rotation rather than
    halting the workspace's healing at itself. `DEDUP_FINGERPRINT_PREFIX + item_class + ":" +
    subject` holds that group's `[live copies, max updated_at]` as of its last completed sweep;
    a tick whose group still matches skips the embed pass entirely, since nothing has entered or
    left it, which is what keeps a healed workspace from re-embedding its whole group every hour
    forever. A row still under DEDUP_MIN_AGE is invisible to that aggregate too, so ageing past the
    floor moves the group's count and makes the fingerprint force the re-sweep that first reads it.
    The fingerprint is written only after a sweep completes, so a collapse (which moves both halves)
    and a raise alike leave the group due."""

    embed: EmbedClient
    transaction: Transaction
    workspace_id: UUID
    store: ScopedStore

    async def run(self) -> None:
        groups = await self._groups()
        if not groups:
            return
        swept = self._cursor(await self.store.get(DEDUP_CURSOR_KEY))
        position = next((index for index, group in enumerate(groups) if group.key > swept), 0)
        group = groups[position]
        await self.store.put(DEDUP_CURSOR_KEY, list(group.key))
        fingerprint = f"{DEDUP_FINGERPRINT_PREFIX}{group.item_class}:{group.subject}"
        if await self.store.get(fingerprint) == group.fingerprint:
            return
        await self._dedup_group(group)
        await self.store.put(fingerprint, group.fingerprint)

    def _cursor(self, stored: JsonValue | None) -> tuple[str, ...]:
        """The group the last tick read, as the walk's exclusive lower bound. Only an absent key
        means no tick has run; a value this sweep did not write is a corrupted key space, not a
        first run, and restarting the walk on it would hide the corruption for good."""
        if stored is None:
            return ()
        match stored:
            case [str(subject), str(item_class)]:
                return (subject, item_class)
            case _:
                raise RuntimeError(
                    f"{DEDUP_CURSOR_KEY} holds {stored!r}, not [subject, item_class]"
                )

    async def _groups(self) -> tuple[_Group, ...]:
        """The workspace's groups holding at least MIN_DUPLICATE_COPIES live tool-written rows, in
        the order the cursor walks, each with the fingerprint that decides whether it needs reading
        at all — one aggregate, so dueness costs no query of its own."""
        async with self.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        memory_item.c.subject,
                        memory_item.c.item_class,
                        sa.func.count().label("copies"),
                        sa.func.max(memory_item.c.updated_at).label("latest"),
                    )
                    .where(
                        memory_item.c.workspace_id == self.workspace_id,
                        memory_item.c.created_from_page_id.is_(None),
                        memory_item.c.superseded_by.is_(None),
                        memory_item.c.created_at <= datetime.now(UTC) - DEDUP_MIN_AGE,
                    )
                    .group_by(memory_item.c.subject, memory_item.c.item_class)
                    .having(sa.func.count() >= MIN_DUPLICATE_COPIES)
                    .order_by(memory_item.c.subject, memory_item.c.item_class)
                )
            ).all()
        return tuple(_Group(row.subject, row.item_class, row.copies, row.latest) for row in rows)

    async def _dedup_group(self, group: _Group) -> None:
        copies = await self._live_copies(group)
        embeddings = await self._embed(copies)
        for cluster in await asyncio.to_thread(self._clusters, copies, embeddings):
            if len(cluster) >= MIN_DUPLICATE_COPIES:
                await self._collapse(group, cluster)

    async def _live_copies(self, group: _Group) -> tuple[_LiveCopy, ...]:
        """The group's live tool-written rows, newest first — the order that decides the winner,
        since each cluster keeps the copy it meets first. DEDUP_GROUP_MAX bounds the tick: it is the
        whole population the pass compares, so a group past it collapses its newest copies and stays
        a candidate for the next tick, rather than reading an unbounded set into one transaction and
        clustering it."""
        async with self.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(memory_item.c.id, memory_item.c.body)
                    .where(*self._live_group(group))
                    .order_by(memory_item.c.created_at.desc(), memory_item.c.id.desc())
                    .limit(DEDUP_GROUP_MAX)
                )
            ).all()
        return tuple(_LiveCopy(row.id, row.body) for row in rows)

    async def _embed(self, copies: tuple[_LiveCopy, ...]) -> dict[UUID, tuple[float, ...]]:
        embeddings: dict[UUID, tuple[float, ...]] = {}
        for batch in batched(copies, DEDUP_EMBED_BATCH):
            vectors = await self.embed.embed(
                tuple(copy.body[:CONSOLIDATE_EMBED_CHARS] for copy in batch)
            )
            embeddings.update(zip((copy.id for copy in batch), vectors, strict=True))
        return embeddings

    def _clusters(
        self, copies: tuple[_LiveCopy, ...], embeddings: dict[UUID, tuple[float, ...]]
    ) -> tuple[tuple[_LiveCopy, ...], ...]:
        """Greedy cosine clustering over the newest-first read: each copy joins the first cluster
        whose head is within SUPERSEDE_COSINE — measured on the eval corpus to retire near-verbatim
        copies and never collapse distinct facts — and a copy with no embedding starts a singleton
        the MIN_DUPLICATE_COPIES gate then drops.

        Every copy is compared against every head this pass has opened, because a duplicate pair is
        found by distance and not by position: any slicing of the population by rank silently stops
        collapsing the pairs that straddle the slice, and the group's own ordering says nothing
        about which rows restate each other. What that costs is bounded instead by
        DEDUP_GROUP_MAX and by the pass being cheap — one 26us cosine per (copy, head) pair, 0.5s
        at 200 distinct copies and 52s at the 2000 the read admits. Pure and synchronous so it runs
        under `asyncio.to_thread`: GIL-bound arithmetic of that span belongs in a pool deliberately,
        never inline on the one serve loop."""
        clusters: list[list[_LiveCopy]] = []
        for copy in copies:
            vector = embeddings.get(copy.id)
            joined = False
            if vector:
                for cluster in clusters:
                    head = embeddings.get(cluster[0].id)
                    if head and cosine(vector, head) >= SUPERSEDE_COSINE:
                        cluster.append(copy)
                        joined = True
                        break
            if not joined:
                clusters.append([copy])
        return tuple(tuple(cluster) for cluster in clusters)

    async def _collapse(self, group: _Group, cluster: tuple[_LiveCopy, ...]) -> None:
        """Stamp every copy of the cluster but its head at that head, under a lock the pass takes
        itself: the head and its donors are selected FOR UPDATE in id order, so two passes reaching
        the same rows serialize on them rather than deadlocking or stamping a cycle. The head
        is verified with the donors, so a head another writer superseded while this pass embedded
        leaves the cluster alone — stamping a donor at a dead head would hide it from every reader
        with nothing left to bring it back."""
        head, *donors = cluster
        expected = {copy.id: copy.body for copy in cluster}
        cluster_ids = [copy.id for copy in cluster]
        donor_ids = [copy.id for copy in donors]
        async with self.transaction() as connection:
            locked = (
                sa.select(memory_item.c.id, memory_item.c.body)
                .where(*self._live_group(group), memory_item.c.id.in_(cluster_ids))
                .order_by(memory_item.c.id)
            )
            if connection.dialect.name == "postgresql":
                locked = locked.with_for_update()
            present = {row.id: row.body for row in (await connection.execute(locked)).all()}
            if present != expected:
                return
            updated = await connection.execute(
                sa.update(memory_item)
                .values(superseded_by=head.id, updated_at=sa.func.now())
                .where(*self._live_group(group), memory_item.c.id.in_(donor_ids))
            )
            if updated.rowcount != len(donor_ids):
                raise RuntimeError("memory dedup donors changed while locked")

    def _live_group(self, group: _Group) -> tuple[ColumnElement[bool], ...]:
        return (
            memory_item.c.workspace_id == self.workspace_id,
            memory_item.c.subject == group.subject,
            memory_item.c.item_class == group.item_class,
            memory_item.c.created_from_page_id.is_(None),
            memory_item.c.superseded_by.is_(None),
            memory_item.c.created_at <= datetime.now(UTC) - DEDUP_MIN_AGE,
        )
