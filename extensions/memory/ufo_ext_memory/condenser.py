"""The memory condenser: the two producers that feed recall's otherwise-dead machinery.

`FactDeriver` is the memory extension's second `page_change` consumer — it distills each replayed
source page into durable `fact` memory_items with one bounded metered model pass per batch, so a
synced document becomes recallable facts, not only RAG chunks. `MemoryConsolidator` is the periodic
job that clusters aged `fact` items by embedding cosine and collapses each cluster into one
`semantic` summary through a bounded metered model pass, stamping `superseded_by` on the clustered
originals — the sole producer that makes recall's `superseded_by IS NULL` drop and its
decay-exempt `semantic` handling fire. Both meter through `ctx.model` and both are fail-soft: with
no model wired the model pass is skipped (the deriver still advances its cursor, the consolidator
writes nothing), mirroring gbrain's Tier-B. Every model and embed payload is bounded next to its
call, and each model call runs before the write transaction, never holding it open."""

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
from ufo.sdk.models import Message, ModelRequest
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
FACT_EXTRACT_MAX_TOKENS = 2_048
FACT_EXTRACT_REASONING: Literal["low"] = "low"
EXTRACT_KEEP_NOTABILITY = frozenset({"high", "medium"})
FACT_EXTRACT_SYSTEM = (
    "Distill durable, standalone facts from the source pages the user sends as JSON "
    '({"pages":[{"page_id":"...","body":"..."}]}). For each candidate fact judge its notability '
    "high, medium, or low and emit only high and medium ones. Write each fact as a concise "
    "third-person claim that stands alone without the page, carrying its source page_id, a "
    "memory_kind (one of fact, preference, decision, event, task), and a confidence 1-10. Return "
    'JSON only, no prose: {"facts":[{"page_id":"<id>","notability":"high","memory_kind":"fact",'
    '"confidence":7,"body":"..."}]}.'
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


@dataclass(frozen=True)
class FactDeriver:
    """Distill each replayed source-page change into durable `fact` memory_items, off the write
    path. The core page-change runner owns the cursor and batch loop (a cursor independent of the
    memory indexer's) and hands one delivered batch to `apply`; this filters out tombstones and
    trivially short bodies, then runs one bounded metered model pass per bounded page group and
    commits the kept facts through the store's content-addressed upsert. Once-delivery is the
    cursor's guarantee — each changed page reaches this handler once; the content-addressed commit
    additionally dedups an identical re-derivation onto the same row, but it is no guarantee against
    variant wording, so a non-deterministic pass over an edited page can write a new row. Fail-soft:
    with no model wired the batch is skipped and the runner still advances the cursor (gbrain
    Tier-B)."""

    store: MemoryStore
    model: ModelAccess | None = None

    async def apply(self, changes: tuple[PageChange, ...]) -> None:
        eligible = tuple(
            change
            for change in changes
            if not change.tombstone and len(change.body) >= MIN_PAGE_BODY_CHARS
        )
        if self.model is None or not eligible:
            return
        for group in batched(eligible, EXTRACT_PAGE_BATCH):
            await self._derive(self.model, group)

    async def _derive(self, model: ModelAccess, pages: tuple[PageChange, ...]) -> None:
        by_id = {str(page.page_id): page for page in pages}
        for fact in await self._extract(model, pages):
            page = by_id.get(fact.page_id)
            if page is None or fact.notability.lower() not in EXTRACT_KEEP_NOTABILITY:
                continue
            await self.store.commit(
                MemoryWrite(
                    subject=page.subject,
                    body=fact.body,
                    item_class=FACT,
                    memory_kind=fact.memory_kind,
                    confidence=fact.confidence,
                    source_ref=str(page.page_id),
                )
            )

    async def _extract(
        self, model: ModelAccess, pages: tuple[PageChange, ...]
    ) -> tuple[ExtractedFact, ...]:
        payload = {
            "pages": [
                {"page_id": str(page.page_id), "body": page.body[:MAX_PAGE_BODY_CHARS]}
                for page in pages
            ]
        }
        request = ModelRequest(
            model=model.model,
            system=FACT_EXTRACT_SYSTEM,
            messages=(Message(role="user", content=json.dumps(payload, separators=(",", ":"))),),
            max_tokens=FACT_EXTRACT_MAX_TOKENS,
            reasoning=FACT_EXTRACT_REASONING,
        )
        return _parse_facts(await model.complete(request))


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
        async with self.transaction() as connection:
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
                    embedding_digest=None,
                    superseded_by=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.update(memory_item)
                .values(superseded_by=summary_id, updated_at=sa.func.now())
                .where(memory_item.c.id.in_([fact.id for fact in cluster]))
            )

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


def _parse_facts(text: str) -> tuple[ExtractedFact, ...]:
    """Slice the first JSON object out of the model's completion and validate its `facts` list one
    item at a time, dropping any that fail validation — the extraction stays best-effort so one
    malformed fact never fails the whole batch and wedges the cursor."""
    start = text.find("{")
    if start < 0:
        return ()
    try:
        payload, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return ()
    raw = payload.get("facts") if isinstance(payload, dict) else None
    if not isinstance(raw, list):
        return ()
    facts: list[ExtractedFact] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            facts.append(ExtractedFact.model_validate(item))
        except ValidationError:
            continue
    return tuple(facts)
