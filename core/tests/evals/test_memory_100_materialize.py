from collections.abc import AsyncIterator
from pathlib import Path, PurePosixPath
from uuid import uuid4, uuid5

import pytest
import sqlalchemy as sa
import ufo_ext_sample as sample
from pydantic import BaseModel
from ufo_ext_index_default import DefaultIndex, pack_embedding
from ufo_ext_memory import manifest as memory_manifest
from ufo_ext_memory.store import (
    MEMORY_BODY_MAX_CHARS,
    MEMORY_ITEM_NAMESPACE,
    MemoryStore,
    memory_item,
    recall_subjects,
)
from ufo_ext_sources import manifest as sources_manifest
from ufo_testsupport.migrations import apply_cached_migrations

from evals.memory_100.materialize import Memory100Materializer
from evals.memory_100.models import (
    SnapshotCase,
    SnapshotMemory,
    SnapshotPage,
    UpstreamAsset,
)
from evals.memory_100.snapshot import content_digest, load_snapshot, write_snapshot
from evals.memory_100.state import CorpusAttestor
from ufo.blob import FilesystemBlobStore
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.ext.context import ScopedStore, SourceReader, context_for
from ufo.sandbox.containment import ContainmentError
from ufo.schema import tables
from ufo.sources.sync import page_id_for
from ufo.turns.audience import conversation_audience
from ufo.turns.subjects import member_subject
from ufo.workspace import ws

PAGE_REF_SCALE = 12_500
ALICE_BODY = "Alice's private launch phrase is alpha lantern."


class DeterministicEmbed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, 0.0, 0.0) for _ in texts)


class SamplePageChangeRecord(BaseModel):
    page_ids: tuple[str, ...]
    model_wired: bool


@pytest.fixture
def memory_100_database_url(database_url: str, tmp_path: Path) -> str:
    if database_url.startswith("postgresql"):
        pytest.skip("memory_100 materialization proof uses SQLite's real default index")
    url = f"sqlite+aiosqlite:///{tmp_path / 'memory_100.db'}"
    apply_cached_migrations(url)
    return url


@pytest.fixture
async def memory_100_db(memory_100_database_url: str) -> AsyncIterator[None]:
    init_db(memory_100_database_url)
    try:
        yield
    finally:
        await dispose_db()


def _snapshot(root: Path, alice_body: str = ALICE_BODY) -> None:
    cases = (
        tuple(
            SnapshotCase(
                id=f"enterprise-{index}",
                corpus="enterprise",
                category="retrieval",
                audience="shared",
                question=f"enterprise question {index}",
                expected_answer="answer",
                evidence_refs=("drive/runbook.md",),
            )
            for index in range(60)
        )
        + tuple(
            SnapshotCase(
                id=f"longmem-{index}",
                corpus="longmem",
                category="session",
                audience="alice" if index % 2 == 0 else "bob",
                question=f"longmem question {index}",
                expected_answer="answer",
                evidence_refs=("session/alice",) if index % 2 == 0 else ("session/bob",),
            )
            for index in range(30)
        )
        + tuple(
            SnapshotCase(
                id=f"ufo-{index}",
                corpus="ufo",
                category="isolation",
                audience="alice",
                question=f"ufo question {index}",
                expected_answer="answer",
                evidence_refs=("session/alice",),
            )
            for index in range(12)
        )
    )
    pages = (
        SnapshotPage(
            source_ref="drive/runbook.md",
            audience="shared",
            body="The shared incident commander is Captain Vega.",
            digest=content_digest("The shared incident commander is Captain Vega."),
            origin="enterprise",
        ),
        SnapshotPage(
            source_ref="slack/launch.txt",
            audience="shared",
            body="Project Aurora launches on Thursday.",
            digest=content_digest("Project Aurora launches on Thursday."),
            origin="enterprise",
        ),
    )
    memories = (
        SnapshotMemory(
            source_ref="session/alice",
            audience="alice",
            body=alice_body,
            digest=content_digest(alice_body),
        ),
        SnapshotMemory(
            source_ref="session/bob",
            audience="bob",
            body="Bob's private launch phrase is beta harbor.",
            digest=content_digest("Bob's private launch phrase is beta harbor."),
        ),
    )
    write_snapshot(
        root,
        upstreams=(
            UpstreamAsset(
                name="fixture",
                url="https://example.com/fixture",
                revision="fixture-1",
                size_bytes=1,
                sha256="sha256:" + "0" * 64,
                license="MIT",
            ),
        ),
        builder_digest="sha256:" + "1" * 64,
        cases=cases,
        pages=pages,
        memories=memories,
    )


def test_page_ref_validation_scales_and_rejects_file_directory_collisions(
    tmp_path: Path,
) -> None:
    snapshot_root = tmp_path / "snapshot"
    _snapshot(snapshot_root)
    snapshot = load_snapshot(snapshot_root)
    page = snapshot.pages[0]
    pages = tuple(
        page.model_copy(update={"source_ref": f"enterprise/{index:05}.txt"})
        for index in range(PAGE_REF_SCALE)
    )
    scaled = snapshot.model_copy(update={"pages": pages})

    refs = Memory100Materializer._page_refs(scaled)

    assert refs[0] == PurePosixPath("enterprise/00000.txt")
    assert refs[-1] == PurePosixPath(f"enterprise/{PAGE_REF_SCALE - 1:05}.txt")
    collision = scaled.model_copy(
        update={
            "pages": (
                page.model_copy(update={"source_ref": "enterprise/report"}),
                page.model_copy(update={"source_ref": "enterprise/report/part.txt"}),
            )
        }
    )
    with pytest.raises(ValueError, match="file/directory collision"):
        Memory100Materializer._page_refs(collision)


@pytest.mark.parametrize("bad_ref", ["../escape.txt", "enterprise/../../escape.txt", "/escape.txt"])
def test_staging_refuses_a_page_ref_that_leaves_the_stage(tmp_path: Path, bad_ref: str) -> None:
    """A dataset's `source_ref` builds a host path, so it is confined by the shared containment
    guard rather than by a lexical check of the harness's own: a ref climbing out of the stage
    writes nothing and publishes no stage."""
    snapshot_root = tmp_path / "snapshot"
    _snapshot(snapshot_root)
    snapshot = load_snapshot(snapshot_root)
    page = snapshot.pages[0]
    escaping = snapshot.model_copy(
        update={"pages": (page.model_copy(update={"source_ref": bad_ref}),)}
    )
    pages_root = tmp_path / "stage" / "pages"

    with pytest.raises(ContainmentError):
        Memory100Materializer._stage_pages(escaping, pages_root)

    assert not pages_root.exists()
    assert list((tmp_path / "stage").iterdir()) == []
    assert not (tmp_path / "escape.txt").exists()


def test_a_stage_holding_a_planted_symlink_is_not_this_snapshots(tmp_path: Path) -> None:
    """A stage left by an earlier run is reused only when it matches the snapshot file for file. A
    link planted where a page belongs is not a page of this snapshot: it is refused rather than read
    through, and the outside file it points at is never taken for the page's body."""
    snapshot_root = tmp_path / "snapshot"
    _snapshot(snapshot_root)
    snapshot = load_snapshot(snapshot_root)
    pages_root = tmp_path / "stage" / "pages"
    Memory100Materializer._stage_pages(snapshot, pages_root)
    outside = tmp_path / "outside.md"
    outside.write_text(snapshot.pages[0].body)
    planted = pages_root.joinpath(*PurePosixPath(snapshot.pages[0].source_ref).parts)
    planted.unlink()
    planted.symlink_to(outside)

    with pytest.raises(RuntimeError, match="does not match snapshot"):
        Memory100Materializer._stage_pages(snapshot, pages_root)

    assert outside.read_text() == snapshot.pages[0].body


async def test_failed_database_precheck_leaves_no_stage_and_retry_materializes(
    memory_100_db: None, tmp_path: Path
) -> None:
    snapshot_root = tmp_path / "snapshot"
    _snapshot(snapshot_root)
    embed = DeterministicEmbed()
    materializer = Memory100Materializer.from_snapshot(
        snapshot_root,
        tmp_path / "state",
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        index=DefaultIndex(transaction=workspace_tx),
        embed=embed,
    )
    blocking_workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=blocking_workspace_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )

    with pytest.raises(RuntimeError, match="requires a clean dedicated database"):
        await materializer.run()
    assert not materializer.pages_root.exists()

    async with workspace_tx() as connection:
        await connection.execute(
            sa.delete(tables.workspace).where(tables.workspace.c.id == blocking_workspace_id)
        )

    readiness = await materializer.run()
    assert readiness.pages_root == materializer.pages_root
    assert materializer.pages_root.is_dir()


async def test_post_stage_database_failure_rolls_back_and_retry_reuses_stage(
    memory_100_db: None, tmp_path: Path
) -> None:
    snapshot_root = tmp_path / "snapshot"
    _snapshot(snapshot_root)
    embed = DeterministicEmbed()
    materializer = Memory100Materializer.from_snapshot(
        snapshot_root,
        tmp_path / "state",
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        index=DefaultIndex(transaction=workspace_tx),
        embed=embed,
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.text(
                "create trigger reject_memory_100_agent before insert on agent "
                "begin select raise(abort, 'injected agent failure'); end"
            )
        )

    with pytest.raises(sa.exc.IntegrityError, match="injected agent failure"):
        await materializer.run()
    assert materializer.pages_root.is_dir()
    async with workspace_tx() as connection:
        workspace_count = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.workspace))
        ).scalar_one()
        await connection.execute(sa.text("drop trigger reject_memory_100_agent"))
    assert workspace_count == 0

    readiness = await materializer.run()
    assert readiness.pages_root == materializer.pages_root
    assert readiness.page_count == len(load_snapshot(snapshot_root).pages)


async def test_materializes_a_memory_body_past_the_live_write_bound(
    memory_100_db: None, tmp_path: Path
) -> None:
    """A haystack memory is a whole recorded session, far past the bound the live write path holds
    over an authored body. The snapshot predates that bound, so its body lands whole, under the id
    the store's own content address would give it, and recall reaches it."""
    session = ALICE_BODY + " Session transcript line.\n" * 400
    assert len(session) > MEMORY_BODY_MAX_CHARS
    snapshot_root = tmp_path / "snapshot"
    _snapshot(snapshot_root, session)
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    embed = DeterministicEmbed()
    index = DefaultIndex(transaction=workspace_tx)
    materializer = Memory100Materializer.from_snapshot(
        snapshot_root,
        tmp_path / "state",
        blob=blob,
        index=index,
        embed=embed,
    )

    readiness = await materializer.run()

    alice = {binding.alias: binding.member_id for binding in readiness.audiences}["alice"]
    assert alice is not None
    with ws(readiness.workspace_id):
        memory_context = context_for("memory", frozenset())
        store = MemoryStore(
            index,
            embed,
            workspace_tx,
            readiness.workspace_id,
            memory_context.page_states,
            memory_context.readable_page_states,
            memory_context.readable_source_ids,
        )
        recalled = await store.recall(
            "alpha lantern",
            recall_subjects(conversation_audience(alice)),
            8,
            source_reader=SourceReader(
                agent_id=uuid5(readiness.workspace_id, "memory_100/agent"),
                requesting_member_id=alice,
                subjects=recall_subjects(conversation_audience(alice)),
            ),
        )

    assert readiness.memory_count == 2
    assert [item.body for item in recalled] == [session]
    assert [item.memory_id for item in recalled] == [
        uuid5(
            MEMORY_ITEM_NAMESPACE,
            "\x00".join((str(readiness.workspace_id), member_subject(alice), "fact", session)),
        )
    ]


async def test_materializes_snapshot_through_real_memory_and_page_pipelines(
    memory_100_db: None, tmp_path: Path
) -> None:
    snapshot_root = tmp_path / "snapshot"
    _snapshot(snapshot_root)
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    embed = DeterministicEmbed()
    index = DefaultIndex(transaction=workspace_tx)
    materializer = Memory100Materializer.from_snapshot(
        snapshot_root,
        tmp_path / "state",
        blob=blob,
        index=index,
        embed=embed,
        manifests=(sources_manifest.manifest(), memory_manifest.manifest(), sample.manifest()),
    )

    readiness = await materializer.run()
    audience = {binding.alias: binding for binding in readiness.audiences}
    alice = audience["alice"].member_id
    bob = audience["bob"].member_id
    assert alice is not None and bob is not None
    assert readiness.page_count == 2
    assert readiness.memory_count == 2
    assert readiness.chunk_count == 4
    assert {owner.source_ref for owner in readiness.evidence} == {
        "drive/runbook.md",
        "slack/launch.txt",
        "session/alice",
        "session/bob",
    }
    assert {
        owner.source_ref: owner.owner_id
        for owner in readiness.evidence
        if owner.owner_kind == "page"
    } == {
        source_ref: page_id_for(readiness.source_id, source_ref)
        for source_ref in ("drive/runbook.md", "slack/launch.txt")
    }

    with ws(readiness.workspace_id):
        memory_context = context_for("memory", frozenset())
        store = MemoryStore(
            index,
            embed,
            workspace_tx,
            readiness.workspace_id,
            memory_context.page_states,
            memory_context.readable_page_states,
            memory_context.readable_source_ids,
        )
        alice_memory = await store.recall(
            "alpha lantern",
            recall_subjects(conversation_audience(alice)),
            8,
            source_reader=SourceReader(
                agent_id=uuid5(readiness.workspace_id, "memory_100/agent"),
                requesting_member_id=alice,
                subjects=recall_subjects(conversation_audience(alice)),
            ),
        )
        bob_memory = await store.recall(
            "alpha lantern",
            recall_subjects(conversation_audience(bob)),
            8,
            source_reader=SourceReader(
                agent_id=uuid5(readiness.workspace_id, "memory_100/agent"),
                requesting_member_id=bob,
                subjects=recall_subjects(conversation_audience(bob)),
            ),
        )
        shared = await store.search_sources(
            "incident commander",
            recall_subjects(conversation_audience(alice)),
            8,
            source_reader=SourceReader(
                agent_id=uuid5(readiness.workspace_id, "memory_100/agent"),
                requesting_member_id=alice,
                subjects=recall_subjects(conversation_audience(alice)),
            ),
        )
        async with workspace_tx() as connection:
            memory_count = (
                await connection.execute(sa.select(sa.func.count()).select_from(memory_item))
            ).scalar_one()
            agent_count = (
                await connection.execute(sa.select(sa.func.count()).select_from(tables.agent))
            ).scalar_one()
        sample_delivery = SamplePageChangeRecord.model_validate(
            await ScopedStore(extension=sample.NAME).get(sample.HOOK_PAGE_CHANGE_KEY)
        )

    assert [item.source_ref for item in alice_memory] == ["session/alice"]
    assert [item.source_ref for item in bob_memory] == ["session/bob"]
    assert shared[0].page_id == page_id_for(readiness.source_id, "drive/runbook.md")
    assert memory_count == 2
    assert agent_count == 1
    assert sample_delivery.model_wired is False
    assert set(sample_delivery.page_ids) == {
        str(page_id_for(readiness.source_id, "drive/runbook.md")),
        str(page_id_for(readiness.source_id, "slack/launch.txt")),
    }

    with ws(readiness.workspace_id):
        orphan_digest = "sha256:" + "f" * 64
        async with workspace_tx() as connection:
            await connection.execute(
                sa.text(
                    "insert into chunk "
                    "(chunk_digest, owner_kind, owner_id, subject, ordinal, text, embedding) "
                    "values (:digest, 'page', 'orphan', 'shared', 0, 'orphan text', null)"
                ),
                {"digest": orphan_digest},
            )
            await connection.execute(
                sa.text(
                    "insert into chunk_fts (chunk_digest, text) values (:digest, 'orphan text')"
                ),
                {"digest": orphan_digest},
            )
        with pytest.raises(RuntimeError, match="default-index chunks are not ready"):
            await CorpusAttestor(
                snapshot=load_snapshot(snapshot_root),
                workspace_id=readiness.workspace_id,
                source_id=readiness.source_id,
                pages_root=readiness.pages_root,
                audiences=readiness.audiences,
                asker_email=readiness.asker_email,
                blob=blob,
            ).attest()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.text("delete from chunk_fts where chunk_digest = :digest"),
                {"digest": orphan_digest},
            )
            await connection.execute(
                sa.text("delete from chunk where chunk_digest = :digest"),
                {"digest": orphan_digest},
            )

        async with workspace_tx() as connection:
            chunk_digest = (
                await connection.execute(
                    sa.text("select chunk_digest from chunk order by chunk_digest limit 1")
                )
            ).scalar_one()
            await connection.execute(
                sa.text("update chunk set embedding = :embedding where chunk_digest = :digest"),
                {"digest": chunk_digest, "embedding": pack_embedding((0.0, 1.0, 0.0))},
            )
        rematerialized = await CorpusAttestor(
            snapshot=load_snapshot(snapshot_root),
            workspace_id=readiness.workspace_id,
            source_id=readiness.source_id,
            pages_root=readiness.pages_root,
            audiences=readiness.audiences,
            asker_email=readiness.asker_email,
            blob=blob,
        ).attest()
        assert rematerialized.corpus_digest != readiness.corpus_digest

        runbook_id = page_id_for(readiness.source_id, "drive/runbook.md")
        runbook_digest = content_digest("The shared incident commander is Captain Vega.")
        canonical_body_ref = (
            f"sources/{readiness.source_id}/{runbook_id}/{runbook_digest.removeprefix('sha256:')}"
        )
        alternate_body_ref = f"eval-corruption/{runbook_id}"
        await blob.put(alternate_body_ref, await blob.get(canonical_body_ref))
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.page)
                .values(body_ref=alternate_body_ref)
                .where(tables.page.c.id == runbook_id)
            )
        with pytest.raises(RuntimeError, match=r"page 'drive/runbook\.md' is not ready"):
            await CorpusAttestor(
                snapshot=load_snapshot(snapshot_root),
                workspace_id=readiness.workspace_id,
                source_id=readiness.source_id,
                pages_root=readiness.pages_root,
                audiences=readiness.audiences,
                asker_email=readiness.asker_email,
                blob=blob,
            ).attest()
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.page)
                .values(body_ref=canonical_body_ref)
                .where(tables.page.c.id == runbook_id)
            )
            await connection.execute(
                sa.update(tables.ext_store)
                .values(value=None)
                .where(
                    tables.ext_store.c.workspace_id == readiness.workspace_id,
                    tables.ext_store.c.extension == "memory",
                    tables.ext_store.c.key == "page_change_cursor:index_pages",
                )
            )
        with pytest.raises(RuntimeError, match="page consumers have pending changes"):
            await CorpusAttestor(
                snapshot=load_snapshot(snapshot_root),
                workspace_id=readiness.workspace_id,
                source_id=readiness.source_id,
                pages_root=readiness.pages_root,
                audiences=readiness.audiences,
                asker_email=readiness.asker_email,
                blob=blob,
            ).attest()
