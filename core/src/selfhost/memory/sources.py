"""Content sources: a backend fetches documents into pages, a core job syncs them on an interval.

`SourceBackend` is the seam — `fetch(config, cursor, auth) -> SyncResult` returns the documents a
source currently holds plus a resume cursor. `config` is the backend's own typed model (each backend
owns `config_model`, so a source carries typed parameters, never an untyped bag); `auth` is the
workspace the sync runs for, so a connector backend can resolve its provider token itself — core
never mints or holds one. Core ships `FolderSource` (a local directory); connector/S3/GitHub
backends are extensions registered through the `sources` Manifest point and sourced into
`SyncDriver.backends` at boot. `SyncDriver` is the core sync job: it claims due sources (one worker
per source, dialect-native — Postgres `FOR UPDATE SKIP LOCKED`, SQLite the single writer), fetches,
writes each page's body to the blob store, and upserts page rows — skipping ones unchanged by
digest, tombstoning the ones a full-snapshot fetch no longer holds or a delta fetch explicitly
deletes. It writes NO chunks: a changed page just bumps its `updated_at`, and `PageFeed` — the seam
threaded onto an extension's context — replays those changes to a downstream indexer under a
`(updated_at, id)` cursor. The core `page` row carries only substrate (digest, body, subject,
tombstone); derivation state lives in the indexer's own mirror. The driver polls; it never fires on
the writes it makes."""

import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar, Protocol, TypeVar
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import sqlalchemy as sa
from pydantic import BaseModel

from selfhost.blob import BlobStore
from selfhost.config import SourceConfig, SourceEntry
from selfhost.db import workspace_tx
from selfhost.o11y import log
from selfhost.schema import tables
from selfhost.subjects import SHARED_SUBJECT

FOLDER_BACKEND = "folder"
SOURCE_SYNC_JOB = "source_sync"
SOURCE_SYNC_SCHEDULE = "0 * * * * *"
SOURCE_SYNC_INTERVAL_SECONDS = 60
SOURCE_ERROR_BACKOFF_CAP_SECONDS = 3600
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
    """What a backend's `fetch` returns for one run: the documents the source holds now, the resume
    cursor, and how the driver reconciles what's gone. `snapshot=True` declares this fetch an
    authoritative full collection, so the driver tombstones every prior page absent from `pages`.
    A delta/incremental backend leaves `snapshot=False` and names removals in `deletes` (the
    source_refs to tombstone), so the driver tombstones only those and never sweeps the pages a
    partial fetch simply didn't mention."""

    pages: tuple[Page, ...]
    next_cursor: str | None = None
    deletes: tuple[str, ...] = ()
    snapshot: bool = False


class CursorExpired(Exception):
    """A `SourceBackend.fetch` raises this when its incremental cursor is no longer valid — a
    provider delta/sync token the source rejected (aged out, invalidated). The driver clears the
    stored cursor so the next run refetches from scratch, rather than re-failing on the dead cursor
    every interval forever."""


class StreamSkipped(RuntimeError):
    """A `SourceBackend.fetch` raises this when the provider refuses this source's stream in a way
    that is not a data failure — a missing OAuth scope, a disabled workspace object, a plan gate.
    The driver records the run as skipped, not failed: it commits no pages, so snapshot
    delete-detection never runs and the source's existing pages stand, and it reschedules at the
    normal interval with the cursor held and the error counter untouched, rather than backing the
    source off as if it had errored. A genuine fault still raises through and fails the run."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SourceAuth:
    """What the sync runner threads into a backend's `fetch` so it can reach its provider without
    core minting or holding a token: the workspace the sync runs for. A connector backend derives
    its broker user from `workspace_id` and reads the account's real access token itself (the
    broker is the extension's concern); the folder backend ignores it. A value object, never
    persisted."""

    workspace_id: UUID


ConfigT = TypeVar("ConfigT", bound=BaseModel)


class SourceBackend(Protocol[ConfigT]):
    """A content-source backend, keyed by its `backend` name onto `source` rows. `config_model` is
    the typed per-source config the driver validates a row's JSON `config` against — each backend
    owns its own model, so a source carries typed parameters, never an untyped bag. `fetch` returns
    the documents the source holds now plus a resume cursor, given that config, the prior `cursor`,
    and the workspace `auth` the runner threads. A backend that reads a complete collection each run
    returns `snapshot=True`, and the driver tombstones prior pages the fetch no longer holds; a
    delta/incremental backend returns `snapshot=False` and names removals explicitly in
    `SyncResult.deletes`, so the driver tombstones only those and never sweeps pages a partial fetch
    didn't mention. It raises `CursorExpired` when a stored incremental cursor is rejected by the
    provider, so the driver clears it and the next run refetches fresh. It raises `StreamSkipped`
    when the provider refuses the stream for this account (a missing scope, a plan gate), so the
    driver records the run skipped, not failed — no pages commit, nothing is tombstoned — and
    reschedules at the normal interval."""

    config_model: type[ConfigT]

    async def fetch(self, config: ConfigT, cursor: str | None, auth: SourceAuth) -> SyncResult: ...


@dataclass(frozen=True)
class FolderSource:
    """Reads a local directory into pages: each file becomes one page keyed by its path relative to
    the root, digested by content, scoped to the shared subject. A full scan each sync — the driver
    skips unchanged pages by digest and tombstones pages whose file is gone. A file removed from a
    present folder tombstones its page; the whole folder going missing instead raises, so the sync
    fails closed (a transient mount blip can't sweep the index) — purge a folder's docs by emptying
    it or removing the source, never by deleting the folder."""

    config_model: ClassVar[type[SourceConfig]] = SourceConfig

    async def fetch(self, config: SourceConfig, cursor: str | None, auth: SourceAuth) -> SyncResult:
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
        return SyncResult(pages=pages, next_cursor=None, snapshot=True)

    @staticmethod
    def _read(root: Path) -> tuple[tuple[str, str], ...]:
        if not root.is_dir():
            raise FileNotFoundError(f"source folder not found: {root}")
        return tuple(
            (str(path.relative_to(root)), path.read_text(encoding="utf-8"))
            for path in sorted(root.rglob("*"))
            if path.is_file()
        )


def source_row_id(workspace_id: UUID, backend: str, config: Mapping[str, object]) -> UUID:
    """The deterministic id of a source row for this workspace + backend + config, so a restart
    (folder `[[sources]]`) or a re-registration (an extension's `register_source`) settles on the
    same row rather than duplicating the sync."""
    return uuid5(
        NAMESPACE_URL,
        f"{workspace_id}/source/{backend}/{json.dumps(dict(config), sort_keys=True)}",
    )


def page_id_for(source_id: UUID, source_ref: str) -> UUID:
    """The deterministic id of a page within a source, so an upsert, a re-fetch of an unchanged
    document, and an explicit `deletes` entry for one `source_ref` all settle on the same row."""
    return uuid5(NAMESPACE_URL, f"{source_id}/page/{source_ref}")


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
            source_id = source_row_id(workspace_id, entry.backend, config)
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
    workspace_id: UUID
    backend: str
    config: Mapping[str, object]
    cursor: str | None
    consecutive_errors: int


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
            except StreamSkipped as skipped:
                log(
                    "source_sync.skipped",
                    source_id=str(source.source_id),
                    backend=source.backend,
                    reason=skipped.reason,
                )
                await self._skip(source)
            except Exception as error:
                cursor_reset = isinstance(error, CursorExpired)
                log(
                    "source_sync.failed",
                    source_id=str(source.source_id),
                    backend=source.backend,
                    error_class=type(error).__name__,
                    cursor_reset=cursor_reset,
                )
                await self._release(source, cursor_reset)

    async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]:
        now = datetime.now(UTC)
        due = (
            sa.select(
                tables.source.c.id,
                tables.source.c.workspace_id,
                tables.source.c.backend,
                tables.source.c.config,
                tables.source.c.cursor,
                tables.source.c.consecutive_errors,
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
                workspace_id=row["workspace_id"],
                backend=row["backend"],
                config=row["config"],
                cursor=row["cursor"],
                consecutive_errors=row["consecutive_errors"],
            )
            for row in rows
        )

    async def _fetch(self, source: ClaimedSource) -> SyncResult:
        backend = self.backends.get(source.backend)
        if backend is None:
            raise RuntimeError(f"no source backend for {source.backend!r}")
        config = backend.config_model.model_validate(source.config)
        auth = SourceAuth(workspace_id=source.workspace_id)
        return await backend.fetch(config, source.cursor, auth)

    async def _commit(self, source: ClaimedSource, result: SyncResult) -> None:
        prior = await self._prior_pages(source.source_id)
        fetched: list[UUID] = []
        changed: list[tuple[UUID, str, str, str]] = []
        for page in result.pages:
            page_id = page_id_for(source.source_id, page.source_ref)
            fetched.append(page_id)
            existing = prior.get(page_id)
            if existing is None or existing[0] != page.digest or existing[1]:
                body_ref = f"{SOURCE_BLOB_PREFIX}/{source.source_id}/{page_id}"
                await self.blob.put(body_ref, page.body.encode())
                changed.append((page_id, body_ref, page.digest, page.subject))
        deleted = [page_id_for(source.source_id, ref) for ref in result.deletes]
        await self._write(source, result.next_cursor, changed, fetched, deleted, result.snapshot)

    async def _prior_pages(self, source_id: UUID) -> dict[UUID, tuple[str, bool]]:
        async with workspace_tx() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(
                            tables.page.c.id, tables.page.c.digest, tables.page.c.tombstone
                        ).where(tables.page.c.source_id == source_id)
                    )
                )
                .mappings()
                .all()
            )
        return {row["id"]: (row["digest"], bool(row["tombstone"])) for row in rows}

    async def _write(
        self,
        source: ClaimedSource,
        next_cursor: str | None,
        changed: list[tuple[UUID, str, str, str]],
        fetched: list[UUID],
        deleted: list[UUID],
        snapshot: bool,
    ) -> None:
        """Page `created_at`/`updated_at` are stamped with one microsecond wall-clock `now` per
        write, not the database's second-precision clock: `PageFeed`'s `(updated_at, id)` cursor
        must strictly advance on every change, so a page re-written in the same second it was first
        indexed is not missed."""
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
            for page_id, body_ref, digest, subject in changed:
                updated = await connection.execute(
                    sa.update(tables.page)
                    .values(
                        digest=digest,
                        body_ref=body_ref,
                        subject=subject,
                        tombstone=False,
                        updated_at=now,
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
                            tombstone=False,
                            created_at=now,
                            updated_at=now,
                        )
                    )
            if deleted:
                await connection.execute(
                    sa.update(tables.page)
                    .values(tombstone=True, updated_at=now)
                    .where(
                        tables.page.c.source_id == source.source_id,
                        tables.page.c.tombstone.is_(False),
                        tables.page.c.id.in_(deleted),
                    )
                )
            if snapshot:
                await connection.execute(
                    sa.update(tables.page)
                    .values(tombstone=True, updated_at=now)
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
                    consecutive_errors=0,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
                .where(tables.source.c.id == source.source_id)
            )

    async def _release(self, source: ClaimedSource, cursor_reset: bool) -> None:
        """Free a source whose fetch or commit raised: clear its claim, count the error, and push
        next_sync_at forward by a bounded exponential backoff (base interval doubling per
        consecutive error, capped) so a persistently-failing source neither blocks its siblings this
        run nor hammers its provider every lease cycle. On `CursorExpired` the stored cursor is
        cleared so the next run refetches from scratch; otherwise it resumes where it left off. A
        successful sync resets the counter and the interval in `_write`."""
        errors = source.consecutive_errors + 1
        backoff = min(
            SOURCE_SYNC_INTERVAL_SECONDS * 2 ** (errors - 1), SOURCE_ERROR_BACKOFF_CAP_SECONDS
        )
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.source)
                .values(
                    cursor=None if cursor_reset else source.cursor,
                    next_sync_at=now + timedelta(seconds=backoff),
                    consecutive_errors=errors,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
                .where(tables.source.c.id == source.source_id)
            )

    async def _skip(self, source: ClaimedSource) -> None:
        """A backend raised `StreamSkipped`: the source is intentionally unreadable this run (a
        missing scope, a plan gate), not failed. Free the claim and reschedule at the normal
        interval with the cursor held and the error counter reset — no pages committed, so snapshot
        delete-detection never runs and the source's existing pages stand."""
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.source)
                .values(
                    next_sync_at=now + timedelta(seconds=SOURCE_SYNC_INTERVAL_SECONDS),
                    consecutive_errors=0,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
                .where(tables.source.c.id == source.source_id)
            )


PAGE_FEED_BATCH_MAX = 50


@dataclass(frozen=True)
class PageChange:
    """One page's current state as the feed replays it: the inlined body (empty when tombstoned),
    the content digest, and `changed_at` — the page's `updated_at`, which is the cursor field."""

    page_id: UUID
    subject: str
    body: str
    digest: str
    tombstone: bool
    created_at: datetime
    changed_at: datetime


@dataclass(frozen=True)
class PageBatch:
    changes: tuple[PageChange, ...]
    next_cursor: str | None


class PageFeed(Protocol):
    """The page-substrate seam an indexer reads through `ExtensionContext.pages`: replay every page
    changed since a `changed_at|page_id` cursor, bodies inlined, in a bounded batch and total order
    (`ORDER BY updated_at, id`) — dialect-neutral and replay-safe, so a single-owner cursor advances
    monotonically and a restart resumes where it left off."""

    async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch: ...


@dataclass(frozen=True)
class CorePageFeed:
    """The core `PageFeed`: reads the `page` table in `(updated_at, id)` order after the cursor and
    inlines each non-tombstoned body from the blob store, bounding every batch to
    PAGE_FEED_BATCH_MAX so the inlined bodies stay a small payload. A tombstoned page carries an
    empty body; its reader drops the page's chunks and mirror on that signal."""

    blob: BlobStore

    async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch:
        query = (
            sa.select(
                tables.page.c.id,
                tables.page.c.subject,
                tables.page.c.body_ref,
                tables.page.c.digest,
                tables.page.c.tombstone,
                tables.page.c.created_at,
                tables.page.c.updated_at,
            )
            .order_by(tables.page.c.updated_at, tables.page.c.id)
            .limit(min(limit, PAGE_FEED_BATCH_MAX))
        )
        if cursor is not None:
            stamp_str, page_id_str = cursor.split("|", 1)
            stamp, page_id = datetime.fromisoformat(stamp_str), UUID(page_id_str)
            query = query.where(
                sa.or_(
                    tables.page.c.updated_at > stamp,
                    sa.and_(tables.page.c.updated_at == stamp, tables.page.c.id > page_id),
                )
            )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).mappings().all()
        changes: list[PageChange] = []
        for row in rows:
            body = "" if row["tombstone"] else (await self.blob.get(row["body_ref"])).decode()
            changes.append(
                PageChange(
                    page_id=row["id"],
                    subject=row["subject"],
                    body=body,
                    digest=row["digest"],
                    tombstone=bool(row["tombstone"]),
                    created_at=row["created_at"],
                    changed_at=row["updated_at"],
                )
            )
        next_cursor = f"{rows[-1]['updated_at'].isoformat()}|{rows[-1]['id']}" if rows else None
        return PageBatch(changes=tuple(changes), next_cursor=next_cursor)
