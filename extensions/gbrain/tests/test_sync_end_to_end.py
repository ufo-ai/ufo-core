"""End-to-end proof of the gbrain seam: register a gbrain source, drive the real SyncDriver
through the job dispatch, and read what landed — page rows, fed bodies, chunks, and a search
hit. No conftest: the shared ufo_testsupport plugin provides the database fixtures."""

import io
import tarfile
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import sqlalchemy as sa
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_gbrain.folder import FOLDER_BACKEND, GbrainFolderSource
from ufo_ext_gbrain.git import (
    GIT_BACKEND,
    GITHUB_TOKEN_SLOT,
    GbrainGitConfig,
    GbrainGitSource,
)
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import MemoryStore, PageIndexer

from ufo.blob import FilesystemBlobStore
from ufo.config import SourceConfig, SourceEntry
from ufo.db import workspace_tx
from ufo.runtime.ext.context import CredentialAccess, ExtensionContext, SourceReader, context_for
from ufo.runtime.ext.manifest import JobSpec
from ufo.runtime.indexing import TextChunker
from ufo.runtime.jobs import CORE_EXTENSION, JobRunner, bindings_from
from ufo.runtime.sources.sync import (
    SOURCE_SYNC_JOB,
    CorePageFeed,
    SyncDriver,
    page_id_for,
    register_sources,
)
from ufo.runtime.turns.subjects import SHARED_SUBJECT
from ufo.runtime.workspace import ws
from ufo.schema import tables

VECTOR = tuple([1.0] + [0.0] * (EMBED_DIM - 1))
TARBALL_SHA = "a1b2c3"
TARBALL_ETAG = 'W/"gbrain-head"'


class FixedEmbed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(VECTOR for _ in texts)


async def _workspace() -> tuple[UUID, UUID]:
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
    return workspace_id, agent_id


async def _sync(driver: SyncDriver) -> None:
    async def _handler(context: ExtensionContext) -> None:
        await driver.run()

    spec = JobSpec(
        name=SOURCE_SYNC_JOB,
        schedule=None,
        handler=_handler,
        candidates=driver.candidate_workspaces,
    )
    runner = JobRunner(bindings=bindings_from((), (spec,)), manifests=())
    for workspace_id in await runner.candidates(f"{CORE_EXTENSION}:{SOURCE_SYNC_JOB}"):
        await runner.fire(f"{CORE_EXTENSION}:{SOURCE_SYNC_JOB}", workspace_id)


async def _make_due() -> None:
    async with workspace_tx() as connection:
        await connection.execute(sa.update(tables.source).values(next_sync_at=sa.func.now()))


async def _pages() -> list[sa.RowMapping]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(
                        tables.page.c.id,
                        tables.page.c.source_id,
                        tables.page.c.title,
                        tables.page.c.digest,
                        tables.page.c.body_ref,
                        tables.page.c.subject,
                        tables.page.c.tombstone,
                    ).order_by(tables.page.c.title)
                )
            ).mappings()
        )


def _write_brain(root: Path) -> None:
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "bob.md").write_text(
        "---\ntitle: Bob Smith\ntags: [people]\n---\n\nBob works at Acme on the tempest launch."
    )
    (root / "README.md").write_text("# Team Handbook\nthe handbook body")
    (root / "logo.png").write_bytes(b"\x89PNG\x00\x01")
    git_dir = root / ".git" / "objects"
    git_dir.mkdir(parents=True)
    (git_dir / "aa").write_bytes(b"\x00\x01\x02")


async def _register_folder(root: Path) -> None:
    await register_sources(
        (SourceEntry(backend=FOLDER_BACKEND, config=SourceConfig(root=str(root))),)
    )


async def test_folder_markdown_lands_and_searches(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id, agent_id = await _workspace()
    root = tmp_path / "brain"
    _write_brain(root)
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    driver = SyncDriver(
        backends={FOLDER_BACKEND: GbrainFolderSource()},
        blob=blob,
        postgres=database_url.startswith("postgresql"),
    )
    with ws(workspace_id):
        await _register_folder(root)
    await _sync(driver)

    rows = await _pages()
    assert [row["title"] for row in rows] == ["Bob Smith", "Team Handbook"]
    source_id = rows[0]["source_id"]
    assert [row["id"] for row in rows] == [
        page_id_for(source_id, "notes/bob.md"),
        page_id_for(source_id, "README.md"),
    ]
    assert all(row["subject"] == SHARED_SUBJECT and not row["tombstone"] for row in rows)
    bob_body = await blob.get(rows[0]["body_ref"])
    assert bob_body.decode().startswith("Bob works at Acme")

    memory_ext = context_for("memory", frozenset())
    index = DefaultIndex(transaction=workspace_tx)
    indexer = PageIndexer(
        index=index,
        embed=FixedEmbed(),
        transaction=workspace_tx,
        chunker=TextChunker(),
        workspace_id=workspace_id,
        page_states=memory_ext.page_states,
    )
    feed = CorePageFeed(blob=blob)
    with ws(workspace_id):
        await indexer.apply((await feed.pages_changed_since(None, 50)).changes)
    service = MemoryStore(
        index=index,
        embed=FixedEmbed(),
        transaction=workspace_tx,
        workspace_id=workspace_id,
        page_states=memory_ext.page_states,
        readable_page_states=memory_ext.readable_page_states,
        readable_source_ids=memory_ext.readable_source_ids,
    )
    with ws(workspace_id):
        matches = await service.search_sources(
            "tempest launch",
            frozenset({SHARED_SUBJECT}),
            8,
            source_reader=SourceReader(
                agent_id=agent_id,
                requesting_member_id=None,
                subjects=frozenset({SHARED_SUBJECT}),
            ),
        )
    assert matches and "tempest launch" in matches[0].text


async def test_folder_file_removal_tombstones(db: None, database_url: str, tmp_path: Path) -> None:
    workspace_id, _ = await _workspace()
    root = tmp_path / "brain"
    _write_brain(root)
    driver = SyncDriver(
        backends={FOLDER_BACKEND: GbrainFolderSource()},
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
    )
    with ws(workspace_id):
        await _register_folder(root)
    await _sync(driver)

    (root / "README.md").unlink()
    await _make_due()
    await _sync(driver)

    rows = await _pages()
    source_id = rows[0]["source_id"]
    tombstoned = {row["id"]: bool(row["tombstone"]) for row in rows}
    assert tombstoned[page_id_for(source_id, "README.md")] is True
    assert tombstoned[page_id_for(source_id, "notes/bob.md")] is False


def _repo_tarball() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, data in (
            (
                f"acme-brain-{TARBALL_SHA}/wiki/bob.md",
                b"---\ntitle: Bob Smith\n---\n\nBob runs the tempest launch.",
            ),
            (f"acme-brain-{TARBALL_SHA}/logo.png", b"\x89PNG\x00"),
            (f"acme-brain-{TARBALL_SHA}/.git/config", b"\x00"),
        ):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


async def test_git_repo_syncs_then_idles_on_304(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id, _ = await _workspace()
    tarball = _repo_tarball()
    tarball_requests = 0

    def _github(request: httpx.Request) -> httpx.Response:
        nonlocal tarball_requests
        if request.url.path == "/repos/acme/brain/commits/HEAD":
            if request.headers.get("If-None-Match") == TARBALL_ETAG:
                return httpx.Response(304)
            return httpx.Response(200, text=TARBALL_SHA, headers={"etag": TARBALL_ETAG})
        if request.url.path == f"/repos/acme/brain/tarball/{TARBALL_SHA}":
            tarball_requests += 1
            return httpx.Response(200, content=tarball)
        return httpx.Response(404)

    driver = SyncDriver(
        backends={
            GIT_BACKEND: GbrainGitSource(
                credentials=CredentialAccess(declared=frozenset({GITHUB_TOKEN_SLOT})),
                transport=httpx.MockTransport(_github),
            )
        },
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
    )
    with ws(workspace_id):
        ext = context_for("gbrain", frozenset({GITHUB_TOKEN_SLOT}))
        await ext.register_source(
            GIT_BACKEND,
            GbrainGitConfig(repo="acme/brain"),
            connection_id=await ext.register_connection(GIT_BACKEND),
        )
    await _sync(driver)

    rows = await _pages()
    assert [row["title"] for row in rows] == ["Bob Smith"]
    assert rows[0]["id"] == page_id_for(rows[0]["source_id"], "wiki/bob.md")
    async with workspace_tx() as connection:
        cursor = (await connection.execute(sa.select(tables.source.c.cursor))).scalar_one()
    assert TARBALL_SHA in cursor

    first_digest = rows[0]["digest"]
    await _make_due()
    await _sync(driver)
    rows = await _pages()
    assert tarball_requests == 1
    assert [row["digest"] for row in rows] == [first_digest]
