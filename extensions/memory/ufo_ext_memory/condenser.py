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
`commit` derives nothing, so every restatement it takes lands live.
`SectionWriter` is the periodic pass that opens each band of the wiki: it re-reads the band's live
facts and rewrites that band's one `section` paragraph in place, so what a member reads above the
rows is what the rows currently amount to rather than a ledger of what they once did.
`OverviewWriter` is the same act one altitude up: it re-reads every live fact the workspace holds
and rewrites the one `overview` paragraph the page opens on, which says where the company stands.
Each retires the paragraph standing where its floor has stopped earning a rewrite: nothing else
writes either class, so a paragraph these two leave alone stands over rows no longer under it.
`ProfileWriter` writes the People band — one role and one current focus per member of the roster,
into the extension's own `memory_profile` table, because a profile is written *about* a colleague
and `memory_item.subject` is the disclosure audience.
`PagePass` is the tier above all of them: it reads one subject's whole page at once on the deploy's
own model — every band, its paragraph and its rows — and curates it, stamping `retired_at` on the
rows that repeat one another or carry only the motion of a tool. It is the only reader that can see
six rows written from six source pages as one claim, and retirement is the only act it takes: the
writers above own every paragraph, so a member reads one answer to who wrote what stands over their
rows. The deriver, the consolidator, the two paragraph passes, the People pass and the page pass all
meter through `ctx.model`; all but the deriver write nothing without one (gbrain Tier-B), while the
deriver requires one, since a batch it cannot derive is a batch whose replacements do not exist.
Every model and embed payload is bounded next to its call, each model call runs before the write
transaction, never holding it open, and the clustering both periodic jobs do is arithmetic a worker
thread carries, never the loop."""

import asyncio
import json
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import batched
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from ufo.sdk.context import JsonValue, ModelAccess, ScopedStore
from ufo.sdk.delivery_register import DELIVERY_REGISTER_BLOCK
from ufo.sdk.index import EmbedClient
from ufo.sdk.models import Message, ModelRequest, ToolSchema, ToolUseBlock
from ufo.sdk.o11y import warn
from ufo.sdk.seats import Seats, workspace_domain
from ufo.sdk.sources import PageChange
from ufo.sdk.subjects import SHARED_SUBJECT, subject_shared
from ufo_ext_memory.store import (
    DEFAULT_CONFIDENCE,
    FACT,
    KIND_FACT,
    MAX_CONFIDENCE,
    MEMORY_BODY_MAX_CHARS,
    OVERVIEW,
    SECTION,
    SEMANTIC,
    ItemClass,
    MemoryKind,
    MemoryStore,
    MemoryWrite,
    Transaction,
    clip_to_word,
    mem_page,
    memory_item,
)

PROMPTS = Path(__file__).parent / "prompts"

MAX_PAGE_BODY_CHARS = 8_000
MIN_PAGE_BODY_CHARS = 40
MACHINE_STATUS_STREAMS = frozenset(
    {
        "commit_comment_reactions",
        "contributor_activity",
        "issue_comment_reactions",
        "issue_reactions",
        "issue_timeline_events",
        "pull_request_comment_reactions",
        "pull_request_commits",
        "pull_request_stats",
        "stargazers",
        "workflow_jobs",
        "workflow_runs",
    }
)
"""The streams whose pages a run of the machine writes about itself: build outcomes and durations,
reaction tallies, commit walks, timeline entries. Every one of them states something the system can
state again on demand, so a row distilled from one tells a member what they could have read from the
source and displaces a row that tells them something they could not. The gate is structural rather
than a sentence in the extraction prompt, because a page that never reaches the model cannot be
recorded against the member's judgement of what matters.

A `PageChange` carries the provider's bare stream name and never says which provider it came from,
so a name here matches every connector that happens to use it. That is why `workflows` is absent: it
is GitHub's list of workflow definitions, which is configuration a member may well want, and it is
also HubSpot's marketing automation flows and Wrike's task workflows — and this gate does not merely
skip a page, it retires what that page already derived. A name two connectors share would delete a
member's rows about their own work, so a test holds this set to names only one connector uses."""
EXTRACT_PAGE_BATCH = 10
FACT_EXTRACT_MAX_TOKENS = 16_384
FACT_EXTRACT_TOOL = "record_facts"
FACT_EXTRACT_TOOL_DESCRIPTION = (
    "Record every concrete, source-supported fact from the source pages, one entry per fact."
)
FACT_EXTRACT_SYSTEM = (
    DELIVERY_REGISTER_BLOCK + "\n\n" + (PROMPTS / "fact_extract.md").read_text().strip()
)
EXTRACT_RESTATEMENT_OVERLAP = 0.9
MIN_RESTATEMENT_WORDS = 4
FILLER_WORDS = frozenset(
    "a an and as at be been by for from had has have in is it its of on or that the their there "
    "this to was were which who whose will with".split()
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
MAX_SUMMARY_WORDS = 150
MAX_SUMMARY_SENTENCES = 5
"""The shape of a written paragraph, the memory text a member reads whole rather than scans: at most
150 words in at most 5 sentences, where the plain-language guidance the comps study collected
settles."""
CONSOLIDATE_MAX_TOKENS = 1_024
CONSOLIDATE_REASONING: Literal["low"] = "low"
CONSOLIDATE_SYSTEM = (
    DELIVERY_REGISTER_BLOCK + "\n\n" + (PROMPTS / "consolidate.md").read_text().strip()
)
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

WORKSPACE_SECTION_HEADINGS: dict[MemoryKind, str] = {
    "preference": "How the team works",
    "decision": "Decisions",
    "task": "Open work",
    "event": "History",
    KIND_FACT: "Facts",
}
"""The heading each band of the workspace's own page carries, by the memory_kind whose rows stand
under it."""
MEMBER_SECTION_HEADINGS: dict[MemoryKind, str] = {
    "preference": "How you work",
    "decision": "Decisions",
    "task": "Tasks",
    "event": "History",
    KIND_FACT: "Facts",
}
"""The heading each band of one member's own page carries. The same kinds, addressed to the person
they are about rather than to the company — which is why the two sets exist at all."""


def section_headings(subject: str) -> dict[MemoryKind, str]:
    """The headings the page draws over one subject's bands. The paragraph a pass writes opens the
    band the member reads it under, and the model is told which band that is, so a paragraph written
    under the workspace's wording would address the company on a page addressed to one person."""
    if subject_shared(subject):
        return WORKSPACE_SECTION_HEADINGS
    return MEMBER_SECTION_HEADINGS


MIN_SECTION_FACTS = 3
"""How many rows a band needs before its paragraph says more than the rows do. Under three, what a
paragraph can say is what the member reads underneath it anyway, at the cost of a model pass and of
a heading that opens with a restatement."""
SECTION_SYSTEM = DELIVERY_REGISTER_BLOCK + "\n\n" + (PROMPTS / "section.md").read_text().strip()

MIN_OVERVIEW_FACTS = 10
"""How many live facts a page needs before its opening paragraph says more than the page does. The
overview answers what the company does, how it is doing and what is about to happen; under ten rows
the whole page is a glance, and the paragraph would restate it at the cost of a model pass."""
OVERVIEW_FACTS_MAX = 200
"""How many of a page's live facts one overview is written from, newest first. The paragraph leads
with what has changed or is at stake, so what it can afford to lose is the oldest of what the page
holds — and a bound is what keeps one metered pass off an unbounded read."""
OVERVIEW_SYSTEM = DELIVERY_REGISTER_BLOCK + "\n\n" + (PROMPTS / "overview.md").read_text().strip()

PROFILE_ROSTER_MAX = 200
"""How many members one People pass writes for, in roster order — the order `Seats.snapshot` reads,
oldest member first. A roster past the bound leaves its newest members without an entry rather than
sending an unbounded payload, and they are the members whose facts a workspace holds least of."""
PROFILE_FACTS_MAX = 200
PROFILE_ROLE_MAX_CHARS = 60
"""How long a role runs. The prompt asks for a short phrase and its own longest example —
`Cofounder, product and finance` — is thirty characters; sixty leaves room for a second clause and
refuses a sentence, which is the failure this band reads worst with."""
PROFILE_MAX_TOKENS = 8_192
PROFILE_TOOL = "write_people"
PROFILE_TOOL_DESCRIPTION = (
    "Record one entry per member of the roster: what they do here and what they are carrying now."
)
PROFILE_SYSTEM = DELIVERY_REGISTER_BLOCK + "\n\n" + (PROMPTS / "people.md").read_text().strip()
PROFILE_ADMIN_STANDING = "workspace admin"
PROFILE_MEMBER_STANDING = "workspace member"
PROFILE_SEATED_STANDING = "seated"
PROFILE_UNSEATED_STANDING = "unseated"

PAGE_PASS_MIN_ROWS = 10
"""How many live rows a subject's page needs before the deploy model reads it whole. Under ten rows
a member takes the page in at a glance, and the repetition this pass exists to find is what several
source pages write about one claim — a page that thin has not accumulated it yet, and the pass would
spend the deploy default's tokens to answer that nothing repeats."""
PAGE_PASS_SECTION_ROWS = 100
PAGE_PASS_ROW_CHARS = 300
PAGE_PASS_MAX_TOKENS = 8_192
PAGE_PASS_REASONING: Literal["off"] = "off"
"""What this pass buys is the deploy default's judgement over a page no other reader sees whole, not
optional background thinking on top of it — and the round is one compelled tool call, which is what
every forced call in this repo asks for. A model that requires reasoning uses its minimum adaptive
effort, which supports forced tool use."""
PAGE_PASS_TOOL = "curate_page"
PAGE_PASS_TOOL_DESCRIPTION = "Record the rows this page reads better without, one entry each."
PAGE_PASS_SYSTEM = DELIVERY_REGISTER_BLOCK + "\n\n" + (PROMPTS / "page_pass.md").read_text().strip()
MAX_BAND_RETIREMENT = 0.8
"""The most of one band's rows a single pass may retire. Every retirement admitted has named a
row that keeps the claim, so the bar is not measuring loss — it measures how much of a band one
answer may assert away onto rows of its own choosing, which is a claim no code here can check: a
fifth of the band stands, and a band of one still admits its row."""


def live_page_link() -> ColumnElement[bool]:
    """The join that keeps a read to the facts a member can actually be served. A fact carries the
    page and revision it was derived from, and the page mirror carries where that page stands now,
    so a row whose page moved on without replacing it — a body too thin to send, an extraction that
    recorded none, a stream the deriver stopped reading — matches nothing here. `MemoryObjects` and
    `MemoryStore._surviving` fence the same way in Python for the reads a member drives; every
    background pass that writes what a member reads has to agree with them, or it writes a paragraph
    from rows the page under it does not carry."""
    return sa.and_(
        mem_page.c.page_uid == memory_item.c.created_from_page_uid,
        mem_page.c.workspace_id == memory_item.c.workspace_id,
        mem_page.c.subject == memory_item.c.subject,
        mem_page.c.revision == memory_item.c.created_from_page_revision,
    )


def member_servable() -> ColumnElement[bool]:
    """Whether a member could be served this row, in the one form `MemoryObjects._page` and
    `MemoryStore._surviving` already answer it: a row an agent wrote carries no page and always
    stands, and a row derived from a page stands only while that page still sits at the revision it
    was read from. Every pass writing prose a member reads above a band has to agree with the band,
    or it summarises rows the band does not carry — and a member's own correction, which carries no
    page at all, belongs in that prose above everything else. `PagePass` takes the narrower join
    instead: its judgement is destructive and nothing restores what it retires, so it is kept off
    the rows a member wrote."""
    return sa.or_(
        memory_item.c.created_from_page_uid.is_(None),
        sa.select(mem_page.c.page_uid).where(live_page_link()).exists(),
    )


class ExtractedFact(BaseModel):
    """One fact the extraction model read out of a source page — untrusted model output validated
    at this boundary before it reaches `memory_item`. `page_id` maps the fact back to the page that
    scopes its subject. The row budget is part of that validation: the prompt states it, and a body
    that overran it arrives cut back to its last whole word, so what a member reads is a row that
    ends on a word rather than one the object index ends mid-word with an ellipsis."""

    page_id: str
    body: str
    memory_kind: MemoryKind = Field(
        default=KIND_FACT,
        description=(
            "Which part of the workspace's wiki this fact is written into. "
            "`decision` — a choice this workspace settled, which stands until someone changes it. "
            "`task` — work this workspace took on and still has to carry. "
            "`event` — something that already happened and is finished. "
            "`preference` — how this workspace has said it wants things done. "
            "`fact` — what is durably true of a person, a thing, or the workspace. "
            "A completed action is `event`, never `task`: a review that was requested, a branch "
            "that was merged and a payment that cleared are all finished. Choose `task` only where "
            "the work is still owed. "
            "`decision`, `task` and `preference` speak for this workspace, so a row takes one "
            "only where this workspace is the party that settled it, took it on, or wants it. "
            "A due date does not make a row `task`. A requirement that arrived from outside — a "
            "supplier's cut-off, a regulator's deadline, a platform's end of support — is a "
            "`fact` about the party that set it, however much work it makes here; it is this "
            "workspace's `task` only where someone here said this workspace would do it."
        ),
    )
    """The kind is not a label on the row — it is the band the wiki files it under, and three of
    those bands speak for the workspace. It reaches the model as this description and nowhere else:
    scoping `preference` alone moved its cases while `task` stayed at zero, on the same commit and
    the same model, which is what says the schema carries this and the prompt does not."""
    confidence: int = Field(default=DEFAULT_CONFIDENCE, ge=1, le=MAX_CONFIDENCE)

    @field_validator("body")
    @classmethod
    def within_row_budget(cls, body: str) -> str:
        return clip_to_word(body, MEMORY_BODY_MAX_CHARS)


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
    longer there is retired outright, since nothing will ever replace its facts, a machine-status
    page is retired outright for the same reason — the gate refuses its replacement, so a row an
    earlier sync derived from it would otherwise stand for ever — and every other page goes through
    one bounded metered model pass per bounded group of substantial live pages — whose committed
    facts are the only thing that authorizes retiring the revisions they replace.
    Removal is conditional on the replacement's committed result, not merely later than it: a page
    the pass leaves without a fact (a body too thin to send, an extraction carrying none) keeps
    every fact it has, fenced out of recall by its revision until a derivation supersedes it. An
    extraction the pass cannot read is no settlement either, and settles nothing by raising: like a
    batch with no model wired, it holds the cursor where it stands so the next tick replays those
    pages, rather than advancing past facts nothing will ever derive again. Once-delivery is the
    cursor's guarantee — each changed page reaches this handler once; the content-addressed commit
    dedups an identical re-derivation onto the same row, so a replayed batch names the same rows and
    the retirement finds nothing left. What each pass retires is every other link the page still
    carries, which is what makes a rebuild work: the cursor is sent back over pages whose revisions
    never moved, and the statements being replaced sit at the very revision the pass settles on."""

    store: MemoryStore
    model: ModelAccess

    async def apply(self, changes: tuple[PageChange, ...]) -> None:
        live = await self.store.page_states(tuple(change.page_id for change in changes))
        for change in changes:
            if change.page_id not in live or change.stream in MACHINE_STATUS_STREAMS:
                await self.store.supersede_page_facts(change.page_id, None)
        eligible = tuple(
            change
            for change in changes
            if change.page_id in live
            and not change.tombstone
            and len(change.body) >= MIN_PAGE_BODY_CHARS
            and change.stream not in MACHINE_STATUS_STREAMS
        )
        for group in batched(eligible, EXTRACT_PAGE_BATCH):
            for page_id, kept in (await self._derive(group)).items():
                await self.store.supersede_page_facts(page_id, kept)

    async def _derive(self, pages: tuple[PageChange, ...]) -> dict[UUID, frozenset[UUID]]:
        """Commit the kept facts of one bounded model pass over the pages still exactly where the
        change found them, and return, for each page a fact actually landed for, the rows that page
        then stands behind — the only pages with a replacement to retire anything against, and the
        only account of what that replacement is."""
        current = await self.store.page_states(tuple(page.page_id for page in pages))
        authorized = tuple(
            page
            for page in pages
            if (state := current.get(page.page_id)) is not None
            and state.subject == page.subject
            and state.revision == page.revision
        )
        if not authorized:
            return {}
        extracted = await self._extract(authorized)
        by_id = {str(page.page_id): page for page in authorized}
        settled: dict[UUID, frozenset[UUID]] = {}
        for fact in extracted:
            page = by_id.get(fact.page_id)
            if page is None:
                continue
            latest = (await self.store.page_states((page.page_id,))).get(page.page_id)
            if latest is None or latest.subject != page.subject or latest.revision != page.revision:
                continue
            landed = await self.store.commit(
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
            settled[page.page_id] = settled.get(page.page_id, frozenset()) | {landed}
        return settled

    async def _extract(self, pages: tuple[PageChange, ...]) -> tuple[ExtractedFact, ...]:
        """The one bounded metered model pass over a group, returning the facts it recorded. The
        pass compels one `record_facts` call whose input schema is `ExtractedFacts`, so the model's
        answer is arguments the provider decoded — never structured data sliced out of a completion,
        where one character the model failed to escape costs the whole group its facts. Each entry
        is validated on its own, so an entry the contract does not satisfy drops without taking the
        rest with it, while a reply carrying no recorded facts at all raises: it is not the same
        answer as "these pages hold nothing", and the caller must settle nothing for the group.
        Each page carries its title and stream into the payload, because a fact naming "the pull
        request" reads as a subject to a model holding the page and as nothing to the member who
        meets that row alone. One reply is also the one place a page's restatements of a single
        claim are visible to each other — the store's content address catches only identical text,
        and the dedup sweep never reads a page-derived row — so a restatement collapses here onto
        the entry that carries the most.
        The request asks for reasoning off to avoid paying for optional background thinking; a
        model that requires reasoning uses its minimum adaptive effort, which supports forced tool
        use."""
        payload = {
            "pages": [
                {
                    "page_id": str(page.page_id),
                    "title": page.title,
                    "stream": page.stream,
                    "body": page.body[:MAX_PAGE_BODY_CHARS],
                }
                for page in pages
            ]
        }
        request = ModelRequest(
            model=self.model.model,
            system=FACT_EXTRACT_SYSTEM,
            messages=(Message(role="user", content=json.dumps(payload, separators=(",", ":"))),),
            max_tokens=FACT_EXTRACT_MAX_TOKENS,
            conversation_cache_ttl="5m",
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
                fact = ExtractedFact.model_validate(entry)
            except ValidationError:
                continue
            restated = next(
                (
                    index
                    for index, kept in enumerate(facts)
                    if kept.page_id == fact.page_id and _restates(kept.body, fact.body)
                ),
                None,
            )
            if restated is None:
                facts.append(fact)
            elif len(fact.body) > len(facts[restated].body):
                facts[restated] = fact
        return tuple(facts)


def _content_words(body: str) -> frozenset[str]:
    return frozenset(word for word in re.split(r"\W+", body.lower()) if word) - FILLER_WORDS


def _restates(kept: str, candidate: str) -> bool:
    """Whether one entry of a reply says what another entry already says. The measure is how much of
    the shorter entry's content the other one covers, because a restatement is one claim carrying
    extra words: "Ivan suggested that Marshall book a call with Nalu, who was identified as a
    co-founder of Idler.ai" is covered whole by the same sentence naming the scheduling link. Two
    entries that share a topic while each carries content of its own stay apart, which a measure
    dividing by the union does not manage on this data — those two sentences score 0.23 against a
    threshold no distinct pair of claims survives."""
    left, right = _content_words(kept), _content_words(candidate)
    smaller = min(len(left), len(right))
    if smaller < MIN_RESTATEMENT_WORDS:
        return False
    return len(left & right) / smaller >= EXTRACT_RESTATEMENT_OVERLAP


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
                            memory_item.c.created_from_page_uid.is_(None),
                            memory_item.c.superseded_by.is_(None),
                            memory_item.c.retired_at.is_(None),
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
                    memory_item.c.created_from_page_uid.is_(None),
                    memory_item.c.superseded_by.is_(None),
                    memory_item.c.retired_at.is_(None),
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
                    memory_item.c.created_from_page_uid.is_(None),
                    memory_item.c.superseded_by.is_(None),
                    memory_item.c.retired_at.is_(None),
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
            conversation_cache_ttl="5m",
            reasoning=CONSOLIDATE_REASONING,
        )
        return _to_overview_budget((await model.complete(request)).strip())


def _to_overview_budget(summary: str) -> str:
    """A paragraph cut to the budget its prompt states, on whole sentences. A summary inside the
    budget stands exactly as written. One that overran gives up its last sentences, which is what
    its own reader loses least by: the paragraph opens on what the rows behind it amount to, and the
    sentences that fall are the elaboration behind that. A first sentence alone past the character
    budget has no sentence boundary to cut on and falls back to the word."""
    kept: list[str] = []
    for sentence in SENTENCE_END.split(summary)[:MAX_SUMMARY_SENTENCES]:
        candidate = " ".join((*kept, sentence))
        if len(candidate) > MEMORY_BODY_MAX_CHARS or len(candidate.split()) > MAX_SUMMARY_WORDS:
            break
        kept.append(sentence)
    return " ".join(kept) if kept else clip_to_word(summary, MEMORY_BODY_MAX_CHARS)


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
    sweep runs over the classes a statement accretes copies in (the copies that accreted are
    `semantic` ledgers) and gates on no age, newest-wins being safe on a row of any age. A `section`
    row is never read and never stamped: `SectionWriter` keeps exactly one live paragraph per
    `(subject, memory_kind)`, so a subject's sections are one row per band of the wiki — five
    different statements sharing a class, which this sweep would otherwise read as five copies of
    one and collapse onto whichever was written last.

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
                        memory_item.c.created_from_page_uid.is_(None),
                        memory_item.c.item_class != SECTION,
                        memory_item.c.superseded_by.is_(None),
                        memory_item.c.retired_at.is_(None),
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
            memory_item.c.created_from_page_uid.is_(None),
            memory_item.c.superseded_by.is_(None),
            memory_item.c.retired_at.is_(None),
            memory_item.c.created_at <= datetime.now(UTC) - DEDUP_MIN_AGE,
        )


@dataclass(frozen=True)
class _SectionFact:
    body: str
    confidence: int


@dataclass(frozen=True)
class _Standing:
    """The identity of a paragraph that stands rather than accretes: exactly one live row per
    `(subject, item_class, memory_kind)`. A band of the wiki is `(subject, section, kind)` and a
    page's opening is `(subject, overview, fact)` — one rule, so a second writer of either cannot
    invent a second answer to what "the paragraph standing here" means."""

    subject: str
    item_class: ItemClass
    memory_kind: MemoryKind


async def _rewrite_in_place(
    connection: AsyncConnection,
    workspace_id: UUID,
    standing: _Standing,
    paragraph: str,
    confidence: int,
) -> None:
    """Land one paragraph and retire the one it replaces, on the caller's connection. The live
    paragraphs are selected FOR UPDATE in id order before the insert, so two passes reaching one
    place serialize on them rather than each leaving a paragraph behind, and a row that moved under
    the lock raises instead of leaving a reader two openings to choose between. The section pass and
    the overview pass both write here, so what "one live paragraph" means is stated once and cannot
    drift between the writers of it."""
    paragraph_id = uuid4()
    live = (
        memory_item.c.workspace_id == workspace_id,
        memory_item.c.subject == standing.subject,
        memory_item.c.item_class == standing.item_class,
        memory_item.c.memory_kind == standing.memory_kind,
        memory_item.c.superseded_by.is_(None),
    )
    replacing = sa.select(memory_item.c.id).where(*live).order_by(memory_item.c.id)
    if connection.dialect.name == "postgresql":
        replacing = replacing.with_for_update()
    retiring = [row.id for row in (await connection.execute(replacing)).all()]
    await connection.execute(
        sa.insert(memory_item).values(
            id=paragraph_id,
            workspace_id=workspace_id,
            subject=standing.subject,
            body=paragraph,
            item_class=standing.item_class,
            memory_kind=standing.memory_kind,
            confidence=confidence,
            source_ref=None,
            created_from_page_id=None,
            embedding_digest=None,
            superseded_by=None,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    if not retiring:
        return
    updated = await connection.execute(
        sa.update(memory_item)
        .values(superseded_by=paragraph_id, updated_at=sa.func.now())
        .where(*live, memory_item.c.id.in_(retiring))
    )
    if updated.rowcount != len(retiring):
        raise RuntimeError("memory paragraph changed while locked")


async def _retire_standing(
    connection: AsyncConnection, workspace_id: UUID, standing: _Standing
) -> None:
    """Take the paragraph standing at one place off the wiki, on the caller's connection. A pass
    writes only where the rows under it clear its floor, so a place that has fallen under one is a
    place no pass reaches again: without this its paragraph would stand for ever over rows it was
    never written from, which is the drift `_rewrite_in_place` prevents only for as long as a place
    keeps earning a rewrite. Retirement is `retired_at`, the fence every live-row read already
    honours, and the digest goes with it so the chunks the paragraph published leave the index on
    the tick that follows — a paragraph nobody may read must not hold a candidate slot recall could
    spend on a live row. No writer of either class exists but these two passes, so a paragraph
    nothing here retires is a paragraph nothing retires at all."""
    await connection.execute(
        sa.update(memory_item)
        .values(
            retired_at=sa.func.now(),
            embedding_digest=None,
            embedding_claimed_at=None,
            updated_at=sa.func.now(),
        )
        .where(
            memory_item.c.workspace_id == workspace_id,
            memory_item.c.subject == standing.subject,
            memory_item.c.item_class == standing.item_class,
            memory_item.c.memory_kind == standing.memory_kind,
            memory_item.c.superseded_by.is_(None),
            memory_item.c.retired_at.is_(None),
        )
    )


@dataclass(frozen=True)
class SectionWriter:
    """Rewrite the paragraph that opens each band of a workspace's wiki. A periodic job fed only by
    its own interval — never a page change or a memory write — so it can never fire on the facts a
    derivation just produced. `run` takes every `(subject, memory_kind)` band holding at least
    MIN_SECTION_FACTS live facts, reads that band's newest facts (page-derived rows included: what
    stands under these headings is mostly what the synced sources said, so a paragraph blind to them
    would describe a wiki nobody has), and turns them into one paragraph through a bounded metered
    model pass. The paragraph lands as a `section` item carrying the band's own memory_kind, and the
    band's previous paragraph is stamped `superseded_by` it in the same transaction.

    So each run is a re-read of what stands rather than an entry appended to a ledger: a band holds
    exactly one live paragraph however often the pass runs, and a fact that has since been retired
    leaves the wiki's prose in the same tick as it leaves the rows. A band that has fallen under
    MIN_SECTION_FACTS since its last pass loses its paragraph rather than keeping one: the floor
    decides whether a band is written, and a floor that decided only which bands to skip would let
    the last paragraph a band earned stand over rows the page has stopped holding. Fail-soft: with
    no model wired nothing is written."""

    transaction: Transaction
    workspace_id: UUID
    model: ModelAccess | None = None

    async def run(self) -> None:
        if self.model is None:
            return
        sections = await self._sections()
        for band in await self._standing():
            if band in sections:
                continue
            async with self.transaction() as connection:
                await _retire_standing(connection, self.workspace_id, band)
        for section in sections:
            facts = await self._facts(section)
            if len(facts) < MIN_SECTION_FACTS:
                continue
            paragraph = await self._summarize(self.model, section, facts)
            if not paragraph:
                continue
            async with self.transaction() as connection:
                await _rewrite_in_place(
                    connection,
                    self.workspace_id,
                    section,
                    paragraph,
                    max(fact.confidence for fact in facts),
                )

    async def _sections(self) -> tuple[_Standing, ...]:
        """The workspace's bands worth a paragraph, in a fixed order: those holding at least
        MIN_SECTION_FACTS of the facts `_facts` would send. The floor counts what the paragraph
        would be written from, or a band whose rows a member cannot be served would be neither
        written nor retired — absent from the payload, present in this read, and so keeping for ever
        the paragraph its rows no longer support. A memory_kind the subject's own headings do not
        name is a band nobody could read the paragraph under, so it raises rather than being written
        into a section that does not exist."""
        async with self.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(memory_item.c.subject, memory_item.c.memory_kind)
                    .where(member_servable())
                    .where(
                        memory_item.c.workspace_id == self.workspace_id,
                        memory_item.c.item_class == FACT,
                        memory_item.c.superseded_by.is_(None),
                        memory_item.c.retired_at.is_(None),
                    )
                    .group_by(memory_item.c.subject, memory_item.c.memory_kind)
                    .having(sa.func.count() >= MIN_SECTION_FACTS)
                    .order_by(memory_item.c.subject, memory_item.c.memory_kind)
                )
            ).all()
        unknown = sorted(
            {
                row.memory_kind
                for row in rows
                if row.memory_kind not in section_headings(row.subject)
            }
        )
        if unknown:
            raise RuntimeError(f"memory_kind {unknown} names no wiki section")
        return tuple(_Standing(row.subject, SECTION, row.memory_kind) for row in rows)

    async def _standing(self) -> tuple[_Standing, ...]:
        """The bands holding a paragraph now, whatever their rows have since come to. A band under
        the floor is absent from `_sections`, so what it holds is exactly what no rewrite would
        reach — the pass reads what stands so it can retire the difference."""
        async with self.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(memory_item.c.subject, memory_item.c.memory_kind)
                    .where(
                        memory_item.c.workspace_id == self.workspace_id,
                        memory_item.c.item_class == SECTION,
                        memory_item.c.superseded_by.is_(None),
                        memory_item.c.retired_at.is_(None),
                    )
                    .group_by(memory_item.c.subject, memory_item.c.memory_kind)
                    .order_by(memory_item.c.subject, memory_item.c.memory_kind)
                )
            ).all()
        return tuple(_Standing(row.subject, SECTION, row.memory_kind) for row in rows)

    async def _facts(self, section: _Standing) -> tuple[_SectionFact, ...]:
        """The band's live facts, newest first and bounded by MAX_BUCKET_FACTS — the population one
        paragraph is written from, so a band past the bound is written from what a member reads at
        the top of it rather than from an unbounded read."""
        async with self.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(memory_item.c.body, memory_item.c.confidence)
                    .where(member_servable())
                    .where(
                        memory_item.c.workspace_id == self.workspace_id,
                        memory_item.c.subject == section.subject,
                        memory_item.c.item_class == FACT,
                        memory_item.c.memory_kind == section.memory_kind,
                        memory_item.c.superseded_by.is_(None),
                        memory_item.c.retired_at.is_(None),
                    )
                    .order_by(memory_item.c.created_at.desc(), memory_item.c.id.desc())
                    .limit(MAX_BUCKET_FACTS)
                )
            ).all()
        return tuple(_SectionFact(row.body, row.confidence) for row in rows)

    async def _summarize(
        self, model: ModelAccess, section: _Standing, facts: tuple[_SectionFact, ...]
    ) -> str:
        """The one bounded metered model pass per band, run before any write transaction is opened.
        The heading travels with the facts because the paragraph is read directly under it: a
        paragraph written blind to its band restates the heading or answers a different one, and one
        written under the workspace's wording addresses the company on a member's own page."""
        payload = {
            "section": section_headings(section.subject)[section.memory_kind],
            "facts": [fact.body[:CONSOLIDATE_FACT_CHARS] for fact in facts],
        }
        request = ModelRequest(
            model=model.model,
            system=SECTION_SYSTEM,
            messages=(Message(role="user", content=json.dumps(payload, separators=(",", ":"))),),
            max_tokens=CONSOLIDATE_MAX_TOKENS,
            conversation_cache_ttl="5m",
            reasoning=CONSOLIDATE_REASONING,
        )
        return _to_overview_budget((await model.complete(request)).strip())


@dataclass(frozen=True)
class OverviewWriter:
    """Rewrite the one paragraph a workspace's wiki opens on. A periodic job fed only by its own
    interval — never a page change or a memory write — so it can never fire on the facts a
    derivation just produced. `run` reads every live fact the workspace-shared subject holds, across
    every band, and turns them into one paragraph through a bounded metered model pass. The
    paragraph lands as an `overview` item and the previous one is stamped `superseded_by` it in the
    same transaction, so a subject holds exactly one live overview however often the pass runs.

    The shared subject alone. The paragraph says where the company stands — what it does, the
    numbers it steers by, what is in flight — and a member's own page is not a company; writing one
    from a person's rows under that prompt would tell them about a business they are the whole of.
    A page that has fallen under MIN_OVERVIEW_FACTS loses its opening paragraph rather than keeping
    it, on the same rule the section pass keeps: the floor decides whether a page is written, so a
    page it turns away has to be a page with nothing standing over it. Fail-soft: with no model
    wired nothing is written."""

    transaction: Transaction
    workspace_id: UUID
    model: ModelAccess | None = None

    async def run(self) -> None:
        if self.model is None:
            return
        standing = _Standing(SHARED_SUBJECT, OVERVIEW, KIND_FACT)
        facts = await self._facts()
        if len(facts) < MIN_OVERVIEW_FACTS:
            async with self.transaction() as connection:
                await _retire_standing(connection, self.workspace_id, standing)
            return
        async with self.transaction() as connection:
            domain = await workspace_domain(connection, self.workspace_id)
        paragraph = await self._write(self.model, domain, facts)
        if not paragraph:
            return
        async with self.transaction() as connection:
            await _rewrite_in_place(
                connection,
                self.workspace_id,
                standing,
                paragraph,
                max(fact.confidence for fact in facts),
            )

    async def _facts(self) -> tuple[_SectionFact, ...]:
        """The workspace's live facts, newest first and bounded by OVERVIEW_FACTS_MAX — every band
        at once, which is what makes this paragraph the page's opening rather than a sixth band. A
        row the page pass retired and a row consolidation superseded are both gone from the wiki, so
        a paragraph written from either would state a page nobody reads."""
        async with self.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(memory_item.c.body, memory_item.c.confidence)
                    .where(member_servable())
                    .where(
                        memory_item.c.workspace_id == self.workspace_id,
                        memory_item.c.subject == SHARED_SUBJECT,
                        memory_item.c.item_class == FACT,
                        memory_item.c.superseded_by.is_(None),
                        memory_item.c.retired_at.is_(None),
                    )
                    .order_by(memory_item.c.created_at.desc(), memory_item.c.id.desc())
                    .limit(OVERVIEW_FACTS_MAX)
                )
            ).all()
        return tuple(_SectionFact(row.body, row.confidence) for row in rows)

    async def _write(
        self, model: ModelAccess, domain: str | None, facts: tuple[_SectionFact, ...]
    ) -> str:
        """The one bounded metered model pass, run before any write transaction is opened. The
        workspace travels with the facts because the prompt writes about a company by name, and what
        names it is the domain its members sign in on. A workspace whose members have all left
        carries no domain and travels without one, which the prompt already answers: it names only
        what a fact carries."""
        payload = {
            "workspace": domain,
            "facts": [fact.body[:CONSOLIDATE_FACT_CHARS] for fact in facts],
        }
        request = ModelRequest(
            model=model.model,
            system=OVERVIEW_SYSTEM,
            messages=(Message(role="user", content=json.dumps(payload, separators=(",", ":"))),),
            max_tokens=CONSOLIDATE_MAX_TOKENS,
            conversation_cache_ttl="5m",
            reasoning=CONSOLIDATE_REASONING,
        )
        return _to_overview_budget((await model.complete(request)).strip())


memory_profile = sa.table(
    "memory_profile",
    sa.column("workspace_id", sa.Uuid),
    sa.column("member_id", sa.Uuid),
    sa.column("role", sa.Text),
    sa.column("focus", sa.Text),
    sa.column("written_at", sa.DateTime(timezone=True)),
)
"""What the workspace knows about each of its members, one row per member. It is not a
`memory_item`: `subject` there is the disclosure audience, so an entry about a colleague filed under
`member:<id>` would be private to the very person it describes, and one filed under `shared` would
be indistinguishable from a row of the Facts band. The `profile` object kind publishes these rows;
`ProfileWriter` is the only writer."""


class WrittenProfile(BaseModel):
    """One member's entry as the People pass wrote it — untrusted model output validated at this
    boundary before anything is stored. Each field carries its own budget: the prompt states the
    shape, and a value that overran arrives cut back to its last whole word, so what a member reads
    is a phrase that ends on a word."""

    name: str = Field(min_length=1, description="The member, exactly as the roster names them.")
    role: str = Field(
        min_length=1,
        description="What this person does here, in a short phrase. Not a sentence.",
    )
    focus: str = Field(
        min_length=1,
        description="What they are carrying now, in one sentence, with the name and the date that "
        "make it concrete.",
    )

    @field_validator("role")
    @classmethod
    def within_role_budget(cls, role: str) -> str:
        return clip_to_word(role, PROFILE_ROLE_MAX_CHARS)

    @field_validator("focus")
    @classmethod
    def within_row_budget(cls, focus: str) -> str:
        return clip_to_word(focus, MEMORY_BODY_MAX_CHARS)


class WrittenPeople(BaseModel):
    """The arguments of the People pass's `write_people` call — the shape whose JSON schema is the
    tool contract the model records against, so the entries arrive as arguments the provider decoded
    rather than as JSON this code reads out of prose."""

    people: tuple[WrittenProfile, ...] = ()


@dataclass(frozen=True)
class _Rostered:
    member_id: UUID
    name: str
    standing: str


@dataclass(frozen=True)
class ProfileWriter:
    """Write what the workspace knows about each of its members: a role and a current focus, one row
    per member, rewritten in place. A periodic job fed only by its own interval, so it can never
    fire on the facts a derivation just produced.

    The facts it reads are the workspace-shared ones alone. A profile is read by everyone the roster
    holds, so a pass that read a member's own rows would carry one person's private memory onto a
    page their colleagues open. What it can say about someone is therefore exactly what those
    colleagues could already read in the Facts band, grouped under the person it names.

    One metered pass per workspace compels one `write_people` call; each entry is validated on its
    own, so an entry the contract does not satisfy drops without taking the rest with it, and an
    entry naming nobody on the roster is discarded rather than guessed at. Fail-soft: with no model
    wired nothing is written."""

    transaction: Transaction
    workspace_id: UUID
    model: ModelAccess | None = None

    async def run(self) -> None:
        if self.model is None:
            return
        roster = await self._roster()
        if not roster:
            return
        facts = await self._facts()
        written = await self._write(self.model, roster, facts)
        by_name = {member.name: member.member_id for member in roster}
        entries = tuple(
            (member_id, entry)
            for entry in written
            if (member_id := by_name.get(entry.name)) is not None
        )
        if entries:
            await self._store(entries)

    async def _roster(self) -> tuple[_Rostered, ...]:
        """The workspace's members as the seat rules read them, oldest first and bounded by
        PROFILE_ROSTER_MAX. `name` is the address the member is known by, because a member carries
        no other name; `standing` is the pair the roster already states — admin or member, seated or
        not — in the words the member object row says them in."""
        async with self.transaction() as connection:
            snapshot = await Seats(self.workspace_id).snapshot(connection)
        return tuple(
            _Rostered(
                entry.id,
                entry.email,
                ", ".join(
                    (
                        PROFILE_ADMIN_STANDING if entry.admin else PROFILE_MEMBER_STANDING,
                        PROFILE_SEATED_STANDING if entry.seated else PROFILE_UNSEATED_STANDING,
                    )
                ),
            )
            for entry in snapshot.members[:PROFILE_ROSTER_MAX]
        )

    async def _facts(self) -> tuple[str, ...]:
        async with self.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(memory_item.c.body)
                    .where(member_servable())
                    .where(
                        memory_item.c.workspace_id == self.workspace_id,
                        memory_item.c.subject == SHARED_SUBJECT,
                        memory_item.c.item_class == FACT,
                        memory_item.c.superseded_by.is_(None),
                        memory_item.c.retired_at.is_(None),
                    )
                    .order_by(memory_item.c.created_at.desc(), memory_item.c.id.desc())
                    .limit(PROFILE_FACTS_MAX)
                )
            ).all()
        return tuple(row.body for row in rows)

    async def _write(
        self, model: ModelAccess, roster: tuple[_Rostered, ...], facts: tuple[str, ...]
    ) -> tuple[WrittenProfile, ...]:
        """The one bounded metered model pass, run before any write transaction is opened. A reply
        carrying no recorded call at all raises: it is not the same answer as "this workspace knows
        nothing about anybody", and a pass that cannot read its answer must settle nothing."""
        payload = {
            "members": [
                {"name": member.name, "email": member.name, "standing": member.standing}
                for member in roster
            ],
            "facts": [fact[:CONSOLIDATE_FACT_CHARS] for fact in facts],
        }
        request = ModelRequest(
            model=model.model,
            system=PROFILE_SYSTEM,
            messages=(Message(role="user", content=json.dumps(payload, separators=(",", ":"))),),
            max_tokens=PROFILE_MAX_TOKENS,
            conversation_cache_ttl="5m",
            tools=(
                ToolSchema(
                    name=PROFILE_TOOL,
                    description=PROFILE_TOOL_DESCRIPTION,
                    input_schema=WrittenPeople.model_json_schema(),
                ),
            ),
            tool_choice=PROFILE_TOOL,
            reasoning=PAGE_PASS_REASONING,
        )
        reply = await model.turn(request)
        blocks = () if isinstance(reply.content, str) else reply.content
        recorded = next(
            (
                block
                for block in blocks
                if isinstance(block, ToolUseBlock) and block.name == PROFILE_TOOL
            ),
            None,
        )
        if recorded is None:
            raise ValueError(f"the people pass recorded no {PROFILE_TOOL} call")
        entries = recorded.input.get("people")
        written: list[WrittenProfile] = []
        for entry in entries if isinstance(entries, list) else ():
            try:
                written.append(WrittenProfile.model_validate(entry))
            except ValidationError:
                continue
        return tuple(written)

    async def _store(self, entries: tuple[tuple[UUID, WrittenProfile], ...]) -> None:
        """The pass's one write: every entry it kept, each replacing the one that stood for that
        member. The key is (workspace, member), so the replacement is the primary key's own work
        rather than a rule this pass remembers, and one transaction is what keeps the People band
        from being read half-rewritten."""
        written_at = datetime.now(UTC)
        async with self.transaction() as connection:
            insert = (
                pg_insert(memory_profile)
                if connection.dialect.name == "postgresql"
                else sqlite_insert(memory_profile)
            )
            for member_id, entry in entries:
                statement = insert.values(
                    workspace_id=self.workspace_id,
                    member_id=member_id,
                    role=entry.role,
                    focus=entry.focus,
                    written_at=written_at,
                )
                await connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=[memory_profile.c.workspace_id, memory_profile.c.member_id],
                        set_={
                            "role": statement.excluded.role,
                            "focus": statement.excluded.focus,
                            "written_at": statement.excluded.written_at,
                        },
                    )
                )


@dataclass(frozen=True)
class _PageRow:
    index: int
    id: UUID
    body: str


@dataclass(frozen=True)
class _Band:
    memory_kind: MemoryKind
    heading: str
    summary: str
    rows: tuple[_PageRow, ...]


class RetiredRow(BaseModel):
    """One row the page pass judged the wiki reads better without — untrusted model output validated
    at this boundary before anything is stamped. `id` is the index the payload gave that row and
    never a stored id: an index the model half-copied resolves to nothing and costs one row, where a
    uuid it half-copied does the same at forty times the tokens, over a page carrying hundreds."""

    id: int = Field(ge=1, description="The id this row was sent with.")
    reason: str = Field(min_length=1, description="Why the page reads better without this row.")
    duplicate_of: int | None = Field(
        default=None,
        description=(
            "Where another row states this row's claim, the id of that row. Name a row this page "
            "sent and one you are not retiring: where the rows carrying a claim only point at each "
            "other, one of them stays. Leave this out where no row carries what this one said."
        ),
    )


class CuratedPage(BaseModel):
    """The arguments of the curation's `curate_page` call — the shape whose JSON schema is the tool
    contract the model records against, so the judgements arrive as arguments the provider decoded
    rather than as JSON this code reads out of prose. A page that reads well as it stands records
    nothing."""

    retire: tuple[RetiredRow, ...] = ()


@dataclass(frozen=True)
class AdmittedCuration:
    """What one page's curation is allowed to cost. `refused` carries why the whole page was left
    alone, and is empty where the retirements stand."""

    retiring: frozenset[int]
    refused: str


def admitted_curation(
    bands: tuple[tuple[int, ...], ...], retire: tuple[RetiredRow, ...]
) -> AdmittedCuration:
    """Which of a curation's judgements a page is willing to act on, over the row ids each band was
    sent with. Two questions, and both are asked because the pass runs unattended and nothing
    upstream writes a retired row again.

    An id naming no row this page sent buys nothing and is dropped. A row retired as another row's
    duplicate is retired on the promise that the other row keeps the claim, so the promise is
    checked: a row deferring to one this page never sent, or to one this same answer retires, stays
    instead. Duplicates pointing only at each other therefore leave the page one of themselves
    rather than none.

    A retirement naming nobody is dropped outright: there is one reason to retire a row and it is
    that another row states the claim, so an answer that cannot name that row has not made the case.

    What survives is bounded per band by MAX_BAND_RETIREMENT, because naming a keeper is a claim the
    model makes and not one this code can check — every row on a page can be asserted to duplicate
    row 1, and the page would collapse onto it. Past the bar the whole page's curation is refused: a
    judgement that wrong about one band is not one to trust about another."""
    sent = {index for band in bands for index in band}
    keepers = {entry.id: entry.duplicate_of for entry in retire if entry.duplicate_of is not None}
    going = {entry.id for entry in retire if entry.id in keepers} & sent
    for index in sorted(keepers):
        if keepers[index] not in sent - going:
            going.discard(index)
    for band in bands:
        costing = tuple(index for index in band if index in going)
        admitted = max(1, int(len(band) * MAX_BAND_RETIREMENT))
        if len(costing) > admitted:
            return AdmittedCuration(
                frozenset(),
                f"retiring {len(costing)} of {len(band)} rows in one band onto keepers of its "
                f"own choosing, past the {admitted} one pass admits",
            )
    return AdmittedCuration(frozenset(going), "")


@dataclass(frozen=True)
class PagePass:
    """Read a subject's whole wiki page at once on the deploy's own model, and retire the rows it
    reads better without. A periodic job fed only by its own interval.

    Retirement is all it does. It is the only reader that sees the page whole — extraction reads one
    source page and the section pass one band, so six rows written from six documents about one pull
    request are six claims to every writer upstream of here and one claim to this one — and no
    per-page extractor and no cosine threshold reaches that judgement, which is why this tier runs
    on `auto_model` rather than the background default. What it does not do is write: the section
    and overview passes own every paragraph on the page, and a second writer of one paragraph on a
    second schedule is a race and a second answer to who wrote what a member reads.

    It reads a page-derived row at its live revision and nothing else. Joining the page mirror
    fences out the two rows a member cannot see anyway — a fact whose page has moved on without
    replacing it, and one an agent wrote through `memory_update` — and both would be retired onto a
    keeper the wiki never draws, for good: `retired_at` is the one column `commit` never clears, so
    a member restating what this pass took would land back under the same fence. What a member wrote
    is theirs to correct, and this pass exists for the rows several source pages wrote about one
    claim.

    One metered pass per subject records the rows the page reads better without; each entry is
    validated on its own, so an entry the contract does not satisfy drops without taking the rest
    with it, and an id naming no row the payload sent is discarded rather than guessed at. The
    paragraph standing over each band travels with its rows because it says what the band already
    claims, which is what makes a row under it a restatement.

    A retirement is stamped in `retired_at`, the one column `commit` never clears: a page still
    synced re-commits the identical body on every derivation, so a judgement written anywhere the
    upsert reaches would be undone by the next tick. The stamp also clears the row's digest, so the
    per-minute index job withdraws the chunks it had published, and the next night's paragraphs are
    written from the rows that survived.

    The pass destroys what a member reads, unattended and nightly, so `admitted_curation` bounds
    what one answer can cost before anything is written: a row retired as another's duplicate keeps
    the page only if that other row survives, a row retired against nothing at all stays, and a band
    that would lose more than MAX_BAND_RETIREMENT of its rows to keepers one answer chose keeps
    every row it had. A subject refused is a subject skipped — the pass goes on to the next one,
    because a page the model reads badly is not a reason to leave every other page uncurated for
    good. Fail-soft: with no model wired nothing is written."""

    transaction: Transaction
    workspace_id: UUID
    model: ModelAccess | None = None

    async def run(self) -> None:
        """Curate every page long enough to be worth the deploy model, one metered pass each. The
        pass runs before the write transaction is opened, and one transaction per subject carries
        that subject's retirements together, so a member never reads a page half-curated."""
        if self.model is None:
            return
        for subject in await self._subjects():
            bands = await self._page(subject)
            if not bands:
                continue
            curated = await self._curate(self.model, bands)
            retiring = self._retiring(subject, bands, curated.retire)
            if retiring:
                await self._apply(retiring)

    async def _subjects(self) -> tuple[str, ...]:
        async with self.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(memory_item.c.subject)
                    .where(
                        memory_item.c.workspace_id == self.workspace_id,
                        memory_item.c.item_class == FACT,
                        memory_item.c.superseded_by.is_(None),
                        memory_item.c.retired_at.is_(None),
                    )
                    .group_by(memory_item.c.subject)
                    .having(sa.func.count() >= PAGE_PASS_MIN_ROWS)
                    .order_by(memory_item.c.subject)
                )
            ).all()
        return tuple(row.subject for row in rows)

    async def _page(self, subject: str) -> tuple[_Band, ...]:
        bands: list[_Band] = []
        index = 0
        async with self.transaction() as connection:
            for memory_kind, heading in section_headings(subject).items():
                rows = (
                    await connection.execute(
                        sa.select(memory_item.c.id, memory_item.c.body)
                        .join(mem_page, live_page_link())
                        .where(
                            memory_item.c.workspace_id == self.workspace_id,
                            memory_item.c.subject == subject,
                            memory_item.c.item_class == FACT,
                            memory_item.c.memory_kind == memory_kind,
                            memory_item.c.superseded_by.is_(None),
                            memory_item.c.retired_at.is_(None),
                        )
                        .order_by(memory_item.c.created_at.desc(), memory_item.c.id.desc())
                        .limit(PAGE_PASS_SECTION_ROWS)
                    )
                ).all()
                if not rows:
                    continue
                summary = (
                    await connection.execute(
                        sa.select(memory_item.c.body).where(
                            memory_item.c.workspace_id == self.workspace_id,
                            memory_item.c.subject == subject,
                            memory_item.c.item_class == SECTION,
                            memory_item.c.memory_kind == memory_kind,
                            memory_item.c.superseded_by.is_(None),
                        )
                    )
                ).scalar_one_or_none()
                bands.append(
                    _Band(
                        memory_kind=memory_kind,
                        heading=heading,
                        summary=summary or "",
                        rows=tuple(
                            _PageRow(index + offset, row.id, row.body)
                            for offset, row in enumerate(rows, start=1)
                        ),
                    )
                )
                index += len(rows)
        return tuple(bands)

    async def _curate(self, model: ModelAccess, bands: tuple[_Band, ...]) -> CuratedPage:
        payload = {
            "sections": [
                {
                    "section": band.heading,
                    "summary": band.summary[:MEMORY_BODY_MAX_CHARS],
                    "rows": [
                        {"id": row.index, "body": row.body[:PAGE_PASS_ROW_CHARS]}
                        for row in band.rows
                    ],
                }
                for band in bands
            ]
        }
        request = ModelRequest(
            model=model.model,
            system=PAGE_PASS_SYSTEM,
            messages=(Message(role="user", content=json.dumps(payload, separators=(",", ":"))),),
            max_tokens=PAGE_PASS_MAX_TOKENS,
            conversation_cache_ttl="5m",
            tools=(
                ToolSchema(
                    name=PAGE_PASS_TOOL,
                    description=PAGE_PASS_TOOL_DESCRIPTION,
                    input_schema=CuratedPage.model_json_schema(),
                ),
            ),
            tool_choice=PAGE_PASS_TOOL,
            reasoning=PAGE_PASS_REASONING,
        )
        reply = await model.turn(request)
        blocks = () if isinstance(reply.content, str) else reply.content
        recorded = next(
            (
                block
                for block in blocks
                if isinstance(block, ToolUseBlock) and block.name == PAGE_PASS_TOOL
            ),
            None,
        )
        if recorded is None:
            raise ValueError(f"page curation recorded no {PAGE_PASS_TOOL} call")
        asked = recorded.input.get("retire")
        retire: list[RetiredRow] = []
        for entry in asked if isinstance(asked, list) else ():
            try:
                retire.append(RetiredRow.model_validate(entry))
            except ValidationError:
                continue
        return CuratedPage(retire=tuple(retire))

    def _retiring(
        self, subject: str, bands: tuple[_Band, ...], retire: tuple[RetiredRow, ...]
    ) -> frozenset[UUID]:
        admitted = admitted_curation(
            tuple(tuple(row.index for row in band.rows) for band in bands), retire
        )
        if admitted.refused:
            warn("memory.page_pass.refused", subject=subject, detail=admitted.refused)
            return frozenset()
        return frozenset(
            row.id for band in bands for row in band.rows if row.index in admitted.retiring
        )

    async def _apply(self, retiring: frozenset[UUID]) -> None:
        async with self.transaction() as connection:
            await connection.execute(
                sa.update(memory_item)
                .values(
                    retired_at=sa.func.now(),
                    embedding_digest=None,
                    embedding_claimed_at=None,
                    updated_at=sa.func.now(),
                )
                .where(
                    memory_item.c.workspace_id == self.workspace_id,
                    memory_item.c.id.in_(sorted(retiring)),
                    memory_item.c.superseded_by.is_(None),
                    memory_item.c.retired_at.is_(None),
                )
            )
