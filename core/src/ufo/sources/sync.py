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
deletes. It writes NO chunks: the database assigns each material change a workspace-monotonic
revision, and `PageFeed` — the seam threaded onto an extension's context — replays those changes
to a downstream indexer under a `(revision, id)` cursor. The core `page` row carries source
substrate and browse metadata;
derivation state lives in the indexer's own mirror. The driver polls; it never fires on the writes
it makes. `source_sync.failed` and `source_sync_failed_total` name a failed provider stream;
`source_sync.ok` records what a successful run wrote."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar, Protocol, TypeVar
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.blob import BlobStore
from ufo.config import SourceConfig, SourceEntry
from ufo.connectors import AuthProxy, SourceCredentialResolver
from ufo.db import owner_tx, workspace_tx
from ufo.o11y import emit_metric, log, log_error
from ufo.schema import tables
from ufo.subjects import SHARED_SUBJECT

FOLDER_BACKEND = "folder"
SOURCE_SYNC_JOB = "source_sync"
SOURCE_SYNC_SCHEDULE = "0 * * * * *"
SOURCE_SYNC_INTERVAL_SECONDS = 60
SOURCE_ERROR_BACKOFF_CAP_SECONDS = 3600
CLAIM_LEASE_SECONDS = 300
DUE_BATCH_MAX_SOURCES = 50
SOURCE_BLOB_PREFIX = "sources"
SOURCE_SYNC_FAILED_METRIC = "source_sync_failed_total"
SYNC_PROVIDER_FAULT_MAX_CHARS = 500


def normalize_page_timestamp(value: str) -> str:
    if value.isdigit():
        try:
            raw = int(value)
            seconds = raw / 1_000 if len(value) >= 13 else raw
            parsed = datetime.fromtimestamp(seconds, UTC)
        except (OSError, OverflowError, ValueError) as error:
            raise ValueError(f"invalid page timestamp {value!r}") from error
    else:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError(f"invalid page timestamp {value!r}") from error
        if parsed.tzinfo is None:
            if len(value) == 10 and parsed.time() == datetime.min.time():
                parsed = parsed.replace(tzinfo=UTC)
            else:
                raise ValueError(f"page timestamp lacks a timezone: {value!r}")
        elif parsed.utcoffset() is None:
            raise ValueError(f"page timestamp lacks a timezone: {value!r}")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds")


class Page(BaseModel):
    """One fetched document: its stable key within the source, body, and browse metadata."""

    model_config = ConfigDict(extra="forbid")

    source_ref: str
    body: str
    stream: str = Field(min_length=1)
    title: str = Field(min_length=1)
    created_at: str | None = None
    updated_at: str | None = None

    @property
    def digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.body.encode()).hexdigest()

    @field_validator("created_at", "updated_at")
    @classmethod
    def normalize_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_page_timestamp(value)


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
    core minting or holding a token: the workspace the sync runs for, and the selected `auth_proxy`
    the deploy resolves connector credentials through. A connector backend asks `auth_proxy` for the
    `Credential` authenticating its provider (a broker's proxying transport, or a member-added key
    read host-side). `self_user_id` is the live external speaker resolved by a same-named surface,
    so a source can reject only records the product itself authored. The folder backend ignores
    both. A value object, never persisted."""

    workspace_id: UUID
    auth_proxy: AuthProxy | None = None
    self_user_id: str | None = None


SourceIdentityResolver = Callable[[UUID], Awaitable[str | None]]


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

    @property
    def config_model(self) -> type[ConfigT]: ...

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
                body=text,
                stream="files",
                title=source_ref,
            )
            for source_ref, text in entries
        )
        return SyncResult(pages=pages, next_cursor=None, snapshot=True)

    @staticmethod
    def _read(root: Path) -> tuple[tuple[str, str], ...]:
        if not root.is_dir():
            raise FileNotFoundError(f"source folder not found: {root}")
        return tuple(
            (str(path.relative_to(root)), path.read_bytes().decode("utf-8"))
            for path in sorted(root.rglob("*"))
            if path.is_file()
        )


def source_row_id(
    workspace_id: UUID,
    backend: str,
    config: Mapping[str, object],
    *,
    connection_id: UUID | None = None,
) -> UUID:
    """The deterministic source row id. Brokered rows include their connection generation."""
    generation = "" if connection_id is None else f"/connection/{connection_id}"
    return uuid5(
        NAMESPACE_URL,
        f"{workspace_id}/source/{backend}/{json.dumps(dict(config), sort_keys=True)}{generation}",
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
        main_agent_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(
                    tables.agent.c.workspace_id == workspace_id,
                    tables.agent.c.is_main.is_(True),
                )
            )
        ).scalar_one_or_none()
        if main_agent_id is None:
            raise RuntimeError("registering configured sources requires a main agent")
        insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
        for entry in configured:
            config = entry.config.model_dump()
            source_id = source_row_id(workspace_id, entry.backend, config)
            present = (
                await connection.execute(
                    sa.select(tables.source.c.id, tables.source.c.removed_at).where(
                        tables.source.c.id == source_id
                    )
                )
            ).one_or_none()
            if present is not None and present.removed_at is not None:
                continue
            if present is None:
                await connection.execute(
                    sa.insert(tables.source).values(
                        id=source_id,
                        workspace_id=workspace_id,
                        backend=entry.backend,
                        config=config,
                        subject=SHARED_SUBJECT,
                        owner_member_id=None,
                        cursor=None,
                        next_sync_at=now,
                        claimed_by=None,
                        claim_expires_at=None,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
                await connection.execute(
                    insert(tables.source_grant)
                    .values(
                        workspace_id=workspace_id,
                        source_id=source_id,
                        agent_id=main_agent_id,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            tables.source_grant.c.workspace_id,
                            tables.source_grant.c.source_id,
                            tables.source_grant.c.agent_id,
                        ]
                    )
                )


@dataclass(frozen=True)
class ClaimedSource:
    source_id: UUID
    workspace_id: UUID
    claim: str
    backend: str
    config: Mapping[str, object]
    subject: str
    owner_member_id: UUID | None
    connection_id: UUID | None
    cursor: str | None
    consecutive_errors: int
    claimed_at: datetime


def _rescheduled(claimed: ClaimedSource, when: datetime) -> sa.Case[datetime]:
    """The completing writer's `next_sync_at`: `when`, unless a resync was requested while this
    claim held the row. `schedule_source_sync` writes `next_sync_at=now` under a live claim by
    design — the lease serializes concurrent *syncs*, not the scheduling of the next one — so any
    value now later than the moment this claim was taken is a request this run never covered, and
    pushing it out to an interval or an error backoff would strand it. Requests are recognized by
    that ordering rather than by a second column, so `next_sync_at` stays the one truth about when
    a source is due."""
    return sa.case(
        (tables.source.c.next_sync_at <= claimed.claimed_at, when),
        else_=tables.source.c.next_sync_at,
    )


def _stream_tags(source: ClaimedSource) -> dict[str, str]:
    """Which provider stream a sync outcome belongs to, as metric dimensions. A connector row is one
    provider stream for one account (`ConnectorSourceConfig`), so the pair names the source a member
    registered; a backend whose config declares no stream — the folder source — reports an empty
    one, keeping the series bounded by the backends' own vocabulary."""
    return {"provider": source.backend, "stream": _config_value(source, "stream")}


def _config_value(source: ClaimedSource, key: str) -> str:
    value = source.config.get(key)
    return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class PageBrowse:
    id: UUID
    stream: str
    title: str
    record_created_at: str | None
    record_updated_at: str | None


@dataclass(frozen=True)
class ChangedPage:
    browse: PageBrowse
    body_ref: str
    digest: str


@dataclass(frozen=True)
class SyncDriver:
    """The core sync job: for the workspace the dispatcher bound, claim its due sources, fetch each
    backend, and commit its pages — the claim and every write go through RLS on that workspace. Runs
    downward — claim, fetch, commit — one source at a time, so a slow backend never blocks the run.
    `candidate_workspaces` names the workspaces holding a due source through one `owner_tx` read, so
    the dispatcher binds only those and a workspace with nothing due is never opened. On a
    per-tenant deploy `owner_tx` resolves to the single workspace, unchanged."""

    backends: Mapping[str, SourceBackend]
    blob: BlobStore
    postgres: bool
    source_credentials: SourceCredentialResolver | None = None
    identity_resolvers: Mapping[str, SourceIdentityResolver] = field(default_factory=dict)

    async def candidate_workspaces(self) -> tuple[UUID, ...]:
        """Workspaces holding a source due for sync — one distinct `workspace_id` per such
        workspace, found in a single `owner_tx` read (RLS bypass) so the dispatcher binds only those
        and a workspace with nothing due runs no per-workspace transaction on the tick."""
        now = datetime.now(UTC)
        async with owner_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.source.c.workspace_id)
                    .where(
                        tables.source.c.next_sync_at <= now,
                        tables.source.c.removed_at.is_(None),
                        sa.or_(
                            tables.source.c.claimed_by.is_(None),
                            tables.source.c.claim_expires_at < now,
                        ),
                    )
                    .distinct()
                )
            ).all()
        return tuple(row.workspace_id for row in rows)

    async def run(self) -> None:
        claim = uuid4().hex
        for source in await self._claim_due(claim):
            try:
                result = await self._fetch(source)
                await self._commit(source, result)
            except StreamSkipped as skipped:
                with suppress(Exception):
                    log(
                        "source_sync.skipped",
                        source_id=str(source.source_id),
                        **_stream_tags(source),
                        reason=skipped.reason,
                    )
                await self._skip(source)
            except Exception as error:
                cursor_reset = isinstance(error, CursorExpired)
                errors, next_sync_at = self._error_backoff(source, datetime.now(UTC))
                self._report_failed(source, error, cursor_reset, errors, next_sync_at)
                await self._release(source, cursor_reset, errors, next_sync_at)

    async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]:
        now = datetime.now(UTC)
        due = (
            sa.select(
                tables.source.c.id,
                tables.source.c.workspace_id,
                tables.source.c.backend,
                tables.source.c.config,
                tables.source.c.subject,
                tables.source.c.owner_member_id,
                tables.source.c.connection_id,
                tables.source.c.cursor,
                tables.source.c.consecutive_errors,
            )
            .where(
                tables.source.c.next_sync_at <= now,
                tables.source.c.removed_at.is_(None),
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
                claim=claim,
                backend=row["backend"],
                config=row["config"],
                subject=row["subject"],
                owner_member_id=row["owner_member_id"],
                connection_id=row["connection_id"],
                cursor=row["cursor"],
                consecutive_errors=row["consecutive_errors"],
                claimed_at=now,
            )
            for row in rows
        )

    async def _fetch(self, source: ClaimedSource) -> SyncResult:
        backend = self.backends.get(source.backend)
        if backend is None:
            raise RuntimeError(f"no source backend for {source.backend!r}")
        config = backend.config_model.model_validate(source.config)
        resolver = self.identity_resolvers.get(source.backend)
        self_user_id = None if resolver is None else await resolver(source.workspace_id)
        auth = SourceAuth(
            workspace_id=source.workspace_id,
            auth_proxy=(
                None
                if self.source_credentials is None
                else self.source_credentials.bind(
                    source.connection_id,
                    source.owner_member_id,
                )
            ),
            self_user_id=self_user_id,
        )
        return await backend.fetch(config, source.cursor, auth)

    async def _commit(self, source: ClaimedSource, result: SyncResult) -> None:
        prior = await self._prior_pages(source.source_id)
        fetched: list[UUID] = []
        changed: list[ChangedPage] = []
        metadata: list[PageBrowse] = []
        for page in result.pages:
            page_id = page_id_for(source.source_id, page.source_ref)
            fetched.append(page_id)
            existing = prior.get(page_id)
            browse = PageBrowse(
                id=page_id,
                stream=page.stream,
                title=page.title,
                record_created_at=page.created_at,
                record_updated_at=page.updated_at,
            )
            if existing is None or existing[:2] != (page.digest, False):
                body_ref = (
                    f"{SOURCE_BLOB_PREFIX}/{source.source_id}/{page_id}/"
                    f"{page.digest.removeprefix('sha256:')}"
                )
                await self.blob.put(body_ref, page.body.encode())
                changed.append(
                    ChangedPage(
                        browse=browse,
                        body_ref=body_ref,
                        digest=page.digest,
                    )
                )
            elif existing[2] != browse:
                metadata.append(browse)
        deleted = [page_id_for(source.source_id, ref) for ref in result.deletes]
        tombstoned = await self._write(
            source,
            result.next_cursor,
            changed,
            metadata,
            fetched,
            deleted,
            result.snapshot,
        )
        self._report_ok(source, len(result.pages), len(changed) + len(metadata), tombstoned)

    async def _prior_pages(self, source_id: UUID) -> dict[UUID, tuple[str, bool, PageBrowse]]:
        async with workspace_tx() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(
                            tables.page.c.id,
                            tables.page.c.digest,
                            tables.page.c.tombstone,
                            tables.page.c.stream,
                            tables.page.c.title,
                            tables.page.c.record_created_at,
                            tables.page.c.record_updated_at,
                        ).where(tables.page.c.source_id == source_id)
                    )
                )
                .mappings()
                .all()
            )
        return {
            row["id"]: (
                row["digest"],
                bool(row["tombstone"]),
                PageBrowse(
                    id=row["id"],
                    stream=row["stream"],
                    title=row["title"],
                    record_created_at=row["record_created_at"],
                    record_updated_at=row["record_updated_at"],
                ),
            )
            for row in rows
        }

    async def _write(
        self,
        source: ClaimedSource,
        next_cursor: str | None,
        changed: list[ChangedPage],
        metadata: list[PageBrowse],
        fetched: list[UUID],
        deleted: list[UUID],
        snapshot: bool,
    ) -> int:
        """Persist one fetched batch and return how many pages it tombstoned — the delete refs that
        named a live row plus the snapshot sweep, which names no refs at all. The database orders
        material changes for `PageFeed`."""
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
            authority = sa.select(tables.source.c.subject).where(
                tables.source.c.id == source.source_id,
                tables.source.c.workspace_id == workspace_id,
                tables.source.c.claimed_by == source.claim,
                tables.source.c.removed_at.is_(None),
            )
            if connection.dialect.name == "postgresql":
                authority = authority.with_for_update()
            subject = (await connection.execute(authority)).scalar_one_or_none()
            if subject is None:
                raise RuntimeError(f"source {source.source_id} disappeared during sync")
            for changed_page in changed:
                updated = await connection.execute(
                    sa.update(tables.page)
                    .values(
                        digest=changed_page.digest,
                        body_ref=changed_page.body_ref,
                        stream=changed_page.browse.stream,
                        title=changed_page.browse.title,
                        record_created_at=changed_page.browse.record_created_at,
                        record_updated_at=changed_page.browse.record_updated_at,
                        subject=subject,
                        tombstone=False,
                        updated_at=now,
                    )
                    .where(tables.page.c.id == changed_page.browse.id)
                )
                if updated.rowcount == 0:
                    await connection.execute(
                        sa.insert(tables.page).values(
                            id=changed_page.browse.id,
                            workspace_id=workspace_id,
                            source_id=source.source_id,
                            digest=changed_page.digest,
                            body_ref=changed_page.body_ref,
                            stream=changed_page.browse.stream,
                            title=changed_page.browse.title,
                            record_created_at=changed_page.browse.record_created_at,
                            record_updated_at=changed_page.browse.record_updated_at,
                            subject=subject,
                            tombstone=False,
                            created_at=now,
                            updated_at=now,
                        )
                    )
            for browse_page in metadata:
                await connection.execute(
                    sa.update(tables.page)
                    .values(
                        stream=browse_page.stream,
                        title=browse_page.title,
                        record_created_at=browse_page.record_created_at,
                        record_updated_at=browse_page.record_updated_at,
                    )
                    .where(tables.page.c.id == browse_page.id)
                )
            tombstoned = 0
            if deleted:
                swept = await connection.execute(
                    sa.update(tables.page)
                    .values(tombstone=True, updated_at=now)
                    .where(
                        tables.page.c.source_id == source.source_id,
                        tables.page.c.tombstone.is_(False),
                        tables.page.c.id.in_(deleted),
                    )
                )
                tombstoned += swept.rowcount
            if snapshot:
                swept = await connection.execute(
                    sa.update(tables.page)
                    .values(tombstone=True, updated_at=now)
                    .where(
                        tables.page.c.source_id == source.source_id,
                        tables.page.c.tombstone.is_(False),
                        tables.page.c.id.not_in(fetched),
                    )
                )
                tombstoned += swept.rowcount
            await connection.execute(
                sa.update(tables.page)
                .values(subject=subject, updated_at=now)
                .where(
                    tables.page.c.source_id == source.source_id,
                    tables.page.c.tombstone.is_(False),
                    tables.page.c.subject != subject,
                )
            )
            await connection.execute(
                sa.update(tables.source)
                .values(
                    cursor=next_cursor,
                    next_sync_at=_rescheduled(
                        source, now + timedelta(seconds=SOURCE_SYNC_INTERVAL_SECONDS)
                    ),
                    consecutive_errors=0,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.source.c.id == source.source_id,
                    tables.source.c.claimed_by == source.claim,
                    tables.source.c.removed_at.is_(None),
                )
            )
        return tombstoned

    def _report_ok(
        self, source: ClaimedSource, fetched: int, written: int, tombstoned: int
    ) -> None:
        with suppress(Exception):
            log(
                "source_sync.ok",
                source_id=str(source.source_id),
                **_stream_tags(source),
                account_id=_config_value(source, "account"),
                pages_fetched=fetched,
                pages_written=written,
                pages_tombstoned=tombstoned,
            )

    def _error_backoff(self, source: ClaimedSource, now: datetime) -> tuple[int, datetime]:
        """A failing source's new error count and the moment its backoff lets the next run start:
        the base interval doubling per consecutive error, capped. Computed before `_release` so the
        failure event reports them even when the release is the transaction that cannot write."""
        errors = source.consecutive_errors + 1
        backoff = min(
            SOURCE_SYNC_INTERVAL_SECONDS * 2 ** (errors - 1), SOURCE_ERROR_BACKOFF_CAP_SECONDS
        )
        return errors, now + timedelta(seconds=backoff)

    def _report_failed(
        self,
        source: ClaimedSource,
        error: Exception,
        cursor_reset: bool,
        errors: int,
        next_sync_at: datetime,
    ) -> None:
        """One stream's failure as evidence: which provider stream and account failed, what class
        raised, how many runs in a row have failed now, and when the backoff lets the next one
        start. The exception's own text stays out of the record — h11 quotes the raw header value it
        rejects, which is the credential a member pasted, and `_raise_for_status` builds a status
        error's message out of the provider's response body — so a provider fault renders as the
        status and URL of the request that drew it, query dropped, bounded; any other class is named
        by `error_class` alone. The log goes first and each emission is suppressed on its own: this
        sits on the failure path, where a telemetry fault would replace the error it exists to
        report and strand the claim the release is about to free, and where a fault reaching the
        collector would leave a count with nothing to search."""
        tags = _stream_tags(source)
        error_class = type(error).__name__
        with suppress(Exception):
            fault = (
                f"{error.response.status_code} {error.request.method} "
                f"{error.request.url.copy_with(query=None)}"
                if isinstance(error, httpx.HTTPStatusError)
                else ""
            )
            log_error(
                "source_sync.failed",
                source_id=str(source.source_id),
                **tags,
                account_id=_config_value(source, "account"),
                error_class=error_class,
                provider_fault=fault[:SYNC_PROVIDER_FAULT_MAX_CHARS],
                consecutive_errors=errors,
                next_sync_at=next_sync_at.isoformat(),
                cursor_reset=cursor_reset,
            )
        with suppress(Exception):
            emit_metric(SOURCE_SYNC_FAILED_METRIC, **tags, error_class=error_class)

    async def _release(
        self, source: ClaimedSource, cursor_reset: bool, errors: int, next_sync_at: datetime
    ) -> None:
        """Free a source whose fetch or commit raised: clear its claim, count the error, and push
        next_sync_at forward by the bounded exponential backoff `_error_backoff` computed, so a
        persistently-failing source neither blocks its siblings this run nor hammers its provider
        every lease cycle — the backoff is this driver's own rescheduling, so a resync requested
        while the failing sync ran stands instead (`_rescheduled`). On `CursorExpired` the stored
        cursor is cleared so the next run refetches from scratch; otherwise it resumes where it left
        off. A successful sync resets the counter and the interval in `_write`."""
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.source)
                .values(
                    cursor=None if cursor_reset else source.cursor,
                    next_sync_at=_rescheduled(source, next_sync_at),
                    consecutive_errors=errors,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.source.c.id == source.source_id,
                    tables.source.c.claimed_by == source.claim,
                    tables.source.c.removed_at.is_(None),
                )
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
                    next_sync_at=_rescheduled(
                        source, now + timedelta(seconds=SOURCE_SYNC_INTERVAL_SECONDS)
                    ),
                    consecutive_errors=0,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.source.c.id == source.source_id,
                    tables.source.c.claimed_by == source.claim,
                    tables.source.c.removed_at.is_(None),
                )
            )


PAGE_FEED_BATCH_MAX = 50


@dataclass(frozen=True)
class PageChange:
    """One page's current state as the feed replays it: the source row it belongs to, the provider
    `stream` and `title` the sync driver landed it under, the inlined body (empty when tombstoned),
    the content digest, monotonic revision, and `as_of` — the provider's update or creation time,
    falling back to ingestion time. `created_at == changed_at` marks a page this replay adds rather
    than updates."""

    page_id: UUID
    source_id: UUID
    subject: str
    stream: str
    title: str
    body: str
    digest: str
    revision: int
    tombstone: bool
    created_at: datetime
    as_of: datetime
    changed_at: datetime


@dataclass(frozen=True)
class PageBatch:
    changes: tuple[PageChange, ...]
    next_cursor: str | None


class PageFeed(Protocol):
    """The page-substrate seam an indexer reads through `ExtensionContext.pages`: replay every page
    changed since a `revision|page_id` cursor, bodies inlined, in a bounded batch and total order
    (`ORDER BY revision, id`) — dialect-neutral and replay-safe, so a single-owner cursor advances
    monotonically and a restart resumes where it left off."""

    async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch: ...


def page_cursor(cursor: object) -> tuple[int, UUID]:
    if not isinstance(cursor, str):
        raise ValueError("page cursor must be a string")
    revision, separator, page_id = cursor.partition("|")
    if not separator or not revision.isdecimal():
        raise ValueError(f"invalid page cursor {cursor!r}")
    try:
        return int(revision), UUID(page_id)
    except ValueError as error:
        raise ValueError(f"invalid page cursor {cursor!r}") from error


@dataclass(frozen=True)
class CorePageFeed:
    """The core `PageFeed`: reads the `page` table in `(revision, id)` order after the cursor and
    inlines each non-tombstoned body from the blob store, bounding every batch to
    PAGE_FEED_BATCH_MAX so the inlined bodies stay a small payload. A tombstoned page carries an
    empty body; its reader drops the page's chunks and mirror on that signal."""

    blob: BlobStore

    async def pages_changed_since(self, cursor: str | None, limit: int) -> PageBatch:
        query = (
            sa.select(
                tables.page.c.id,
                tables.page.c.source_id,
                tables.page.c.subject,
                tables.page.c.stream,
                tables.page.c.title,
                tables.page.c.body_ref,
                tables.page.c.digest,
                tables.page.c.revision,
                tables.page.c.tombstone,
                tables.page.c.record_created_at,
                tables.page.c.record_updated_at,
                tables.page.c.created_at,
                tables.page.c.updated_at,
            )
            .order_by(tables.page.c.revision, tables.page.c.id)
            .limit(min(limit, PAGE_FEED_BATCH_MAX))
        )
        if cursor is not None:
            revision, page_id = page_cursor(cursor)
            query = query.where(
                sa.or_(
                    tables.page.c.revision > revision,
                    sa.and_(
                        tables.page.c.revision == revision,
                        tables.page.c.id > page_id,
                    ),
                )
            )
        async with workspace_tx() as connection:
            rows = (await connection.execute(query)).mappings().all()
        changes: list[PageChange] = []
        for row in rows:
            body = "" if row["tombstone"] else (await self.blob.get(row["body_ref"])).decode()
            record_as_of = row["record_updated_at"] or row["record_created_at"]
            changes.append(
                PageChange(
                    page_id=row["id"],
                    source_id=row["source_id"],
                    subject=row["subject"],
                    stream=row["stream"],
                    title=row["title"],
                    body=body,
                    digest=row["digest"],
                    revision=row["revision"],
                    tombstone=bool(row["tombstone"]),
                    created_at=row["created_at"],
                    as_of=(
                        datetime.fromisoformat(record_as_of)
                        if record_as_of is not None
                        else row["created_at"]
                    ),
                    changed_at=row["updated_at"],
                )
            )
        next_cursor = f"{rows[-1]['revision']}|{rows[-1]['id']}" if rows else None
        return PageBatch(changes=tuple(changes), next_cursor=next_cursor)
