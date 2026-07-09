from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_memory.manifest as memory_manifest
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import MemoryStore, PageIndexer, mem_page, recall_subjects

from ufo.blob import FilesystemBlobStore
from ufo.config import SourceConfig, SourceEntry
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, ScopedStore, context_for
from ufo.ext.manifest import (
    HookContext,
    HookOutcome,
    HookSpec,
    JobSpec,
    Manifest,
    PageChangeBatch,
)
from ufo.indexing import OWNER_KIND_PAGE, Chunk, TextChunker
from ufo.jobs import (
    CORE_EXTENSION,
    PAGE_CHANGE_JOB,
    SANDBOX_REAP_JOB,
    SPEND_RESUME_JOB,
    JobRunner,
    PageChangeConsumer,
    PageChangeRunner,
    SandboxReaper,
    SpendResume,
    bindings_from,
    core_jobs,
)
from ufo.sandbox.local import LocalCarrier
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sources.sync import (
    FOLDER_BACKEND,
    SOURCE_SYNC_JOB,
    CorePageFeed,
    CursorExpired,
    FolderSource,
    Page,
    PageBatch,
    PageFeed,
    SourceAuth,
    StreamSkipped,
    SyncDriver,
    SyncResult,
    page_id_for,
    register_sources,
    source_row_id,
)
from ufo.subjects import SHARED_SUBJECT, member_subject
from ufo.tools.context import SpawnResult, ToolContext, ToolResult
from ufo.workspace import ws, ws_current

MEMORY_TOOLS = {tool.name: tool for tool in memory_manifest.manifest().tools}


def vec(*axes: tuple[int, float]) -> tuple[float, ...]:
    values = [0.0] * EMBED_DIM
    for index, value in axes:
        values[index] = value
    return tuple(values)


class StubEmbed:
    """A deterministic stand-in EmbedClient the indexers embed through and search queries through;
    the tests assert the page rows and the SourceMatch snippets, never this stand-in."""

    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector for _ in texts)


class CountingEmbed:
    """Counts embed calls so a test can witness that a resumed cursor re-embeds nothing already
    indexed — the call count is the only witness; the derived chunks are asserted elsewhere."""

    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector
        self.calls = 0

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        return tuple(self._vector for _ in texts)


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in the source tests")


@pytest.fixture
async def clean(db: None, database_url: str) -> AsyncIterator[None]:
    async with workspace_tx() as connection:
        await connection.execute(sa.text("delete from chunk"))
        await connection.execute(sa.text("delete from mem_page"))
        if database_url.startswith("sqlite"):
            await connection.execute(sa.text("delete from chunk_fts"))
    yield


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


def _wire(
    database_url: str, vector: tuple[float, ...], blob_root: Path, workspace_id: UUID
) -> tuple[SyncDriver, Callable[[], Awaitable[None]], MemoryStore]:
    embed = StubEmbed(vector)
    index = DefaultIndex(embed=embed, transaction=workspace_tx)
    blob = FilesystemBlobStore(root=blob_root)
    feed = CorePageFeed(blob=blob)
    driver = SyncDriver(
        backends={FOLDER_BACKEND: FolderSource()},
        blob=blob,
        postgres=database_url.startswith("postgresql"),
    )
    indexer = PageIndexer(
        index=index,
        embed=embed,
        transaction=workspace_tx,
        chunker=TextChunker(),
        workspace_id=workspace_id,
    )

    async def index_pages() -> None:
        batch = await feed.pages_changed_since(None, 50)
        await indexer.apply(batch.changes)

    service = MemoryStore(
        index=index, embed=embed, transaction=workspace_tx, workspace_id=workspace_id
    )
    return driver, index_pages, service


async def _register_folder(root: Path) -> None:
    await register_sources(
        (SourceEntry(backend=FOLDER_BACKEND, config=SourceConfig(root=str(root))),)
    )


async def _sync(driver: SyncDriver) -> None:
    """Drive the sync job through the real dispatch: the source-sync JobSpec's candidate names the
    workspaces holding a due source, and `fire` binds each before running the driver — so the claim,
    fetch, and commit run scoped per workspace and never unbound, exactly as the fleet fires it."""

    async def _handler(context: ExtensionContext) -> None:
        await driver.run()

    spec = JobSpec(
        name=SOURCE_SYNC_JOB,
        schedule=None,
        handler=_handler,
        candidates=driver.candidate_workspaces,
    )
    runner = JobRunner(bindings=bindings_from((), (spec,)))
    await runner.fire(f"{CORE_EXTENSION}:{SOURCE_SYNC_JOB}")


async def _fire_page_change(runner: PageChangeRunner, consumer: PageChangeConsumer) -> None:
    """Drive one page-change consumer through the real dispatch: its JobSpec candidate names the
    workspaces with pages changed since the consumer's cursor, and `fire` binds each before running
    the consumer's cursor loop — so the replay runs scoped per workspace and a change-free workspace
    (absent from the candidates) is never opened."""

    async def _handler(context: ExtensionContext) -> None:
        await runner.drive(consumer)

    async def _candidates() -> tuple[UUID, ...]:
        return await runner.workspaces_with_changes(consumer)

    name = f"{PAGE_CHANGE_JOB}:{consumer.extension}:{consumer.discriminator}"
    spec = JobSpec(name=name, schedule=None, handler=_handler, candidates=_candidates)
    job_runner = JobRunner(bindings=bindings_from((), (spec,)))
    await job_runner.fire(f"{CORE_EXTENSION}:{name}")


async def _pages() -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(
                        tables.page.c.subject,
                        tables.page.c.digest,
                        tables.page.c.body_ref,
                        tables.page.c.tombstone,
                        tables.page.c.updated_at,
                    )
                )
            ).mappings()
        )


async def _chunk_count() -> int:
    async with workspace_tx() as connection:
        return (await connection.execute(sa.text("select count(*) from chunk"))).scalar_one()


async def _make_due() -> None:
    """Simulate the sync interval elapsing so the next `run()` re-claims the source."""
    async with workspace_tx() as connection:
        await connection.execute(sa.update(tables.source).values(next_sync_at=sa.func.now()))


def _context(memory: MemoryStore, member_id: UUID | None, blob_root: Path) -> ToolContext:
    ext = context_for(
        "memory",
        frozenset(),
        index=memory.index,
        embed=memory.embed,
    )
    return ToolContext(
        sandbox=None,
        blob=FilesystemBlobStore(root=blob_root),
        turn=Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        member_id=member_id,
        artifact_token_secret="",
        ext=ext,
    )


async def _search(memory: MemoryStore, member_id: UUID | None, blob_root: Path, query: str) -> str:
    tool = MEMORY_TOOLS["memory_search"]
    with ws(memory.workspace_id):
        result: ToolResult = await tool.handler(
            _context(memory, member_id, blob_root),
            tool.input_model.model_validate({"queries": [query]}),
        )
    return result.content[0].text


async def test_folder_syncs_a_page_body_to_blob_no_chunk_until_indexed(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "brief.md").write_text("the quarterly revenue target is twelve million dollars")
    driver, index_pages, _ = _wire(database_url, vec((7, 1.0)), tmp_path / "blobs", workspace_id)
    await _register_folder(root)

    await _sync(driver)
    pages = await _pages()
    assert len(pages) == 1
    assert pages[0]["subject"] == SHARED_SUBJECT
    assert pages[0]["digest"].startswith("sha256:")
    assert pages[0]["tombstone"] is False or pages[0]["tombstone"] == 0
    body = await FilesystemBlobStore(root=tmp_path / "blobs").get(pages[0]["body_ref"])
    assert b"quarterly revenue" in body
    assert await _chunk_count() == 0

    await index_pages()
    assert await _chunk_count() >= 1


async def test_synced_page_content_is_found_via_memory_search(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "wiki.md").write_text("the office fire assembly point is the north car park")
    driver, index_pages, service = _wire(
        database_url, vec((8, 1.0)), tmp_path / "blobs", workspace_id
    )
    await _register_folder(root)
    await _sync(driver)
    await index_pages()

    found = await _search(service, uuid4(), tmp_path / "blobs", "fire assembly point")
    assert "[source]" in found
    assert "north car park" in found


async def test_unchanged_doc_resync_does_not_reindex_or_duplicate(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "note.md").write_text("the mascot is a friendly otter named pip")
    driver, index_pages, _ = _wire(database_url, vec((9, 1.0)), tmp_path / "blobs", workspace_id)
    await _register_folder(root)
    await _sync(driver)
    await index_pages()
    stamped = (await _pages())[0]["updated_at"]
    chunks = await _chunk_count()

    await _make_due()
    await _sync(driver)
    resynced = await _pages()
    assert len(resynced) == 1
    assert resynced[0]["updated_at"] == stamped
    await index_pages()
    assert await _chunk_count() == chunks


async def test_changed_doc_resync_marks_due_and_reindexes(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    doc = root / "spec.md"
    doc.write_text("the release date is friday")
    driver, index_pages, service = _wire(
        database_url, vec((10, 1.0)), tmp_path / "blobs", workspace_id
    )
    await _register_folder(root)
    await _sync(driver)
    await index_pages()
    first_digest = (await _pages())[0]["digest"]

    doc.write_text("the release date is monday")
    await _make_due()
    await _sync(driver)
    pages = await _pages()
    assert len(pages) == 1
    assert pages[0]["digest"] != first_digest
    await index_pages()
    found = await _search(service, uuid4(), tmp_path / "blobs", "release date monday")
    assert "monday" in found


async def test_edited_page_leaves_no_stale_chunk_in_search_sources(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    """Re-indexing an edited page prunes the old body's chunks: a term unique to the prior body no
    longer surfaces through `search_sources`, while the new body's term does. Without the prune the
    orphaned old chunk stays indexed under the same page and returns as a stale snippet."""
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    doc = root / "spec.md"
    doc.write_text("the launch codename is thunderbird")
    driver, index_pages, service = _wire(
        database_url, vec((16, 1.0)), tmp_path / "blobs", workspace_id
    )
    await _register_folder(root)
    await _sync(driver)
    await index_pages()
    before = await service.search_sources("launch codename", frozenset({SHARED_SUBJECT}), 8)
    assert before and "thunderbird" in before[0].text

    doc.write_text("the launch codename is nighthawk")
    await _make_due()
    await _sync(driver)
    await index_pages()

    matches = await service.search_sources(
        "launch codename thunderbird", frozenset({SHARED_SUBJECT}), 8
    )
    assert matches and all("thunderbird" not in match.text for match in matches)
    assert "nighthawk" in matches[0].text


async def test_removed_file_tombstones_page_and_index_drops_its_chunks(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "keep.md").write_text("penguins huddle for warmth in antarctica")
    gone = root / "gone.md"
    gone.write_text("volcano magma chamber pressure readings")
    driver, index_pages, service = _wire(
        database_url, vec((11, 1.0)), tmp_path / "blobs", workspace_id
    )
    await _register_folder(root)
    await _sync(driver)
    await index_pages()
    assert "magma" in await _search(service, uuid4(), tmp_path / "blobs", "volcano magma chamber")

    gone.unlink()
    await _make_due()
    await _sync(driver)
    tombstoned = [p for p in await _pages() if p["tombstone"] not in (False, 0)]
    assert len(tombstoned) == 1
    await index_pages()

    blobs = tmp_path / "blobs"
    assert "magma" not in await _search(service, uuid4(), blobs, "volcano magma chamber")
    assert "penguins" in await _search(service, uuid4(), blobs, "penguins antarctica")


async def test_page_change_runner_cursor_resumes_across_ticks(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    """The core page-change runner rides each consumer's own cursor, so a later tick re-embeds
    nothing already indexed and picks up only the pages changed since — the embed call count is the
    witness. Driven with the real memory page-indexer consumer over the real feed (the memory
    manifest registers two page_change hooks; this selects the indexer by its discriminator),
    proving the runner's producer and the consumer end to end."""
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "a.md").write_text("alpha document about apples")
    embed = CountingEmbed(vec((15, 1.0)))
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    postgres = database_url.startswith("postgresql")
    driver = SyncDriver(backends={FOLDER_BACKEND: FolderSource()}, blob=blob, postgres=postgres)
    runner = PageChangeRunner(
        manifests=(memory_manifest.manifest(),),
        pages=CorePageFeed(blob=blob),
        index=DefaultIndex(embed=embed, transaction=workspace_tx),
        embed=embed,
    )
    consumer = next(c for c in runner.consumers() if c.discriminator == "index_pages")

    await _register_folder(root)
    await _sync(driver)
    with ws(workspace_id):
        await runner.drive(consumer)
    indexed_a = embed.calls
    assert indexed_a >= 1

    with ws(workspace_id):
        await runner.drive(consumer)
    assert embed.calls == indexed_a

    (root / "b.md").write_text("beta document about bananas")
    await _make_due()
    await _sync(driver)
    with ws(workspace_id):
        await runner.drive(consumer)
    assert embed.calls == indexed_a + 1


PROBE_EXTENSION = "page_change_probe"
SEEN_PAGES_KEY = "seen_pages"


async def record_pages(ctx: HookContext) -> HookOutcome:
    """A `page_change` probe consumer: records the page ids it was handed into its own scoped store,
    so a test reads back — through the public store, scoped to the ambient workspace — which pages
    each workspace's drive replayed. The discriminator is this handler's `__name__`."""
    assert isinstance(ctx.payload, PageChangeBatch)
    await ctx.ext.store.put(
        SEEN_PAGES_KEY, ",".join(sorted(str(change.page_id) for change in ctx.payload.changes))
    )
    return None


async def _seed_page(workspace_id: UUID) -> UUID:
    """A workspace with one source and one (tombstoned) page — enough for the runner to enumerate
    the workspace and replay the page; tombstoned so the feed inlines an empty body without a blob.
    Returns the page id the drive should replay to this workspace."""
    page_id, source_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend=FOLDER_BACKEND,
                config={},
                cursor=None,
                next_sync_at=sa.func.now(),
                claimed_by=None,
                claim_expires_at=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
                digest="d",
                body_ref="",
                subject=SHARED_SUBJECT,
                tombstone=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return page_id


@dataclass
class _RecordingFeed:
    """Wraps the real `CorePageFeed` and records the workspace each `pages_changed_since` runs
    under — the witness of which workspaces a drive actually opened, since the feed is read only
    inside `drive`, under the `with ws(...)` block the dispatcher opens per candidate. A real feed
    delegated to, never a fake: the delivered pages come from Postgres/SQLite as always; this only
    observes the scope."""

    inner: CorePageFeed
    opened: list[UUID] = field(default_factory=list)

    async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch:
        self.opened.append(ws_current().workspace_id)
        return await self.inner.pages_changed_since(cursor, limit)


def _probe_runner(tmp_path: Path, pages: PageFeed | None = None) -> PageChangeRunner:
    manifest = Manifest(
        name=PROBE_EXTENSION,
        version="0",
        hooks=(HookSpec(event="page_change", handler=record_pages),),
    )
    return PageChangeRunner(
        manifests=(manifest,),
        pages=pages or CorePageFeed(blob=FilesystemBlobStore(root=tmp_path / "blobs")),
    )


async def test_page_change_drive_enumerates_workspaces_with_changes(
    db: None, tmp_path: Path
) -> None:
    """One consumer's drive finds every workspace with pages changed since its cursor through the
    single owner_tx read (two freshly-seeded workspaces here, neither driven before) and runs the
    cursor loop bound to each — no caller binds a workspace. Each workspace's probe records its own
    page into its own scoped store, so two stores written, each carrying its own page, is the
    witness the drive fanned per workspace and scoped each."""
    ws_a, ws_b = uuid4(), uuid4()
    page_a = await _seed_page(ws_a)
    page_b = await _seed_page(ws_b)
    runner = _probe_runner(tmp_path)
    (consumer,) = runner.consumers()

    await _fire_page_change(runner, consumer)

    with ws(ws_a):
        seen_a = await ScopedStore(extension=PROBE_EXTENSION).get(SEEN_PAGES_KEY)
    with ws(ws_b):
        seen_b = await ScopedStore(extension=PROBE_EXTENSION).get(SEEN_PAGES_KEY)
    assert isinstance(seen_a, str) and str(page_a) in seen_a
    assert isinstance(seen_b, str) and str(page_b) in seen_b


async def test_page_change_drive_skips_a_workspace_unchanged_since_its_cursor(
    db: None, tmp_path: Path
) -> None:
    """Selectivity: a workspace that holds pages but has none changed since this consumer's cursor
    is never opened. The recording feed logs the workspace each drive opens. ws_a is seeded and
    drained by a first drive (opened once); then ws_b is seeded fresh. The second drive opens ws_b
    alone — ws_a, still holding its page but unchanged since its cursor, is not a candidate, so no
    transaction runs against it on the tick."""
    ws_a, ws_b = uuid4(), uuid4()
    page_a = await _seed_page(ws_a)
    feed = _RecordingFeed(inner=CorePageFeed(blob=FilesystemBlobStore(root=tmp_path / "blobs")))
    runner = _probe_runner(tmp_path, pages=feed)
    (consumer,) = runner.consumers()

    await _fire_page_change(runner, consumer)
    assert feed.opened == [ws_a]
    with ws(ws_a):
        drained = await ScopedStore(extension=PROBE_EXTENSION).get(SEEN_PAGES_KEY)
    assert isinstance(drained, str) and str(page_a) in drained

    page_b = await _seed_page(ws_b)
    feed.opened.clear()
    await _fire_page_change(runner, consumer)
    assert feed.opened == [ws_b]
    with ws(ws_b):
        seen_b = await ScopedStore(extension=PROBE_EXTENSION).get(SEEN_PAGES_KEY)
    assert isinstance(seen_b, str) and str(page_b) in seen_b


async def test_shared_page_scoping_excludes_a_member_only_search(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "policy.md").write_text("expense reports are due on the last business day")
    driver, index_pages, service = _wire(
        database_url, vec((12, 1.0)), tmp_path / "blobs", workspace_id
    )
    await _register_folder(root)
    await _sync(driver)
    await index_pages()

    shared = await service.search_sources("expense reports due", frozenset({SHARED_SUBJECT}), 8)
    assert len(shared) == 1 and "expense reports" in shared[0].text

    member_only = await service.search_sources(
        "expense reports due", frozenset({member_subject(uuid4())}), 8
    )
    assert member_only == ()

    with_shared = await service.search_sources("expense reports due", recall_subjects(uuid4()), 8)
    assert len(with_shared) == 1


async def test_member_scoped_page_is_invisible_to_another_member(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    alice, bob = uuid4(), uuid4()
    probe = vec((13, 1.0))
    page_id = uuid4()
    async with workspace_tx() as connection:
        source_id = uuid4()
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend=FOLDER_BACKEND,
                config={"root": "/seed"},
                cursor=None,
                next_sync_at=sa.func.now(),
                claimed_by=None,
                claim_expires_at=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
                digest="sha256:seed",
                body_ref="sources/seed",
                subject=member_subject(alice),
                tombstone=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(mem_page).values(
                page_id=page_id,
                workspace_id=workspace_id,
                subject=member_subject(alice),
                created_at=sa.func.now(),
            )
        )
    _, _, service = _wire(database_url, probe, tmp_path / "blobs", workspace_id)
    await service.index.upsert(
        (
            Chunk(
                "pg-" + page_id.hex,
                OWNER_KIND_PAGE,
                str(page_id),
                member_subject(alice),
                0,
                "alices private onboarding checklist",
                probe,
            ),
        )
    )

    mine = await service.search_sources("onboarding checklist", recall_subjects(alice), 8)
    assert len(mine) == 1 and mine[0].page_id == page_id
    assert await service.search_sources("onboarding checklist", recall_subjects(bob), 8) == ()


async def test_a_failing_source_is_isolated_and_released(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    """One bad source must not wedge the run: its fetch raises, but the sibling still syncs and the
    failing source is released (claim cleared) and backed off (next_sync_at advanced) rather than
    left claimed to re-fail every lease cycle."""
    workspace_id = await _workspace()
    good = tmp_path / "good"
    good.mkdir()
    (good / "doc.md").write_text("the wifi password is maple syrup")
    missing = tmp_path / "missing"  # never created → FolderSource._read raises FileNotFoundError
    driver, _, _ = _wire(database_url, vec((14, 1.0)), tmp_path / "blobs", workspace_id)
    await _register_folder(good)
    await _register_folder(missing)

    async def _row(root: Path) -> sa.RowMapping:
        async with workspace_tx() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(
                            tables.source.c.config,
                            tables.source.c.claimed_by,
                            tables.source.c.next_sync_at,
                        )
                    )
                )
                .mappings()
                .all()
            )
        return next(row for row in rows if row["config"]["root"] == str(root))

    before = (await _row(missing))["next_sync_at"]

    await _sync(driver)

    assert len(await _pages()) == 1  # the good source synced despite the bad sibling
    good_row, missing_row = await _row(good), await _row(missing)
    assert good_row["claimed_by"] is None
    assert missing_row["claimed_by"] is None  # released, not stuck claimed
    assert missing_row["next_sync_at"] > before  # backed off, won't re-fail every lease


SCRIPTED_BACKEND = "scripted"


class _ScriptedSource:
    """A `SourceBackend` stand-in whose `fetch` is scripted per call: it raises the given error or
    returns the given result, recording the cursor it was handed. The dependency the driver drives,
    never the thing asserted — the tests read the driver's effect (the stored cursor, error count,
    and backoff) back from the real source row."""

    config_model: ClassVar[type[SourceConfig]] = SourceConfig

    def __init__(self, outcomes: list[SyncResult | Exception]) -> None:
        self._outcomes = outcomes
        self.cursors: list[str | None] = []

    async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult:
        self.cursors.append(cursor)
        outcome = self._outcomes.pop(0)
        match outcome:
            case Exception():
                raise outcome
            case _:
                return outcome


def _scripted_driver(
    outcomes: list[SyncResult | Exception], database_url: str, blob_root: Path
) -> tuple[SyncDriver, _ScriptedSource]:
    backend = _ScriptedSource(outcomes)
    driver = SyncDriver(
        backends={SCRIPTED_BACKEND: backend},
        blob=FilesystemBlobStore(root=blob_root),
        postgres=database_url.startswith("postgresql"),
    )
    return driver, backend


async def _seed_scripted_source(workspace_id: UUID, cursor: str | None) -> UUID:
    source_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend=SCRIPTED_BACKEND,
                config={"root": "/unused"},
                cursor=cursor,
                next_sync_at=sa.func.now(),
                claimed_by=None,
                claim_expires_at=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return source_id


async def _source_state(source_id: UUID) -> sa.RowMapping:
    async with workspace_tx() as connection:
        return (
            (
                await connection.execute(
                    sa.select(
                        tables.source.c.cursor,
                        tables.source.c.consecutive_errors,
                        tables.source.c.next_sync_at,
                    ).where(tables.source.c.id == source_id)
                )
            )
            .mappings()
            .one()
        )


async def _tombstone(page_id: UUID) -> bool:
    async with workspace_tx() as connection:
        return bool(
            (
                await connection.execute(
                    sa.select(tables.page.c.tombstone).where(tables.page.c.id == page_id)
                )
            ).scalar_one()
        )


async def _seed_prior_page(workspace_id: UUID, source_id: UUID, source_ref: str) -> UUID:
    """A page a prior snapshot of the source already landed and indexed — active, unrelated to the
    delta run under test."""
    page_id = page_id_for(source_id, source_ref)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
                digest="sha256:prior",
                body_ref=f"sources/{source_id}/{page_id}",
                subject=SHARED_SUBJECT,
                tombstone=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return page_id


async def test_cursor_expired_clears_stored_cursor_and_next_run_refetches(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """A source on an expiring delta token must recover: when the backend raises `CursorExpired`,
    the driver clears the stored cursor so the next run refetches from scratch rather than
    re-failing on the dead cursor forever."""
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, "stale-token")
    page = Page(
        source_ref="doc",
        digest="sha256:fresh",
        subject=SHARED_SUBJECT,
        body="the launch window opens at dawn",
    )
    driver, backend = _scripted_driver(
        [
            CursorExpired("delta token aged out"),
            SyncResult(pages=(page,), next_cursor="fresh-token"),
        ],
        database_url,
        tmp_path / "blobs",
    )

    await _sync(driver)
    expired = await _source_state(source_id)
    assert backend.cursors == ["stale-token"]
    assert expired["cursor"] is None
    assert expired["consecutive_errors"] == 1

    await _make_due()
    await _sync(driver)
    refetched = await _source_state(source_id)
    assert backend.cursors == ["stale-token", None]
    assert refetched["cursor"] == "fresh-token"
    assert refetched["consecutive_errors"] == 0
    assert len(await _pages()) == 1


async def test_consecutive_errors_back_off_and_a_success_resets_the_counter(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """A persistently-failing source counts its errors and pushes next_sync_at out further each
    time, so it doesn't hammer its provider every interval; a successful sync resets the counter."""
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, None)
    driver, _ = _scripted_driver(
        [RuntimeError("provider 500"), RuntimeError("provider 500"), SyncResult(pages=())],
        database_url,
        tmp_path / "blobs",
    )

    baseline_first = (await _source_state(source_id))["next_sync_at"]
    await _sync(driver)
    first = await _source_state(source_id)
    assert first["consecutive_errors"] == 1
    gap_first = first["next_sync_at"] - baseline_first

    await _make_due()
    baseline_second = (await _source_state(source_id))["next_sync_at"]
    await _sync(driver)
    second = await _source_state(source_id)
    assert second["consecutive_errors"] == 2
    gap_second = second["next_sync_at"] - baseline_second
    assert gap_second > gap_first

    await _make_due()
    await _sync(driver)
    assert (await _source_state(source_id))["consecutive_errors"] == 0


async def test_delta_delete_tombstones_only_named_page_never_blanket_sweeps(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """A delta backend (`snapshot=False`) that lands a page, then next run explicitly deletes it,
    tombstones only that page — an unrelated prior page from a different snapshot survives. This is
    the both-ends proof that a partial fetch never triggers the blanket unseen-page sweep."""
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, None)
    kept_id = await _seed_prior_page(workspace_id, source_id, "kept/doc")
    delta = Page(
        source_ref="delta/doc",
        digest="sha256:delta",
        subject=SHARED_SUBJECT,
        body="the launch window opens at dawn",
    )
    delta_id = page_id_for(source_id, "delta/doc")
    driver, _ = _scripted_driver(
        [
            SyncResult(pages=(delta,), snapshot=False),
            SyncResult(pages=(), deletes=("delta/doc",), snapshot=False),
        ],
        database_url,
        tmp_path / "blobs",
    )

    await _sync(driver)
    assert await _tombstone(delta_id) is False
    assert await _tombstone(kept_id) is False  # delta run 1 did not sweep the unseen prior page

    await _make_due()
    await _sync(driver)
    assert await _tombstone(delta_id) is True  # explicitly deleted → tombstoned
    assert await _tombstone(kept_id) is False  # survives: no blanket sweep on a delta run


async def test_snapshot_fetch_tombstones_prior_pages_absent_from_the_fetch(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """A `snapshot=True` fetch is an authoritative full collection: a prior page the fetch no longer
    holds is swept to a tombstone, while a page still present stays active."""
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, None)
    gone_id = await _seed_prior_page(workspace_id, source_id, "gone/doc")
    kept = Page(
        source_ref="kept/doc",
        digest="sha256:kept",
        subject=SHARED_SUBJECT,
        body="the mascot is a friendly otter named pip",
    )
    kept_id = page_id_for(source_id, "kept/doc")
    driver, _ = _scripted_driver(
        [SyncResult(pages=(kept,), snapshot=True)], database_url, tmp_path / "blobs"
    )

    await _sync(driver)
    assert await _tombstone(gone_id) is True  # absent from the authoritative snapshot → swept
    assert await _tombstone(kept_id) is False


async def test_stream_skipped_records_a_skip_not_a_failure_and_never_tombstones(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """A backend that raises `StreamSkipped` (a scope/plan gate) records a skip, not a failure: the
    error counter stays reset and the cursor is held, and the source's existing pages are never
    swept. A sibling that raises a real error still fails, and a healthy sibling still syncs — the
    both-ends proof that a controlled skip suppresses snapshot delete-detection while a genuine
    fault does not."""
    workspace_id = await _workspace()
    skipped_id = await _seed_scripted_source(workspace_id, "held-cursor")
    kept_id = await _seed_prior_page(workspace_id, skipped_id, "kept/doc")
    good = tmp_path / "good"
    good.mkdir()
    (good / "doc.md").write_text("the wifi password is maple syrup")
    missing = tmp_path / "missing"  # never created → FolderSource._read raises FileNotFoundError
    driver = SyncDriver(
        backends={
            SCRIPTED_BACKEND: _ScriptedSource(
                [StreamSkipped("github: org scope not granted (403)")]
            ),
            FOLDER_BACKEND: FolderSource(),
        },
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
    )
    await _register_folder(good)
    await _register_folder(missing)
    baseline = (await _source_state(skipped_id))["next_sync_at"]

    await _sync(driver)

    skip_state = await _source_state(skipped_id)
    assert skip_state["consecutive_errors"] == 0  # a skip is not a failure
    assert skip_state["cursor"] == "held-cursor"  # cursor held, not reset
    assert skip_state["next_sync_at"] > baseline  # rescheduled and released, not stuck claimed
    assert await _tombstone(kept_id) is False  # existing page not swept — no snapshot delete

    failed_id = source_row_id(workspace_id, FOLDER_BACKEND, {"root": str(missing)})
    assert (await _source_state(failed_id))["consecutive_errors"] == 1  # real error still fails

    active = [page for page in await _pages() if page["tombstone"] in (False, 0)]
    assert len(active) == 2  # the held page and the healthy sibling's page, both synced


def test_source_sync_spend_resume_and_sandbox_reap_register_as_core_jobs(
    database_url: str, tmp_path: Path
) -> None:
    driver = SyncDriver(
        backends={FOLDER_BACKEND: FolderSource()},
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
    )
    runner = PageChangeRunner(
        manifests=(),
        pages=CorePageFeed(blob=FilesystemBlobStore(root=tmp_path / "pages")),
    )
    specs = core_jobs(
        driver,
        SpendResume(client=None),
        SandboxReaper(carrier=LocalCarrier(), backend="local"),
        runner,
    )
    assert [spec.name for spec in specs] == [
        SOURCE_SYNC_JOB,
        SPEND_RESUME_JOB,
        SANDBOX_REAP_JOB,
    ]
    assert all(spec.schedule is not None for spec in specs)
    keys = {binding.key for binding in bindings_from((), specs)}
    assert keys == {
        f"{CORE_EXTENSION}:{SOURCE_SYNC_JOB}",
        f"{CORE_EXTENSION}:{SPEND_RESUME_JOB}",
        f"{CORE_EXTENSION}:{SANDBOX_REAP_JOB}",
    }
