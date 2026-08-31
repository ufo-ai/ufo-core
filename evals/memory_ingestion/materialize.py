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
from pydantic import BaseModel

from evals.budget import EvalRunBudget
from evals.memory_ingestion.models import IngestionSnapshot, load_snapshot
from ufo.blob import WorkspaceBlobStore, blob_store_for
from ufo.config import Config, SourceConfig, SourceEntry, load_config
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.harness.containment import (
    ContainmentError,
    contained_file,
    contained_root,
    is_contained_regular,
)
from ufo.harness.models.registry import ModelRegistry, model_registry
from ufo.host.ext.loader import embed_backend, index_backend, load_manifests
from ufo.onboard.onboarding import DEFAULT_AGENT_MODEL, DEFAULT_AGENT_PROMPT
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.ext.context import ScopedStore, context_for
from ufo.runtime.ext.manifest import Manifest
from ufo.runtime.indexing import (
    OWNER_KIND_MEMORY_ITEM,
    OWNER_KIND_PAGE,
    EmbedClient,
    IndexBackend,
    TextChunker,
)
from ufo.runtime.jobs import PageChangeRunner
from ufo.runtime.sources.sync import (
    FOLDER_BACKEND,
    SOURCE_BLOB_PREFIX,
    CorePageFeed,
    FolderSource,
    SyncDriver,
    page_id_for,
    register_sources,
    source_row_id,
)
from ufo.runtime.workspace import init_workspace_credentials, ws
from ufo.schema import tables
from ufo.schema.records import (
    DEFAULT_AGENT_NAME,
    DEFAULT_REASONING_EFFORT,
    ReasoningEffort,
)

index_default = import_module("ufo_ext_index_default")
memory_manifest = import_module("ufo_ext_memory.manifest")
memory_store = import_module("ufo_ext_memory.store")

DERIVATION_MODEL = "gpt-5.6-luna"
STAGE_FILE_MODE = 0o644
ASKER_EMAIL = "memory-ingestion+asker@eval.invalid"


class DerivedEvidence(BaseModel):
    source_ref: str
    memory_ids: tuple[UUID, ...]


class IngestionReadiness(BaseModel):
    snapshot_digest: str
    corpus_digest: str
    derivation_model: str
    workspace_id: UUID
    source_id: UUID
    pages_root: Path
    page_count: int
    memory_count: int
    chunk_count: int
    asker_email: str
    evidence: tuple[DerivedEvidence, ...]


@dataclass(frozen=True)
class IngestionAttestor:
    snapshot: IngestionSnapshot
    workspace_id: UUID
    source_id: UUID
    pages_root: Path
    blob: WorkspaceBlobStore

    async def attest(self) -> IngestionReadiness:
        """Attest source ingestion, Luna-derived provenance, and memory-only indexing."""
        page_rows, memory_rows, chunk_rows, source_row, cursors = await self._rows()
        expected_source_id = source_row_id(
            self.workspace_id, FOLDER_BACKEND, {"root": str(self.pages_root)}
        )
        if self.source_id != expected_source_id:
            raise RuntimeError("memory_ingestion folder source id is not canonical")
        if source_row is None:
            raise RuntimeError("memory_ingestion folder source is missing")
        if (
            source_row.workspace_id != self.workspace_id
            or source_row.backend != FOLDER_BACKEND
            or source_row.config != {"root": str(self.pages_root)}
            or source_row.claimed_by is not None
            or source_row.consecutive_errors != 0
        ):
            raise RuntimeError("memory_ingestion folder source is not settled")
        expected_pages = {
            page_id_for(self.source_id, page.source_ref): page for page in self.snapshot.pages
        }
        actual_pages = {row.id: row for row in page_rows}
        if set(actual_pages) != set(expected_pages):
            raise RuntimeError("memory_ingestion page rows do not match the snapshot")
        for page_id, page in expected_pages.items():
            row = actual_pages[page_id]
            if (
                row.workspace_id != self.workspace_id
                or row.source_id != self.source_id
                or row.body_ref
                != (
                    f"{SOURCE_BLOB_PREFIX}/{self.source_id}/{page_id}/"
                    f"{page.digest.removeprefix('sha256:')}"
                )
                or row.digest != page.digest
                or row.subject != "shared"
                or row.tombstone
            ):
                raise RuntimeError(f"memory_ingestion page {page.source_ref!r} is not ready")
            body = await self.blob.get(row.body_ref)
            if body.decode() != page.body:
                raise RuntimeError(f"memory_ingestion page {page.source_ref!r} body differs")
        page_by_id = {page_id: expected_pages[page_id] for page_id in expected_pages}
        memory_by_page: dict[UUID, list[sa.Row]] = {}
        for row in memory_rows:
            if (
                row.created_from_page_id not in expected_pages
                or row.created_from_page_revision != actual_pages[row.created_from_page_id].revision
                or row.source_id != self.source_id
                or row.subject != "shared"
                or row.item_class != "fact"
                or row.embedding_digest is None
                or row.embedding_claimed_at is not None
                or row.superseded_by is not None
            ):
                raise RuntimeError("memory_ingestion derived memory rows are not ready")
            memory_by_page.setdefault(row.created_from_page_id, []).append(row)
        evidence_refs = sorted({ref for case in self.snapshot.cases for ref in case.evidence_refs})
        evidence = tuple(
            DerivedEvidence(
                source_ref=source_ref,
                memory_ids=tuple(
                    sorted(
                        (
                            row.id
                            for page_id, page in page_by_id.items()
                            if page.evidence_ref == source_ref
                            for row in memory_by_page.get(page_id, [])
                        ),
                        key=str,
                    )
                ),
            )
            for source_ref in evidence_refs
        )
        if any(row.owner_kind == OWNER_KIND_PAGE for row in chunk_rows):
            raise RuntimeError("memory_ingestion raw pages reached the recall index")
        indexed_ids = {
            UUID(row.owner_id) for row in chunk_rows if row.owner_kind == OWNER_KIND_MEMORY_ITEM
        }
        if indexed_ids != {row.id for row in memory_rows}:
            raise RuntimeError("memory_ingestion derived memories are not fully indexed")
        high_water = f"{page_rows[-1].revision}|{page_rows[-1].id}" if page_rows else None
        expected_cursors = {
            "page_change_cursor:derive_facts": high_water,
            "page_change_cursor:index_pages": high_water,
        }
        if cursors != expected_cursors:
            raise RuntimeError("memory_ingestion page consumers are not settled")
        return IngestionReadiness(
            snapshot_digest=self.snapshot.manifest.digest,
            corpus_digest=self._digest(page_rows, memory_rows, chunk_rows),
            derivation_model=DERIVATION_MODEL,
            workspace_id=self.workspace_id,
            source_id=self.source_id,
            pages_root=self.pages_root,
            page_count=len(page_rows),
            memory_count=len(memory_rows),
            chunk_count=len(chunk_rows),
            asker_email=ASKER_EMAIL,
            evidence=evidence,
        )

    async def _rows(
        self,
    ) -> tuple[list[sa.Row], list[sa.Row], list[sa.Row], sa.Row | None, dict[str, object]]:
        async with workspace_tx() as connection:
            page_rows = list(
                (
                    await connection.execute(
                        sa.select(tables.page).order_by(tables.page.c.revision, tables.page.c.id)
                    )
                ).all()
            )
            memory_rows = list(
                (
                    await connection.execute(
                        sa.select(memory_store.memory_item).order_by(memory_store.memory_item.c.id)
                    )
                ).all()
            )
            chunk_rows = list(
                (
                    await connection.execute(
                        sa.text(
                            "select chunk_digest, owner_kind, owner_id, subject, ordinal, text "
                            "from chunk order by owner_kind, owner_id, ordinal"
                        )
                    )
                ).all()
            )
            source_row = (
                await connection.execute(
                    sa.select(tables.source).where(tables.source.c.id == self.source_id)
                )
            ).one_or_none()
            cursor_rows = (
                await connection.execute(
                    sa.select(tables.ext_store.c.key, tables.ext_store.c.value).where(
                        tables.ext_store.c.workspace_id == self.workspace_id,
                        tables.ext_store.c.extension == memory_manifest.NAME,
                        tables.ext_store.c.key.in_(
                            (
                                "page_change_cursor:index_pages",
                                "page_change_cursor:derive_facts",
                            )
                        ),
                    )
                )
            ).all()
        return (
            page_rows,
            memory_rows,
            chunk_rows,
            source_row,
            {row.key: row.value for row in cursor_rows},
        )

    def _digest(
        self, page_rows: list[sa.Row], memory_rows: list[sa.Row], chunk_rows: list[sa.Row]
    ) -> str:
        records: list[tuple[str, ...]] = [
            ("page", str(row.id), str(row.revision), row.digest, row.subject) for row in page_rows
        ]
        records.extend(
            (
                "memory",
                str(row.id),
                row.body,
                str(row.created_from_page_id),
                str(row.created_from_page_revision),
                row.memory_kind,
                str(row.confidence),
            )
            for row in memory_rows
        )
        records.extend(
            (
                "chunk",
                row.owner_kind,
                row.owner_id,
                str(row.ordinal),
                hashlib.sha256(row.text.encode()).hexdigest(),
            )
            for row in chunk_rows
        )
        payload = json.dumps(sorted(records), separators=(",", ":")).encode()
        return f"sha256:{hashlib.sha256(payload).hexdigest()}"


@dataclass(frozen=True)
class MemoryIngestionMaterializer:
    snapshot: IngestionSnapshot
    pages_root: Path
    blob: WorkspaceBlobStore
    index: IndexBackend
    embed: EmbedClient
    manifests: tuple[Manifest, ...]
    registry: ModelRegistry
    background_model: str
    agent_reasoning: ReasoningEffort = DEFAULT_REASONING_EFFORT
    postgres: bool = False
    run_budget: EvalRunBudget | None = None

    @classmethod
    def from_snapshot(
        cls,
        root: Path,
        state_root: Path,
        *,
        blob: WorkspaceBlobStore,
        index: IndexBackend,
        embed: EmbedClient,
        manifests: tuple[Manifest, ...],
        registry: ModelRegistry,
        background_model: str,
        agent_reasoning: ReasoningEffort = DEFAULT_REASONING_EFFORT,
        postgres: bool = False,
        run_budget: EvalRunBudget | None = None,
    ) -> "MemoryIngestionMaterializer":
        """Load a snapshot and locate its deterministic staging directory."""
        snapshot = load_snapshot(root)
        pages_root = state_root / snapshot.manifest.digest.removeprefix("sha256:") / "pages"
        return cls(
            snapshot,
            pages_root.resolve(),
            blob,
            index,
            embed,
            manifests,
            registry,
            background_model,
            agent_reasoning,
            postgres,
            run_budget,
        )

    async def run(self) -> IngestionReadiness:
        """Ingest pages, derive facts with Luna, and attest memory-only recall state."""
        if self.background_model != DERIVATION_MODEL:
            raise ValueError(
                f"memory_ingestion requires background model {DERIVATION_MODEL!r}, "
                f"found {self.background_model!r}"
            )
        if not isinstance(self.index, index_default.DefaultIndex):
            raise TypeError("memory_ingestion materialization requires the default database index")
        workspace_id = uuid5(
            NAMESPACE_URL, f"memory_ingestion/workspace/{self.snapshot.manifest.digest}"
        )
        await self._create_workspace(workspace_id)
        if self.run_budget is not None:
            await self.run_budget.install(workspace_id)
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
            await self._derive_facts()
            await self._drain_memory_index()
            return await IngestionAttestor(
                snapshot=self.snapshot,
                workspace_id=workspace_id,
                source_id=source_id,
                pages_root=self.pages_root,
                blob=self.blob,
            ).attest()

    async def _create_workspace(self, workspace_id: UUID) -> None:
        async with workspace_tx() as connection:
            workspace_count = (
                await connection.execute(sa.select(sa.func.count()).select_from(tables.workspace))
            ).scalar_one()
            chunk_count = (
                await connection.execute(sa.text("select count(*) from chunk"))
            ).scalar_one()
            if workspace_count or chunk_count:
                raise RuntimeError(
                    "memory_ingestion materialization requires a clean dedicated database"
                )
            await asyncio.to_thread(self._stage_pages, self.snapshot, self.pages_root)
            await connection.execute(
                sa.insert(tables.workspace).values(
                    id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                )
            )
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=uuid5(workspace_id, "memory_ingestion/agent"),
                    workspace_id=workspace_id,
                    name=DEFAULT_AGENT_NAME,
                    prompt=DEFAULT_AGENT_PROMPT,
                    model=DEFAULT_AGENT_MODEL,
                    reasoning=self.agent_reasoning,
                    is_main=True,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.member).values(
                    id=uuid5(workspace_id, "memory_ingestion/asker"),
                    workspace_id=workspace_id,
                    email=ASKER_EMAIL,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

    @classmethod
    def _stage_pages(cls, snapshot: IngestionSnapshot, pages_root: Path) -> None:
        refs = cls._page_refs(snapshot)
        if pages_root.exists():
            cls._validate_stage(snapshot, refs, pages_root)
            return
        pages_root.parent.mkdir(parents=True, exist_ok=True)
        temporary_root = pages_root.with_name(f".{pages_root.name}.{uuid4().hex}.tmp")
        temporary_root.mkdir()
        try:
            for page, ref in zip(snapshot.pages, refs, strict=True):
                with contained_file(ref.as_posix(), temporary_root, create_parent=True) as target:
                    target.replace_bytes(page.body.encode(), STAGE_FILE_MODE)
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
    def _page_refs(snapshot: IngestionSnapshot) -> tuple[PurePosixPath, ...]:
        refs = tuple(PurePosixPath(page.source_ref) for page in snapshot.pages)
        ordered = sorted(refs, key=lambda ref: ref.parts)
        if any(left == right or left in right.parents for left, right in pairwise(ordered)):
            raise ValueError("memory_ingestion page refs contain a file or directory collision")
        return refs

    @staticmethod
    def _validate_stage(
        snapshot: IngestionSnapshot, refs: tuple[PurePosixPath, ...], pages_root: Path
    ) -> None:
        try:
            root = contained_root(pages_root)
        except ContainmentError as error:
            raise RuntimeError(
                f"memory_ingestion stage does not match snapshot: {pages_root}"
            ) from error
        actual = {
            PurePosixPath(path.relative_to(root).as_posix())
            for path in root.rglob("*")
            if is_contained_regular(path, root)
        }
        if actual != set(refs):
            raise RuntimeError(f"memory_ingestion stage does not match snapshot: {pages_root}")
        for page, ref in zip(snapshot.pages, refs, strict=True):
            body = page.body.encode()
            with contained_file(ref.as_posix(), root) as staged:
                if staged.read_bytes(len(body) + 1) != body:
                    raise RuntimeError(
                        f"memory_ingestion stage does not match snapshot: {pages_root}"
                    )

    async def _derive_facts(self) -> None:
        runner = PageChangeRunner(
            manifests=self.manifests,
            pages=CorePageFeed(blob=self.blob),
            index=self.index,
            embed=self.embed,
            blob=self.blob,
            registry=self.registry,
            background_model=self.background_model,
        )
        consumers = {
            (consumer.extension, consumer.discriminator): consumer
            for consumer in runner.consumers()
        }
        derive = (memory_manifest.NAME, "derive_facts")
        index = (memory_manifest.NAME, "index_pages")
        if not {derive, index} <= set(consumers):
            raise RuntimeError("memory_ingestion expected both memory page consumers")
        await runner.drive(consumers[derive])
        store = ScopedStore(extension=memory_manifest.NAME)
        high_water = await store.get("page_change_cursor:derive_facts")
        if self.snapshot.pages and not isinstance(high_water, str):
            raise RuntimeError("memory_ingestion fact derivation did not reach a cursor")
        await store.put("page_change_cursor:index_pages", high_water)

    async def _drain_memory_index(self) -> None:
        indexer = memory_store.MemoryIndexer(
            index=self.index,
            embed=self.embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            page_states=context_for(memory_manifest.NAME, frozenset()).page_states,
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
                raise RuntimeError("memory_ingestion memory index made no progress")


async def _run(
    config: Config,
    snapshot: Path,
    state_root: Path,
    run_budget: EvalRunBudget | None,
    agent_reasoning: ReasoningEffort,
) -> IngestionReadiness:
    if config.models.background_jobs_model != DERIVATION_MODEL:
        raise ValueError(
            f"memory_ingestion requires models.background_jobs_model = {DERIVATION_MODEL!r}"
        )
    init_db(config.database.url)
    key = os.environ.get(config.credentials.key_env)
    credentials = CredentialStore(fernet=Fernet(key.encode())) if key else None
    init_workspace_credentials(credentials)
    try:
        manifests = load_manifests(config.pack.name)
        registry = model_registry(config, manifests)
        embed = embed_backend(manifests, config.memory.embed_backend, credentials)
        index = index_backend(manifests, config.memory.index_backend, credentials)
        return await MemoryIngestionMaterializer.from_snapshot(
            snapshot,
            state_root,
            blob=WorkspaceBlobStore(backend=blob_store_for(config.blob)),
            index=index,
            embed=embed,
            manifests=manifests,
            registry=registry,
            background_model=config.models.background_jobs_model,
            agent_reasoning=agent_reasoning,
            postgres=config.database.url.startswith("postgresql"),
            run_budget=run_budget,
        ).run()
    finally:
        init_workspace_credentials(None)
        await dispose_db()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.memory_ingestion.materialize")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--run-id", type=UUID)
    parser.add_argument("--budget-micro-usd", type=int)
    parser.add_argument(
        "--agent-reasoning",
        choices=("auto", "off", "low", "medium", "high"),
        default=DEFAULT_REASONING_EFFORT,
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if (args.run_id is None) != (args.budget_micro_usd is None):
        parser.error("--run-id and --budget-micro-usd must be provided together")
    run_budget = None if args.run_id is None else EvalRunBudget(args.run_id, args.budget_micro_usd)
    readiness = asyncio.run(
        _run(load_config(), args.snapshot, args.state, run_budget, args.agent_reasoning)
    )
    output = args.state / readiness.snapshot_digest.removeprefix("sha256:") / "readiness.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(readiness.model_dump(mode="json"), sort_keys=True, separators=(",", ":")) + "\n"
    )
    print(output)


if __name__ == "__main__":
    main()
