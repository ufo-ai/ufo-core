from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from selfhost.db import workspace_tx
from selfhost.jobs import (
    CORE_EXTENSION,
    MEMORY_INDEX_JOB,
    MEMORY_INDEX_SCHEDULE,
    bindings_from,
    core_jobs,
)
from selfhost.memory.chunk import TextChunker
from selfhost.memory.embed import EMBED_DIM
from selfhost.memory.index import index_backend_for
from selfhost.memory.indexer import MemoryIndexer
from selfhost.memory.service import MemoryService, member_subject, recall_subjects
from selfhost.schema import tables
from selfhost.schema.records import MemoryWrite


def vec(*axes: tuple[int, float]) -> tuple[float, ...]:
    values = [0.0] * EMBED_DIM
    for index, value in axes:
        values[index] = value
    return tuple(values)


class StubEmbed:
    """Deterministic stand-in EmbedClient the indexer embeds through and recall queries through; the
    tests assert the derived chunk rows and the Recalled items, never this stand-in."""

    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector for _ in texts)


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


def _wire(database_url: str, vector: tuple[float, ...]) -> tuple[MemoryService, MemoryIndexer]:
    embed = StubEmbed(vector)
    index = index_backend_for(database_url, embed)
    service = MemoryService(index=index, embed=embed)
    indexer = MemoryIndexer(index=index, embed=embed, chunker=TextChunker())
    return service, indexer


async def _chunk_count() -> int:
    async with workspace_tx() as connection:
        return (await connection.execute(sa.text("select count(*) from chunk"))).scalar_one()


async def test_index_job_derives_chunks_and_stamps_digest(clean: None, database_url: str) -> None:
    await _workspace()
    service, indexer = _wire(database_url, vec((0, 1.0)))
    await service.commit(MemoryWrite(subject="shared", body="the capital of france is paris"))
    assert await _chunk_count() == 0

    await indexer.run()
    derived = await _chunk_count()
    assert derived >= 1
    async with workspace_tx() as connection:
        digest = (
            await connection.execute(sa.select(tables.memory_item.c.embedding_digest))
        ).scalar_one()
    assert digest is not None and digest.startswith("sha256:")

    await indexer.run()
    assert await _chunk_count() == derived


async def test_committed_fact_recalls_after_indexing(clean: None, database_url: str) -> None:
    await _workspace()
    probe = vec((4, 1.0))
    service, indexer = _wire(database_url, probe)
    await service.commit(MemoryWrite(subject="shared", body="the mascot is named zoltar"))
    await indexer.run()

    hits = await service.recall("zoltar mascot", frozenset({"shared"}), 5)
    assert len(hits) == 1
    assert "zoltar" in hits[0].body


async def test_member_memory_is_invisible_to_another_member(clean: None, database_url: str) -> None:
    await _workspace()
    alice, bob = uuid4(), uuid4()
    probe = vec((5, 1.0))
    service, indexer = _wire(database_url, probe)
    await service.commit(
        MemoryWrite(subject=member_subject(alice), body="alice prefers a window seat")
    )
    await indexer.run()

    assert await service.recall("window seat", recall_subjects(bob), 5) == ()
    mine = await service.recall("window seat", recall_subjects(alice), 5)
    assert len(mine) == 1 and "alice" in mine[0].body


def test_memory_index_registers_as_a_core_job(database_url: str) -> None:
    embed = StubEmbed(())
    indexer = MemoryIndexer(
        index=index_backend_for(database_url, embed), embed=embed, chunker=TextChunker()
    )
    specs = core_jobs(indexer)
    assert [spec.name for spec in specs] == [MEMORY_INDEX_JOB]
    assert specs[0].schedule == MEMORY_INDEX_SCHEDULE
    bindings = bindings_from((), specs)
    key = f"{CORE_EXTENSION}:{MEMORY_INDEX_JOB}"
    assert [(b.key, b.extension, b.declared) for b in bindings] == [
        (key, CORE_EXTENSION, frozenset())
    ]
