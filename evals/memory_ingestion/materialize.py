import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from importlib import import_module
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.budget import EvalRunBudget
from evals.memory_ingestion.models import IngestionSnapshot, canonical_json, load_snapshot
from ufo.blob import WorkspaceBlobStore, blob_store_for
from ufo.config import Config, SourceConfig, SourceEntry, load_config
from ufo.db import dispose_db, init_db, workspace_tx
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
    CorePageFeed,
    FolderSource,
    SyncDriver,
    feed_handle,
    register_sources,
    source_body_ref_matches,
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


class DerivedFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    source_ref: str = Field(min_length=1)
    body: str = Field(min_length=1)
    memory_kind: Literal["fact", "preference", "decision", "event", "task"]
    confidence: int = Field(ge=1, le=10)
    as_of: datetime


class DerivedCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    derivation_model: str
    facts: tuple[DerivedFact, ...]
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @classmethod
    def build(
        cls,
        snapshot_digest: str,
        derivation_model: str,
        facts: tuple[DerivedFact, ...],
    ) -> "DerivedCorpus":
        """Build the canonical, content-attested corpus shared by paired eval targets."""
        ordered = tuple(sorted(facts, key=_fact_key))
        return cls(
            snapshot_digest=snapshot_digest,
            derivation_model=derivation_model,
            facts=ordered,
            digest=_derived_corpus_digest(snapshot_digest, derivation_model, ordered),
        )

    @model_validator(mode="after")
    def canonical_and_attested(self) -> "DerivedCorpus":
        if self.facts != tuple(sorted(self.facts, key=_fact_key)):
            raise ValueError("memory_ingestion derived facts are not canonical")
        expected = _derived_corpus_digest(self.snapshot_digest, self.derivation_model, self.facts)
        if self.digest != expected:
            raise ValueError("memory_ingestion derived corpus digest does not match its facts")
        return self


class IngestionReadiness(BaseModel):
    snapshot_digest: str
    corpus_digest: str
    derived_corpus_digest: str
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
class IngestionMaterialization:
    readiness: IngestionReadiness
    corpus: DerivedCorpus


def _fact_key(fact: DerivedFact) -> tuple[str, str, str, int, str]:
    return (
        fact.source_ref,
        fact.body,
        fact.memory_kind,
        fact.confidence,
        fact.as_of.isoformat(),
    )


def _derived_corpus_digest(
    snapshot_digest: str, derivation_model: str, facts: tuple[DerivedFact, ...]
) -> str:
    payload = json.dumps(
        {
            "snapshot_digest": snapshot_digest,
            "derivation_model": derivation_model,
            "facts": [fact.model_dump(mode="json") for fact in facts],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _derived_corpus(
    snapshot: IngestionSnapshot, page_rows: list[sa.Row], memory_rows: list[sa.Row]
) -> DerivedCorpus:
    ref_by_uid = {row.uid: row.source_identity for row in page_rows}
    facts = tuple(
        sorted(
            (
                DerivedFact(
                    source_ref=ref_by_uid[row.created_from_page_uid],
                    body=row.body,
                    memory_kind=row.memory_kind,
                    confidence=row.confidence,
                    as_of=row.as_of,
                )
                for row in memory_rows
            ),
            key=_fact_key,
        )
    )
    return DerivedCorpus.build(snapshot.manifest.digest, DERIVATION_MODEL, facts)


@dataclass(frozen=True)
class IngestionAttestor:
    snapshot: IngestionSnapshot
    workspace_id: UUID
    source_id: UUID
    pages_root: Path
    blob: WorkspaceBlobStore
    expected_corpus: DerivedCorpus | None = None

    async def attest(self) -> IngestionMaterialization:
        """Attest source ingestion, Luna-derived provenance, and memory-only indexing."""
        page_rows, memory_rows, chunk_rows, source_row, cursors = await self._rows()
        if source_row is None:
            raise RuntimeError("memory_ingestion folder source is missing")
        if source_row.uid != self.source_id:
            raise RuntimeError("memory_ingestion folder source is not the registered row")
        if (
            source_row.workspace_id != self.workspace_id
            or source_row.backend != FOLDER_BACKEND
            or source_row.config != {"root": str(self.pages_root)}
            or source_row.claimed_by is not None
            or source_row.consecutive_errors != 0
        ):
            raise RuntimeError("memory_ingestion folder source is not settled")
        expected_pages = {page.source_ref: page for page in self.snapshot.pages}
        actual_pages = {row.source_identity: row for row in page_rows}
        if set(actual_pages) != set(expected_pages):
            raise RuntimeError("memory_ingestion page rows do not match the snapshot")
        for identity, page in expected_pages.items():
            row = actual_pages[identity]
            if (
                row.workspace_id != self.workspace_id
                or row.source_uid != self.source_id
                or not source_body_ref_matches(row.body_ref, self.source_id, row.uid, page.digest)
                or row.digest != page.digest
                or row.subject != "shared"
                or row.tombstone
            ):
                raise RuntimeError(f"memory_ingestion page {page.source_ref!r} is not ready")
            body = await self.blob.get(row.body_ref)
            if body.decode() != page.body:
                raise RuntimeError(f"memory_ingestion page {page.source_ref!r} body differs")
        page_by_uid = {row.uid: expected_pages[row.source_identity] for row in page_rows}
        revision_by_uid = {row.uid: row.revision for row in page_rows}
        memory_by_page: dict[UUID, list[sa.Row]] = {}
        for row in memory_rows:
            derived_from = row.created_from_page_uid
            if (
                derived_from not in page_by_uid
                or row.created_from_page_revision != revision_by_uid[derived_from]
                or row.source_uid != source_row.uid
                or row.subject != "shared"
                or row.item_class != "fact"
                or row.embedding_digest is None
                or row.embedding_claimed_at is not None
                or row.superseded_by is not None
            ):
                raise RuntimeError("memory_ingestion derived memory rows are not ready")
            memory_by_page.setdefault(derived_from, []).append(row)
        evidence_refs = sorted({ref for case in self.snapshot.cases for ref in case.evidence_refs})
        evidence = tuple(
            DerivedEvidence(
                source_ref=source_ref,
                memory_ids=tuple(
                    sorted(
                        (
                            row.id
                            for page_uid, page in page_by_uid.items()
                            if page.evidence_ref == source_ref
                            for row in memory_by_page.get(page_uid, [])
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
        high_water = f"{page_rows[-1].revision}|{page_rows[-1].uid}" if page_rows else None
        expected_cursors = {
            "page_change_cursor:derive_facts": high_water,
            "page_change_cursor:index_pages": high_water,
        }
        if cursors != expected_cursors:
            raise RuntimeError("memory_ingestion page consumers are not settled")
        corpus = _derived_corpus(self.snapshot, page_rows, memory_rows)
        if self.expected_corpus is not None and corpus != self.expected_corpus:
            raise RuntimeError("memory_ingestion installed corpus differs from its producer")
        readiness = IngestionReadiness(
            snapshot_digest=self.snapshot.manifest.digest,
            corpus_digest=self._digest(page_rows, memory_rows, chunk_rows),
            derived_corpus_digest=corpus.digest,
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
        return IngestionMaterialization(readiness=readiness, corpus=corpus)

    async def _rows(
        self,
    ) -> tuple[list[sa.Row], list[sa.Row], list[sa.Row], sa.Row | None, dict[str, object]]:
        async with workspace_tx() as connection:
            page_rows = list(
                (
                    await connection.execute(
                        sa.select(tables.page).order_by(tables.page.c.revision, tables.page.c.uid)
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
                    sa.select(tables.source).where(tables.source.c.uid == self.source_id)
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
            ("page", str(row.uid), str(row.revision), row.digest, row.subject) for row in page_rows
        ]
        records.extend(
            (
                "memory",
                str(row.id),
                row.body,
                str(row.created_from_page_uid),
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
    corpus: DerivedCorpus | None = None

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
        corpus: DerivedCorpus | None = None,
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
            corpus,
        )

    async def run(self) -> IngestionMaterialization:
        """Ingest pages, install one derived corpus, and attest memory-only recall state."""
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
        if self.corpus is not None and self.corpus.snapshot_digest != self.snapshot.manifest.digest:
            raise ValueError("memory_ingestion derived corpus belongs to a different snapshot")
        if self.corpus is not None and self.corpus.derivation_model != DERIVATION_MODEL:
            raise ValueError(
                f"memory_ingestion corpus used {self.corpus.derivation_model!r}, "
                f"expected {DERIVATION_MODEL!r}"
            )
        await self._create_workspace(workspace_id)
        if self.run_budget is not None:
            await self.run_budget.install(workspace_id)
        entry = SourceEntry(
            backend="folder",
            config=SourceConfig(root=str(self.pages_root)),
        )
        with ws(workspace_id):
            ext = context_for(memory_manifest.NAME, frozenset())
            await ext.register_connection(FOLDER_BACKEND, account_id=feed_handle(entry.config))
            (source_id,) = await register_sources((entry,))
            await SyncDriver(
                backends={FOLDER_BACKEND: FolderSource()},
                blob=self.blob,
                postgres=self.postgres,
            ).run()
            if self.corpus is None:
                await self._derive_facts()
            else:
                await self._install_corpus(workspace_id, source_id, self.corpus)
            await self._drain_memory_index()
            return await IngestionAttestor(
                snapshot=self.snapshot,
                workspace_id=workspace_id,
                source_id=source_id,
                pages_root=self.pages_root,
                blob=self.blob,
                expected_corpus=self.corpus,
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
                target = temporary_root / ref
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(page.body.encode())
                target.chmod(STAGE_FILE_MODE)
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
        actual = {
            PurePosixPath(path.relative_to(pages_root).as_posix())
            for path in pages_root.rglob("*")
            if path.is_file()
        }
        if actual != set(refs):
            raise RuntimeError(f"memory_ingestion stage does not match snapshot: {pages_root}")
        for page, ref in zip(snapshot.pages, refs, strict=True):
            if (pages_root / ref).read_bytes() != page.body.encode():
                raise RuntimeError(f"memory_ingestion stage does not match snapshot: {pages_root}")

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

    async def _install_corpus(
        self, workspace_id: UUID, source_id: UUID, corpus: DerivedCorpus
    ) -> None:
        async with workspace_tx() as connection:
            page_rows = list(
                (
                    await connection.execute(
                        sa.select(
                            tables.page.c.uid,
                            tables.page.c.source_identity,
                            tables.page.c.revision,
                            tables.page.c.subject,
                        ).order_by(tables.page.c.revision, tables.page.c.uid)
                    )
                ).all()
            )
        page_by_ref = {row.source_identity: row.uid for row in page_rows}
        missing = sorted({fact.source_ref for fact in corpus.facts} - page_by_ref.keys())
        if missing:
            raise ValueError(f"memory_ingestion corpus names unknown pages: {', '.join(missing)}")
        pages = {row.uid: row for row in page_rows}
        ctx = context_for(memory_manifest.NAME, frozenset())
        store = memory_store.MemoryStore(
            index=self.index,
            embed=self.embed,
            transaction=workspace_tx,
            workspace_id=workspace_id,
            page_states=ctx.page_states,
        )
        kept: dict[UUID, frozenset[UUID]] = {}
        for fact in corpus.facts:
            page_id = page_by_ref[fact.source_ref]
            item_id = await store.commit(
                memory_store.MemoryWrite(
                    subject=pages[page_id].subject,
                    body=fact.body,
                    item_class="fact",
                    memory_kind=fact.memory_kind,
                    confidence=fact.confidence,
                    created_from_page_id=page_id,
                    created_from_page_revision=pages[page_id].revision,
                    source_id=source_id,
                    as_of=fact.as_of,
                )
            )
            kept[pages[page_id].uid] = kept.get(pages[page_id].uid, frozenset()) | {item_id}
        for page_id, item_ids in kept.items():
            await store.supersede_page_facts(page_id, item_ids)
        high_water = f"{page_rows[-1].revision}|{page_rows[-1].uid}" if page_rows else None
        cursor_store = ScopedStore(extension=memory_manifest.NAME)
        await cursor_store.put("page_change_cursor:derive_facts", high_water)
        await cursor_store.put("page_change_cursor:index_pages", high_water)

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
    corpus: DerivedCorpus | None,
) -> IngestionMaterialization:
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
            corpus=corpus,
        ).run()
    finally:
        init_workspace_credentials(None)
        await dispose_db()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.memory_ingestion.materialize")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--corpus-output", type=Path)
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
    corpus = (
        None if args.corpus is None else DerivedCorpus.model_validate_json(args.corpus.read_bytes())
    )
    materialization = asyncio.run(
        _run(load_config(), args.snapshot, args.state, run_budget, args.agent_reasoning, corpus)
    )
    readiness = materialization.readiness
    output = args.state / readiness.snapshot_digest.removeprefix("sha256:") / "readiness.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(readiness.model_dump(mode="json"), sort_keys=True, separators=(",", ":")) + "\n"
    )
    if args.corpus_output is not None:
        args.corpus_output.parent.mkdir(parents=True, exist_ok=True)
        args.corpus_output.write_bytes(canonical_json(materialization.corpus) + b"\n")
    print(output)


if __name__ == "__main__":
    main()
