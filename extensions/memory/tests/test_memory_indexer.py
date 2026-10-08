"""The memory extension's derivation job: commit stays chunk-free until MemoryIndexer runs.

The indexer, the store, and the memory-index JobSpec are the extension's; they are driven here over
the real DefaultIndex and the workspace-scoped transaction, exactly as core threads them onto the
job's context. The embed client is a real dependency counted (never asserted) to witness that
overlapping runs embed each row once."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_memory.manifest as memory_manifest
from ufo_ext_memory.store import (
    MemoryIndexer,
    MemoryStore,
    MemoryWrite,
    memory_item,
    recall_subjects,
)
from ufo_testsupport.index import StubEmbed, default_index, vec

from ufo.db import workspace_tx
from ufo.runtime.ext.context import PageState, context_for
from ufo.runtime.ext.source_reader import SourceReader
from ufo.runtime.indexing import OWNER_KIND_MEMORY_ITEM, TextChunker
from ufo.runtime.jobs import JobRunner, bindings_from
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.sdk.audience import conversation_audience

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]


def _reader(subjects: frozenset[str]) -> SourceReader:
    return SourceReader(agent_id=uuid4(), requesting_member_id=None, subjects=subjects)


class CountingEmbed:
    """Counts embed calls: a double-embed is wasted model spend that the idempotent chunk upsert
    hides, so the call count is the only witness that overlapping runs embed each row once."""

    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector
        self.calls = 0

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls += 1
        return tuple(self._vector for _ in texts)


class CallbackEmbed:
    def __init__(self, vector: tuple[float, ...], callback: Callable[[], Awaitable[None]]) -> None:
        self._vector = vector
        self._callback = callback

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        await self._callback()
        return tuple(self._vector for _ in texts)


class ReclassifyingPage:
    def __init__(
        self,
        page_id: UUID,
        before: str,
        after: str,
        before_revision: int = 1,
        after_revision: int = 1,
    ) -> None:
        self.page_id = page_id
        self.before = before
        self.after = after
        self.before_revision = before_revision
        self.after_revision = after_revision
        self.calls = 0

    async def __call__(self, page_ids: tuple[UUID, ...]) -> dict[UUID, PageState]:
        self.calls += 1
        return {
            self.page_id: PageState(
                subject=self.before if self.calls == 1 else self.after,
                revision=self.before_revision if self.calls == 1 else self.after_revision,
                digest="sha256:test",
                title="Q3 pricing rollout",
                stream="pull_requests",
                indexed=True,
                as_of="2026-03-01",
                backend="github",
                source_id=UUID(int=1),
                connection_id=UUID(int=2),
                created_at=datetime(2026, 3, 1, tzinfo=UTC),
            )
        }


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


def _wire(embed: object, workspace_id: UUID) -> tuple[MemoryStore, MemoryIndexer]:
    index = default_index()
    ext = context_for("memory", frozenset())
    store = MemoryStore(
        index=index,
        embed=embed,
        transaction=workspace_tx,
        workspace_id=workspace_id,
        page_states=ext.page_states,
        readable_page_states=ext.readable_page_states,
        readable_source_ids=ext.readable_source_ids,
    )
    indexer = MemoryIndexer(
        index=index,
        embed=embed,
        transaction=workspace_tx,
        chunker=TextChunker(),
        workspace_id=workspace_id,
        page_states=context_for("memory", frozenset()).page_states,
    )
    return store, indexer


async def _chunk_count() -> int:
    async with workspace_tx() as connection:
        return (await connection.execute(sa.text("select count(*) from chunk"))).scalar_one()


async def test_index_job_derives_chunks_and_stamps_digest(db: None) -> None:
    workspace_id = await _workspace()
    store, indexer = _wire(StubEmbed(vec((0, 1.0))), workspace_id)
    await store.commit(MemoryWrite(subject="shared", body="the capital of france is paris"))
    assert await _chunk_count() == 0

    with ws(workspace_id):
        await indexer.run()
    derived = await _chunk_count()
    assert derived >= 1
    async with workspace_tx() as connection:
        digest = (await connection.execute(sa.select(memory_item.c.embedding_digest))).scalar_one()
    assert digest is not None and digest.startswith("sha256:")

    with ws(workspace_id):
        await indexer.run()
    assert await _chunk_count() == derived


async def test_page_narrowed_during_embed_withdraws_the_write_and_keeps_the_row(
    db: None,
) -> None:
    workspace_id = await _workspace()
    page_id = uuid4()
    source_id = uuid4()
    embed = StubEmbed(vec((3, 1.0)))
    index = default_index()
    store = MemoryStore(
        index=index,
        embed=embed,
        transaction=workspace_tx,
        workspace_id=workspace_id,
        page_states=context_for("memory", frozenset()).page_states,
    )
    await store.commit(
        MemoryWrite(
            subject="shared",
            body="the stale launch codename is polaris",
            created_from_page_id=page_id,
            created_from_page_revision=1,
            source_id=source_id,
        )
    )
    with ws(workspace_id):
        await MemoryIndexer(
            index=index,
            embed=embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=ReclassifyingPage(
                page_id,
                "shared",
                f"member:{uuid4()}",
            ),
        ).run()
        assert (
            await index.lexical(
                "launch codename",
                frozenset({"shared"}),
                OWNER_KIND_MEMORY_ITEM,
                10,
            )
            == ()
        )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(memory_item.c.embedding_digest).where(
                    memory_item.c.created_from_page_uid == page_id
                )
            )
        ).one()
    assert row.embedding_digest is None


async def test_old_indexer_cannot_delete_a_same_body_fact_rebound_to_a_new_page_revision(
    db: None,
) -> None:
    workspace_id, page_id = await _workspace(), uuid4()
    source_id = uuid4()
    body = "the acquisition plan has been redacted"
    index = default_index()

    async def recommit() -> None:
        await store.commit(
            MemoryWrite(
                subject="shared",
                body=body,
                created_from_page_id=page_id,
                created_from_page_revision=2,
                source_id=source_id,
            )
        )

    embed = CallbackEmbed(vec((3, 1.0)), recommit)
    store = MemoryStore(
        index=index,
        embed=embed,
        transaction=workspace_tx,
        workspace_id=workspace_id,
        page_states=context_for("memory", frozenset()).page_states,
    )
    await store.commit(
        MemoryWrite(
            subject="shared",
            body=body,
            created_from_page_id=page_id,
            created_from_page_revision=1,
            source_id=source_id,
        )
    )
    with ws(workspace_id):
        await MemoryIndexer(
            index=index,
            embed=embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=workspace_id,
            page_states=ReclassifyingPage(
                page_id,
                "shared",
                "shared",
                before_revision=1,
                after_revision=2,
            ),
        ).run()
        assert (
            len(
                await index.lexical(
                    "acquisition plan", frozenset({"shared"}), OWNER_KIND_MEMORY_ITEM, 10
                )
            )
            == 1
        )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    memory_item.c.created_from_page_revision,
                    memory_item.c.embedding_claimed_at,
                )
            )
        ).one()
    assert row.created_from_page_revision == 2
    assert row.embedding_claimed_at is None


async def test_member_memory_is_invisible_to_another_member(db: None) -> None:
    workspace_id = await _workspace()
    alice, bob = uuid4(), uuid4()
    probe = vec((5, 1.0))
    store, indexer = _wire(StubEmbed(probe), workspace_id)
    await store.commit(
        MemoryWrite(subject=member_subject(alice), body="alice prefers a window seat")
    )
    with ws(workspace_id):
        await indexer.run()
        assert (
            await store.recall(
                "window seat",
                recall_subjects(conversation_audience(bob)),
                5,
                source_reader=_reader(frozenset({SHARED_SUBJECT})),
            )
            == ()
        )
        mine = await store.recall(
            "window seat",
            recall_subjects(conversation_audience(alice)),
            5,
            source_reader=_reader(frozenset({SHARED_SUBJECT})),
        )
    assert len(mine) == 1 and "alice" in mine[0].body


async def test_memory_index_job_fires_bound_only_on_workspaces_with_unindexed_items(
    db: None,
) -> None:
    embed = StubEmbed(vec((0, 1.0)))
    index = default_index()
    ws_with_work = await _workspace()
    ws_empty = await _workspace()
    with ws(ws_with_work):
        store = MemoryStore(
            index=index,
            embed=embed,
            transaction=workspace_tx,
            workspace_id=ws_with_work,
            page_states=context_for("memory", frozenset()).page_states,
        )
        await store.commit(MemoryWrite(subject="shared", body="the capital of france is paris"))

    manifest = memory_manifest.manifest()
    job = next(j for j in manifest.jobs if j.name == memory_manifest.MEMORY_INDEX_JOB)
    assert set(await job.candidates()) == {ws_with_work}

    runner = JobRunner(
        bindings=bindings_from((manifest,), ()),
        manifests=(manifest,),
        index=index,
        embed=embed,
    )
    index_key = f"{memory_manifest.NAME}:{memory_manifest.MEMORY_INDEX_JOB}"
    for workspace_id in await runner.candidates(index_key):
        await runner.fire(index_key, workspace_id)

    with ws(ws_with_work):
        async with workspace_tx() as connection:
            digest = (
                await connection.execute(sa.select(memory_item.c.embedding_digest))
            ).scalar_one()
    assert digest is not None and digest.startswith("sha256:")
    assert ws_empty not in set(await job.candidates())
