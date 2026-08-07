import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_memory.manifest as memory_manifest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import MemoryStore, PageIndexer, mem_page, recall_subjects

from ufo import o11y
from ufo.audience import conversation_audience
from ufo.blob import FilesystemBlobStore
from ufo.config import SourceConfig, SourceEntry
from ufo.db import workspace_tx
from ufo.ext.context import (
    ExtensionContext,
    ScopedStore,
    SourceReader,
    context_for,
)
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
    PAGE_CHANGE_CURSOR_KEY,
    PAGE_CHANGE_JOB,
    TURN_DISPATCH_JOB,
    JobRunner,
    PageChangeConsumer,
    PageChangeRunner,
    TurnDispatcher,
    bindings_from,
    core_jobs,
)
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sources import rest, sync
from ufo.sources.backend import ConnectorSourceConfig
from ufo.sources.sync import (
    FOLDER_BACKEND,
    SOURCE_SYNC_FAILED_METRIC,
    SOURCE_SYNC_JOB,
    SYNC_PROVIDER_FAULT_MAX_CHARS,
    CorePageFeed,
    CursorExpired,
    FolderSource,
    Page,
    PageBatch,
    PageFeed,
    SourceAuth,
    StreamFault,
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

TOOL_NARRATION = "looking through what they synced"

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


async def _workspace() -> UUID:
    workspace_id = uuid4()
    agent_id = uuid5(NAMESPACE_URL, f"{workspace_id}/main")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="main",
                prompt="p",
                model="m",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id


def _wire(
    database_url: str, vector: tuple[float, ...], blob_root: Path, workspace_id: UUID
) -> tuple[SyncDriver, Callable[[], Awaitable[None]], MemoryStore]:
    embed = StubEmbed(vector)
    index = DefaultIndex(transaction=workspace_tx)
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
        page_states=context_for("memory", frozenset()).page_states,
    )

    async def index_pages() -> None:
        batch = await feed.pages_changed_since(None, 50)
        with ws(workspace_id):
            await indexer.apply(batch.changes)

    service = MemoryStore(
        index=index,
        embed=embed,
        transaction=workspace_tx,
        workspace_id=workspace_id,
        page_states=context_for("memory", frozenset()).page_states,
        readable_page_states=context_for("memory", frozenset()).readable_page_states,
        readable_source_ids=context_for("memory", frozenset()).readable_source_ids,
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
    for workspace_id in await runner.candidates(f"{CORE_EXTENSION}:{SOURCE_SYNC_JOB}"):
        await runner.fire(f"{CORE_EXTENSION}:{SOURCE_SYNC_JOB}", workspace_id)


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
    for workspace_id in await job_runner.candidates(f"{CORE_EXTENSION}:{name}"):
        await job_runner.fire(f"{CORE_EXTENSION}:{name}", workspace_id)


async def _pages() -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(
                        tables.page.c.source_id,
                        tables.page.c.stream,
                        tables.page.c.title,
                        tables.page.c.record_created_at,
                        tables.page.c.record_updated_at,
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


async def _claims(driver: SyncDriver) -> tuple[UUID, ...]:
    """Which sources the driver's next pass would claim, through its own due-selection — so a test
    asserts what the driver will actually do next rather than re-deriving the rule."""
    return tuple(claimed.source_id for claimed in await driver._claim_due("probe"))


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
            agent_id=uuid5(NAMESPACE_URL, f"{memory.workspace_id}/main"),
            seq=1,
            status="running",
            inbound="hi",
            created_at=datetime(2026, 7, 9, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        speaker_member_id=member_id,
        audience=conversation_audience(member_id),
        artifact_token_secret="",
        ext=ext,
    )


async def _search(memory: MemoryStore, member_id: UUID | None, blob_root: Path, query: str) -> str:
    tool = MEMORY_TOOLS["memory_search"]
    with ws(memory.workspace_id):
        result: ToolResult = await tool.handler(
            _context(memory, member_id, blob_root),
            tool.input_model.model_validate(
                {"user_description": TOOL_NARRATION, "queries": [query]}
            ),
        )
    return result.content[0].text


def _reader(
    workspace_id: UUID,
    subjects: frozenset[str],
    requesting_member_id: UUID | None = None,
) -> SourceReader:
    return SourceReader(
        agent_id=uuid5(NAMESPACE_URL, f"{workspace_id}/main"),
        requesting_member_id=requesting_member_id,
        subjects=subjects,
    )


async def test_folder_syncs_a_page_body_to_blob_no_chunk_until_indexed(
    db: None, database_url: str, tmp_path: Path
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

    feed = CorePageFeed(blob=FilesystemBlobStore(root=tmp_path / "blobs"))
    with ws(workspace_id):
        changes = (await feed.pages_changed_since(None, 50)).changes
    assert [change.source_id for change in changes] == [pages[0]["source_id"]]

    await index_pages()
    assert await _chunk_count() >= 1


async def test_folder_sync_preserves_bare_carriage_returns(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    body = b"first line\rsecond line"
    (root / "transcript.txt").write_bytes(body)
    driver, _, _ = _wire(database_url, vec((7, 1.0)), tmp_path / "blobs", workspace_id)
    await _register_folder(root)

    await _sync(driver)

    page = (await _pages())[0]
    stored = await FilesystemBlobStore(root=tmp_path / "blobs").get(page["body_ref"])
    assert stored == body
    assert page["digest"] == "sha256:" + hashlib.sha256(body).hexdigest()


async def test_the_driver_stamps_pages_with_the_source_rows_subject(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "note.md").write_text("member scoped note")
    member_id = uuid4()
    source_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="member@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend=FOLDER_BACKEND,
                config={"root": str(root)},
                subject=member_subject(member_id),
                owner_member_id=member_id,
                cursor=None,
                next_sync_at=sa.func.now(),
                claimed_by=None,
                claim_expires_at=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    driver, _, _ = _wire(database_url, vec((18, 1.0)), tmp_path / "blobs", workspace_id)

    await _sync(driver)

    pages = await _pages()
    assert len(pages) == 1
    assert pages[0]["subject"] == f"member:{member_id}"


async def test_register_source_refuses_a_live_row_with_a_different_subject(db: None) -> None:
    workspace_id = await _workspace()
    ctx = context_for("probe", frozenset())
    config = SourceConfig(root="/shared")
    with ws(workspace_id):
        first = await ctx.register_source(
            FOLDER_BACKEND, config, subject=SHARED_SUBJECT, owner_member_id=None
        )
        second = await ctx.register_source(
            FOLDER_BACKEND, config, subject=SHARED_SUBJECT, owner_member_id=None
        )
        assert first == second

        with pytest.raises(ValueError, match="already registered"):
            await ctx.register_source(
                FOLDER_BACKEND,
                config,
                subject=member_subject(uuid4()),
                owner_member_id=uuid4(),
            )


async def test_registering_a_live_source_for_a_second_agent_grants_that_agent(db: None) -> None:
    """A source syncs once however many agents read it, so adding an already-registered feed for a
    second agent settles on the same row — and must still grant that agent, since registering
    through an agent is what grants it. Reporting the source id while granting nothing leaves the
    agent silently mute about the feed a member just added for it."""
    workspace_id = await _workspace()
    ctx = context_for("probe", frozenset())
    config = SourceConfig(root="/shared")
    research_id, sales_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        for agent_id, name in ((research_id, "research"), (sales_id, "sales")):
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=agent_id,
                    workspace_id=workspace_id,
                    name=name,
                    prompt="p",
                    model="m",
                    is_main=False,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    with ws(workspace_id):
        first = await ctx.register_source(
            FOLDER_BACKEND,
            config,
            subject=SHARED_SUBJECT,
            owner_member_id=None,
            agent_id=research_id,
        )
        second = await ctx.register_source(
            FOLDER_BACKEND,
            config,
            subject=SHARED_SUBJECT,
            owner_member_id=None,
            agent_id=sales_id,
        )
        readable = {
            agent_id: await ctx.readable_source_ids(
                SourceReader(
                    agent_id=agent_id,
                    requesting_member_id=None,
                    subjects=frozenset({SHARED_SUBJECT}),
                )
            )
            for agent_id in (research_id, sales_id)
        }
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(sa.select(sa.func.count()).select_from(tables.source))
            ).scalar_one()
    assert second == first
    assert rows == 1
    assert readable == {research_id: frozenset({first}), sales_id: frozenset({first})}


async def test_register_source_refuses_an_agent_in_another_workspace(db: None) -> None:
    """Registering a source names the agent it grants, and that agent must live in this workspace.
    A caller passing an agent id from another workspace is refused loudly, so a source grant can
    never cross the workspace boundary the whole model rests on."""
    home, other = await _workspace(), await _workspace()
    other_agent = uuid5(NAMESPACE_URL, f"{other}/main")
    with ws(home):
        with pytest.raises(ValueError, match="outside this workspace"):
            await context_for("probe", frozenset()).register_source(
                FOLDER_BACKEND,
                SourceConfig(root="/shared"),
                subject=SHARED_SUBJECT,
                owner_member_id=None,
                agent_id=other_agent,
            )


async def test_register_source_without_a_target_requires_a_main_agent(db: None) -> None:
    """With no explicit agent the source binds to the workspace's main agent; a workspace that has
    none has nothing to grant, so registration fails loudly rather than binding to no one."""
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    with ws(workspace_id):
        with pytest.raises(RuntimeError, match="requires a main agent"):
            await context_for("probe", frozenset()).register_source(
                FOLDER_BACKEND,
                SourceConfig(root="/shared"),
                subject=SHARED_SUBJECT,
                owner_member_id=None,
            )


def test_brokered_source_row_id_includes_connection_generation() -> None:
    workspace_id = uuid4()
    config = {"account": "same-account", "stream": "messages"}
    first_connection, second_connection = uuid4(), uuid4()

    assert source_row_id(
        workspace_id, "gmail", config, connection_id=first_connection
    ) != source_row_id(workspace_id, "gmail", config, connection_id=second_connection)


async def test_register_source_conflict_does_not_leak_the_owner_subject(db: None) -> None:
    workspace_id = await _workspace()
    ctx = context_for("probe", frozenset())
    owner_id = uuid4()
    config = SourceConfig(root="/private")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=owner_id,
                workspace_id=workspace_id,
                email="owner@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        await ctx.register_source(
            FOLDER_BACKEND, config, subject=member_subject(owner_id), owner_member_id=owner_id
        )
        with pytest.raises(ValueError) as caught:
            await ctx.register_source(
                FOLDER_BACKEND,
                config,
                subject=member_subject(uuid4()),
                owner_member_id=uuid4(),
            )
    message = str(caught.value)
    assert str(owner_id) not in message
    assert "member:" not in message


async def test_boot_registered_folder_sources_are_shared(db: None, tmp_path: Path) -> None:
    workspace_id = await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    await _register_folder(root)

    async with workspace_tx() as connection:
        row = (
            (
                await connection.execute(
                    sa.select(tables.source.c.subject, tables.source.c.owner_member_id).where(
                        tables.source.c.workspace_id == workspace_id
                    )
                )
            )
            .mappings()
            .one()
        )
    assert row["subject"] == SHARED_SUBJECT
    assert row["owner_member_id"] is None


async def test_synced_page_content_is_found_via_memory_search(
    db: None, database_url: str, tmp_path: Path
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
    db: None, database_url: str, tmp_path: Path
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
    db: None, database_url: str, tmp_path: Path
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
    db: None, database_url: str, tmp_path: Path
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
    with ws(workspace_id):
        before = await service.search_sources(
            "launch codename",
            frozenset({SHARED_SUBJECT}),
            8,
            source_reader=_reader(workspace_id, frozenset({SHARED_SUBJECT})),
        )
        assert before and "thunderbird" in before[0].text

    doc.write_text("the launch codename is nighthawk")
    await _make_due()
    await _sync(driver)
    await index_pages()

    with ws(workspace_id):
        matches = await service.search_sources(
            "launch codename thunderbird",
            frozenset({SHARED_SUBJECT}),
            8,
            source_reader=_reader(workspace_id, frozenset({SHARED_SUBJECT})),
        )
    assert matches and all("thunderbird" not in match.text for match in matches)
    assert "nighthawk" in matches[0].text


async def test_removed_file_tombstones_page_and_index_drops_its_chunks(
    db: None, database_url: str, tmp_path: Path
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
    db: None, database_url: str, tmp_path: Path
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
        index=DefaultIndex(transaction=workspace_tx),
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


async def test_page_change_candidates_isolate_an_invalid_cursor(
    db: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A workspace whose stored cursor predates the revision-based format (or is otherwise
    corrupt) counts as pending rather than aborting the fleet-wide candidate read — one broken
    workspace must not stop every other workspace's tick from finding its own pending work — and
    names itself, its consumer and its extension in `jobs.page_change_cursor_invalid`, the one
    record that reaches an operator before the failure reappears inside a per-workspace drive.
    Reading the cursor stays fail-loud: the broken workspace's own drive still raises, so it never
    advances past pages it did not deliver."""
    broken_id, healthy_id = uuid4(), uuid4()
    await _seed_page(broken_id)
    healthy_page = await _seed_page(healthy_id)
    runner = _probe_runner(tmp_path)
    (consumer,) = runner.consumers()
    with ws(broken_id):
        await ScopedStore(extension=PROBE_EXTENSION).put(
            f"{PAGE_CHANGE_CURSOR_KEY}:{consumer.discriminator}",
            f"{datetime(2030, 1, 1, tzinfo=UTC).isoformat()}|{uuid4()}",
        )

    with caplog.at_level(logging.WARNING, logger="ufo"):
        pending = await runner.workspaces_with_changes(consumer)
    assert set(pending) == {broken_id, healthy_id}
    record = next(
        record for record in caplog.records if record.message == "jobs.page_change_cursor_invalid"
    )
    assert record.ufo == {
        "workspace_id": str(broken_id),
        "extension": PROBE_EXTENSION,
        "discriminator": consumer.discriminator,
    }

    with ws(healthy_id):
        await runner.drive(consumer)
        seen = await ScopedStore(extension=PROBE_EXTENSION).get(SEEN_PAGES_KEY)
    assert isinstance(seen, str) and str(healthy_page) in seen

    with ws(broken_id), pytest.raises(ValueError, match="invalid page cursor"):
        await runner.drive(consumer)


async def test_shared_page_scoping_excludes_a_member_only_search(
    db: None, database_url: str, tmp_path: Path
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

    with ws(workspace_id):
        shared = await service.search_sources(
            "expense reports due",
            frozenset({SHARED_SUBJECT}),
            8,
            source_reader=_reader(workspace_id, frozenset({SHARED_SUBJECT})),
        )
        assert len(shared) == 1 and "expense reports" in shared[0].text

        member_only = await service.search_sources(
            "expense reports due",
            frozenset({member_subject(uuid4())}),
            8,
            source_reader=_reader(workspace_id, frozenset({member_subject(uuid4())})),
        )
        assert member_only == ()

        with_shared = await service.search_sources(
            "expense reports due",
            recall_subjects(conversation_audience(uuid4())),
            8,
            source_reader=_reader(
                workspace_id,
                recall_subjects(conversation_audience(uuid4())),
            ),
        )
        assert len(with_shared) == 1


async def test_member_scoped_page_is_invisible_to_another_member(
    db: None, database_url: str, tmp_path: Path
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
            sa.insert(tables.source_grant).values(
                workspace_id=workspace_id,
                source_id=source_id,
                agent_id=uuid5(NAMESPACE_URL, f"{workspace_id}/main"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        revision = await connection.scalar(
            sa.select(tables.page.c.revision).where(tables.page.c.id == page_id)
        )
        await connection.execute(
            sa.insert(mem_page).values(
                page_id=page_id,
                workspace_id=workspace_id,
                subject=member_subject(alice),
                revision=revision,
                created_at=sa.func.now(),
            )
        )
    _, _, service = _wire(database_url, probe, tmp_path / "blobs", workspace_id)
    with ws(workspace_id):
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

    with ws(workspace_id):
        mine = await service.search_sources(
            "onboarding checklist",
            recall_subjects(conversation_audience(alice)),
            8,
            source_reader=_reader(
                workspace_id,
                recall_subjects(conversation_audience(alice)),
                alice,
            ),
        )
        assert len(mine) == 1 and mine[0].page_id == page_id
        assert (
            await service.search_sources(
                "onboarding checklist",
                recall_subjects(conversation_audience(bob)),
                8,
                source_reader=_reader(
                    workspace_id,
                    recall_subjects(conversation_audience(bob)),
                    bob,
                ),
            )
            == ()
        )


@dataclass(frozen=True)
class _Authority:
    workspace_id: UUID
    owner_id: UUID
    stranger_id: UUID
    main_agent_id: UUID
    granted_agent_id: UUID
    ungranted_agent_id: UUID
    owned_source_id: UUID
    unowned_source_id: UUID


async def _authority() -> _Authority:
    """A member's private source another agent registered — so that agent holds the only grant and
    main's exact-owner exception is the sole other way in — beside an unowned shared source nobody
    holds a grant for at all."""
    state = _Authority(*(uuid4() for _ in range(8)))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=state.workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member),
            [
                {
                    "id": member_id,
                    "workspace_id": state.workspace_id,
                    "email": f"{member_id.hex}@x.test",
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
                for member_id in (state.owner_id, state.stranger_id)
            ],
        )
        await connection.execute(
            sa.insert(tables.agent),
            [
                {
                    "id": agent_id,
                    "workspace_id": state.workspace_id,
                    "name": name,
                    "prompt": "p",
                    "model": "m",
                    "is_main": is_main,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
                for agent_id, name, is_main in (
                    (state.main_agent_id, "ufo", True),
                    (state.granted_agent_id, "research", False),
                    (state.ungranted_agent_id, "scout", False),
                )
            ],
        )
        for source_id, subject, owner_member_id in (
            (state.owned_source_id, member_subject(state.owner_id), state.owner_id),
            (state.unowned_source_id, SHARED_SUBJECT, None),
        ):
            await connection.execute(
                sa.insert(tables.source).values(
                    id=source_id,
                    workspace_id=state.workspace_id,
                    backend=FOLDER_BACKEND,
                    config={"root": f"/{source_id.hex}"},
                    subject=subject,
                    owner_member_id=owner_member_id,
                    cursor=None,
                    next_sync_at=sa.func.now(),
                    claimed_by=None,
                    claim_expires_at=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        await connection.execute(
            sa.insert(tables.source_grant).values(
                workspace_id=state.workspace_id,
                source_id=state.owned_source_id,
                agent_id=state.granted_agent_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return state


async def _reachable(state: _Authority, reader: SourceReader) -> frozenset[UUID]:
    with ws(state.workspace_id):
        return await context_for("probe", frozenset()).readable_source_ids(reader)


def _authority_reader(
    state: _Authority, agent_id: UUID, requesting_member_id: UUID | None
) -> SourceReader:
    return SourceReader(
        agent_id=agent_id,
        requesting_member_id=requesting_member_id,
        subjects=frozenset({SHARED_SUBJECT, member_subject(state.owner_id)}),
    )


async def test_main_reads_an_owned_source_only_while_its_exact_owner_is_speaking(db: None) -> None:
    """The exact shape of main's exception: it needs all three of the main agent, a live requesting
    member, and that member owning the row. Drop any one and only a `source_grant` opens the
    source — which is what carries the registering agent, speaker or not."""
    state = await _authority()

    assert await _reachable(
        state, _authority_reader(state, state.main_agent_id, state.owner_id)
    ) == {state.owned_source_id}

    assert (
        await _reachable(state, _authority_reader(state, state.main_agent_id, None)) == frozenset()
    )
    assert (
        await _reachable(state, _authority_reader(state, state.main_agent_id, state.stranger_id))
        == frozenset()
    )
    assert (
        await _reachable(state, _authority_reader(state, state.ungranted_agent_id, state.owner_id))
        == frozenset()
    )
    assert await _reachable(state, _authority_reader(state, state.granted_agent_id, None)) == {
        state.owned_source_id
    }


async def test_a_turn_with_no_live_speaker_never_inherits_the_owner_exception(db: None) -> None:
    """A scheduled run and a subagent both act with their initiator's authority and neither has a
    speaker, so both reach `source_reader` as one shape: `acting_member_id` is the owner while
    `requesting_member_id` is None. The exception is the live speaker's alone, so the main agent
    reaches nothing on either."""
    state = await _authority()
    for on_behalf_of_member_id in (state.owner_id, state.stranger_id):
        ctx = ToolContext(
            sandbox=None,
            blob=None,
            turn=Turn(
                id=uuid4(),
                workspace_id=state.workspace_id,
                conversation_id=uuid4(),
                agent_id=state.main_agent_id,
                seq=1,
                status="running",
                inbound="summarise what changed",
                created_at=datetime(2026, 7, 27, tzinfo=UTC),
                on_behalf_of_member_id=on_behalf_of_member_id,
            ),
            agent=Agent(prompt="p", model="claude-opus-4-8"),
            spawn=_unavailable_spawn,
            speaker_member_id=None,
            audience=conversation_audience(None),
            on_behalf_of_member_id=on_behalf_of_member_id,
            artifact_token_secret="",
        )
        assert ctx.acting_member_id == on_behalf_of_member_id
        assert member_subject(on_behalf_of_member_id) in ctx.read_subjects
        assert ctx.source_reader().requesting_member_id is None
        assert await _reachable(state, ctx.source_reader()) == frozenset()


async def test_a_failing_source_is_isolated_and_released(
    db: None, database_url: str, tmp_path: Path
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


@dataclass
class _ResyncingSource:
    """A backend that requests a resync of its own source from inside `fetch` — the live shape of
    a member clicking Resync while that source's sync already holds the claim. The request goes
    through the sanctioned API, so what the driver's completing writer must not clobber is exactly
    what production writes."""

    source_id: UUID
    workspace_id: UUID
    outcome: SyncResult | Exception
    config_model: ClassVar[type[SourceConfig]] = SourceConfig

    async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult:
        with ws(self.workspace_id):
            await context_for("sources", frozenset()).schedule_source_sync((self.source_id,))
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@dataclass
class _BlockingSource:
    result: SyncResult
    config_model: ClassVar[type[SourceConfig]] = SourceConfig
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult:
        self.entered.set()
        await self.release.wait()
        return self.result


@dataclass(frozen=True)
class _BlockingReadBlob(FilesystemBlobStore):
    armed: asyncio.Event = field(default_factory=asyncio.Event)
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def get(self, key: str) -> bytes:
        if self.armed.is_set():
            self.entered.set()
            await self.release.wait()
        return await super().get(key)


@dataclass(frozen=True)
class _BlockingWriteBlob(FilesystemBlobStore):
    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def put(self, key: str, data: bytes) -> None:
        self.entered.set()
        await self.release.wait()
        await super().put(key, data)


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


async def test_sync_uses_source_subject_current_after_fetch(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    member_id, source_id = uuid4(), uuid4()
    backend = _BlockingSource(
        SyncResult(
            pages=(
                Page(
                    source_ref="docs/plan",
                    body="launch plan",
                    stream="docs",
                    title="Launch plan",
                ),
            )
        )
    )
    driver = SyncDriver(
        backends={SCRIPTED_BACKEND: backend},
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
    )
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email="member@example.com",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.source).values(
                    id=source_id,
                    workspace_id=workspace_id,
                    backend=SCRIPTED_BACKEND,
                    config={"root": "/unused"},
                    subject=member_subject(member_id),
                    owner_member_id=member_id,
                    cursor=None,
                    next_sync_at=sa.func.now(),
                    claimed_by=None,
                    claim_expires_at=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        running = asyncio.create_task(driver.run())
        try:
            await backend.entered.wait()
            await context_for("probe", frozenset()).set_source_subject((source_id,), SHARED_SUBJECT)
        finally:
            backend.release.set()
            await running
        async with workspace_tx() as connection:
            page_subject = (
                await connection.execute(
                    sa.select(tables.page.c.subject).where(tables.page.c.source_id == source_id)
                )
            ).scalar_one()
    assert page_subject == SHARED_SUBJECT


async def test_stale_sync_cannot_overwrite_a_re_registered_source(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    alice, bob = uuid4(), uuid4()
    blob = _BlockingWriteBlob(root=tmp_path / "blobs")
    driver = SyncDriver(
        backends={
            SCRIPTED_BACKEND: _ScriptedSource(
                [
                    SyncResult(
                        pages=(
                            Page(
                                source_ref="alice/doc",
                                body="alice data",
                                stream="docs",
                                title="Alice",
                            ),
                        ),
                        next_cursor="alice-cursor",
                    )
                ]
            )
        },
        blob=blob,
        postgres=database_url.startswith("postgresql"),
    )
    config = SourceConfig(root="/unused")
    context = context_for("probe", frozenset())
    with ws(workspace_id):
        async with workspace_tx() as connection:
            for member_id, email in ((alice, "alice@example.com"), (bob, "bob@example.com")):
                await connection.execute(
                    sa.insert(tables.member).values(
                        id=member_id,
                        workspace_id=workspace_id,
                        email=email,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        source_id = await context.register_source(
            SCRIPTED_BACKEND,
            config,
            subject=member_subject(alice),
            owner_member_id=alice,
        )
        running = asyncio.create_task(driver.run())
        try:
            await blob.entered.wait()
            await context.remove_source(source_id)
            assert (
                await context.register_source(
                    SCRIPTED_BACKEND,
                    config,
                    subject=member_subject(bob),
                    owner_member_id=bob,
                )
                == source_id
            )
        finally:
            blob.release.set()
            await running
        async with workspace_tx() as connection:
            source = (
                await connection.execute(
                    sa.select(
                        tables.source.c.owner_member_id,
                        tables.source.c.cursor,
                        tables.source.c.consecutive_errors,
                        tables.source.c.claimed_by,
                        tables.source.c.removed_at,
                    ).where(tables.source.c.id == source_id)
                )
            ).one()
            pages = (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(tables.page)
                    .where(tables.page.c.source_id == source_id)
                )
            ).scalar_one()
    assert source == (bob, None, 0, None, None)
    assert pages == 0


async def test_page_feed_reads_one_immutable_page_version_during_a_sync(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    await _seed_scripted_source(workspace_id, None)
    old = Page(source_ref="docs/plan", body="old plan", stream="docs", title="Plan")
    new = old.model_copy(update={"body": "new plan"})
    blob = _BlockingReadBlob(root=tmp_path / "blobs")
    driver = SyncDriver(
        backends={
            SCRIPTED_BACKEND: _ScriptedSource([SyncResult(pages=(old,)), SyncResult(pages=(new,))])
        },
        blob=blob,
        postgres=database_url.startswith("postgresql"),
    )

    await _sync(driver)
    blob.armed.set()
    with ws(workspace_id):
        reading = asyncio.create_task(CorePageFeed(blob).pages_changed_since(None, 1))
        await blob.entered.wait()
        await _make_due()
        await driver.run()
        blob.release.set()
        (change,) = (await reading).changes

    assert (change.body, change.digest) == (old.body, old.digest)
    async with workspace_tx() as connection:
        body_ref, digest = (
            await connection.execute(
                sa.select(tables.page.c.body_ref, tables.page.c.digest).where(
                    tables.page.c.id == change.page_id
                )
            )
        ).one()
    assert digest == new.digest
    assert body_ref.endswith(new.digest.removeprefix("sha256:"))


async def test_database_revision_orders_an_old_writer_after_a_new_cursor(
    db: None, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, None)
    page_id = uuid4()
    old_ref, new_ref = f"pages/{page_id}/old", f"pages/{page_id}/new"
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    await blob.put(old_ref, b"old body")
    await blob.put(new_ref, b"new body")
    old_stamp = datetime(2020, 1, 1, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.page).values(
                id=page_id,
                workspace_id=workspace_id,
                source_id=source_id,
                digest="sha256:old",
                body_ref=old_ref,
                subject=SHARED_SUBJECT,
                tombstone=False,
                created_at=old_stamp,
                updated_at=old_stamp,
            )
        )
        first_revision = (
            await connection.execute(
                sa.select(tables.page.c.revision).where(tables.page.c.id == page_id)
            )
        ).scalar_one()

    feed = CorePageFeed(blob)
    cursor = f"{first_revision}|{page_id}"
    with ws(workspace_id):
        assert (await feed.pages_changed_since(cursor, 1)).changes == ()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.page)
                .values(
                    digest="sha256:new",
                    body_ref=new_ref,
                    updated_at=old_stamp - timedelta(days=1),
                )
                .where(tables.page.c.id == page_id)
            )
        (change,) = (await feed.pages_changed_since(cursor, 1)).changes
        with pytest.raises(ValueError, match="invalid page cursor"):
            await feed.pages_changed_since(
                f"{datetime(2030, 1, 1, tzinfo=UTC).isoformat()}|{page_id}", 1
            )

    assert change.body == "new body"
    assert change.revision > first_revision


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


def test_page_requires_browse_metadata() -> None:
    page = {
        "source_ref": "docs/launch",
        "body": "Launch window",
        "stream": "docs",
        "title": "Launch window",
    }
    for field_name in ("stream", "title"):
        with pytest.raises(ValueError):
            Page.model_validate(page | {field_name: ""})


def test_page_derives_digest_from_body() -> None:
    page = Page(source_ref="docs/launch", body="Launch window", stream="docs", title="Launch")
    assert page.digest == "sha256:" + hashlib.sha256(page.body.encode()).hexdigest()
    with pytest.raises(ValueError):
        Page.model_validate(page.model_dump() | {"digest": "sha256:caller-controlled"})


def test_page_normalizes_browse_timestamps() -> None:
    page = Page(
        source_ref="docs/launch",
        body="Launch window",
        stream="docs",
        title="Launch window",
        created_at="2026-07-23T14:30:00.5-04:00",
        updated_at="2026-07-23T18:30:00Z",
    )
    assert page.created_at == "2026-07-23T18:30:00.500000+00:00"
    assert page.updated_at == "2026-07-23T18:30:00.000000+00:00"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-23", "2026-07-23T00:00:00.000000+00:00"),
        ("1700000000", "2023-11-14T22:13:20.000000+00:00"),
        ("1700000000000", "2023-11-14T22:13:20.000000+00:00"),
    ],
)
def test_page_normalizes_provider_timestamp_shapes(value: str, expected: str) -> None:
    page = Page(
        source_ref="docs/launch",
        body="Launch window",
        stream="docs",
        title="Launch window",
        created_at=value,
    )
    assert page.created_at == expected


@pytest.mark.parametrize("value", ["not-a-time", "2026-07-23T18:30:00"])
def test_page_rejects_invalid_browse_timestamps(value: str) -> None:
    with pytest.raises(ValueError, match="timestamp"):
        Page(
            source_ref="docs/launch",
            body="Launch window",
            stream="docs",
            title="Launch window",
            created_at=value,
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
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source on an expiring delta token must recover: when the backend raises `CursorExpired`,
    the driver clears the stored cursor so the next run refetches from scratch rather than
    re-failing on the dead cursor forever. The reported run says which of the two it was:
    `cursor_reset` separates a failure that dropped its cursor from one that resumed it, and the
    counter carries the class itself rather than sharing one bucket with every unlisted class."""
    reader = _meter(monkeypatch)
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, "stale-token")
    page = Page(
        source_ref="doc",
        body="the launch window opens at dawn",
        stream="docs",
        title="Launch window",
    )
    driver, backend = _scripted_driver(
        [
            CursorExpired("delta token aged out"),
            SyncResult(pages=(page,), next_cursor="fresh-token"),
        ],
        database_url,
        tmp_path / "blobs",
    )

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)
    expired = await _source_state(source_id)
    assert backend.cursors == ["stale-token"]
    assert expired["cursor"] is None
    assert expired["consecutive_errors"] == 1
    failure = _events(caplog, "source_sync.failed")[0]
    assert failure.ufo["cursor_reset"] is True
    assert failure.ufo["error_class"] == "CursorExpired"
    assert _metric_points(reader, SYNC_METRIC) == [
        {
            "provider": SCRIPTED_BACKEND,
            "stream": "",
            "error_class": "CursorExpired",
        }
    ]

    await _make_due()
    await _sync(driver)
    refetched = await _source_state(source_id)
    assert backend.cursors == ["stale-token", None]
    assert refetched["cursor"] == "fresh-token"
    assert refetched["consecutive_errors"] == 0
    pages = await _pages()
    assert len(pages) == 1
    assert pages[0]["stream"] == page.stream
    assert pages[0]["title"] == page.title


async def test_sync_persists_backend_page_browse_metadata(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    await _seed_scripted_source(workspace_id, None)
    page = Page(
        source_ref="issues/ENG-42",
        body="Issue body",
        stream="issues",
        title="Fix launch sequencing",
        created_at="2026-07-01T12:00:00Z",
        updated_at="2026-07-23T18:30:00Z",
    )
    driver, _ = _scripted_driver(
        [SyncResult(pages=(page,))],
        database_url,
        tmp_path / "blobs",
    )

    await _sync(driver)

    stored = (await _pages())[0]
    assert stored["stream"] == page.stream
    assert stored["title"] == page.title
    assert stored["record_created_at"] == page.created_at
    assert stored["record_updated_at"] == page.updated_at
    feed = CorePageFeed(blob=FilesystemBlobStore(root=tmp_path / "blobs"))
    with ws(workspace_id):
        change = (await feed.pages_changed_since(None, 1)).changes[0]
    assert change.as_of == datetime.fromisoformat(page.updated_at)


async def test_sync_refreshes_browse_metadata_without_replaying_unchanged_content(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    await _seed_scripted_source(workspace_id, None)
    original = Page(
        source_ref="issues/ENG-42",
        body="Issue body",
        stream="issues",
        title="Launch sequence",
        updated_at="2026-07-23T18:30:00Z",
    )
    refreshed = original.model_copy(
        update={"title": "Fix launch sequencing", "updated_at": "2026-07-24T09:00:00Z"}
    )
    driver, _ = _scripted_driver(
        [SyncResult(pages=(original,)), SyncResult(pages=(refreshed,))],
        database_url,
        tmp_path / "blobs",
    )

    await _sync(driver)
    stamped = (await _pages())[0]["updated_at"]
    await _make_due()
    await _sync(driver)

    stored = (await _pages())[0]
    assert stored["title"] == refreshed.title
    assert stored["record_updated_at"] == refreshed.updated_at
    assert stored["updated_at"] == stamped


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


async def test_a_resync_requested_during_a_sync_survives_the_completing_writer(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """The panel's Resync is honoured even when it lands mid-sync: the request writes
    `next_sync_at=now` while the claim is held, and the writer that finishes reschedules only the
    sync it ran, so the driver's very next pass claims the source again. Both completion paths that
    reschedule a claim they still hold are covered — the success interval and the error backoff the
    `errors` column invites a member to resync past — and each releases its claim either way."""
    workspace_id = await _workspace()
    for outcome, expected_errors in ((SyncResult(pages=()), 0), (RuntimeError("provider 500"), 1)):
        source_id = await _seed_scripted_source(workspace_id, None)
        driver = SyncDriver(
            backends={
                SCRIPTED_BACKEND: _ResyncingSource(
                    source_id=source_id, workspace_id=workspace_id, outcome=outcome
                )
            },
            blob=FilesystemBlobStore(root=tmp_path / "blobs"),
            postgres=database_url.startswith("postgresql"),
        )

        await _sync(driver)

        assert (await _source_state(source_id))["consecutive_errors"] == expected_errors
        async with workspace_tx() as connection:
            claim = (
                await connection.execute(
                    sa.select(tables.source.c.claimed_by).where(tables.source.c.id == source_id)
                )
            ).scalar_one()
        assert claim is None  # the lease is still released on both paths
        assert await _claims(driver) == (source_id,)  # the request stands: due again immediately


async def test_an_undisturbed_sync_still_reschedules_itself(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """The other polarity of the same writer: with no request landing under the claim, a completed
    sync pushes `next_sync_at` out to its own interval and a failed one to its backoff, so the
    driver does not re-claim a source it just finished."""
    workspace_id = await _workspace()
    for outcome in (SyncResult(pages=()), RuntimeError("provider 500")):
        await _seed_scripted_source(workspace_id, None)
        driver, _ = _scripted_driver([outcome], database_url, tmp_path / "blobs")

        await _sync(driver)

        assert await _claims(driver) == ()  # rescheduled out to its own interval or backoff


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
        body="the launch window opens at dawn",
        stream="docs",
        title="Launch window",
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
    db: None, database_url: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A `snapshot=True` fetch is an authoritative full collection: a prior page the fetch no longer
    holds is swept to a tombstone, while a page still present stays active. The sweep names no
    delete refs of its own, so what the run reports it tombstoned is counted off the write."""
    workspace_id = await _workspace()
    source_id = await _seed_scripted_source(workspace_id, None)
    gone_id = await _seed_prior_page(workspace_id, source_id, "gone/doc")
    kept = Page(
        source_ref="kept/doc",
        body="the mascot is a friendly otter named pip",
        stream="docs",
        title="Mascot",
    )
    kept_id = page_id_for(source_id, "kept/doc")
    driver, _ = _scripted_driver(
        [SyncResult(pages=(kept,), snapshot=True)], database_url, tmp_path / "blobs"
    )

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)
    assert await _tombstone(gone_id) is True  # absent from the authoritative snapshot → swept
    assert await _tombstone(kept_id) is False
    synced = _events(caplog, "source_sync.ok")[0]
    assert (synced.ufo["pages_fetched"], synced.ufo["pages_written"]) == (1, 1)
    assert synced.ufo["pages_tombstoned"] == 1  # the swept page, named by no delete ref


async def test_stream_skipped_records_a_skip_not_a_failure_and_never_tombstones(
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = await _workspace()
    skipped_id = await _seed_scripted_source(workspace_id, "held-cursor")
    kept_id = await _seed_prior_page(workspace_id, skipped_id, "kept/doc")
    good = tmp_path / "good"
    good.mkdir()
    (good / "doc.md").write_text("the wifi password is maple syrup")
    missing = tmp_path / "missing"
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
    real_log = sync.log

    def _log_gone(event: str, **fields: object) -> None:
        real_log(event, **fields)
        raise RuntimeError("log pipeline unreachable")

    monkeypatch.setattr(sync, "log", _log_gone)

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    skips = _events(caplog, "source_sync.skipped")
    assert len(skips) == 1
    assert skips[0].ufo == {
        "workspace_id": str(workspace_id),
        "source_id": str(skipped_id),
        "provider": SCRIPTED_BACKEND,
        "stream": "",
        "reason": "github: org scope not granted (403)",
    }
    skip_state = await _source_state(skipped_id)
    assert skip_state["consecutive_errors"] == 0
    assert skip_state["cursor"] == "held-cursor"
    assert skip_state["next_sync_at"] > baseline
    assert await _tombstone(kept_id) is False

    failed_id = source_row_id(workspace_id, FOLDER_BACKEND, {"root": str(missing)})
    assert (await _source_state(failed_id))["consecutive_errors"] == 1

    active = [page for page in await _pages() if page["tombstone"] in (False, 0)]
    assert len(active) == 2


CONNECTOR_PROVIDER = "slack"
CONNECTOR_STREAM = "messages"
CONNECTOR_ACCOUNT = "ca_T0ACME"
SYNC_METRIC = f"ufo.{SOURCE_SYNC_FAILED_METRIC}"


class _ConnectorSource:
    """The scripted backend under a connector's config shape: one provider stream for one account,
    which is what a registered Slack or GitHub source row is."""

    config_model: ClassVar[type[ConnectorSourceConfig]] = ConnectorSourceConfig

    def __init__(self, outcomes: list[SyncResult | Exception]) -> None:
        self._outcomes = outcomes

    async def fetch(
        self, config: ConnectorSourceConfig, cursor: str | None, auth: SourceAuth
    ) -> SyncResult:
        outcome = self._outcomes.pop(0)
        match outcome:
            case Exception():
                raise outcome
            case _:
                return outcome


class _NeverOpens:
    """A transaction that never opens, the way one fails against a pool with nothing left."""

    async def __aenter__(self) -> object:
        raise TimeoutError("connection pool exhausted")

    async def __aexit__(self, *_: object) -> None: ...


async def _seed_connector_source(workspace_id: UUID) -> UUID:
    source_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.source).values(
                id=source_id,
                workspace_id=workspace_id,
                backend=CONNECTOR_PROVIDER,
                config={"account": CONNECTOR_ACCOUNT, "stream": CONNECTOR_STREAM},
                cursor=None,
                next_sync_at=sa.func.now(),
                claimed_by=None,
                claim_expires_at=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return source_id


def _connector_driver(
    outcomes: list[SyncResult | Exception], database_url: str, blob_root: Path
) -> SyncDriver:
    return SyncDriver(
        backends={CONNECTOR_PROVIDER: _ConnectorSource(outcomes)},
        blob=FilesystemBlobStore(root=blob_root),
        postgres=database_url.startswith("postgresql"),
    )


def _meter(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(o11y.metrics, "get_meter", provider.get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    return reader


def _metric_points(reader: InMemoryMetricReader, name: str) -> list[dict[str, str]]:
    """A reader that collected nothing returns no data at all rather than an empty envelope, and
    whether this one collected anything depends on which instruments were already bound to an
    earlier provider when the test started — `db_tx_acquire_ms` fires on every transaction. So the
    absence of a metric has to read the same as the absence of every metric."""
    collected = reader.get_metrics_data()
    if collected is None:
        return []
    return [
        dict(point.attributes or {})
        for resource in collected.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


def _events(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.message == event]


def _utc(when: datetime) -> datetime:
    return when if when.tzinfo is not None else when.replace(tzinfo=UTC)


async def test_a_failed_stream_reports_its_provider_stream_and_cause(
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The event names the provider stream, the account, the class that raised, how many runs in a
    row have failed and when the backoff lets the next one start — the same numbers the row lands on
    — at error severity, where a sweep for failures looks. The counter carries provider and stream,
    so an alert points at the source rather than at whatever the failure exhausted."""
    reader = _meter(monkeypatch)
    workspace_id = await _workspace()
    source_id = await _seed_connector_source(workspace_id)
    driver = _connector_driver(
        [RuntimeError("provider 500: conversations.history")], database_url, tmp_path / "blobs"
    )

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    state = await _source_state(source_id)
    assert state["consecutive_errors"] == 1
    failures = _events(caplog, "source_sync.failed")
    assert len(failures) == 1
    assert failures[0].levelno == logging.ERROR
    assert failures[0].ufo == {
        "workspace_id": str(workspace_id),
        "source_id": str(source_id),
        "provider": CONNECTOR_PROVIDER,
        "stream": CONNECTOR_STREAM,
        "account_id": CONNECTOR_ACCOUNT,
        "error_class": "RuntimeError",
        "provider_fault": "",
        "consecutive_errors": 1,
        "next_sync_at": _utc(state["next_sync_at"]).isoformat(),
        "cursor_reset": False,
    }
    assert _metric_points(reader, SYNC_METRIC) == [
        {
            "provider": CONNECTOR_PROVIDER,
            "stream": CONNECTOR_STREAM,
            "error_class": "RuntimeError",
        }
    ]


async def test_a_stream_whose_transaction_never_opened_still_reports_the_failure(
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pool-exhaustion shape: every transaction after the claim times out, so the release that
    would have counted the error cannot write either and the run dies with the error. The event is
    emitted before that release, so the stream, its provider and the `TimeoutError` are on record
    even though the row itself never took the error."""
    workspace_id = await _workspace()
    source_id = await _seed_connector_source(workspace_id)
    driver = _connector_driver([SyncResult(pages=())], database_url, tmp_path / "blobs")
    opened = 0
    real_tx = sync.workspace_tx

    def _only_the_claim_opens() -> object:
        nonlocal opened
        opened += 1
        return real_tx() if opened == 1 else _NeverOpens()

    monkeypatch.setattr(sync, "workspace_tx", _only_the_claim_opens)

    with caplog.at_level(logging.INFO, logger="ufo"), pytest.raises(TimeoutError):
        await _sync(driver)

    monkeypatch.undo()
    failures = _events(caplog, "source_sync.failed")
    assert [
        (record.ufo["provider"], record.ufo["stream"], record.ufo["error_class"])
        for record in failures
    ] == [(CONNECTOR_PROVIDER, CONNECTOR_STREAM, "TimeoutError")]
    assert failures[0].ufo["consecutive_errors"] == 1
    assert (await _source_state(source_id))["consecutive_errors"] == 0


async def test_a_synced_stream_reports_what_it_wrote(
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = _meter(monkeypatch)
    workspace_id = await _workspace()
    source_id = await _seed_connector_source(workspace_id)
    gone_id = await _seed_prior_page(workspace_id, source_id, "C1/1600000000.1")
    page = Page(
        source_ref="C1/1700000000.1",
        body="the deploy is green",
        stream=CONNECTOR_STREAM,
        title="#general",
    )
    driver = _connector_driver(
        [
            SyncResult(pages=(page,), deletes=("C1/1600000000.1", "C1/1500000000.1")),
            SyncResult(pages=(page.model_copy(update={"title": "#deploys"}),)),
        ],
        database_url,
        tmp_path / "blobs",
    )

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)
        await _make_due()
        await _sync(driver)

    synced = _events(caplog, "source_sync.ok")
    assert [record.levelno for record in synced] == [logging.INFO, logging.INFO]
    assert synced[0].ufo == {
        "workspace_id": str(workspace_id),
        "source_id": str(source_id),
        "provider": CONNECTOR_PROVIDER,
        "stream": CONNECTOR_STREAM,
        "account_id": CONNECTOR_ACCOUNT,
        "pages_fetched": 1,
        "pages_written": 1,
        "pages_tombstoned": 1,
    }
    assert await _tombstone(gone_id) is True
    assert (synced[1].ufo["pages_written"], synced[1].ufo["pages_tombstoned"]) == (1, 0)
    assert not _events(caplog, "source_sync.failed")
    assert not _metric_points(reader, SYNC_METRIC)


async def test_a_provider_fault_reports_its_status_and_url_and_never_its_body_or_a_key(
    db: None,
    database_url: str,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The record an operator reads must not be where a secret lands. A status error's message is
    built out of the provider's response body, and a header a protocol rejects is quoted raw — the
    key a member pasted with a trailing newline — so neither message reaches the pipeline: the fault
    renders as the status and URL of the request that drew it, query dropped, and any other class is
    named by `error_class` alone. On the counter a status is a class of its own, not a bucket it
    shares with an extension's crash."""
    reader = _meter(monkeypatch)
    workspace_id = await _workspace()
    await _seed_connector_source(workspace_id)
    request = httpx.Request(
        "GET", "https://slack.com/api/conversations.history?token=xoxb-SUPERSECRET&limit=200"
    )
    body = '{"ok":false,"error":"invalid_auth","provided":"xoxb-SUPERSECRET"}'
    with pytest.raises(httpx.HTTPStatusError) as raised:
        rest._raise_for_status(httpx.Response(401, text=body, request=request))
    rejected_header = httpx.LocalProtocolError("Illegal header value b'Bearer xoxb-SUPERSECRET\\n'")
    driver = _connector_driver([raised.value, rejected_header], database_url, tmp_path / "blobs")

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)
        await _make_due()
        await _sync(driver)

    failures = _events(caplog, "source_sync.failed")
    assert [(record.ufo["error_class"], record.ufo["provider_fault"]) for record in failures] == [
        ("HTTPStatusError", "401 GET https://slack.com/api/conversations.history"),
        ("LocalProtocolError", ""),
    ]
    assert not [record for record in failures if "SUPERSECRET" in str(record.ufo)]
    assert _metric_points(reader, SYNC_METRIC) == [
        {
            "provider": CONNECTOR_PROVIDER,
            "stream": CONNECTOR_STREAM,
            "error_class": "HTTPStatusError",
        },
        {
            "provider": CONNECTOR_PROVIDER,
            "stream": CONNECTOR_STREAM,
            "error_class": "LocalProtocolError",
        },
    ]


async def test_a_stream_fault_reports_the_reason_the_backend_authored_for_it(
    db: None, database_url: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A backend that cannot read what the provider answered knows the request it made and the shape
    that broke, and nothing else knows either — so `StreamFault` carries that reason onto the event
    and a sweep reads which read failed instead of the class alone, which is all a bare `ValueError`
    ever left behind."""
    workspace_id = await _workspace()
    await _seed_connector_source(workspace_id)
    reason = "googlesheets: values:batchGet on spreadsheet s1 asked for 2 ranges and returned 1"
    driver = _connector_driver([StreamFault(reason)], database_url, tmp_path / "blobs")

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    failure = _events(caplog, "source_sync.failed")[0]
    assert (failure.ufo["error_class"], failure.ufo["provider_fault"]) == ("StreamFault", reason)


async def test_a_provider_fault_is_bounded_before_it_reaches_the_record(
    db: None, database_url: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The URL is the provider's, not ours: a path built out of ids and filters runs as long as the
    provider cares to make it, so the rendering is capped where it is emitted."""
    workspace_id = await _workspace()
    await _seed_connector_source(workspace_id)
    request = httpx.Request(
        "GET", "https://slack.com/api/" + "c" * (SYNC_PROVIDER_FAULT_MAX_CHARS * 2)
    )
    with pytest.raises(httpx.HTTPStatusError) as raised:
        rest._raise_for_status(httpx.Response(429, text="ratelimited", request=request))
    driver = _connector_driver([raised.value], database_url, tmp_path / "blobs")

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _sync(driver)

    fault = _events(caplog, "source_sync.failed")[0].ufo["provider_fault"]
    assert fault.startswith("429 GET https://slack.com/api/c")
    assert len(fault) == SYNC_PROVIDER_FAULT_MAX_CHARS


async def test_telemetry_that_raises_never_breaks_the_sync_run(
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = await _workspace()
    failing_id = await _seed_connector_source(workspace_id)
    good = tmp_path / "good"
    good.mkdir()
    (good / "doc.md").write_text("the wifi password is maple syrup")
    driver = SyncDriver(
        backends={
            CONNECTOR_PROVIDER: _ConnectorSource([RuntimeError("provider 500")]),
            FOLDER_BACKEND: FolderSource(),
        },
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
    )
    await _register_folder(good)

    def _collector_gone(*_: object, **__: object) -> None:
        raise RuntimeError("collector unreachable")

    failure: list[tuple[object, object, object]] = []
    success: list[tuple[object, object, object]] = []

    def _log_error_gone(event: str, **fields: object) -> None:
        assert event == "source_sync.failed"
        failure.append((fields["provider"], fields["stream"], fields["error_class"]))
        raise RuntimeError("log pipeline unreachable")

    def _log_gone(event: str, **fields: object) -> None:
        assert event == "source_sync.ok"
        success.append((fields["provider"], fields["stream"], fields["pages_written"]))
        raise RuntimeError("log pipeline unreachable")

    monkeypatch.setattr(sync, "emit_metric", _collector_gone)
    monkeypatch.setattr(sync, "log_error", _log_error_gone)
    monkeypatch.setattr(sync, "log", _log_gone)

    await _sync(driver)

    assert (await _source_state(failing_id))["consecutive_errors"] == 1
    assert len(await _pages()) == 1
    assert await _claims(driver) == ()
    assert failure == [(CONNECTOR_PROVIDER, CONNECTOR_STREAM, "RuntimeError")]
    assert success == [(FOLDER_BACKEND, "", 1)]


def test_source_sync_and_turn_dispatch_register_as_core_jobs(
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
        TurnDispatcher(client=None),
        runner,
    )
    assert [spec.name for spec in specs] == [SOURCE_SYNC_JOB, TURN_DISPATCH_JOB]
    assert all(spec.schedule is not None for spec in specs)
    keys = {binding.key for binding in bindings_from((), specs)}
    assert keys == {
        f"{CORE_EXTENSION}:{SOURCE_SYNC_JOB}",
        f"{CORE_EXTENSION}:{TURN_DISPATCH_JOB}",
    }
