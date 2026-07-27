"""The `issue_recall` production chain: the fixture landing through the real core sync driver, both
memory page-change consumers, and the real memory indexer, then attested into the readiness a run
grades against.

The model and the embedding provider are stood in for — a stub client that distils one fact per page
it is handed, and a term-vector embed — because they are the dependencies, never the asserted thing.
Everything else is real: the folder source backend, `SyncDriver`, `PageChangeRunner`, `index_pages`,
`derive_facts`, `MemoryIndexer`, the default index, and `MemoryStore.recall`. Assertions read the
attested readiness and the recall path back through public surfaces, and the leaf loader consumes
the readiness this produces, so the producer and its consumer are proved together.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_memory.manifest as memory_manifest
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.events import MAX_RECALLED_MEMORY_IDS
from ufo_ext_memory.store import MemoryStore, memory_item, recall_subjects

from evals.issue_recall.corpus import (
    ABSENT_FILINGS,
    CASES,
    FILINGS,
    PAGES,
    ambient_memories,
    corpus_digest,
    rendered_pages,
)
from evals.issue_recall.materialize import Materializer
from evals.issue_recall.runner import load_issue_recall
from evals.issue_recall.state import CorpusAttestor
from ufo.audience import conversation_audience
from ufo.blob import FilesystemBlobStore
from ufo.db import apply_migrations, dispose_db, init_db, workspace_tx
from ufo.ext.context import context_for
from ufo.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.models.interface import ModelEvent, ModelRequest, TextDelta
from ufo.models.registry import ModelRegistry
from ufo.schema import tables
from ufo.schema.records import Usage
from ufo.sources.sync import page_id_for
from ufo.subjects import SHARED_SUBJECT
from ufo.workspace import ws

AUTO_MODEL = "claude-opus-4-8"
TOPIC_TERMS = ("token", "webhook", "export", "sync")


class TopicEmbed:
    """A deterministic stand-in EmbedClient: one axis per topic term the fixture separates on, so
    the index's vector leg discriminates offline instead of tying every chunk."""

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        vectors: list[tuple[float, ...]] = []
        for text in texts:
            lowered = text.lower()
            axes = [float(lowered.count(term)) for term in TOPIC_TERMS]
            vectors.append(tuple([*axes, 1.0]))
        return tuple(vectors)


@dataclass
class PageEchoModelClient:
    """A stub ModelClient standing in for the fact-extraction pass: it reads the page batch out of
    the request the deriver actually built and answers one durable fact per page, carrying that
    page's title line so the derived fact is recallable on the page's own words. Never a live model.
    """

    calls: int = 0

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.calls += 1
        message = request.messages[0].content
        assert isinstance(message, str)
        payload = json.loads(message)
        facts = [
            {
                "page_id": page["page_id"],
                "notability": "high",
                "memory_kind": "fact",
                "confidence": 8,
                "body": page["body"].splitlines()[0].removeprefix("# ").strip(),
            }
            for page in payload["pages"]
        ]
        yield TextDelta(text=json.dumps({"facts": facts}))
        yield Usage(input_tokens=100, output_tokens=50)


def _registry(client: PageEchoModelClient) -> ModelRegistry:
    return ModelRegistry(
        specs={
            spec.id: replace(spec, client=lambda spec, key: client, key_slot="", key_env="")
            for spec in CORE_MODEL_SPECS
        },
        pricing=CORE_PRICING,
        auto_model=AUTO_MODEL,
    )


@pytest.fixture
def issue_recall_database_url(database_url: str, tmp_path: Path) -> str:
    if database_url.startswith("postgresql"):
        pytest.skip("issue_recall materialization proof uses SQLite's real default index")
    url = f"sqlite+aiosqlite:///{tmp_path / 'issue_recall.db'}"
    apply_migrations(url)
    return url


@pytest.fixture
async def seeded_workspace(issue_recall_database_url: str) -> AsyncIterator[UUID]:
    """The database `ufoctl init` leaves behind: one workspace, nothing derived."""
    init_db(issue_recall_database_url)
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    yield workspace_id
    await dispose_db()


def _materializer(
    tmp_path: Path, client: PageEchoModelClient, blob: FilesystemBlobStore
) -> Materializer:
    return Materializer(
        pages=rendered_pages(),
        ambient=ambient_memories(),
        pages_root=(tmp_path / "state" / corpus_digest().removeprefix("sha256:") / "pages"),
        blob=blob,
        index=DefaultIndex(transaction=workspace_tx),
        embed=TopicEmbed(),
        manifests=(memory_manifest.manifest(),),
        registry=_registry(client),
        postgres=False,
    )


async def test_materializes_the_fixture_into_a_recallable_attested_corpus(
    seeded_workspace: UUID, tmp_path: Path
) -> None:
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    client = PageEchoModelClient()

    readiness = await _materializer(tmp_path, client, blob).run()

    pages = rendered_pages()
    assert readiness.corpus_digest == corpus_digest()
    assert readiness.workspace_id == seeded_workspace
    assert readiness.page_count == len(pages) == 23
    assert readiness.fact_count == len(pages)
    assert len(readiness.ambient) == len(ambient_memories())
    assert {owner.ref for owner in readiness.ambient} == {
        memory.ref for memory in ambient_memories()
    }
    assert readiness.chunk_count == 2 * len(pages) + len(ambient_memories())
    assert client.calls == 3
    assert {owner.source_ref for owner in readiness.pages} == {page.source_ref for page in pages}
    assert {owner.source_ref: owner.page_id for owner in readiness.pages} == {
        page.source_ref: page_id_for(readiness.source_id, page.source_ref) for page in pages
    }
    assert all(len(owner.memory_ids) == 1 for owner in readiness.pages)

    token_refs = {page.source_ref for page in pages if page.key in FILINGS[0].related}
    with ws(readiness.workspace_id):
        store = MemoryStore(
            DefaultIndex(transaction=workspace_tx),
            TopicEmbed(),
            workspace_tx,
            readiness.workspace_id,
            context_for("memory", frozenset()).page_states,
        )
        recalled = await store.recall(
            FILINGS[0].symptom,
            recall_subjects(conversation_audience(None)),
            MAX_RECALLED_MEMORY_IDS,
        )
        sources = await store.search_sources(
            FILINGS[0].symptom,
            recall_subjects(conversation_audience(None)),
            MAX_RECALLED_MEMORY_IDS,
        )
        async with workspace_tx() as connection:
            subjects = (
                (await connection.execute(sa.select(memory_item.c.subject).distinct()))
                .scalars()
                .all()
            )

    owners_by_ref = {owner.source_ref: owner for owner in readiness.pages}
    recalled_refs = {
        ref
        for ref, owner in owners_by_ref.items()
        if any(item.memory_id in owner.memory_ids for item in recalled)
    }
    assert recalled, "the derived facts must be reachable through the real recall path"
    assert recalled_refs & token_refs, "the filing topic's own cluster must be recalled"
    assert sources, "the landed pages must be searchable through the mem_page mirror"
    assert list(subjects) == [SHARED_SUBJECT]


async def test_rematerializing_the_same_corpus_reuses_the_stage_and_attests_the_same(
    seeded_workspace: UUID, tmp_path: Path
) -> None:
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    client = PageEchoModelClient()

    first = await _materializer(tmp_path, client, blob).run()
    again = await _materializer(tmp_path, client, blob).run()

    assert again == first
    assert client.calls == 3, "a settled corpus replays no page to the derivation pass"


async def test_a_stage_that_drifted_from_the_fixture_is_refused(
    seeded_workspace: UUID, tmp_path: Path
) -> None:
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    materializer = _materializer(tmp_path, PageEchoModelClient(), blob)
    await materializer.run()
    (materializer.pages_root / PAGES[0].source_ref).write_text("edited out from under the fixture")

    with pytest.raises(RuntimeError, match="stage does not match the fixture"):
        await _materializer(tmp_path, PageEchoModelClient(), blob).run()


async def test_attestation_refuses_an_unembedded_chunk_and_a_foreign_memory(
    seeded_workspace: UUID, tmp_path: Path
) -> None:
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    readiness = await _materializer(tmp_path, PageEchoModelClient(), blob).run()
    attestor = CorpusAttestor(
        pages=rendered_pages(),
        ambient=ambient_memories(),
        workspace_id=readiness.workspace_id,
        source_id=readiness.source_id,
        pages_root=readiness.pages_root,
        blob=blob,
    )

    with ws(readiness.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.text(
                    "insert into chunk "
                    "(chunk_digest, owner_kind, owner_id, subject, ordinal, text, embedding) "
                    "values (:digest, 'page', 'orphan', 'shared', 0, 'orphan text', null)"
                ),
                {"digest": "sha256:" + "f" * 64},
            )
        with pytest.raises(RuntimeError, match="index chunks are not embedded"):
            await attestor.attest()

        async with workspace_tx() as connection:
            await connection.execute(sa.text("delete from chunk where owner_id = 'orphan'"))
            await connection.execute(
                sa.insert(memory_item).values(
                    id=uuid4(),
                    workspace_id=readiness.workspace_id,
                    subject=SHARED_SUBJECT,
                    body="a fact from outside the fixture",
                    item_class="fact",
                    memory_kind="fact",
                    confidence=5,
                    source_ref="not-a-page-id",
                    embedding_digest="sha256:" + "e" * 64,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        with pytest.raises(RuntimeError, match="nor declared ambient memories"):
            await attestor.attest()


async def test_the_readiness_it_writes_loads_as_the_graded_leaf(
    seeded_workspace: UUID, tmp_path: Path
) -> None:
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    readiness = await _materializer(tmp_path, PageEchoModelClient(), blob).run()
    path = tmp_path / "readiness.json"
    path.write_text(readiness.model_dump_json())

    run = load_issue_recall(path)

    assert run.readiness == readiness
    assert len(run.tasks) == 1
    assert len(run.tasks[0].cases) == len(CASES) + len(ABSENT_FILINGS)


async def test_attestation_refuses_an_incomplete_ambient_haystack(
    seeded_workspace: UUID, tmp_path: Path
) -> None:
    """A partial `_commit_ambient` or an edited ambient body lands here: the counts still balance,
    every row is accounted for, and only the declared-set comparison catches it."""
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    readiness = await _materializer(tmp_path, PageEchoModelClient(), blob).run()
    attestor = CorpusAttestor(
        pages=rendered_pages(),
        ambient=ambient_memories(),
        workspace_id=readiness.workspace_id,
        source_id=readiness.source_id,
        pages_root=readiness.pages_root,
        blob=blob,
    )
    dropped = readiness.ambient[0]

    with ws(readiness.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(memory_item).where(memory_item.c.id == dropped.memory_id)
            )
        with pytest.raises(RuntimeError, match="ambient haystack is incomplete or duplicated"):
            await attestor.attest()


async def test_attestation_refuses_a_duplicated_ambient_memory(
    seeded_workspace: UUID, tmp_path: Path
) -> None:
    """`memory_item` carries no uniqueness constraint on `source_ref`, so a `_commit_ambient` that
    double-inserts is reachable — the same guard's other half, with the counts balanced and nothing
    foreign present."""
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    readiness = await _materializer(tmp_path, PageEchoModelClient(), blob).run()
    attestor = CorpusAttestor(
        pages=rendered_pages(),
        ambient=ambient_memories(),
        workspace_id=readiness.workspace_id,
        source_id=readiness.source_id,
        pages_root=readiness.pages_root,
        blob=blob,
    )
    duplicated = readiness.ambient[0]

    with ws(readiness.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(memory_item).values(
                    id=uuid4(),
                    workspace_id=readiness.workspace_id,
                    subject=SHARED_SUBJECT,
                    body="a second row claiming the same declared ambient ref",
                    item_class="fact",
                    memory_kind="fact",
                    confidence=5,
                    source_ref=duplicated.ref,
                    embedding_digest="sha256:" + "d" * 64,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        with pytest.raises(RuntimeError, match="ambient haystack is incomplete or duplicated"):
            await attestor.attest()


async def test_materialization_refuses_a_workspace_holding_foreign_memory(
    seeded_workspace: UUID, tmp_path: Path
) -> None:
    """The refusal lands before any write. memory_100 demands an empty database; this corpus cannot,
    since re-materializing it is a documented no-op — so the bar is that nothing undeclared is
    present, and a workspace with one foreign memory row is refused with no page landed."""
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    with ws(seeded_workspace):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(memory_item).values(
                    id=uuid4(),
                    workspace_id=seeded_workspace,
                    subject=SHARED_SUBJECT,
                    body="a fact this corpus never declared",
                    item_class="fact",
                    memory_kind="fact",
                    confidence=5,
                    source_ref="somewhere-else",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

    with pytest.raises(RuntimeError, match="holding only this corpus"):
        await _materializer(tmp_path, PageEchoModelClient(), blob).run()

    with ws(seeded_workspace):
        async with workspace_tx() as connection:
            pages = (
                await connection.execute(sa.select(sa.func.count()).select_from(tables.page))
            ).scalar_one()

    assert pages == 0


async def test_materialization_refuses_a_page_from_another_source(
    seeded_workspace: UUID, tmp_path: Path
) -> None:
    """The other arm of the same refusal, and a reachable one: editing the fixture moves the corpus
    digest, which moves `pages_root` and so the folder source id, leaving the previous digest's page
    rows behind under a source this run does not own."""
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    stale_source = uuid4()
    with ws(seeded_workspace):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.source).values(
                    id=stale_source,
                    workspace_id=seeded_workspace,
                    backend="folder",
                    config={"root": "/gone/previous-digest/pages"},
                    subject=SHARED_SUBJECT,
                    next_sync_at=sa.func.now(),
                    consecutive_errors=0,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.page).values(
                    id=uuid4(),
                    workspace_id=seeded_workspace,
                    source_id=stale_source,
                    digest="sha256:" + "a" * 64,
                    body_ref=f"sources/{stale_source}/stale",
                    stream="files",
                    title="issues/2400412",
                    subject=SHARED_SUBJECT,
                    tombstone=False,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

    with pytest.raises(RuntimeError, match="holding only this corpus"):
        await _materializer(tmp_path, PageEchoModelClient(), blob).run()

    with ws(seeded_workspace):
        async with workspace_tx() as connection:
            memories = (
                await connection.execute(sa.select(sa.func.count()).select_from(memory_item))
            ).scalar_one()

    assert memories == 0


async def test_a_run_killed_mid_stage_leaves_no_partial_stage(
    seeded_workspace: UUID, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stage lands by one rename, so a run that dies part-way through writing leaves `pages_root`
    absent rather than half-written — a partial stage is refused as drifted by every later run, with
    nothing to do but delete it by hand."""
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    materializer = _materializer(tmp_path, PageEchoModelClient(), blob)
    written = 0
    real_write = Path.write_bytes

    def die_partway(self: Path, data: bytes) -> int:
        nonlocal written
        written += 1
        if written > 5:
            raise KeyboardInterrupt("killed mid-stage")
        return real_write(self, data)

    monkeypatch.setattr(Path, "write_bytes", die_partway)

    with pytest.raises(KeyboardInterrupt):
        await materializer.run()

    monkeypatch.undo()

    assert not materializer.pages_root.exists()
    assert list(materializer.pages_root.parent.glob("*")) == []
    readiness = await _materializer(tmp_path, PageEchoModelClient(), blob).run()
    assert readiness.page_count == len(rendered_pages())


async def test_attestation_refuses_a_page_consumer_left_behind(
    seeded_workspace: UUID, tmp_path: Path
) -> None:
    """A consumer whose cursor sits short of the last page has derived state for only part of the
    corpus, so the readiness would attest a fixture the run never finished — an early exit in
    `PageChangeRunner.drive` looks exactly like this."""
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    readiness = await _materializer(tmp_path, PageEchoModelClient(), blob).run()
    attestor = CorpusAttestor(
        pages=rendered_pages(),
        ambient=ambient_memories(),
        workspace_id=readiness.workspace_id,
        source_id=readiness.source_id,
        pages_root=readiness.pages_root,
        blob=blob,
    )

    with ws(readiness.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.ext_store).where(
                    tables.ext_store.c.extension == "memory",
                    tables.ext_store.c.key == "page_change_cursor:derive_facts",
                )
            )
        with pytest.raises(RuntimeError, match="page consumers have pending changes"):
            await attestor.attest()


async def test_a_run_that_loses_the_stage_rename_verifies_the_winner(
    seeded_workspace: UUID, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs against one `--state` path both find `pages_root` absent and both write a temporary
    stage; one rename lands and the other fails. The loser must accept the winner's stage — both
    wrote the same digest-named corpus — and go on to materialize."""
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    materializer = _materializer(tmp_path, PageEchoModelClient(), blob)
    real_rename = Path.rename
    lost = False

    def lose_once(self: Path, target: str | Path) -> Path:
        nonlocal lost
        if not lost and Path(target) == materializer.pages_root:
            lost = True
            for page in rendered_pages():
                landed = Path(target) / page.source_ref
                landed.parent.mkdir(parents=True, exist_ok=True)
                landed.write_bytes(page.body.encode())
            raise OSError("directory not empty")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", lose_once)

    readiness = await materializer.run()

    assert lost
    assert readiness.page_count == len(rendered_pages())
    assert [path.name for path in materializer.pages_root.parent.iterdir()] == [
        materializer.pages_root.name
    ]


async def test_a_stage_rename_failing_for_any_other_reason_raises(
    seeded_workspace: UUID, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback is scoped to a lost race: a rename that fails with no stage in place is a real
    fault and surfaces."""
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    materializer = _materializer(tmp_path, PageEchoModelClient(), blob)

    def always_fail(self: Path, target: str | Path) -> Path:
        raise OSError("no space left on device")

    monkeypatch.setattr(Path, "rename", always_fail)

    with pytest.raises(OSError, match="no space left on device"):
        await materializer.run()

    monkeypatch.undo()

    assert not materializer.pages_root.exists()
