"""The memory condenser: the two producers that feed recall's otherwise-dead machinery.

`FactDeriver` is the memory extension's second `page_change` consumer — it distills each replayed
source page into durable `fact` memory_items with one bounded metered model pass per batch, so a
synced document becomes recallable facts, not only RAG chunks, and it is the one writer that retires
a page-derived fact — for exactly the pages whose replacement it just committed.
`MemoryConsolidator` is the periodic job that clusters aged `fact` items by embedding cosine and
collapses each cluster into one `semantic` summary through a bounded metered model pass, stamping
`superseded_by` on the clustered originals — the sole producer that makes recall's
`superseded_by IS NULL` drop and its decay-exempt `semantic` handling fire. Both meter through
`ctx.model`; the consolidator writes nothing without one (gbrain Tier-B), while the deriver requires
one, since a batch it cannot derive is a batch whose replacements do not exist. Every model and
embed payload is bounded next to its call, and each model call runs before the write transaction,
never holding it open."""

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import batched
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, Field, ValidationError

from ufo.sdk.context import ModelAccess
from ufo.sdk.index import EmbedClient
from ufo.sdk.models import Message, ModelRequest, ToolSchema, ToolUseBlock
from ufo.sdk.sources import PageChange
from ufo_ext_memory.store import (
    DEFAULT_CONFIDENCE,
    FACT,
    KIND_FACT,
    MAX_CONFIDENCE,
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
                    body=fact.body,
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
            for cluster in self._clusters(facts, embeddings):
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
        singleton the MIN_CLUSTER_SIZE gate then drops."""
        clusters: list[list[_AgedFact]] = []
        for fact in sorted(facts, key=_recency, reverse=True):
            vector = embeddings.get(fact.id)
            joined = False
            if vector:
                for cluster in clusters:
                    head = embeddings.get(cluster[0].id)
                    if head and _cosine(vector, head) >= CLUSTER_THRESHOLD:
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
            donors = sa.select(
                memory_item.c.id,
                memory_item.c.body,
                memory_item.c.confidence,
            ).where(
                memory_item.c.id.in_(ids),
                memory_item.c.workspace_id == self.workspace_id,
                memory_item.c.subject == cluster[0].subject,
                memory_item.c.item_class == FACT,
                memory_item.c.created_from_page_id.is_(None),
                memory_item.c.superseded_by.is_(None),
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


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    denom = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denom == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / denom
