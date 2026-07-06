"""The knowledge-graph domain: the two tables the extension owns, the deterministic page extractor
that rides the core PageFeed, and the k-hop traversal the query surface reads through.

The graph is a derived index over the one page substrate, produced beside memory's embeddings on its
own cursor. `GraphExtractor` replays each changed source page off `PageFeed`, parses entity
references out of its markdown with zero model calls, resolves each reference to a `graph_entity`
node (creating a stub the first time a name is referenced, filling that stub when a page later
defines it), and writes typed `graph_edge` rows from the page's anchor node to each referenced node
— every edge stamped with the `page.digest` it was derived from, so a page is re-extracted only when
its content changes and a tombstoned page soft-deletes the edges it sourced. `GraphStore` is the
read side: it resolves a name to its node(s) and expands a bounded neighbourhood over the edges,
following relationships rather than scoring them.

Two extraction tiers are specified. Tier A — this module — is the deterministic backbone: markdown
wikilinks (`[[Name]]`), typed-link syntax (`[[works_at::Acme]]`), `@mentions`, `#tags`, and bare
URLs all resolve to nodes and typed edges with no model call, and the typed-link form produces the
full bounded edge vocabulary directly. Tier B — a model pass that reads typed relations out of free
prose — is not wired: an extension job reaches a model only by admitting a whole turn through
`ExtensionContext.invoker`, which returns a turn id rather than a structured extraction, and no
metered model client is threaded onto a job's context. Driving a per-page structured extraction from
a background job therefore awaits the extension-job model-access decision (the same seam the page
condenser waits on); bringing an unmetered provider client into the job is the documented egress
anti-pattern and is not done. Until that decision lands the deterministic backbone stands alone, and
because typed-link syntax already exercises every edge type, the graph substrate, the bounded
vocabulary, and traversal are complete without it."""

import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Literal, cast
from uuid import UUID, uuid5

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from selfhost.sdk.context import ScopedStore
from selfhost.sdk.sources import SHARED_SUBJECT, PageChange, PageFeed, member_subject

EdgeType = Literal[
    "mentions",
    "derived_from",
    "works_at",
    "founded",
    "invested_in",
    "advises",
    "attended",
    "reports_to",
]
EDGE_TYPES: frozenset[str] = frozenset(
    {
        "mentions",
        "derived_from",
        "works_at",
        "founded",
        "invested_in",
        "advises",
        "attended",
        "reports_to",
    }
)
MENTIONS: EdgeType = "mentions"

EntityType = Literal["person", "company", "organization", "topic"]
PERSON: EntityType = "person"
TOPIC: EntityType = "topic"

EDGE_TARGET_TYPE: dict[str, EntityType] = {
    "mentions": "topic",
    "derived_from": "topic",
    "works_at": "company",
    "founded": "company",
    "invested_in": "company",
    "advises": "company",
    "attended": "organization",
    "reports_to": "person",
}

GRAPH_ENTITY_NAMESPACE = UUID("1d9d0b1e-3a2c-5e4f-8a7b-6c5d4e3f2a1b")
GRAPH_EDGE_NAMESPACE = UUID("2e8c1a2b-4b3d-5f6e-9b8a-7d6c5e4f3a2b")
DETERMINISTIC_CONFIDENCE = 1.0

PAGE_EXTRACT_BATCH = 50
PAGE_CURSOR_KEY = "graph_extract_cursor"
PAGE_ANCHOR_PREFIX = "page:"

MAX_HOPS = 3
DEFAULT_HOPS = 2
RESULT_EDGE_CAP = 100
FRONTIER_CAP = 50
CANDIDATE_MAX = 500

TYPED_SEP = "::"
ALIAS_SEP = "|"
URL_TRAILING = ".,;:!?)"
H1_RE = re.compile(r"^#[ \t]+(.+?)[ \t]*$", re.MULTILINE)
WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")
MENTION_RE = re.compile(r"(?<!\w)@([A-Za-z0-9_][\w.\-]*)")
TAG_RE = re.compile(r"(?<!\w)#([A-Za-z0-9_][\w\-]*)")
URL_RE = re.compile(r"https?://[^\s<>\])]+")

Transaction = Callable[[], AbstractAsyncContextManager[AsyncConnection]]

_metadata = sa.MetaData()
graph_entity = sa.Table(
    "graph_entity",
    _metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("subject", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("normalized_name", sa.Text, nullable=False),
    sa.Column("entity_type", sa.Text, nullable=False),
    sa.Column("is_stub", sa.Boolean, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)

graph_edge = sa.Table(
    "graph_edge",
    _metadata,
    sa.Column("id", sa.Uuid, primary_key=True),
    sa.Column("workspace_id", sa.Uuid, nullable=False),
    sa.Column("subject", sa.Text, nullable=False),
    sa.Column("edge_type", sa.Text, nullable=False),
    sa.Column("from_entity", sa.Uuid, nullable=False),
    sa.Column("to_entity", sa.Uuid, nullable=False),
    sa.Column("source_page_id", sa.Uuid, nullable=False),
    sa.Column("confidence", sa.Float, nullable=False),
    sa.Column("extracted_digest", sa.Text, nullable=False),
    sa.Column("tombstone", sa.Boolean, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
)


class UnknownEdgeType(ValueError):
    """An edge whose type is outside the bounded vocabulary. Extraction output is validated against
    the closed `EdgeType` set and an unknown type is rejected here rather than persisted as a silent
    no-op — surfaced to the model as a recoverable tool error when it reaches the query filter."""


def to_edge_type(raw: str) -> EdgeType:
    """Validate a candidate edge type against the bounded vocabulary, returning it narrowed or
    raising `UnknownEdgeType`. The one gate every persisted or queried edge type passes through."""
    if raw not in EDGE_TYPES:
        raise UnknownEdgeType(raw)
    return cast(EdgeType, raw)


def normalize_name(name: str) -> str:
    """The resolution key for a reference: whitespace collapsed and lower-cased, so `Sam  Altman`
    and `sam altman` resolve to one node. The display `name` keeps the reference's own casing."""
    return re.sub(r"\s+", " ", name).strip().lower()


def graph_subjects(member_id: UUID | None) -> frozenset[str]:
    """The subjects a query reads under: the member's own space plus the shared space, or shared
    alone when the conversation has no linked member — mirroring memory's subject model."""
    if member_id is None:
        return frozenset({SHARED_SUBJECT})
    return frozenset({member_subject(member_id), SHARED_SUBJECT})


@dataclass(frozen=True)
class Ref:
    """One resolved reference parsed out of a page: the typed relation to write, the referenced
    entity's display name, and the node type the reference resolves to."""

    edge_type: str
    name: str
    entity_type: str


@dataclass(frozen=True)
class ParsedPage:
    """A page's deterministic parse: its anchor title (the first H1, or None) and the deduped
    references its body carries."""

    title: str | None
    refs: tuple[Ref, ...]


@dataclass(frozen=True)
class EntityNode:
    id: UUID
    name: str
    entity_type: str
    is_stub: bool


@dataclass(frozen=True)
class TraversedEdge:
    edge_type: str
    from_entity: UUID
    to_entity: UUID
    source_page_id: UUID
    confidence: float


@dataclass(frozen=True)
class Subgraph:
    nodes: tuple[EntityNode, ...]
    edges: tuple[TraversedEdge, ...]


def parse_page(body: str) -> ParsedPage:
    """Extract the anchor title and every entity reference from a page's markdown, with zero model
    calls. Wikilinks come first: `[[type::Target]]` whose type is in the vocabulary is a typed edge
    to the mapped node type; any other `[[...]]` (including `[[Target|display]]`) is a `mentions`
    edge to a topic. The remaining text — wikilink spans removed so their contents are not
    double-counted — yields `@mentions` (people), `#tags` (topics), and bare URLs (topics), all
    `mentions` edges. References are deduped on `(edge_type, entity_type, normalized name)`."""
    title_match = H1_RE.search(body)
    title = title_match.group(1).strip() if title_match else None
    refs: list[Ref] = []
    seen: set[tuple[str, str, str]] = set()

    def record(edge_type: str, name: str, entity_type: str) -> None:
        cleaned = name.strip()
        if not cleaned:
            return
        key = (edge_type, entity_type, normalize_name(cleaned))
        if key not in seen:
            seen.add(key)
            refs.append(Ref(edge_type=edge_type, name=cleaned, entity_type=entity_type))

    for match in WIKILINK_RE.finditer(body):
        inner = match.group(1).strip()
        prefix, _, target = inner.partition(TYPED_SEP)
        edge_type = prefix.strip().lower()
        if target and edge_type in EDGE_TYPES:
            record(edge_type, target, EDGE_TARGET_TYPE[edge_type])
        else:
            record(MENTIONS, inner.partition(ALIAS_SEP)[0], TOPIC)
    scanned = WIKILINK_RE.sub(" ", body)
    for match in MENTION_RE.finditer(scanned):
        record(MENTIONS, match.group(1), PERSON)
    for match in TAG_RE.finditer(scanned):
        record(MENTIONS, match.group(1), TOPIC)
    for match in URL_RE.finditer(scanned):
        record(MENTIONS, match.group(0).rstrip(URL_TRAILING), TOPIC)
    return ParsedPage(title=title, refs=tuple(refs))


def render_subgraph(subgraph: Subgraph) -> tuple[str, ...]:
    """One line per traversed edge, each carrying its source page as a citation and marking a target
    that is still a stub — the shared rendering the query tool and the inbound hook both format."""
    nodes = {node.id: node for node in subgraph.nodes}
    lines: list[str] = []
    for edge in subgraph.edges:
        source = nodes.get(edge.from_entity)
        target = nodes.get(edge.to_entity)
        if source is None or target is None:
            continue
        stub = " (stub)" if target.is_stub else ""
        lines.append(
            f"{source.name} -{edge.edge_type}-> {target.name} "
            f"({target.entity_type}){stub} [page {edge.source_page_id}]"
        )
    return tuple(lines)


@dataclass(frozen=True)
class GraphExtractor:
    """The derivation job: replay source-page changes off the core `PageFeed` and materialize each
    into graph nodes and typed edges, off the write path. A single-owner `(changed_at, id)` cursor
    lives in the extension's ScopedStore, independent of the memory indexer's — it only advances, so
    a page re-appears only when its `updated_at` bumps and the job can never fire on the graph rows
    it wrote. A tombstoned change soft-deletes the edges the page sourced; every other change,
    unless its digest was already extracted, upserts the page's anchor node, resolves each reference
    to a node (stub-on-reference), writes the typed edges, and prunes edges from a prior digest.
    """

    pages: PageFeed
    transaction: Transaction
    cursor_store: ScopedStore
    workspace_id: UUID

    async def run(self) -> None:
        stored = await self.cursor_store.get(PAGE_CURSOR_KEY)
        cursor = stored if isinstance(stored, str) else None
        while True:
            batch = await self.pages.pages_changed_since(cursor, PAGE_EXTRACT_BATCH)
            if not batch.changes:
                return
            for change in batch.changes:
                await self._apply(change)
            cursor = batch.next_cursor
            await self.cursor_store.put(PAGE_CURSOR_KEY, cursor)
            if len(batch.changes) < PAGE_EXTRACT_BATCH:
                return

    async def _apply(self, change: PageChange) -> None:
        if change.tombstone:
            async with self.transaction() as connection:
                await connection.execute(
                    sa.update(graph_edge)
                    .values(tombstone=True, updated_at=sa.func.now())
                    .where(
                        graph_edge.c.workspace_id == self.workspace_id,
                        graph_edge.c.source_page_id == change.page_id,
                    )
                )
            return
        if await self._already_extracted(change):
            return
        await self._materialize(change, parse_page(change.body))

    async def _already_extracted(self, change: PageChange) -> bool:
        async with self.transaction() as connection:
            row = (
                await connection.execute(
                    sa.select(graph_edge.c.id)
                    .where(
                        graph_edge.c.workspace_id == self.workspace_id,
                        graph_edge.c.source_page_id == change.page_id,
                        graph_edge.c.extracted_digest == change.digest,
                        graph_edge.c.tombstone.is_(False),
                    )
                    .limit(1)
                )
            ).one_or_none()
        return row is not None

    async def _materialize(self, change: PageChange, parsed: ParsedPage) -> None:
        title = parsed.title or f"{PAGE_ANCHOR_PREFIX}{change.page_id}"
        async with self.transaction() as connection:
            anchor = await self._upsert_entity(connection, change.subject, title, TOPIC, fill=True)
            for ref in parsed.refs:
                target = await self._upsert_entity(
                    connection, change.subject, ref.name, ref.entity_type, fill=False
                )
                await self._record_edge(connection, change, anchor, target, ref.edge_type)
            await connection.execute(
                sa.delete(graph_edge).where(
                    graph_edge.c.workspace_id == self.workspace_id,
                    graph_edge.c.source_page_id == change.page_id,
                    graph_edge.c.extracted_digest != change.digest,
                )
            )

    async def _upsert_entity(
        self, connection: AsyncConnection, subject: str, name: str, entity_type: str, fill: bool
    ) -> UUID:
        """Resolve a reference to its node, content-addressed on `(workspace, subject, entity_type,
        normalized name)`. An anchor (`fill=True`, a page defining the node) fills a prior stub and
        refreshes its display name; a reference (`fill=False`) creates a stub only when the node is
        new and never downgrades a filled node back to a stub."""
        normalized = normalize_name(name)
        entity_id = uuid5(
            GRAPH_ENTITY_NAMESPACE,
            "\x00".join((str(self.workspace_id), subject, entity_type, normalized)),
        )
        insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
        statement = insert(graph_entity).values(
            id=entity_id,
            workspace_id=self.workspace_id,
            subject=subject,
            name=name,
            normalized_name=normalized,
            entity_type=entity_type,
            is_stub=not fill,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
        if fill:
            await connection.execute(
                statement.on_conflict_do_update(
                    index_elements=[graph_entity.c.id],
                    set_={
                        graph_entity.c.name: statement.excluded.name,
                        graph_entity.c.is_stub: False,
                        graph_entity.c.updated_at: sa.func.now(),
                    },
                )
            )
        else:
            await connection.execute(
                statement.on_conflict_do_nothing(index_elements=[graph_entity.c.id])
            )
        return entity_id

    async def _record_edge(
        self,
        connection: AsyncConnection,
        change: PageChange,
        from_entity: UUID,
        to_entity: UUID,
        raw_edge_type: str,
    ) -> None:
        edge_type = to_edge_type(raw_edge_type)
        edge_id = uuid5(
            GRAPH_EDGE_NAMESPACE,
            "\x00".join(
                (
                    str(self.workspace_id),
                    edge_type,
                    str(from_entity),
                    str(to_entity),
                    str(change.page_id),
                )
            ),
        )
        insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
        statement = insert(graph_edge).values(
            id=edge_id,
            workspace_id=self.workspace_id,
            subject=change.subject,
            edge_type=edge_type,
            from_entity=from_entity,
            to_entity=to_entity,
            source_page_id=change.page_id,
            confidence=DETERMINISTIC_CONFIDENCE,
            extracted_digest=change.digest,
            tombstone=False,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
        await connection.execute(
            statement.on_conflict_do_update(
                index_elements=[graph_edge.c.id],
                set_={
                    graph_edge.c.extracted_digest: change.digest,
                    graph_edge.c.confidence: DETERMINISTIC_CONFIDENCE,
                    graph_edge.c.tombstone: False,
                    graph_edge.c.updated_at: sa.func.now(),
                },
            )
        )


@dataclass(frozen=True)
class GraphStore:
    """The read side over the extension's scoped handle: resolve a name to its node(s) and expand a
    bounded neighbourhood over the typed edges. Reads only the extension's own tables, scoped to its
    workspace and the caller's subjects; tombstoned edges are never followed."""

    transaction: Transaction
    workspace_id: UUID

    async def traverse(
        self, query: str, subjects: frozenset[str], hops: int, edge_types: frozenset[str]
    ) -> Subgraph:
        """The graph_search query: resolve `query` to its node(s) by normalized name, then expand up
        to `hops` outward — optionally restricted to `edge_types` — bounded by a frontier and result
        cap. Returns the visited nodes and the edges between them, each edge citing its source page.
        """
        seeds = await self._resolve(query, subjects)
        return await self._expand(seeds, subjects, hops, edge_types)

    async def context_for(self, text: str, subjects: frozenset[str], hops: int) -> Subgraph:
        """The inbound-hook query: seed from every known entity whose name appears in `text`, then
        expand the same bounded neighbourhood — the subgraph relevant to the turn."""
        seeds = await self._seed_from_text(text, subjects)
        return await self._expand(seeds, subjects, hops, frozenset())

    async def _resolve(self, query: str, subjects: frozenset[str]) -> frozenset[UUID]:
        normalized = normalize_name(query)
        if not normalized or not subjects:
            return frozenset()
        async with self.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(graph_entity.c.id).where(
                        graph_entity.c.workspace_id == self.workspace_id,
                        graph_entity.c.subject.in_(subjects),
                        graph_entity.c.normalized_name == normalized,
                    )
                )
            ).all()
        return frozenset(row.id for row in rows)

    async def _seed_from_text(self, text: str, subjects: frozenset[str]) -> frozenset[UUID]:
        normalized = normalize_name(text)
        if not normalized or not subjects:
            return frozenset()
        padded = f" {normalized} "
        async with self.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(graph_entity.c.id, graph_entity.c.normalized_name)
                    .where(
                        graph_entity.c.workspace_id == self.workspace_id,
                        graph_entity.c.subject.in_(subjects),
                    )
                    .order_by(graph_entity.c.updated_at.desc())
                    .limit(CANDIDATE_MAX)
                )
            ).all()
        return frozenset(row.id for row in rows if f" {row.normalized_name} " in padded)

    async def _expand(
        self,
        seeds: frozenset[UUID],
        subjects: frozenset[str],
        hops: int,
        edge_types: frozenset[str],
    ) -> Subgraph:
        if not seeds or not subjects:
            return Subgraph((), ())
        visited: set[UUID] = set(seeds)
        frontier: set[UUID] = set(seeds)
        edges: dict[UUID, TraversedEdge] = {}
        for _ in range(hops):
            if not frontier or len(edges) >= RESULT_EDGE_CAP:
                break
            frontier = await self._hop(frontier, subjects, edge_types, edges, visited)
        return Subgraph(nodes=await self._nodes(visited), edges=tuple(edges.values()))

    async def _hop(
        self,
        frontier: set[UUID],
        subjects: frozenset[str],
        edge_types: frozenset[str],
        edges: dict[UUID, TraversedEdge],
        visited: set[UUID],
    ) -> set[UUID]:
        bounded = list(frontier)[:FRONTIER_CAP]
        conditions = [
            graph_edge.c.workspace_id == self.workspace_id,
            graph_edge.c.subject.in_(subjects),
            graph_edge.c.tombstone.is_(False),
            sa.or_(graph_edge.c.from_entity.in_(bounded), graph_edge.c.to_entity.in_(bounded)),
        ]
        if edge_types:
            conditions.append(graph_edge.c.edge_type.in_(edge_types))
        async with self.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        graph_edge.c.id,
                        graph_edge.c.edge_type,
                        graph_edge.c.from_entity,
                        graph_edge.c.to_entity,
                        graph_edge.c.source_page_id,
                        graph_edge.c.confidence,
                    )
                    .where(*conditions)
                    .limit(RESULT_EDGE_CAP)
                )
            ).all()
        next_frontier: set[UUID] = set()
        for row in rows:
            edges[row.id] = TraversedEdge(
                edge_type=row.edge_type,
                from_entity=row.from_entity,
                to_entity=row.to_entity,
                source_page_id=row.source_page_id,
                confidence=row.confidence,
            )
            for endpoint in (row.from_entity, row.to_entity):
                if endpoint not in visited:
                    visited.add(endpoint)
                    next_frontier.add(endpoint)
        return next_frontier

    async def _nodes(self, ids: set[UUID]) -> tuple[EntityNode, ...]:
        if not ids:
            return ()
        async with self.transaction() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        graph_entity.c.id,
                        graph_entity.c.name,
                        graph_entity.c.entity_type,
                        graph_entity.c.is_stub,
                    ).where(
                        graph_entity.c.workspace_id == self.workspace_id,
                        graph_entity.c.id.in_(ids),
                    )
                )
            ).all()
        return tuple(
            EntityNode(
                id=row.id, name=row.name, entity_type=row.entity_type, is_stub=bool(row.is_stub)
            )
            for row in rows
        )
