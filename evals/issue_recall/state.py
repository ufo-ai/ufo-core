"""What one materialized `issue_recall` corpus attests: the workspace and folder source it landed
in, every fixture page's durable row, the durable-memory owners the memory extension derived from
each page, and the ambient haystack those owners compete against.

The grader can only score what the auto-inject path can inject, and that path recalls `memory_item`
rows — so a fixture page reaches recall through the facts `derive_facts` distilled from it. This
readiness is the map from page to those facts, read off the `created_from_page_id` link the
derivation stamps; `source_ref` is a free-form note and carries no page. A page that derived nothing
is recorded with no owners, which the leaf reports as unmapped rather than scoring as a miss. Every
other durable row must be a declared ambient memory: a workspace holding memory from neither source
would make the distractor accounting a guess, so an undeclared row fails attestation.
"""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel
from ufo_ext_memory.store import mem_page, memory_item

from evals.issue_recall.corpus import Ambient, RenderedPage, corpus_digest
from ufo.blob import BlobStore
from ufo.db import workspace_tx
from ufo.runtime.sources.sync import (
    FOLDER_BACKEND,
    source_body_ref_matches,
)
from ufo.schema import tables


class PageOwners(BaseModel):
    """One fixture page, its durable page row, and the derived facts recall can inject for it."""

    source_ref: str
    page_id: UUID
    memory_ids: tuple[UUID, ...]


class AmbientOwner(BaseModel):
    """One declared ambient memory and the durable row holding it."""

    ref: str
    memory_id: UUID


class CorpusReadiness(BaseModel):
    corpus_digest: str
    workspace_id: UUID
    source_id: UUID
    pages_root: Path
    page_count: int
    fact_count: int
    chunk_count: int
    pages: tuple[PageOwners, ...]
    ambient: tuple[AmbientOwner, ...]


@dataclass(frozen=True)
class CorpusAttestor:
    """Prove the materialized corpus is whole and recallable, then record its page-to-fact map."""

    pages: tuple[RenderedPage, ...]
    ambient: tuple[Ambient, ...]
    workspace_id: UUID
    source_id: UUID
    pages_root: Path
    blob: BlobStore

    async def attest(self) -> CorpusReadiness:
        page_rows, memory_rows, mirror_rows, chunk_rows, source_row, cursors = await self._rows()
        self._settled_source(source_row)
        await self._landed_pages(page_rows)
        self._mirrored(page_rows, mirror_rows)
        fact_rows, ambient_rows = self._partitioned(page_rows, memory_rows)
        self._indexed(page_rows, memory_rows, chunk_rows)
        self._drained(page_rows, cursors)
        owners: dict[UUID, list[UUID]] = {row.uid: [] for row in page_rows}
        for row in fact_rows:
            owners[row.created_from_page_uid].append(row.id)
        by_identity = {page.source_ref: page for page in self.pages}
        by_uid = {row.uid: by_identity[row.source_identity] for row in page_rows}
        return CorpusReadiness(
            corpus_digest=corpus_digest(),
            workspace_id=self.workspace_id,
            source_id=self.source_id,
            pages_root=self.pages_root,
            page_count=len(page_rows),
            fact_count=len(fact_rows),
            chunk_count=len(chunk_rows),
            pages=tuple(
                PageOwners(
                    source_ref=by_uid[page_uid].source_ref,
                    page_id=page_uid,
                    memory_ids=tuple(sorted(memory_ids)),
                )
                for page_uid, memory_ids in sorted(
                    owners.items(), key=lambda item: by_uid[item[0]].source_ref
                )
            ),
            ambient=tuple(
                AmbientOwner(ref=row.source_ref, memory_id=row.id)
                for row in sorted(ambient_rows, key=lambda row: row.source_ref)
            ),
        )

    async def _rows(
        self,
    ) -> tuple[
        list[sa.Row], list[sa.Row], list[sa.Row], list[sa.Row], sa.Row | None, dict[str, object]
    ]:
        async with workspace_tx() as connection:
            page_rows = list(
                (
                    await connection.execute(
                        sa.select(tables.page).order_by(tables.page.c.revision, tables.page.c.uid)
                    )
                ).all()
            )
            fact_rows = list(
                (
                    await connection.execute(
                        sa.select(memory_item)
                        .where(
                            memory_item.c.superseded_by.is_(None),
                            memory_item.c.retired_at.is_(None),
                        )
                        .order_by(memory_item.c.id)
                    )
                ).all()
            )
            mirror_rows = list((await connection.execute(sa.select(mem_page))).all())
            chunk_rows = list(
                (
                    await connection.execute(
                        sa.text(
                            "select owner_kind, owner_id, embedding is null as embedding_missing "
                            "from chunk"
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
                            ("page_change_cursor:index_pages", "page_change_cursor:derive_facts")
                        ),
                    )
                )
            ).all()
        return (
            page_rows,
            fact_rows,
            mirror_rows,
            chunk_rows,
            source_row,
            {row.key: row.value for row in cursor_rows},
        )

    def _settled_source(self, source_row: sa.Row | None) -> None:
        config = {"root": str(self.pages_root)}
        if source_row is None:
            raise RuntimeError("issue_recall folder source is missing")
        if source_row.uid != self.source_id:
            raise RuntimeError("issue_recall folder source is not the registered row")
        if (
            source_row.workspace_id != self.workspace_id
            or source_row.backend != FOLDER_BACKEND
            or source_row.config != config
        ):
            raise RuntimeError("issue_recall folder source does not match the staged fixture")
        if source_row.claimed_by is not None or source_row.consecutive_errors != 0:
            raise RuntimeError("issue_recall folder source is not settled")

    async def _landed_pages(self, page_rows: list[sa.Row]) -> None:
        expected = {page.source_ref: page for page in self.pages}
        if {row.source_identity for row in page_rows} != set(expected):
            raise RuntimeError("issue_recall page rows do not match the fixture")
        for row in page_rows:
            page = expected[row.source_identity]
            if (
                row.workspace_id != self.workspace_id
                or row.source_id != self.source_id
                or not source_body_ref_matches(row.body_ref, self.source_id, row.id, page.digest)
                or row.digest != page.digest
                or row.tombstone
            ):
                raise RuntimeError(f"issue_recall page {page.source_ref!r} is not ready")
            body = await self.blob.get(row.body_ref)
            if body.decode() != page.body:
                raise RuntimeError(f"issue_recall page {page.source_ref!r} body differs")

    @staticmethod
    def _mirrored(page_rows: list[sa.Row], mirror_rows: list[sa.Row]) -> None:
        if {row.page_uid for row in mirror_rows} != {row.uid for row in page_rows}:
            raise RuntimeError("issue_recall page mirrors are not ready")

    def _partitioned(
        self, page_rows: list[sa.Row], memory_rows: list[sa.Row]
    ) -> tuple[list[sa.Row], list[sa.Row]]:
        """Split the workspace's durable memory into page-derived facts and the declared ambient
        haystack, refusing a row from neither and an ambient ref that landed twice or not at all."""
        page_ids = {row.uid for row in page_rows}
        declared = {memory.ref for memory in self.ambient}
        facts = [row for row in memory_rows if row.created_from_page_uid in page_ids]
        ambient = [row for row in memory_rows if row.source_ref in declared]
        accounted = {row.id for row in facts} | {row.id for row in ambient}
        foreign = [row for row in memory_rows if row.id not in accounted]
        if foreign:
            sample = [
                f"{row.item_class}/{row.memory_kind} ref={row.source_ref!r} {row.body[:70]!r}"
                for row in foreign[:3]
            ]
            raise RuntimeError(
                f"{len(foreign)} of {len(memory_rows)} durable memory rows are neither derived "
                f"from the issue_recall fixture nor declared ambient memories "
                f"({len(facts)} facts, {len(ambient)} ambient, {len(page_ids)} page ids, "
                f"{len(declared)} declared refs): {sample}"
            )
        if sorted(row.source_ref for row in ambient) != sorted(declared):
            raise RuntimeError("the issue_recall ambient haystack is incomplete or duplicated")
        unembedded = tuple(row for row in memory_rows if row.embedding_digest is None)
        if unembedded:
            raise RuntimeError(f"{len(unembedded)} durable memories are not indexed")
        return facts, ambient

    @staticmethod
    def _indexed(
        page_rows: list[sa.Row], memory_rows: list[sa.Row], chunk_rows: list[sa.Row]
    ) -> None:
        if any(row.embedding_missing for row in chunk_rows):
            raise RuntimeError("issue_recall index chunks are not embedded")
        owners = {(row.owner_kind, row.owner_id) for row in chunk_rows}
        missing = {("page", str(row.uid)) for row in page_rows} - owners
        missing |= {("memory_item", str(row.id)) for row in memory_rows} - owners
        if missing:
            raise RuntimeError(f"issue_recall owners hold no index chunk: {sorted(missing)}")

    @staticmethod
    def _drained(page_rows: list[sa.Row], cursors: dict[str, object]) -> None:
        high_water = f"{page_rows[-1].revision}|{page_rows[-1].uid}"
        expected = {
            "page_change_cursor:index_pages": high_water,
            "page_change_cursor:derive_facts": high_water,
        }
        if cursors != expected:
            raise RuntimeError("issue_recall page consumers have pending changes")
