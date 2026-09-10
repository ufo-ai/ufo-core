import hashlib
import json
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel

from evals.memory_100.models import Snapshot, SnapshotPage
from ufo.blob import BlobStore
from ufo.db import workspace_tx
from ufo.runtime.indexing import OWNER_KIND_MEMORY_ITEM, OWNER_KIND_PAGE, TextChunker
from ufo.runtime.sources.sync import (
    FOLDER_BACKEND,
    source_body_ref_matches,
)
from ufo.schema import tables

memory_store = import_module("ufo_ext_memory.store")


class AudienceBinding(BaseModel):
    alias: str
    email: str | None
    member_id: UUID | None


class EvidenceOwner(BaseModel):
    source_ref: str
    owner_kind: Literal["memory_item", "page"]
    owner_id: UUID
    subject: str


class CorpusReadiness(BaseModel):
    snapshot_digest: str
    corpus_digest: str
    workspace_id: UUID
    source_id: UUID
    pages_root: Path
    page_count: int
    memory_count: int
    chunk_count: int
    audiences: tuple[AudienceBinding, ...]
    asker_email: str
    evidence: tuple[EvidenceOwner, ...]


@dataclass(frozen=True)
class _ExpectedChunk:
    owner_kind: str
    owner_id: str
    subject: str
    ordinal: int
    chunk_digest: str
    text: str
    source_ref: str


@dataclass(frozen=True)
class _IndexedChunk:
    owner_kind: str
    owner_id: str
    subject: str
    ordinal: int
    chunk_digest: str
    text: str
    embedding_digest: str


@dataclass(frozen=True)
class CorpusAttestor:
    snapshot: Snapshot
    workspace_id: UUID
    source_id: UUID
    pages_root: Path
    audiences: tuple[AudienceBinding, ...]
    asker_email: str
    blob: BlobStore

    async def attest(self) -> CorpusReadiness:
        page_rows, memory_rows, mirror_rows, chunk_rows, source_row, cursors = await self._rows()
        if source_row is None:
            raise RuntimeError("memory_100 folder source is missing")
        if source_row.uid != self.source_id:
            raise RuntimeError("memory_100 folder source id is not canonical")
        if (
            source_row.workspace_id != self.workspace_id
            or source_row.backend != FOLDER_BACKEND
            or source_row.config != {"root": str(self.pages_root)}
        ):
            raise RuntimeError("memory_100 folder source does not match its staged snapshot")
        if source_row.claimed_by is not None or source_row.consecutive_errors != 0:
            raise RuntimeError("memory_100 folder source is not settled")

        subjects = {
            binding.alias: (
                "shared" if binding.member_id is None else f"member:{binding.member_id}"
            )
            for binding in self.audiences
        }
        page_by_identity = self._page_by_identity()
        expected_pages = {
            identity: (page, subjects[page.audience]) for identity, page in page_by_identity.items()
        }
        actual_pages = {row.source_identity: row for row in page_rows}
        if set(actual_pages) != set(expected_pages):
            raise RuntimeError("memory_100 page rows do not match the snapshot")
        for identity, (page, subject) in expected_pages.items():
            row = actual_pages[identity]
            if (
                row.workspace_id != self.workspace_id
                or row.source_uid != self.source_id
                or not source_body_ref_matches(row.body_ref, self.source_id, row.uid, page.digest)
                or row.digest != page.digest
                or row.subject != subject
                or row.tombstone
            ):
                raise RuntimeError(f"memory_100 page {page.source_ref!r} is not ready")
            blob_body = await self.blob.get(row.body_ref)
            if hashlib.sha256(blob_body).hexdigest() != page.digest.removeprefix("sha256:"):
                raise RuntimeError(f"memory_100 page {page.source_ref!r} blob digest differs")
            if blob_body.decode() != page.body:
                raise RuntimeError(f"memory_100 page {page.source_ref!r} blob body differs")

        expected_memories = {
            memory.source_ref: (
                memory.digest,
                subjects[memory.audience],
                memory.body,
                memory.item_class,
                memory.memory_kind,
                memory.confidence,
            )
            for memory in self.snapshot.memories
        }
        actual_memories = {row.source_ref: row for row in memory_rows}
        if set(actual_memories) != set(expected_memories):
            raise RuntimeError("memory_100 memory rows do not match the snapshot")
        for source_ref, expected in expected_memories.items():
            row = actual_memories[source_ref]
            digest, subject, body, item_class, memory_kind, confidence = expected
            if (
                row.embedding_digest != digest
                or row.embedding_claimed_at is not None
                or row.subject != subject
                or row.body != body
                or row.item_class != item_class
                or row.memory_kind != memory_kind
                or row.confidence != confidence
                or row.superseded_by is not None
            ):
                raise RuntimeError(f"memory_100 memory {source_ref!r} is not ready")

        expected_mirrors = {(row.uid, row.subject) for row in page_rows if not row.tombstone}
        actual_mirrors = {(row.page_uid, row.subject) for row in mirror_rows}
        if actual_mirrors != expected_mirrors:
            raise RuntimeError("memory_100 page mirrors are not ready")

        expected_chunks = self._expected_chunks(page_rows, memory_rows)
        if any(row.embedding_missing for row in chunk_rows):
            raise RuntimeError("memory_100 default-index chunks are not ready")
        indexed_chunks = tuple(
            _IndexedChunk(
                owner_kind=row.owner_kind,
                owner_id=row.owner_id,
                subject=row.subject,
                ordinal=row.ordinal,
                chunk_digest=row.chunk_digest,
                text=row.text,
                embedding_digest="sha256:"
                + hashlib.sha256(row.embedding_identity.encode()).hexdigest(),
            )
            for row in chunk_rows
        )
        actual_chunks = {
            (
                chunk.owner_kind,
                chunk.owner_id,
                chunk.subject,
                chunk.ordinal,
                chunk.chunk_digest,
                chunk.text,
            )
            for chunk in indexed_chunks
        }
        expected_chunk_rows = {
            (
                chunk.owner_kind,
                chunk.owner_id,
                chunk.subject,
                chunk.ordinal,
                chunk.chunk_digest,
                chunk.text,
            )
            for chunk in expected_chunks
        }
        if actual_chunks != expected_chunk_rows:
            raise RuntimeError("memory_100 default-index chunks are not ready")

        high_water = f"{page_rows[-1].revision}|{page_rows[-1].uid}" if page_rows else None
        expected_cursors = {
            "page_change_cursor:index_pages": high_water,
            "page_change_cursor:derive_facts": high_water,
        }
        if cursors != expected_cursors:
            raise RuntimeError("memory_100 page consumers have pending changes")

        evidence = tuple(
            sorted(
                (
                    *(
                        EvidenceOwner(
                            source_ref=page_by_identity[row.source_identity].source_ref,
                            owner_kind="page",
                            owner_id=row.uid,
                            subject=row.subject,
                        )
                        for row in page_rows
                    ),
                    *(
                        EvidenceOwner(
                            source_ref=row.source_ref,
                            owner_kind="memory_item",
                            owner_id=row.id,
                            subject=row.subject,
                        )
                        for row in memory_rows
                    ),
                ),
                key=lambda owner: (owner.source_ref, owner.owner_kind),
            )
        )
        return CorpusReadiness(
            snapshot_digest=self.snapshot.manifest.digest,
            corpus_digest=self._digest(page_rows, memory_rows, expected_chunks, indexed_chunks),
            workspace_id=self.workspace_id,
            source_id=self.source_id,
            pages_root=self.pages_root,
            page_count=len(page_rows),
            memory_count=len(memory_rows),
            chunk_count=len(chunk_rows),
            audiences=self.audiences,
            asker_email=self.asker_email,
            evidence=evidence,
        )

    async def _rows(
        self,
    ) -> tuple[
        list[sa.Row],
        list[sa.Row],
        list[sa.Row],
        list[sa.Row],
        sa.Row | None,
        dict[str, object],
    ]:
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
            mirror_rows = list(
                (
                    await connection.execute(
                        sa.select(memory_store.mem_page).order_by(memory_store.mem_page.c.page_uid)
                    )
                ).all()
            )
            embedding_identity = (
                "embedding::text" if connection.dialect.name == "postgresql" else "hex(embedding)"
            )
            chunk_rows = list(
                (
                    await connection.execute(
                        sa.text(
                            "select chunk_digest, owner_kind, owner_id, subject, ordinal, text, "
                            f"{embedding_identity} as embedding_identity, "
                            "embedding is null as embedding_missing from chunk "
                            "order by owner_kind, owner_id, ordinal"
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
                        tables.ext_store.c.extension == "memory",
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
            mirror_rows,
            chunk_rows,
            source_row,
            {row.key: row.value for row in cursor_rows},
        )

    def _expected_chunks(
        self, page_rows: list[sa.Row], memory_rows: list[sa.Row]
    ) -> tuple[_ExpectedChunk, ...]:
        page_by_identity = self._page_by_identity()
        memory_by_ref = {memory.source_ref: memory for memory in self.snapshot.memories}
        chunks: list[_ExpectedChunk] = []
        chunker = TextChunker()
        for row in page_rows:
            chunks.extend(
                _ExpectedChunk(
                    chunk.owner_kind,
                    chunk.owner_id,
                    chunk.subject,
                    chunk.ordinal,
                    chunk.chunk_digest,
                    chunk.text,
                    page_by_identity[row.source_identity].source_ref,
                )
                for chunk in chunker.chunk(
                    page_by_identity[row.source_identity].body,
                    OWNER_KIND_PAGE,
                    str(row.uid),
                    row.subject,
                )
            )
        for row in memory_rows:
            chunks.extend(
                _ExpectedChunk(
                    chunk.owner_kind,
                    chunk.owner_id,
                    chunk.subject,
                    chunk.ordinal,
                    chunk.chunk_digest,
                    chunk.text,
                    row.source_ref,
                )
                for chunk in chunker.chunk(
                    memory_by_ref[row.source_ref].body,
                    OWNER_KIND_MEMORY_ITEM,
                    str(row.id),
                    row.subject,
                )
            )
        return tuple(chunks)

    def _digest(
        self,
        page_rows: list[sa.Row],
        memory_rows: list[sa.Row],
        chunks: tuple[_ExpectedChunk, ...],
        indexed_chunks: tuple[_IndexedChunk, ...],
    ) -> str:
        page_by_identity = self._page_by_identity()
        memory_by_ref = {memory.source_ref: memory for memory in self.snapshot.memories}
        embedding_by_chunk = {
            chunk.chunk_digest: chunk.embedding_digest for chunk in indexed_chunks
        }
        records: list[tuple[str, ...]] = [
            (
                "page",
                page_by_identity[row.source_identity].source_ref,
                row.subject,
                page_by_identity[row.source_identity].digest,
            )
            for row in page_rows
        ]
        records.extend(
            (
                "memory",
                row.source_ref,
                row.subject,
                memory_by_ref[row.source_ref].digest,
                row.item_class,
                row.memory_kind,
                str(row.confidence),
            )
            for row in memory_rows
        )
        records.extend(
            (
                "chunk",
                chunk.owner_kind,
                chunk.source_ref,
                chunk.subject,
                str(chunk.ordinal),
                "sha256:" + hashlib.sha256(chunk.text.encode()).hexdigest(),
                embedding_by_chunk[chunk.chunk_digest],
            )
            for chunk in chunks
        )
        payload = json.dumps(sorted(records), separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    def _page_by_identity(self) -> dict[str, SnapshotPage]:
        return {page.source_ref: page for page in self.snapshot.pages}
