import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from importlib import import_module
from itertools import pairwise
from pathlib import Path, PurePosixPath
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import sqlalchemy as sa
from cryptography.fernet import Fernet

from evals.memory_100.models import Snapshot
from evals.memory_100.snapshot import load_snapshot
from evals.memory_100.state import AudienceBinding, CorpusAttestor, CorpusReadiness
from ufo.blob import BlobStore, blob_store_for
from ufo.config import Config, SourceConfig, SourceEntry, load_config
from ufo.credentials import CredentialStore
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.ext.loader import embed_backend, index_backend, load_manifests
from ufo.ext.manifest import Manifest
from ufo.indexing import EmbedClient, IndexBackend, TextChunker
from ufo.jobs import PageChangeRunner
from ufo.onboarding import DEFAULT_AGENT_MODEL, DEFAULT_AGENT_PROMPT
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME
from ufo.sources.sync import (
    FOLDER_BACKEND,
    CorePageFeed,
    FolderSource,
    SyncDriver,
    register_sources,
    source_row_id,
)
from ufo.subjects import SHARED_SUBJECT, member_subject
from ufo.workspace import init_workspace_credentials, ws

index_default = import_module("ufo_ext_index_default")
memory_manifest = import_module("ufo_ext_memory.manifest")
memory_store = import_module("ufo_ext_memory.store")


@dataclass(frozen=True)
class Memory100Materializer:
    snapshot: Snapshot
    pages_root: Path
    blob: BlobStore
    index: IndexBackend
    embed: EmbedClient
    manifests: tuple[Manifest, ...]
    postgres: bool = False

    @classmethod
    def from_snapshot(
        cls,
        root: Path,
        state_root: Path,
        *,
        blob: BlobStore,
        index: IndexBackend,
        embed: EmbedClient,
        manifests: tuple[Manifest, ...] | None = None,
        postgres: bool = False,
    ) -> "Memory100Materializer":
        """Load a snapshot and locate its deterministic staging directory."""
        snapshot = load_snapshot(root)
        pages_root = state_root / snapshot.manifest.digest.removeprefix("sha256:") / "pages"
        active = (memory_manifest.manifest(),) if manifests is None else manifests
        return cls(snapshot, pages_root.resolve(), blob, index, embed, active, postgres)

    async def run(self) -> CorpusReadiness:
        if not isinstance(self.index, index_default.DefaultIndex):
            raise TypeError("memory_100 materialization requires the default database index")
        workspace_id = uuid5(NAMESPACE_URL, f"memory_100/workspace/{self.snapshot.manifest.digest}")
        audiences = self._audiences(workspace_id)
        await self._create_workspace(workspace_id, audiences)
        entry = SourceEntry(
            backend="folder",
            config=SourceConfig(root=str(self.pages_root)),
        )
        source_id = source_row_id(
            workspace_id, FOLDER_BACKEND, entry.config.model_dump(mode="json")
        )
        with ws(workspace_id):
            await register_sources((entry,))
            await SyncDriver(
                backends={FOLDER_BACKEND: FolderSource()},
                blob=self.blob,
                postgres=self.postgres,
            ).run()
            await self._commit_memories(workspace_id, audiences)
            await self._drain_memory_index()
            await self._drain_page_consumers()
            return await CorpusAttestor(
                snapshot=self.snapshot,
                workspace_id=workspace_id,
                source_id=source_id,
                pages_root=self.pages_root,
                audiences=audiences,
                blob=self.blob,
            ).attest()

    def _audiences(self, workspace_id: UUID) -> tuple[AudienceBinding, ...]:
        aliases = sorted(
            {
                *(case.audience for case in self.snapshot.cases),
                *(page.audience for page in self.snapshot.pages),
                *(memory.audience for memory in self.snapshot.memories),
            }
        )
        if any(page.audience != SHARED_SUBJECT for page in self.snapshot.pages):
            raise ValueError("folder-backed memory_100 pages must use the shared audience")
        bindings: list[AudienceBinding] = []
        for alias in aliases:
            if alias == SHARED_SUBJECT:
                bindings.append(AudienceBinding(alias=alias, email=None, member_id=None))
                continue
            member_id = uuid5(workspace_id, f"memory_100/audience/{alias}")
            key = hashlib.sha256(alias.encode()).hexdigest()[:24]
            bindings.append(
                AudienceBinding(
                    alias=alias,
                    email=f"memory-100+{key}@eval.invalid",
                    member_id=member_id,
                )
            )
        return tuple(bindings)

    async def _create_workspace(
        self, workspace_id: UUID, audiences: tuple[AudienceBinding, ...]
    ) -> None:
        async with workspace_tx() as connection:
            workspace_count = (
                await connection.execute(sa.select(sa.func.count()).select_from(tables.workspace))
            ).scalar_one()
            chunk_count = (
                await connection.execute(sa.text("select count(*) from chunk"))
            ).scalar_one()
            if workspace_count or chunk_count:
                raise RuntimeError("memory_100 materialization requires a clean dedicated database")
            await asyncio.to_thread(self._stage_pages, self.snapshot, self.pages_root)
            await connection.execute(
                sa.insert(tables.workspace).values(
                    id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                )
            )
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=uuid5(workspace_id, "memory_100/agent"),
                    workspace_id=workspace_id,
                    name=DEFAULT_AGENT_NAME,
                    prompt=DEFAULT_AGENT_PROMPT,
                    model=DEFAULT_AGENT_MODEL,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            for binding in audiences:
                if binding.member_id is None or binding.email is None:
                    continue
                await connection.execute(
                    sa.insert(tables.member).values(
                        id=binding.member_id,
                        workspace_id=workspace_id,
                        email=binding.email,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )

    @classmethod
    def _stage_pages(cls, snapshot: Snapshot, pages_root: Path) -> None:
        refs = cls._page_refs(snapshot)
        if pages_root.exists():
            cls._validate_stage(snapshot, refs, pages_root)
            return
        pages_root.parent.mkdir(parents=True, exist_ok=True)
        temporary_root = pages_root.with_name(f".{pages_root.name}.{uuid4().hex}.tmp")
        temporary_root.mkdir()
        try:
            for page, ref in zip(snapshot.pages, refs, strict=True):
                target = temporary_root.joinpath(*ref.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(page.body.encode("utf-8"))
            try:
                temporary_root.rename(pages_root)
            except OSError:
                if not pages_root.exists():
                    raise
                cls._validate_stage(snapshot, refs, pages_root)
        finally:
            if temporary_root.exists():
                shutil.rmtree(temporary_root)

    @staticmethod
    def _page_refs(snapshot: Snapshot) -> tuple[PurePosixPath, ...]:
        refs = tuple(PurePosixPath(page.source_ref) for page in snapshot.pages)
        if any(ref.is_absolute() or ".." in ref.parts or "." in ref.parts for ref in refs):
            raise ValueError("memory_100 page source_ref must be a safe relative path")
        ordered = sorted(refs, key=lambda ref: ref.parts)
        if any(left == right or left in right.parents for left, right in pairwise(ordered)):
            raise ValueError("memory_100 page source_refs contain a file/directory collision")
        return refs

    @staticmethod
    def _validate_stage(
        snapshot: Snapshot, refs: tuple[PurePosixPath, ...], pages_root: Path
    ) -> None:
        if pages_root.is_symlink() or not pages_root.is_dir():
            raise RuntimeError(f"memory_100 stage does not match snapshot: {pages_root}")
        actual: set[PurePosixPath] = set()
        for path in pages_root.rglob("*"):
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise RuntimeError(f"memory_100 stage does not match snapshot: {pages_root}")
            if path.is_file():
                actual.add(PurePosixPath(path.relative_to(pages_root).as_posix()))
        if actual != set(refs):
            raise RuntimeError(f"memory_100 stage does not match snapshot: {pages_root}")
        for page, ref in zip(snapshot.pages, refs, strict=True):
            target = pages_root.joinpath(*ref.parts)
            if target.read_bytes().decode("utf-8") != page.body:
                raise RuntimeError(f"memory_100 stage does not match snapshot: {pages_root}")

    async def _commit_memories(
        self, workspace_id: UUID, audiences: tuple[AudienceBinding, ...]
    ) -> None:
        members = {binding.alias: binding.member_id for binding in audiences}
        store = memory_store.MemoryStore(
            index=self.index,
            embed=self.embed,
            transaction=workspace_tx,
            workspace_id=workspace_id,
        )
        for memory in self.snapshot.memories:
            member_id = members[memory.audience]
            await store.commit(
                memory_store.MemoryWrite(
                    subject=(SHARED_SUBJECT if member_id is None else member_subject(member_id)),
                    body=memory.body,
                    item_class=memory.item_class,
                    memory_kind=memory.memory_kind,
                    confidence=memory.confidence,
                    source_ref=memory.source_ref,
                )
            )

    async def _drain_memory_index(self) -> None:
        indexer = memory_store.MemoryIndexer(
            index=self.index,
            embed=self.embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
        )
        while True:
            async with workspace_tx() as connection:
                before = (
                    await connection.execute(
                        sa.select(sa.func.count())
                        .select_from(memory_store.memory_item)
                        .where(memory_store.memory_item.c.embedding_digest.is_(None))
                    )
                ).scalar_one()
            if before == 0:
                return
            await indexer.run()
            async with workspace_tx() as connection:
                after = (
                    await connection.execute(
                        sa.select(sa.func.count())
                        .select_from(memory_store.memory_item)
                        .where(memory_store.memory_item.c.embedding_digest.is_(None))
                    )
                ).scalar_one()
            if after >= before:
                raise RuntimeError("memory_100 memory index made no progress")

    async def _drain_page_consumers(self) -> None:
        runner = PageChangeRunner(
            manifests=self.manifests,
            pages=CorePageFeed(blob=self.blob),
            index=self.index,
            embed=self.embed,
            blob=self.blob,
            registry=None,
        )
        consumers = runner.consumers()
        required = {("memory", "index_pages"), ("memory", "derive_facts")}
        available = {(consumer.extension, consumer.discriminator) for consumer in consumers}
        if not required <= available:
            raise RuntimeError("memory_100 expected both memory page-change consumers")
        for consumer in consumers:
            await runner.drive(consumer)


async def _run(config: Config, snapshot: Path, state_root: Path) -> CorpusReadiness:
    init_db(config.database.url)
    key = os.environ.get(config.credentials.key_env)
    credentials = CredentialStore(fernet=Fernet(key.encode())) if key else None
    init_workspace_credentials(credentials)
    try:
        manifests = load_manifests(config.pack.name)
        embed = embed_backend(manifests, config.memory.embed_backend, credentials)
        index = index_backend(manifests, config.memory.index_backend, credentials)
        return await Memory100Materializer.from_snapshot(
            snapshot,
            state_root,
            blob=blob_store_for(config.blob),
            index=index,
            embed=embed,
            manifests=manifests,
            postgres=config.database.url.startswith("postgresql"),
        ).run()
    finally:
        init_workspace_credentials(None)
        await dispose_db()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.memory_100.materialize")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    readiness = asyncio.run(_run(load_config(), args.snapshot, args.state))
    output = args.state / readiness.snapshot_digest.removeprefix("sha256:") / "readiness.json"
    output.write_text(
        json.dumps(readiness.model_dump(mode="json"), sort_keys=True, separators=(",", ":")) + "\n"
    )
    print(output)


if __name__ == "__main__":
    main()
