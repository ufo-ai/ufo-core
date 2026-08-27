"""The memory extension's derivation job: commit stays chunk-free until MemoryIndexer runs.

The indexer, the store, and the memory-index JobSpec are the extension's; they are driven here over
the real DefaultIndex and the workspace-scoped transaction, exactly as core threads them onto the
job's context. The embed client is a real dependency counted (never asserted) to witness that
overlapping runs embed each row once."""

import asyncio
from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

import sqlalchemy as sa
import ufo_ext_memory.manifest as memory_manifest
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import (
    MemoryIndexer,
    MemoryStore,
    MemoryWrite,
    memory_item,
    recall_subjects,
)

from ufo.db import workspace_tx
from ufo.ext.context import PageState, SourceReader, context_for
from ufo.indexing import OWNER_KIND_MEMORY_ITEM, TextChunker
from ufo.runtime.jobs import CORE_EXTENSION, JobRunner, bindings_from
from ufo.schema import tables
from ufo.sdk.audience import conversation_audience
from ufo.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.workspace import ws


def _reader(subjects: frozenset[str]) -> SourceReader:
    return SourceReader(agent_id=uuid4(), requesting_member_id=None, subjects=subjects)


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
                body_ref=f"pages/{self.page_id}",
                title="Q3 pricing rollout",
                stream="pull_requests",
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
    index = DefaultIndex(transaction=workspace_tx)
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


async def test_overlapping_index_runs_embed_each_row_once(db: None) -> None:
    workspace_id = await _workspace()
    embed = CountingEmbed(vec((0, 1.0)))
    store, _ = _wire(embed, workspace_id)
    bodies = tuple(f"fact number {n} worth remembering" for n in range(6))
    for body in bodies:
        await store.commit(MemoryWrite(subject="shared", body=body))

    runs = tuple(
        MemoryIndexer(
            index=DefaultIndex(transaction=workspace_tx),
            embed=embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            page_states=context_for("memory", frozenset()).page_states,
        )
        for _ in range(2)
    )
    with ws(workspace_id):
        await asyncio.gather(*(indexer.run() for indexer in runs))

    assert embed.calls == len(bodies)
    async with workspace_tx() as connection:
        pending = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(memory_item)
                .where(memory_item.c.embedding_digest.is_(None))
            )
        ).scalar_one()
    assert pending == 0


async def test_page_narrowed_during_embed_withdraws_the_write_and_keeps_the_row(
    db: None,
) -> None:
    """A page that narrows while the indexer embeds must not leave the wider subject's chunks
    published — but the row itself is the fact deriver's to retire, so the indexer withdraws only
    what it wrote and leaves the item intact and still due."""
    workspace_id = await _workspace()
    page_id = uuid4()
    source_id = uuid4()
    embed = StubEmbed(vec((3, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
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
                    memory_item.c.created_from_page_id == page_id
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
    index = DefaultIndex(transaction=workspace_tx)

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


async def test_committed_fact_recalls_after_indexing(db: None) -> None:
    workspace_id = await _workspace()
    probe = vec((4, 1.0))
    store, indexer = _wire(StubEmbed(probe), workspace_id)
    await store.commit(MemoryWrite(subject="shared", body="the mascot is named zoltar"))
    with ws(workspace_id):
        await indexer.run()
        hits = await store.recall(
            "zoltar mascot",
            frozenset({"shared"}),
            5,
            source_reader=_reader(frozenset({SHARED_SUBJECT})),
        )
    assert len(hits) == 1
    assert "zoltar" in hits[0].body


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
    """The memory-index job runs through the real dispatch: its candidate names only the workspaces
    holding an un-indexed memory_item (read through the RLS-bypass path, never `ws_current`, so no
    WorkspaceUnbound), and `fire` binds each before the indexer derives its chunks — so the job runs
    scoped to that workspace as the fleet fires it. A workspace with nothing pending is not a
    candidate and is never opened."""
    embed = StubEmbed(vec((0, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
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


def test_memory_index_registers_as_an_extension_job() -> None:
    manifest = memory_manifest.manifest()
    job = next(job for job in manifest.jobs if job.name == memory_manifest.MEMORY_INDEX_JOB)
    assert job.schedule == memory_manifest.MEMORY_INDEX_SCHEDULE
    bindings = bindings_from((manifest,), ())
    keys = {binding.key for binding in bindings}
    assert f"{manifest.name}:{memory_manifest.MEMORY_INDEX_JOB}" in keys
    assert f"{CORE_EXTENSION}:{memory_manifest.MEMORY_INDEX_JOB}" not in keys
