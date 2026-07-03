"""Content sources: a backend fetches documents into pages, a core job syncs them on an interval.

`SourceBackend` is the seam — `fetch(config, cursor) -> SyncResult` returns the documents a source
currently holds plus a resume cursor. Core ships `FolderSource` (a local directory); S3/GitHub/
connector backends are extensions. `SyncDriver` is the core sync job: it claims due sources (one
worker per source, dialect-native — Postgres `FOR UPDATE SKIP LOCKED`, SQLite the single writer),
fetches, writes each page's body to the blob store, and upserts page rows — skipping ones unchanged
by digest and tombstoning ones whose document is gone. It writes NO chunks: a synced page carries a
NULL `embedding_digest`, marking it due for the page index job (the sole chunk producer). The driver
polls; it never fires on the writes it makes."""

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import sqlalchemy as sa
from pydantic import BaseModel

from selfhost.blob import BlobStore
from selfhost.config import SourceConfig, SourceEntry
from selfhost.db import workspace_tx
from selfhost.memory.service import SHARED_SUBJECT
from selfhost.o11y import log
from selfhost.schema import tables

FOLDER_BACKEND = "folder"
SOURCE_SYNC_JOB = "source_sync"
SOURCE_SYNC_SCHEDULE = "0 * * * * *"
SOURCE_SYNC_INTERVAL_SECONDS = 60
CLAIM_LEASE_SECONDS = 300
DUE_BATCH_MAX_SOURCES = 50
SOURCE_BLOB_PREFIX = "sources"


class Page(BaseModel):
    """One fetched document: its stable key within the source, a content digest for change
    detection, the subject that scopes its recall, and the body the driver stores in the blob."""

    source_ref: str
    digest: str
    subject: str
    body: str


class SyncResult(BaseModel):
    pages: tuple[Page, ...]
    next_cursor: str | None = None


class SourceBackend(Protocol):
    async def fetch(self, config: SourceConfig, cursor: str | None) -> SyncResult: ...


@dataclass(frozen=True)
class FolderSource:
    """Reads a local directory into pages: each file becomes one page keyed by its path relative to
    the root, digested by content, scoped to the shared subject. A full scan each sync — the driver
    skips unchanged pages by digest and tombstones pages whose file is gone. A file removed from a
    present folder tombstones its page; the whole folder going missing instead raises, so the sync
    fails closed (a transient mount blip can't sweep the index) — purge a folder's docs by emptying
    it or removing the source, never by deleting the folder."""

    async def fetch(self, config: SourceConfig, cursor: str | None) -> SyncResult:
        entries = await asyncio.to_thread(self._read, Path(config.root))
        pages = tuple(
            Page(
                source_ref=source_ref,
                digest="sha256:" + hashlib.sha256(text.encode()).hexdigest(),
                subject=SHARED_SUBJECT,
                body=text,
            )
            for source_ref, text in entries
        )
        return SyncResult(pages=pages, next_cursor=None)

    @staticmethod
    def _read(root: Path) -> tuple[tuple[str, str], ...]:
        if not root.is_dir():
            raise FileNotFoundError(f"source folder not found: {root}")
        return tuple(
            (str(path.relative_to(root)), path.read_text(encoding="utf-8"))
            for path in sorted(root.rglob("*"))
            if path.is_file()
        )


async def register_sources(configured: tuple[SourceEntry, ...]) -> None:
    """Ensure a source row exists for each configured `[[sources]]` entry. The row id is derived
    from the workspace, backend, and config, so a restart re-registers the same rows without
    duplicating them. Runs once at boot, off the sync poll."""
    if not configured:
        return
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
        for entry in configured:
            config = entry.config.model_dump()
            source_id = uuid5(
                NAMESPACE_URL,
                f"{workspace_id}/source/{entry.backend}/{json.dumps(config, sort_keys=True)}",
            )
            present = (
                await connection.execute(
                    sa.select(tables.source.c.id).where(tables.source.c.id == source_id)
                )
            ).one_or_none()
            if present is not None:
                continue
            await connection.execute(
                sa.insert(tables.source).values(
                    id=source_id,
                    workspace_id=workspace_id,
                    backend=entry.backend,
                    config=config,
                    cursor=None,
                    next_sync_at=now,
                    claimed_by=None,
                    claim_expires_at=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )


@dataclass(frozen=True)
class ClaimedSource:
    source_id: UUID
    backend: str
    config: SourceConfig
    cursor: str | None


@dataclass(frozen=True)
class SyncDriver:
    """The core sync job: claim due sources, fetch each backend, commit its pages. Runs downward —
    claim, fetch, commit — one source at a time, so a slow backend never blocks the claim."""

    backends: Mapping[str, SourceBackend]
    blob: BlobStore
    postgres: bool

    async def run(self) -> None:
        claim = uuid4().hex
        for source in await self._claim_due(claim):
            try:
                result = await self._fetch(source)
                await self._commit(source, result)
            except Exception as error:
                log(
                    "source_sync.failed",
                    source_id=str(source.source_id),
                    backend=source.backend,
                    error_class=type(error).__name__,
                )
                await self._release(source)

    async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]:
        now = datetime.now(UTC)
        due = (
            sa.select(
                tables.source.c.id,
                tables.source.c.backend,
                tables.source.c.config,
                tables.source.c.cursor,
            )
            .where(
                tables.source.c.next_sync_at <= now,
                sa.or_(
                    tables.source.c.claimed_by.is_(None),
                    tables.source.c.claim_expires_at < now,
                ),
            )
            .limit(DUE_BATCH_MAX_SOURCES)
        )
        if self.postgres:
            due = due.with_for_update(skip_locked=True)
        expires = now + timedelta(seconds=CLAIM_LEASE_SECONDS)
        async with workspace_tx() as connection:
            rows = (await connection.execute(due)).mappings().all()
            if rows:
                await connection.execute(
                    sa.update(tables.source)
                    .values(claimed_by=claim, claim_expires_at=expires, updated_at=sa.func.now())
                    .where(tables.source.c.id.in_([row["id"] for row in rows]))
                )
        return tuple(
            ClaimedSource(
                source_id=row["id"],
                backend=row["backend"],
                config=SourceConfig.model_validate(row["config"]),
                cursor=row["cursor"],
            )
            for row in rows
        )

    async def _fetch(self, source: ClaimedSource) -> SyncResult:
        backend = self.backends.get(source.backend)
        if backend is None:
            raise RuntimeError(f"no source backend for {source.backend!r}")
        return await backend.fetch(source.config, source.cursor)

    async def _commit(self, source: ClaimedSource, result: SyncResult) -> None:
        prior = await self._prior_pages(source.source_id)
        fetched: list[UUID] = []
        changed: list[tuple[UUID, str, str, str]] = []
        for page in result.pages:
            page_id = uuid5(NAMESPACE_URL, f"{source.source_id}/page/{page.source_ref}")
            fetched.append(page_id)
            existing = prior.get(page_id)
            if existing is None or existing[0] != page.digest or existing[1]:
                body_ref = f"{SOURCE_BLOB_PREFIX}/{source.source_id}/{page_id}"
                await self.blob.put(body_ref, page.body.encode())
                changed.append((page_id, body_ref, page.digest, page.subject))
        await self._write(source, result.next_cursor, changed, fetched)

    async def _prior_pages(self, source_id: UUID) -> dict[UUID, tuple[str, bool]]:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(
                        tables.page.c.id, tables.page.c.digest, tables.page.c.tombstone
                    ).where(tables.page.c.source_id == source_id)
                )
            ).mappings().all()
        return {row["id"]: (row["digest"], bool(row["tombstone"])) for row in rows}

    async def _write(
        self,
        source: ClaimedSource,
        next_cursor: str | None,
        changed: list[tuple[UUID, str, str, str]],
        fetched: list[UUID],
    ) -> None:
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            workspace_id = (
                await connection.execute(sa.select(tables.workspace.c.id))
            ).scalar_one()
            for page_id, body_ref, digest, subject in changed:
                updated = await connection.execute(
                    sa.update(tables.page)
                    .values(
                        digest=digest,
                        body_ref=body_ref,
                        subject=subject,
                        tombstone=False,
                        embedding_digest=None,
                        updated_at=sa.func.now(),
                    )
                    .where(tables.page.c.id == page_id)
                )
                if updated.rowcount == 0:
                    await connection.execute(
                        sa.insert(tables.page).values(
                            id=page_id,
                            workspace_id=workspace_id,
                            source_id=source.source_id,
                            digest=digest,
                            body_ref=body_ref,
                            subject=subject,
                            embedding_digest=None,
                            tombstone=False,
                            created_at=sa.func.now(),
                            updated_at=sa.func.now(),
                        )
                    )
            await connection.execute(
                sa.update(tables.page)
                .values(tombstone=True, embedding_digest=None, updated_at=sa.func.now())
                .where(
                    tables.page.c.source_id == source.source_id,
                    tables.page.c.tombstone.is_(False),
                    tables.page.c.id.not_in(fetched),
                )
            )
            await connection.execute(
                sa.update(tables.source)
                .values(
                    cursor=next_cursor,
                    next_sync_at=now + timedelta(seconds=SOURCE_SYNC_INTERVAL_SECONDS),
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
                .where(tables.source.c.id == source.source_id)
            )

    async def _release(self, source: ClaimedSource) -> None:
        """Free a source whose fetch or commit raised: clear its claim and push next_sync_at forward
        one interval, so one bad source neither blocks its siblings this run nor re-fails on every
        lease cycle — its cursor is untouched, so the next attempt resumes where it left off."""
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.source)
                .values(
                    next_sync_at=now + timedelta(seconds=SOURCE_SYNC_INTERVAL_SECONDS),
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
                .where(tables.source.c.id == source.source_id)
            )
