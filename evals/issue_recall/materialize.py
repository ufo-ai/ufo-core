"""Materialize the `issue_recall` fixture into a seeded workspace, before serve boots.

Runs the production chain the leaf grades: the rendered fixture pages land through the core sync
driver as a folder source, both memory `page_change` consumers drain with the model wired — so
`index_pages` writes the chunks and mirror rows and `derive_facts` distills each page into durable
facts — then the memory indexer embeds those facts so vector recall can reach them. Attestation
records the page-to-fact map the leaf grades against and prints the readiness path.

    python -m evals.issue_recall.materialize --state <run>/state
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_ext_memory.store import MemoryIndexer, MemoryStore, MemoryWrite, memory_item

from evals.issue_recall.corpus import (
    Ambient,
    RenderedPage,
    ambient_memories,
    corpus_digest,
    rendered_pages,
)
from evals.issue_recall.state import CorpusAttestor, CorpusReadiness
from ufo.blob import BlobStore, blob_store_for
from ufo.config import Config, SourceConfig, SourceEntry, load_config
from ufo.credentials import CredentialStore
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.ext.loader import embed_backend, index_backend, load_manifests
from ufo.ext.manifest import Manifest
from ufo.indexing import EmbedClient, IndexBackend, TextChunker
from ufo.jobs import PageChangeRunner
from ufo.models.registry import ModelRegistry, model_registry
from ufo.schema import tables
from ufo.sources.sync import (
    FOLDER_BACKEND,
    CorePageFeed,
    FolderSource,
    SyncDriver,
    page_id_for,
    register_sources,
    source_row_id,
)
from ufo.subjects import SHARED_SUBJECT
from ufo.workspace import init_workspace_credentials, ws, ws_current

PAGE_CONSUMERS = frozenset({("memory", "index_pages"), ("memory", "derive_facts")})


@dataclass(frozen=True)
class Materializer:
    """Land the fixture, derive its recallable state, and attest what a run may grade."""

    pages: tuple[RenderedPage, ...]
    ambient: tuple[Ambient, ...]
    pages_root: Path
    blob: BlobStore
    index: IndexBackend
    embed: EmbedClient
    manifests: tuple[Manifest, ...]
    registry: ModelRegistry
    postgres: bool

    async def run(self) -> CorpusReadiness:
        workspace_id = await self._workspace()
        entry = SourceEntry(backend="folder", config=SourceConfig(root=str(self.pages_root)))
        source_id = source_row_id(
            workspace_id, FOLDER_BACKEND, entry.config.model_dump(mode="json")
        )
        with ws(workspace_id):
            await self._refuse_foreign_state(source_id)
        await asyncio.to_thread(self._stage_pages)
        with ws(workspace_id):
            await register_sources((entry,))
            await SyncDriver(
                backends={FOLDER_BACKEND: FolderSource()},
                blob=self.blob,
                postgres=self.postgres,
            ).run()
            await self._drain_page_consumers()
            await self._commit_ambient()
            await self._drain_memory_index()
            return await CorpusAttestor(
                pages=self.pages,
                ambient=self.ambient,
                workspace_id=workspace_id,
                source_id=source_id,
                pages_root=self.pages_root,
                blob=self.blob,
            ).attest()

    @staticmethod
    async def _workspace() -> UUID:
        async with workspace_tx() as connection:
            rows = (await connection.execute(sa.select(tables.workspace.c.id))).scalars().all()
        if len(rows) != 1:
            raise RuntimeError(
                f"issue_recall materialization requires exactly one seeded workspace, found "
                f"{len(rows)}"
            )
        return rows[0]

    async def _refuse_foreign_state(self, source_id: UUID) -> None:
        """Refuse before writing when the workspace already holds material this corpus does not
        declare. The sibling memory_100 materializer demands an empty database; this one cannot,
        because re-materializing the same corpus is a documented no-op — so the bar is that every
        durable row is a declared ambient ref or a fact whose `created_from_page_id` names one of
        this fixture's pages, and every page belongs to this fixture's source. Anything else and the
        run would commit some 460 ambient rows plus a sync into a workspace it does not own, leaving
        the attestor to discover it afterwards."""
        declared = {memory.ref for memory in self.ambient}
        page_ids = {page_id_for(source_id, page.source_ref) for page in self.pages}
        async with workspace_tx() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(memory_item.c.source_ref, memory_item.c.created_from_page_id)
                    )
                )
                .mappings()
                .all()
            )
            foreign_pages = (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(tables.page)
                    .where(tables.page.c.source_id != source_id)
                )
            ).scalar_one()
        foreign = sorted(
            {
                str(row["source_ref"])
                for row in rows
                if row["source_ref"] not in declared and row["created_from_page_id"] not in page_ids
            }
        )
        if foreign or foreign_pages:
            raise RuntimeError(
                f"issue_recall materialization requires a workspace holding only this corpus; "
                f"found {len(foreign)} undeclared memory rows and {foreign_pages} foreign pages"
            )

    def _stage_pages(self) -> None:
        """Write each rendered page to its `<stream>/<id>` file, verifying an existing stage rather
        than rewriting it — the fixture digest names the directory, so a re-materialization of the
        same corpus reuses the files it already staged.

        The write goes to a sibling temporary directory and lands by one rename, so `pages_root`
        never exists in a half-written state: a run killed mid-write would otherwise leave a partial
        stage that every later run refuses as drifted, with nothing to do but delete it by hand. A
        concurrent run winning the rename is not a conflict — its stage is verified instead."""
        bodies = {page.source_ref: page.body for page in self.pages}
        if self.pages_root.exists():
            self._verify_stage(bodies)
            return
        self.pages_root.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.pages_root.with_name(f".{self.pages_root.name}.{uuid4().hex}.tmp")
        temporary.mkdir()
        try:
            for source_ref, body in bodies.items():
                target = temporary / source_ref
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(body.encode())
            try:
                temporary.rename(self.pages_root)
            except OSError:
                if not self.pages_root.exists():
                    raise
                self._verify_stage(bodies)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def _verify_stage(self, bodies: dict[str, str]) -> None:
        staged = {
            path.relative_to(self.pages_root).as_posix(): path.read_bytes().decode()
            for path in sorted(self.pages_root.rglob("*"))
            if path.is_file()
        }
        if staged != bodies:
            raise RuntimeError(f"issue_recall stage does not match the fixture: {self.pages_root}")

    async def _drain_page_consumers(self) -> None:
        runner = PageChangeRunner(
            manifests=self.manifests,
            pages=CorePageFeed(blob=self.blob),
            index=self.index,
            embed=self.embed,
            blob=self.blob,
            registry=self.registry,
        )
        consumers = runner.consumers()
        available = {(consumer.extension, consumer.discriminator) for consumer in consumers}
        if not PAGE_CONSUMERS <= available:
            raise RuntimeError("issue_recall expected both memory page-change consumers")
        for consumer in consumers:
            await runner.drive(consumer)
        await self._derived_facts_carry_their_page()

    @staticmethod
    async def _derived_facts_carry_their_page() -> None:
        """Fail at the write boundary rather than at attestation: the whole leaf hangs on each
        derived fact carrying the `created_from_page_id` link back to the page it came from, since
        that is the only map from a synced issue to the memory auto-inject can reach. A fact without
        one is a broken chain, and saying so here names the derivation pass instead of leaving the
        attestor to report an unaccounted row several steps later."""
        async with workspace_tx() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(memory_item.c.id, memory_item.c.body).where(
                            memory_item.c.created_from_page_id.is_(None),
                            memory_item.c.source_ref.is_(None),
                        )
                    )
                )
                .mappings()
                .all()
            )
        if rows:
            sample = [row["body"][:70] for row in rows[:3]]
            raise RuntimeError(
                f"derivation produced {len(rows)} facts linked to no page, so no page maps to a "
                f"recallable memory: {sample}"
            )

    async def _commit_ambient(self) -> None:
        """Commit the haystack the graded facts compete against: durable shared memory that no page
        produced, written through the store's own upsert so a re-materialization settles on the same
        rows rather than accreting a second bank."""
        store = MemoryStore(
            index=self.index,
            embed=self.embed,
            transaction=workspace_tx,
            workspace_id=ws_current().workspace_id,
        )
        for memory in self.ambient:
            await store.commit(
                MemoryWrite(subject=SHARED_SUBJECT, body=memory.body, source_ref=memory.ref)
            )

    async def _drain_memory_index(self) -> None:
        indexer = MemoryIndexer(
            index=self.index, embed=self.embed, transaction=workspace_tx, chunker=TextChunker()
        )
        while True:
            pending = await self._unindexed_facts()
            if pending == 0:
                return
            await indexer.run()
            if await self._unindexed_facts() >= pending:
                raise RuntimeError("issue_recall memory index made no progress")

    @staticmethod
    async def _unindexed_facts() -> int:
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(sa.func.count())
                    .select_from(memory_item)
                    .where(memory_item.c.embedding_digest.is_(None))
                )
            ).scalar_one()


async def _run(config: Config, state_root: Path) -> CorpusReadiness:
    init_db(config.database.url)
    key = os.environ.get(config.credentials.key_env)
    credentials = CredentialStore(fernet=Fernet(key.encode())) if key else None
    init_workspace_credentials(credentials)
    try:
        manifests = load_manifests(config.pack.name)
        return await Materializer(
            pages=rendered_pages(),
            ambient=ambient_memories(),
            pages_root=(state_root / corpus_digest().removeprefix("sha256:") / "pages").resolve(),
            blob=blob_store_for(config.blob),
            index=index_backend(manifests, config.memory.index_backend, credentials),
            embed=embed_backend(manifests, config.memory.embed_backend, credentials),
            manifests=manifests,
            registry=model_registry(config, manifests),
            postgres=config.database.url.startswith("postgresql"),
        ).run()
    finally:
        init_workspace_credentials(None)
        await dispose_db()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.issue_recall.materialize")
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    readiness = asyncio.run(_run(load_config(), args.state))
    output = args.state / readiness.corpus_digest.removeprefix("sha256:") / "readiness.json"
    output.write_text(
        json.dumps(readiness.model_dump(mode="json"), sort_keys=True, separators=(",", ":")) + "\n"
    )
    print(output)


if __name__ == "__main__":
    main()
