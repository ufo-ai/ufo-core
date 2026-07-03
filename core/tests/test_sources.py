from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from selfhost.blob import FilesystemBlobStore
from selfhost.config import SourceConfig, SourceEntry
from selfhost.db import workspace_tx
from selfhost.jobs import (
    CORE_EXTENSION,
    MEMORY_INDEX_JOB,
    PAGE_INDEX_JOB,
    bindings_from,
    core_jobs,
)
from selfhost.memory.chunk import Chunk, TextChunker
from selfhost.memory.embed import EMBED_DIM
from selfhost.memory.index import index_backend_for
from selfhost.memory.indexer import MemoryIndexer, PageIndexer
from selfhost.memory.service import (
    OWNER_KIND_PAGE,
    SHARED_SUBJECT,
    MemoryService,
    member_subject,
    recall_subjects,
)
from selfhost.memory.sources import (
    FOLDER_BACKEND,
    SOURCE_SYNC_JOB,
    FolderSource,
    SyncDriver,
    register_sources,
)
from selfhost.schema import tables
from selfhost.schema.records import Agent, Turn
from selfhost.tools.builtins import BUILTIN_TOOLS
from selfhost.tools.context import SpawnResult, ToolContext, ToolResult
from selfhost.tools.registry import ToolRegistry

REGISTRY = ToolRegistry(BUILTIN_TOOLS)


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


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in the source tests")


@pytest.fixture
async def clean(db: None, database_url: str) -> AsyncIterator[None]:
    async with workspace_tx() as connection:
        await connection.execute(sa.text("delete from chunk"))
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
    database_url: str, vector: tuple[float, ...], blob_root: Path
) -> tuple[SyncDriver, PageIndexer, MemoryService]:
    embed = StubEmbed(vector)
    index = index_backend_for(database_url, embed)
    blob = FilesystemBlobStore(root=blob_root)
    driver = SyncDriver(
        backends={FOLDER_BACKEND: FolderSource()},
        blob=blob,
        postgres=database_url.startswith("postgresql"),
    )
    page_indexer = PageIndexer(index=index, embed=embed, chunker=TextChunker(), blob=blob)
    service = MemoryService(index=index, embed=embed)
    return driver, page_indexer, service


async def _register_folder(root: Path) -> None:
    await register_sources(
        (SourceEntry(backend=FOLDER_BACKEND, config=SourceConfig(root=str(root))),)
    )


async def _pages() -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(
                        tables.page.c.subject,
                        tables.page.c.digest,
                        tables.page.c.body_ref,
                        tables.page.c.embedding_digest,
                        tables.page.c.tombstone,
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


def _context(memory: MemoryService, member_id: UUID | None, blob_root: Path) -> ToolContext:
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
        memory=memory,
        member_id=member_id,
        artifact_token_secret="",
    )


async def _search(
    memory: MemoryService, member_id: UUID | None, blob_root: Path, query: str
) -> str:
    tool = REGISTRY.get("memory_search")
    result: ToolResult = await tool.handler(
        _context(memory, member_id, blob_root), tool.input_model.model_validate({"query": query})
    )
    return result.content[0].text


async def test_folder_syncs_a_page_body_to_blob_no_chunk_until_indexed(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "brief.md").write_text("the quarterly revenue target is twelve million dollars")
    driver, page_indexer, _ = _wire(database_url, vec((7, 1.0)), tmp_path / "blobs")
    await _register_folder(root)

    await driver.run()
    pages = await _pages()
    assert len(pages) == 1
    assert pages[0]["subject"] == SHARED_SUBJECT
    assert pages[0]["digest"].startswith("sha256:")
    assert pages[0]["embedding_digest"] is None
    assert pages[0]["tombstone"] is False or pages[0]["tombstone"] == 0
    body = await FilesystemBlobStore(root=tmp_path / "blobs").get(pages[0]["body_ref"])
    assert b"quarterly revenue" in body
    assert await _chunk_count() == 0

    await page_indexer.run()
    assert await _chunk_count() >= 1
    assert (await _pages())[0]["embedding_digest"] is not None


async def test_synced_page_content_is_found_via_memory_search(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "wiki.md").write_text("the office fire assembly point is the north car park")
    driver, page_indexer, service = _wire(database_url, vec((8, 1.0)), tmp_path / "blobs")
    await _register_folder(root)
    await driver.run()
    await page_indexer.run()

    found = await _search(service, uuid4(), tmp_path / "blobs", "fire assembly point")
    assert "[source]" in found
    assert "north car park" in found


async def test_unchanged_doc_resync_does_not_reindex_or_duplicate(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "note.md").write_text("the mascot is a friendly otter named pip")
    driver, page_indexer, _ = _wire(database_url, vec((9, 1.0)), tmp_path / "blobs")
    await _register_folder(root)
    await driver.run()
    await page_indexer.run()
    stamped = (await _pages())[0]["embedding_digest"]
    chunks = await _chunk_count()

    await _make_due()
    await driver.run()
    resynced = await _pages()
    assert len(resynced) == 1
    assert resynced[0]["embedding_digest"] == stamped
    await page_indexer.run()
    assert await _chunk_count() == chunks


async def test_changed_doc_resync_marks_due_and_reindexes(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    doc = root / "spec.md"
    doc.write_text("the release date is friday")
    driver, page_indexer, service = _wire(database_url, vec((10, 1.0)), tmp_path / "blobs")
    await _register_folder(root)
    await driver.run()
    await page_indexer.run()
    first_digest = (await _pages())[0]["digest"]

    doc.write_text("the release date is monday")
    await _make_due()
    await driver.run()
    pages = await _pages()
    assert len(pages) == 1
    assert pages[0]["digest"] != first_digest
    assert pages[0]["embedding_digest"] is None
    await page_indexer.run()
    found = await _search(service, uuid4(), tmp_path / "blobs", "release date monday")
    assert "monday" in found


async def test_removed_file_tombstones_page_and_index_drops_its_chunks(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "keep.md").write_text("penguins huddle for warmth in antarctica")
    gone = root / "gone.md"
    gone.write_text("volcano magma chamber pressure readings")
    driver, page_indexer, service = _wire(database_url, vec((11, 1.0)), tmp_path / "blobs")
    await _register_folder(root)
    await driver.run()
    await page_indexer.run()
    assert "magma" in await _search(service, uuid4(), tmp_path / "blobs", "volcano magma chamber")

    gone.unlink()
    await _make_due()
    await driver.run()
    tombstoned = [p for p in await _pages() if p["tombstone"] not in (False, 0)]
    assert len(tombstoned) == 1
    await page_indexer.run()

    blobs = tmp_path / "blobs"
    assert "magma" not in await _search(service, uuid4(), blobs, "volcano magma chamber")
    assert "penguins" in await _search(service, uuid4(), blobs, "penguins antarctica")


async def test_shared_page_scoping_excludes_a_member_only_search(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "policy.md").write_text("expense reports are due on the last business day")
    driver, page_indexer, service = _wire(database_url, vec((12, 1.0)), tmp_path / "blobs")
    await _register_folder(root)
    await driver.run()
    await page_indexer.run()

    shared = await service.search_sources("expense reports due", frozenset({SHARED_SUBJECT}), 8)
    assert len(shared) == 1 and "expense reports" in shared[0].text

    member_only = await service.search_sources(
        "expense reports due", frozenset({member_subject(uuid4())}), 8
    )
    assert member_only == ()

    with_shared = await service.search_sources(
        "expense reports due", recall_subjects(uuid4()), 8
    )
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
                embedding_digest="sha256:seed",
                tombstone=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    _, _, service = _wire(database_url, probe, tmp_path / "blobs")
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
    await _workspace()
    good = tmp_path / "good"
    good.mkdir()
    (good / "doc.md").write_text("the wifi password is maple syrup")
    missing = tmp_path / "missing"  # never created → FolderSource._read raises FileNotFoundError
    driver, _, _ = _wire(database_url, vec((14, 1.0)), tmp_path / "blobs")
    await _register_folder(good)
    await _register_folder(missing)

    async def _row(root: Path) -> sa.RowMapping:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.source.c.config,
                        tables.source.c.claimed_by,
                        tables.source.c.next_sync_at,
                    )
                )
            ).mappings().all()
        return next(row for row in rows if row["config"]["root"] == str(root))

    before = (await _row(missing))["next_sync_at"]

    await driver.run()

    assert len(await _pages()) == 1  # the good source synced despite the bad sibling
    good_row, missing_row = await _row(good), await _row(missing)
    assert good_row["claimed_by"] is None
    assert missing_row["claimed_by"] is None  # released, not stuck claimed
    assert missing_row["next_sync_at"] > before  # backed off, won't re-fail every lease


def test_sync_and_page_index_register_as_core_jobs(database_url: str, tmp_path: Path) -> None:
    driver, page_indexer, service = _wire(database_url, (), tmp_path / "blobs")
    memory_indexer = MemoryIndexer(
        index=service.index, embed=service.embed, chunker=TextChunker()
    )
    specs = core_jobs(memory_indexer, page_indexer, driver)
    assert [spec.name for spec in specs] == [MEMORY_INDEX_JOB, PAGE_INDEX_JOB, SOURCE_SYNC_JOB]
    assert all(spec.schedule is not None for spec in specs)
    keys = {binding.key for binding in bindings_from((), specs)}
    assert keys == {
        f"{CORE_EXTENSION}:{MEMORY_INDEX_JOB}",
        f"{CORE_EXTENSION}:{PAGE_INDEX_JOB}",
        f"{CORE_EXTENSION}:{SOURCE_SYNC_JOB}",
    }
