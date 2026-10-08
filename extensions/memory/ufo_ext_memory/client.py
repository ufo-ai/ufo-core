"""The memory service's client: the `/v1/memory` verbs the assistant calls, as the bound workspace.

Requests forbid fields the contract does not name; answers ignore fields it adds. `write` and
`search` send only the fields their caller set, so an optional key left alone is absent and one set
to null is sent as null. Every list a call sends is held to the verb's bound before it is sent."""

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ufo.sdk.cloud import CloudApi, CloudRefused

ItemClass = Literal["fact", "episodic", "semantic", "section", "overview"]
MemoryKind = Literal["fact", "preference", "decision", "event", "task"]
PassName = Literal["clusters", "sections", "page"]

SEARCH_LIMIT_MAX = 50
QUERIES_MAX = 3
QUERY_MAX_CHARS = 1000
SUBJECTS_MAX = 64
LIST_LIMIT_MAX = 200
PAGES_BATCH = 200
EXTRACT_PAGES_MAX = 50
EXTRACT_FACTS_MAX = 200
IMPORT_BATCH = 500
NOT_FOUND_STATUS = 404
MEMORIES_PATH = "/v1/memory/memories"
QUERY_AT_A_WORD = re.compile(rf"(.{{0,{QUERY_MAX_CHARS}}})\s", re.DOTALL)

SENT = ConfigDict(extra="forbid")
READ = ConfigDict(extra="ignore")
SENT_PASS = ConfigDict(extra="forbid", serialize_by_alias=True, validate_by_name=True)
READ_PASS = ConfigDict(extra="ignore", serialize_by_alias=True, validate_by_name=True)


class PageSource(BaseModel):
    """A page a memory was drawn from, named only to a reader who may read it."""

    model_config = READ
    kind: Literal["page"]
    page_id: UUID
    title: str
    provider: str


class ConversationSource(BaseModel):
    """A conversation a memory was drawn from."""

    model_config = READ
    kind: Literal["conversation"]
    conversation_id: UUID


class Memory(BaseModel):
    """One memory as the service answers it: live, superseded, overtaken or retired."""

    model_config = READ
    id: UUID
    subject: str
    kind: MemoryKind
    item_class: ItemClass
    body: str
    confidence: int
    source_ref: str | None
    as_of: AwareDatetime | None
    created_at: AwareDatetime
    valid_at: AwareDatetime
    invalid_at: AwareDatetime | None
    invalidated_by: UUID | None
    superseded_by: UUID | None
    retired_at: AwareDatetime | None
    conversation_id: UUID | None
    backend: str
    labels: dict[str, str]
    sources: list[Annotated[PageSource | ConversationSource, Field(discriminator="kind")]]


class Reach(BaseModel):
    """The reader a read is for: an agent, and the member it acts for or None."""

    model_config = SENT
    agent_id: UUID
    member_id: UUID | None


class Write(BaseModel):
    """A memory to write."""

    model_config = SENT
    subject: str
    body: str
    kind: MemoryKind
    item_class: Literal["fact", "episodic", "overview"]
    confidence: int
    source_ref: str | None = None
    deprecates: list[str] = []
    as_of: AwareDatetime | None = None
    conversation_id: UUID | None = None


class Search(BaseModel):
    """A search over `subjects` for `reach`, one or more queries merged into one ranking."""

    model_config = SENT
    queries: list[str]
    subjects: list[str]
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    limit: int
    reach: Reach


class Matches(BaseModel):
    """A search's matches in rank order."""

    model_config = READ
    matches: list[Memory]


class MemoryPage(BaseModel):
    """One page of a list, newest first, and the positions of the pages beside it."""

    model_config = READ
    items: list[Memory]
    next_cursor: str | None
    prev_cursor: str | None


class Correct(BaseModel):
    """A correction of a memory: its new body, under `subject`."""

    model_config = SENT
    body: str
    subject: str
    conversation_id: UUID | None = None


class Deleted(BaseModel):
    """A deleted memory."""

    model_config = READ
    id: UUID
    deleted_at: AwareDatetime


class SubjectDeleted(BaseModel):
    """How many memories of a subject a delete removed."""

    model_config = READ
    subject: str
    deleted: int


class IngestSettings(BaseModel):
    """What the service derives memories from."""

    model_config = READ
    sources: bool


class Settings(BaseModel):
    """The workspace's memory settings."""

    model_config = READ
    default_backend: str
    ingest: IngestSettings
    retention_days: dict[MemoryKind, int] | None
    consolidation: bool


class Pending(BaseModel):
    """The memories the service has yet to index."""

    model_config = READ
    pending: int


class PageMirror(BaseModel):
    """A page's state as the service's page mirror holds it."""

    model_config = SENT
    page_id: UUID
    subject: str
    revision: int
    indexed: bool
    tombstone: bool
    title: str
    provider: str


class Pages(BaseModel):
    """A batch of page states to mirror."""

    model_config = SENT
    pages: list[PageMirror]


class Settled(BaseModel):
    """How many mirrored page states the service settled."""

    model_config = READ
    settled: int


class ExtractedFact(BaseModel):
    """A fact a model call drew from a page."""

    model_config = SENT
    body: str
    kind: MemoryKind
    confidence: int


class ExtractPage(BaseModel):
    """The facts drawn from one revision of a page."""

    model_config = SENT
    page_id: UUID
    source_id: UUID
    connection_id: UUID
    subject: str
    revision: int
    as_of: AwareDatetime
    facts: list[ExtractedFact]


class Extract(BaseModel):
    """The pages one extraction call drew facts from."""

    model_config = SENT
    pages: list[ExtractPage]


class ExtractOutcome(BaseModel):
    """Whether a page's facts were settled, or its revision is no longer the live one."""

    model_config = READ
    page_id: UUID
    outcome: Literal["settled", "stale"]


class Extracted(BaseModel):
    """The outcome of each extracted page."""

    model_config = READ
    pages: list[ExtractOutcome]


class Consolidate(BaseModel):
    """The consolidation pass whose work to read."""

    model_config = SENT_PASS
    pass_: PassName = Field(alias="pass")


class ClusterFact(BaseModel):
    """A fact of a cluster of near duplicates, and a donor of its summary."""

    model_config = READ
    id: UUID
    body: str
    confidence: int


class Cluster(BaseModel):
    """Near-duplicate facts of one subject."""

    model_config = READ
    subject: str
    facts: list[ClusterFact]


class ClusterWork(BaseModel):
    """The clusters pass's work."""

    model_config = READ_PASS
    pass_: Literal["clusters"] = Field(alias="pass")
    clusters: list[Cluster]


class SectionFact(BaseModel):
    """A fact of a section band."""

    model_config = READ
    body: str
    confidence: int


class SectionBand(BaseModel):
    """The facts of one subject and kind a section paragraph stands for."""

    model_config = READ
    subject: str
    kind: MemoryKind
    facts: list[SectionFact]


class SectionWork(BaseModel):
    """The sections pass's work."""

    model_config = READ_PASS
    pass_: Literal["sections"] = Field(alias="pass")
    bands: list[SectionBand]


class PageRow(BaseModel):
    """A memory of a page band."""

    model_config = READ
    id: UUID
    body: str


class PageBand(BaseModel):
    """The memories of one kind on a subject's page, under its standing paragraph."""

    model_config = READ
    kind: MemoryKind
    summary: str
    rows: list[PageRow]


class SubjectPage(BaseModel):
    """One subject's page: its bands."""

    model_config = READ
    subject: str
    bands: list[PageBand]


class PageWork(BaseModel):
    """The page pass's work."""

    model_config = READ_PASS
    pass_: Literal["page"] = Field(alias="pass")
    pages: list[SubjectPage]


class ClusterSummary(BaseModel):
    """The summary written over a cluster's donors."""

    model_config = SENT
    subject: str
    body: str
    donors: list[ClusterFact]


class ClusterResults(BaseModel):
    """The clusters pass's summaries."""

    model_config = SENT_PASS
    pass_: Literal["clusters"] = Field(default="clusters", alias="pass")
    summaries: list[ClusterSummary]


class Paragraph(BaseModel):
    """A section paragraph to write."""

    model_config = SENT
    subject: str
    kind: MemoryKind
    body: str
    confidence: int


class SectionResults(BaseModel):
    """The sections pass's paragraphs."""

    model_config = SENT_PASS
    pass_: Literal["sections"] = Field(default="sections", alias="pass")
    paragraphs: list[Paragraph]


class Retirement(BaseModel):
    """A memory the page pass retires, and the memory it duplicates when it does."""

    model_config = SENT
    id: UUID
    duplicate_of: UUID | None


class PageResults(BaseModel):
    """The page pass's verdict on one subject: its bands in order and the memories it retires."""

    model_config = SENT_PASS
    pass_: Literal["page"] = Field(default="page", alias="pass")
    subject: str
    bands: list[list[UUID]]
    retire: list[Retirement]


class ConsolidateOutcome(BaseModel):
    """What a pass's results wrote and retired, the summaries skipped because their donors
    changed, and why the page pass's results were refused."""

    model_config = READ
    written: list[UUID]
    conflicts: list[int]
    retired: int
    refused: str | None


class ImportedPage(BaseModel):
    """The page an imported memory was drawn from."""

    model_config = SENT
    page_id: UUID
    revision: int
    source_id: UUID
    connection_id: UUID


class ImportRecord(BaseModel):
    """A memory moved into the service with its id and pointers unchanged."""

    model_config = SENT
    id: UUID
    subject: str
    body: str
    body_digest: str | None
    item_class: ItemClass
    kind: MemoryKind
    confidence: int
    source_ref: str | None
    page: ImportedPage | None
    conversation_id: UUID | None
    as_of: AwareDatetime | None
    superseded_by: UUID | None
    overtaken_by: UUID | None
    deprecates: list[str] | None
    swept_at: AwareDatetime | None
    retired_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class Import(BaseModel):
    """A batch of memories to import."""

    model_config = SENT
    records: list[ImportRecord]


class Merged(BaseModel):
    """An imported memory the service folded into one it already held."""

    model_config = READ
    id: UUID
    into: UUID


class Unresolved(BaseModel):
    """An imported memory whose pointer names a memory the service does not hold yet."""

    model_config = READ
    id: UUID
    pointer: Literal["superseded_by", "overtaken_by"]
    target: UUID


class Imported(BaseModel):
    """What an import batch did with each record."""

    model_config = READ
    imported: int
    unchanged: int
    merged: list[Merged]
    unresolved: list[Unresolved]


WORK: dict[PassName, type[ClusterWork | SectionWork | PageWork]] = {
    "clusters": ClusterWork,
    "sections": SectionWork,
    "page": PageWork,
}


@dataclass(frozen=True)
class MemoryApi:
    """The memory service's verbs the assistant calls, over the cloud API."""

    cloud: CloudApi

    async def write(self, write: Write) -> Memory:
        """Write a memory. An admission refusal raises `CloudRefused` carrying the service's
        sentence."""
        return await self.cloud.send("POST", MEMORIES_PATH, body=write, partial=True, answer=Memory)

    async def search(self, search: Search) -> tuple[Memory, ...]:
        """The matches of `search` in rank order. Each query is cut to `QUERY_MAX_CHARS` at a word
        and a query left empty is dropped; with none left nothing is sent."""
        if search.limit > SEARCH_LIMIT_MAX:
            raise ValueError(f"A memory search answers at most {SEARCH_LIMIT_MAX} matches.")
        if len(search.queries) > QUERIES_MAX:
            raise ValueError(f"A memory search carries at most {QUERIES_MAX} queries.")
        if len(search.subjects) > SUBJECTS_MAX:
            raise ValueError(f"A memory search names at most {SUBJECTS_MAX} subjects.")
        queries = [cut for query in search.queries if (cut := _query_at_a_word(query))]
        if not queries:
            return ()
        found = await self.cloud.send(
            "POST",
            "/v1/memory/search",
            body=search.model_copy(update={"queries": queries}),
            partial=True,
            answer=Matches,
        )
        return tuple(found.matches)

    async def list(
        self,
        *,
        subjects: Collection[str] = (),
        item_classes: Collection[str] = (),
        kinds: Collection[str] = (),
        cursor: str | None = None,
        limit: int = LIST_LIMIT_MAX,
        reach: Reach | None = None,
    ) -> MemoryPage:
        """One page of live memories newest first, from `cursor`; naming no subject lists every
        subject the token reaches."""
        if limit > LIST_LIMIT_MAX:
            raise ValueError(f"A memory list answers at most {LIST_LIMIT_MAX} memories a page.")
        cursors = () if cursor is None else (("cursor", cursor),)
        return await self.cloud.send(
            "GET",
            MEMORIES_PATH,
            params=(
                *_subjects(subjects),
                *(("kind", kind) for kind in sorted(kinds)),
                *(("item_class", item_class) for item_class in sorted(item_classes)),
                *cursors,
                ("limit", str(limit)),
                *_reach(reach),
            ),
            answer=MemoryPage,
        )

    async def newest(
        self,
        *,
        subjects: Collection[str] = (),
        item_classes: Collection[str] = (),
        kinds: Collection[str] = (),
        reach: Reach | None = None,
        total: int,
    ) -> tuple[Memory, ...]:
        """The newest `total` live memories, or every one when fewer, a page at a time."""
        memories: list[Memory] = []
        cursor: str | None = None
        while len(memories) < total:
            page = await self.list(
                subjects=subjects,
                item_classes=item_classes,
                kinds=kinds,
                cursor=cursor,
                limit=min(LIST_LIMIT_MAX, total - len(memories)),
                reach=reach,
            )
            memories.extend(page.items[: total - len(memories)])
            if page.next_cursor is None or not page.items:
                break
            cursor = page.next_cursor
        return tuple(memories)

    async def get(
        self, memory_id: UUID, *, subjects: Collection[str], reach: Reach | None
    ) -> Memory | None:
        """The memory `memory_id` within `subjects` as `reach` may read it, or None when the
        service holds none visible."""
        try:
            return await self.cloud.send(
                "GET",
                f"{MEMORIES_PATH}/{memory_id}",
                params=(*_subjects(subjects), *_reach(reach)),
                answer=Memory,
            )
        except CloudRefused as refusal:
            if refusal.status == NOT_FOUND_STATUS:
                return None
            raise

    async def correct(self, memory_id: UUID, correct: Correct) -> Memory:
        """Correct the memory `memory_id`; the answer is the correction."""
        return await self.cloud.send(
            "PATCH", f"{MEMORIES_PATH}/{memory_id}", body=correct, answer=Memory
        )

    async def delete(self, memory_id: UUID) -> Deleted:
        """Delete the memory `memory_id` and the memories it superseded."""
        return await self.cloud.send("DELETE", f"{MEMORIES_PATH}/{memory_id}", answer=Deleted)

    async def delete_subject(
        self, subject: str, *, source_ref_prefix: str | None = None
    ) -> SubjectDeleted:
        """Delete the memories of `subject`, or those whose `source_ref` starts with
        `source_ref_prefix`."""
        prefixes = () if source_ref_prefix is None else (("source_ref_prefix", source_ref_prefix),)
        return await self.cloud.send(
            "DELETE",
            MEMORIES_PATH,
            params=(("subject", subject), *prefixes),
            answer=SubjectDeleted,
        )

    async def settings(self) -> Settings:
        """The workspace's memory settings."""
        return await self.cloud.send("GET", "/v1/memory/settings", answer=Settings)

    async def pending(self) -> int:
        """How many memories the service has yet to index."""
        index = await self.cloud.send("GET", "/v1/memory/index", answer=Pending)
        return index.pending

    async def pages(self, mirrors: Sequence[PageMirror]) -> int:
        """Mirror `mirrors` into the service's page mirror, `PAGES_BATCH` a call; answers how many
        it settled."""
        settled = 0
        for start in range(0, len(mirrors), PAGES_BATCH):
            batch = Pages(pages=list(mirrors[start : start + PAGES_BATCH]))
            answer = await self.cloud.send("POST", "/v1/memory/pages", body=batch, answer=Settled)
            settled += answer.settled
        return settled

    async def extract(self, pages: Sequence[ExtractPage]) -> tuple[ExtractOutcome, ...]:
        """Post the facts one extraction call drew from `pages`, in one call."""
        if not 1 <= len(pages) <= EXTRACT_PAGES_MAX:
            raise ValueError(f"An extraction posts 1 to {EXTRACT_PAGES_MAX} pages.")
        if any(not 1 <= len(page.facts) <= EXTRACT_FACTS_MAX for page in pages):
            raise ValueError(f"An extracted page carries 1 to {EXTRACT_FACTS_MAX} facts.")
        extracted = await self.cloud.send(
            "POST", "/v1/memory/extract", body=Extract(pages=list(pages)), answer=Extracted
        )
        return tuple(extracted.pages)

    async def consolidate(self, name: PassName) -> ClusterWork | SectionWork | PageWork:
        """The work of the consolidation pass `name`; every list is empty while consolidation is
        off."""
        return await self.cloud.send(
            "POST",
            "/v1/memory/consolidate",
            body=Consolidate.model_validate({"pass": name}),
            answer=WORK[name],
        )

    async def consolidate_results(
        self, results: ClusterResults | SectionResults | PageResults
    ) -> ConsolidateOutcome:
        """Post a pass's results. While consolidation is off it raises `CloudRefused` with the
        code `conflict`."""
        return await self.cloud.send(
            "POST", "/v1/memory/consolidate/results", body=results, answer=ConsolidateOutcome
        )

    async def import_records(self, records: Sequence[ImportRecord]) -> Imported:
        """Import `records`, at most `IMPORT_BATCH`, in one call."""
        if not 1 <= len(records) <= IMPORT_BATCH:
            raise ValueError(f"A memory import posts 1 to {IMPORT_BATCH} records.")
        return await self.cloud.send(
            "POST", "/v1/memory/import", body=Import(records=list(records)), answer=Imported
        )


def _query_at_a_word(query: str) -> str:
    query = query.strip()
    if len(query) <= QUERY_MAX_CHARS:
        return query
    match = QUERY_AT_A_WORD.match(query)
    return "" if match is None else match.group(1).rstrip()


def _subjects(subjects: Collection[str]) -> tuple[tuple[str, str], ...]:
    if len(subjects) > SUBJECTS_MAX:
        raise ValueError(f"A memory read names at most {SUBJECTS_MAX} subjects.")
    return tuple(("subject", subject) for subject in sorted(subjects))


def _reach(reach: Reach | None) -> tuple[tuple[str, str], ...]:
    if reach is None:
        return ()
    members = () if reach.member_id is None else (("reach.member_id", str(reach.member_id)),)
    return (("reach.agent_id", str(reach.agent_id)), *members)
