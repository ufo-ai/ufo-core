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
`source_sync.ok` records what a successful run wrote and how many records it dropped;
the `ufo.source_sync` service check carries each source row's current state, CRITICAL from the run
that failed until the run that succeeds. A run this deploy's own database ended is none of those:
`source_sync.deferred` records it, the error counter stands, and the row returns at the normal
interval, because the database pages its own monitor and the provider was never asked anything.
A provider that keeps refusing a stream is a case of its own: after
`SOURCE_REFUSAL_PARK_THRESHOLD` refusals the row parks — held at
`SOURCE_PARK_RETRY_SECONDS` instead of the interval, and recorded by `source_sync.parked` and
`source_sync_parked_total` — until a run of it succeeds. A park pages nobody: only a member widening
a grant ends the refusal, so it is a warning to read, never an alert to answer."""

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

import asyncpg
import httpx
import sqlalchemy as sa
import sqlalchemy.exc as sa_exc
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ufo.blob import WorkspaceBlobStore
from ufo.config import SourceConfig, SourceEntry
from ufo.db import owner_tx, workspace_tx
from ufo.harness.o11y import (
    SERVICE_CHECK_CRITICAL,
    SERVICE_CHECK_OK,
    emit_metric,
    emit_service_check,
    log,
    log_error,
    warn,
)
from ufo.runtime.access.connectors import AuthProxy, SourceCredentialResolver
from ufo.runtime.billing.balance import funded
from ufo.runtime.sources.rest import list_or_empty
from ufo.runtime.turns.subjects import connection_subject
from ufo.schema import tables

FOLDER_BACKEND = "folder"
SOURCE_SYNC_JOB = "source_sync"
SOURCE_SYNC_SCHEDULE = "0 * * * * *"
SOURCE_SYNC_INTERVAL_SECONDS = 60
SOURCE_ERROR_BACKOFF_CAP_SECONDS = 3600
SOURCE_REFUSAL_PARK_THRESHOLD = 3
SOURCE_EMPTY_IDLE_THRESHOLD = 5
SOURCE_EMPTY_IDLE_SECONDS = 24 * 3600
"""How a connection carries a stream its account does not use. `canonical` is a connector constant
— the same for every account that ever connects the provider — so a stream that is content for the
accounts using it and empty for the rest cannot be flagged per account, and the flag alone would
make every one of those a request a minute forever. A run that lands nothing counts, and a row that
has never landed a page at all falls back to a daily look after `SOURCE_EMPTY_IDLE_THRESHOLD` of
them. The first page it ever lands clears the counter and it never idles again, so a populated
stream that happens to be quiet keeps the interval — being quiet is not being unused."""
SOURCE_PARK_RETRY_SECONDS = 3600
# A park nothing but a grant event can lift still carries a date, not an infinity: every release
# path writes `next_sync_at = now()`, and a year out is the backstop for the day they all miss one.
SOURCE_PARK_HOLD_SECONDS = 365 * 24 * 3600
CLAIM_LEASE_SECONDS = 300
CLAIM_REFRESH_SECONDS = 60
DUE_BATCH_MAX_SOURCES = 50
SOURCE_BLOB_PREFIX = "sources"
SOURCE_SYNC_FAILED_METRIC = "source_sync_failed_total"
SOURCE_SYNC_PARKED_METRIC = "source_sync_parked_total"
SOURCE_SYNC_CHECK = "source_sync"
SYNC_PROVIDER_FAULT_MAX_CHARS = 500
_ASYNCPG_CONNECTION_ERRORS = (
    asyncpg.PostgresConnectionError,
    asyncpg.CannotConnectNowError,
    asyncpg.InterfaceError,
)


class SourceRowConfig(BaseModel):
    """A backend's typed source config holding parameters of the dataset a row syncs alongside the
    fields that say WHICH dataset it is. A backend with no such parameters needs neither this base
    nor its declarations; the default is that every field identifies the row.

    `non_identity_fields` are left out of `source_row_id`'s hash, so changing one settles on the row
    already syncing that dataset. `resolved_fields` is the subset each caller resolves for itself
    against its own `now`, so `register_source` holds a live row only to the difference: what was
    asked for must match, what it resolved to is the winner's to set. Each is declared by the model
    that owns the fields, never by a name core matches across every backend."""

    non_identity_fields: ClassVar[frozenset[str]] = frozenset()
    resolved_fields: ClassVar[frozenset[str]] = frozenset()

    @classmethod
    def requested_fields(cls) -> frozenset[str]:
        """The non-identity fields a re-registration must state identically: those a caller asked
        for rather than resolved."""
        return cls.non_identity_fields - cls.resolved_fields


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
    """One fetched document: its source reference, provider identity, body, and browse metadata."""

    model_config = ConfigDict(extra="forbid")

    source_ref: str
    source_identity: str | None = None
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
    partial fetch simply didn't mention.

    `dropped` counts the provider records this run could not represent as a page and discarded. A
    run that drops every record it fetched is a success by every other signal it emits, so the count
    rides onto `source_sync.ok`: the event that says what a run wrote says what it lost with it."""

    pages: tuple[Page, ...]
    next_cursor: str | None = None
    deletes: tuple[str, ...] = ()
    snapshot: bool = False
    dropped: int = 0


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
    normal interval with the cursor held and the error counter cleared, rather than backing the
    source off as if it had errored. It also counts the refusal, and parks the source at
    `SOURCE_REFUSAL_PARK_THRESHOLD` of them, which writes a warning log and alerts nobody. A raiser
    therefore does not have to know whether the refusal will clear: one that does costs an hour, and
    one that does not costs a request an hour instead of a request a minute. A fault the caller can
    distinguish still reads better as a fault — it raises through and takes the error backoff — but
    nothing about a stream stopping rests on the caller getting that right.

    `awaits_grant` says the refusal cannot lift on its own, and only a raiser that knows this
    passes it. A missing scope does not qualify: an administrator widens one out of band and no
    event reaches us, so the hourly park is the only way that stream is ever found again. A broker
    reporting the account itself unusable does qualify — it answers that on every read until the
    member reconnects, and the reconnect is an event `GrantStore.record` already delivers. Polling
    that stream asks a question whose answer arrives another way, so the park holds it at
    `SOURCE_PARK_HOLD_SECONDS` instead, and the release path is what wakes it."""

    def __init__(self, reason: str, *, awaits_grant: bool = False) -> None:
        super().__init__(reason)
        self.awaits_grant = awaits_grant
        self.reason = reason


def validation_fault(error: ValidationError) -> str:
    """A rejected model as the field paths and rules that rejected it — `title: string_too_short`.
    The rejected values stay out of it: they are the provider's payload, or the parameters a member
    registered a source with, and a record carries neither."""
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc'])}: {item['type']}" for item in error.errors()
    )


def response_fault(response: httpx.Response) -> str:
    """The reason a refused response names, read from the two envelopes a provider answers a
    refusal with, each rendered as the refusal beside the code that classifies it.

    A GraphQL endpoint carries a top-level `errors` array: each entry's `message`, and its
    `extensions.code` where one rides — Linear answers a removed field with `400` and names the
    field there, and nothing else knows it. A Google API nests its own array under `error` and names
    the refusal in a closed vocabulary: the `reason` of each `errors` and `details` entry — the
    older APIs carry the first, the newer ones the second — beside the envelope's canonical
    `status`, or that status alone where neither array rides. Nothing else separates the unrelated
    things a Google `403` spells: a quota throttle that clears as the window rolls, a token whose
    scopes were never granted, and a calendar nobody shared all answer `403`, and only the reason
    says which, so without it a sweep reads a status and a URL and cannot tell them apart.

    Only those keys ride, never the body whole: an error body echoes the request that drew it —
    Slack names the token it rejected under `provided` — and a record is not where a credential
    lands. That is why Google's free-text `message` stays out while its enumerated `reason` rides.
    A body carrying neither envelope renders nothing, and the fault stays the status and the URL
    alone."""
    try:
        body = response.json()
    except (ValueError, httpx.ResponseNotRead):
        return ""
    if not isinstance(body, dict):
        return ""
    error = body.get("error")
    if isinstance(error, dict):
        status = error.get("status")
        status = status if isinstance(status, str) else ""
        reasons = "; ".join(
            dict.fromkeys(
                item["reason"]
                for item in list_or_empty(error.get("errors")) + list_or_empty(error.get("details"))
                if isinstance(item.get("reason"), str) and item["reason"]
            )
        )
        if not reasons:
            return status
        return f"{reasons} [{status}]" if status else reasons
    messages = []
    for item in list_or_empty(body.get("errors")):
        message = item.get("message")
        if not isinstance(message, str) or not message:
            continue
        extensions = item.get("extensions")
        code = extensions.get("code") if isinstance(extensions, dict) else None
        messages.append(f"{message} [{code}]" if isinstance(code, str) and code else message)
    return "; ".join(messages)


class StreamFault(RuntimeError):
    """A `SourceBackend.fetch` raises this when the provider answered with a shape the stream cannot
    read. The run fails and backs off as any fault does, and `reason` reaches the failure event as
    its `provider_fault`: the backend authored that text against the request it made, so it names
    the object and the shape that broke without carrying the provider's payload — the reason a
    status error's own message never reaches the record."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SourceAuth:
    """What the sync runner threads into a backend's `fetch` so it can reach its provider without
    core minting or holding a token: the workspace the sync runs for, and the selected `auth_proxy`
    the deploy resolves connector credentials through. A connector backend asks `auth_proxy` for the
    `Credential` authenticating its provider (a broker's proxying transport, or a member-added key
    read host-side). `base_url` is the connection's tenant API URL, for the per-tenant providers
    whose connector class declares no host of its own. `self_user_id` is the live external speaker
    resolved by a same-named surface, so a source can reject only records the product itself
    authored. The folder backend ignores all three. A value object, never persisted."""

    workspace_id: UUID
    auth_proxy: AuthProxy | None = None
    base_url: str | None = None
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
    connection_id: UUID,
    non_identity_keys: frozenset[str] = frozenset(),
) -> UUID:
    """The deterministic source row id: the connection generation this row hangs off, and what of
    its config says which dataset it is.

    `non_identity_keys` — the caller's `SourceRowConfig.non_identity_fields`, empty for a model that
    declares none — stay out of the hash: a backfill window is a parameter of the dataset a row
    syncs, not part of which dataset it is, so one stream settles on one row however far back it was
    told to reach. The set is the config model's to declare rather than core's to match by name, so
    one backend naming a field cannot drop it from another's identity."""
    return uuid5(
        NAMESPACE_URL,
        f"{workspace_id}/source/{backend}/{_source_identity(config, non_identity_keys)}"
        f"/connection/{connection_id}",
    )


def _source_identity(config: Mapping[str, object], non_identity_keys: frozenset[str]) -> str:
    return json.dumps(
        {key: value for key, value in config.items() if key not in non_identity_keys},
        sort_keys=True,
    )


def feed_handle(config: BaseModel) -> str:
    """The connection `account_id` of a feed that names no broker account and no member — a
    repository, a folder root: its config's identity, the very string `source_row_id` hashes. The
    `[[sources]]` boot path and an extension's object kind both mint the connection from it, so one
    root registered by either settles on one connection, and two roots of one backend are two
    connections — deleting one cascades none of the other's rows or pages. It begins `{`, which no
    broker's account id does, so `brokered_account` reads it as no account and the feed routes to
    the workspace's key."""
    non_identity = (
        type(config).non_identity_fields
        if isinstance(config, SourceRowConfig)
        else frozenset[str]()
    )
    return _source_identity(config.model_dump(mode="json"), non_identity)


def page_id_for(source_id: UUID, source_ref: str) -> UUID:
    """The deterministic id of a page within a source, so an upsert, a re-fetch of an unchanged
    document, and an explicit `deletes` entry for one `source_ref` all settle on the same row."""
    return uuid5(NAMESPACE_URL, f"{source_id}/page/{source_ref}")


def source_body_ref_matches(body_ref: str, source_id: UUID, page_id: UUID, digest: str) -> bool:
    """Whether a blob ref names this page's content under one source-sync claim."""
    prefix = f"{SOURCE_BLOB_PREFIX}/{source_id}/{page_id}/"
    suffix = f"/{digest.removeprefix('sha256:')}"
    claim = body_ref.removeprefix(prefix).removesuffix(suffix)
    return (
        body_ref.startswith(prefix)
        and body_ref.endswith(suffix)
        and len(claim) == 32
        and set(claim) <= set("0123456789abcdef")
    )


async def register_sources(configured: tuple[SourceEntry, ...]) -> None:
    """Ensure a source row exists for each configured `[[sources]]` entry, each hanging off a
    connection of its own keyed by `feed_handle` — the authority a feed no member owns runs under,
    shared because nobody owns it, and one per feed so removing one root never takes another's
    pages. The row id is derived from the workspace, backend, connection, and config, so a restart
    re-registers the same rows without duplicating them. Runs once at boot, off the sync poll."""
    if not configured:
        return
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
        insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
        for entry in configured:
            handle = feed_handle(entry.config)
            await connection.execute(
                insert(tables.connection)
                .values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    provider=entry.backend,
                    account_id=handle,
                    host="",
                    owner_member_id=None,
                    shared=True,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        tables.connection.c.workspace_id,
                        tables.connection.c.provider,
                        tables.connection.c.account_id,
                    ]
                )
            )
            connection_id = (
                await connection.execute(
                    sa.select(tables.connection.c.id).where(
                        tables.connection.c.workspace_id == workspace_id,
                        tables.connection.c.provider == entry.backend,
                        tables.connection.c.account_id == handle,
                    )
                )
            ).scalar_one()
            config = entry.config.model_dump(mode="json")
            await connection.execute(
                insert(tables.source)
                .values(
                    id=source_row_id(
                        workspace_id, entry.backend, config, connection_id=connection_id
                    ),
                    workspace_id=workspace_id,
                    backend=entry.backend,
                    config=config,
                    connection_id=connection_id,
                    cursor=None,
                    next_sync_at=now,
                    claimed_by=None,
                    claim_expires_at=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
                .on_conflict_do_nothing(index_elements=[tables.source.c.id])
            )


@dataclass(frozen=True)
class ClaimedSource:
    source_id: UUID
    workspace_id: UUID
    claim: str
    backend: str
    config: Mapping[str, object]
    connection_id: UUID
    account_id: str
    base_url: str | None
    cursor: str | None
    consecutive_errors: int
    claimed_at: datetime


def _rescheduled(claimed: ClaimedSource, when: datetime | sa.Case[datetime]) -> sa.Case[datetime]:
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


def _check_tags(source: ClaimedSource) -> dict[str, str]:
    """Which source row a sync outcome belongs to, as service-check tags. Datadog identifies a check
    instance by the check name, the host, and the tags together, so the row id has to be one of
    them: the provider stream alone puts every row that shares it on one status history — two
    workspaces that each connect Slack — where the healthy row's OK lands on the tags holding the
    failing row's CRITICAL, no run accumulates a second consecutive CRITICAL, and a source that
    cannot sync raises no alert. The id stays off the metric dimensions, which the backends' own
    vocabulary bounds."""
    return {**_stream_tags(source), "source_id": str(source.source_id)}


def _config_value(source: ClaimedSource, key: str) -> str:
    value = source.config.get(key)
    return value if isinstance(value, str) else ""


def _database_unreachable(error: BaseException) -> bool:
    """Whether a run died on this deploy's own database rather than on the source it syncs: a
    checkout no pooled connection was left to serve, or a connection that died under the
    transaction. `workspace_tx` and `owner_tx` surround every fetch and every write, so such a fault
    raises on rows whose provider was never asked anything — it belongs to the database's own
    monitor, and naming the stream that happened to be claimed would page the sync monitor for a
    subsystem it does not watch.

    A pool at its ceiling raises `sqlalchemy.exc.TimeoutError`; a connection that died raises
    `InterfaceError` from the driver, or `OperationalError` carrying a connection-class asyncpg
    error as its origin. Nothing but the database layer raises those classes, so where they raised
    says nothing more, and the phase of the run says nothing either. A dial that never landed
    carries no DBAPI wrapper at all — it is the socket's own `TimeoutError` or `OSError`, the two
    classes the provider's socket raises under the fetch and the blob volume raises under the
    commit — so neither the class nor the phase attributes it to the database, and it stays the
    failed run this driver exists to report. An `OperationalError` from a live connection — a lock
    timeout, a constraint the statement broke — is the sync's own failure and is classified as
    one."""
    if isinstance(error, sa_exc.TimeoutError | sa_exc.InterfaceError):
        return True
    if isinstance(error, sa_exc.OperationalError):
        return isinstance(error.orig, _ASYNCPG_CONNECTION_ERRORS)
    return False


@dataclass(frozen=True)
class PageBrowse:
    id: UUID
    source_identity: str | None
    stream: str
    title: str
    record_created_at: str | None
    record_updated_at: str | None


@dataclass(frozen=True)
class ChangedPage:
    browse: PageBrowse
    body_ref: str
    digest: str


class _SourceClaimLost(RuntimeError):
    pass


def _source_authority() -> sa.Join:
    """A source joined to the connection that authorizes it — the reach an agent is granted and the
    disclosure every page it syncs carries. Every source has one, so this is an inner join."""
    return tables.source.join(
        tables.connection,
        sa.and_(
            tables.connection.c.workspace_id == tables.source.c.workspace_id,
            tables.connection.c.id == tables.source.c.connection_id,
        ),
    )


def _readers_remain() -> sa.ColumnElement[bool]:
    """A correlated predicate on `source` alone, so a caller selecting from `source` and one
    selecting from `source` joined to its connection both get the same answer: every subquery here
    names its own FROM and correlates only `source`, rather than letting the enclosing query's
    tables decide. Left to inference, the shared-connection arm correlates both its tables out
    against a joined caller and none against a bare one — the first raises, and the second would
    quietly test every source against every connection in the workspace.

    What it answers: the archive has not taken every agent that reads it.
    A source whose connection is granted only to archived agents costs a fetch, a page write and
    the model tokens its facts are extracted with, for a feed no turn can reach — so it waits for a
    restore. A connection nobody was granted is a different row with a different history, and syncs
    as it always did. A shared connection has the main agent as a reader with no grant at all, so it
    keeps syncing while a live main agent exists — the same rule `_source_readable` reads by, so the
    main agent is never answering members from pages a stopped feed left behind."""
    granted = (
        sa.select(sa.literal(1))
        .select_from(tables.connector_grant)
        .where(
            tables.connector_grant.c.workspace_id == tables.source.c.workspace_id,
            tables.connector_grant.c.connection_id == tables.source.c.connection_id,
        )
        .correlate(tables.source)
    )
    granted_to_a_live_agent = (
        sa.select(sa.literal(1))
        .select_from(
            tables.connector_grant.join(
                tables.agent,
                sa.and_(
                    tables.agent.c.workspace_id == tables.connector_grant.c.workspace_id,
                    tables.agent.c.id == tables.connector_grant.c.agent_id,
                ),
            )
        )
        .where(
            tables.connector_grant.c.workspace_id == tables.source.c.workspace_id,
            tables.connector_grant.c.connection_id == tables.source.c.connection_id,
            tables.agent.c.archived_at.is_(None),
        )
        .correlate(tables.source)
    )
    read_by_a_live_main = (
        sa.select(sa.literal(1))
        .select_from(tables.agent)
        .where(
            tables.agent.c.workspace_id == tables.source.c.workspace_id,
            tables.agent.c.is_main.is_(True),
            tables.agent.c.archived_at.is_(None),
        )
        .correlate(tables.source)
    )
    shared_connection = (
        sa.select(sa.literal(1))
        .select_from(tables.connection)
        .where(
            tables.connection.c.workspace_id == tables.source.c.workspace_id,
            tables.connection.c.id == tables.source.c.connection_id,
            tables.connection.c.shared,
        )
        .correlate(tables.source)
    )
    return sa.or_(
        ~sa.exists(granted),
        sa.exists(granted_to_a_live_agent),
        sa.and_(sa.exists(shared_connection), sa.exists(read_by_a_live_main)),
    )


@dataclass(frozen=True)
class SyncDriver:
    """The core sync job: for the workspace the dispatcher bound, claim its due sources, fetch each
    backend, and commit its pages — the claim and every write go through RLS on that workspace. Runs
    downward — claim, fetch, commit — one source at a time. Every claimed row renews while it waits
    or fetches, so a slow backend cannot let this worker's later claims expire into a second run.
    `candidate_workspaces` names the workspaces holding a due source through one `owner_tx` read, so
    the dispatcher binds only those and a workspace with nothing due is never opened. On a
    per-tenant deploy `owner_tx` resolves to the single workspace, unchanged. Due means readable
    too: a source the archive took every reader from is nobody's feed, so it is neither a candidate
    nor claimed until a restore gives it one back. A parked row — one the provider refused often
    enough that `_skip` slowed it to an hour — is due like any other row, an hour out instead of a
    minute. A workspace at or under its balance line (`funded`) holds nothing due at all, and its
    rows come back with the credit that lifts it; `own_key_slots` names every key slot the deploy's
    models key from, so a workspace serving any of them on its own key is never held."""

    backends: Mapping[str, SourceBackend]
    blob: WorkspaceBlobStore
    postgres: bool
    source_credentials: SourceCredentialResolver | None = None
    identity_resolvers: Mapping[str, SourceIdentityResolver] = field(default_factory=dict)
    own_key_slots: tuple[str, ...] = ()

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
                        sa.or_(
                            tables.source.c.claimed_by.is_(None),
                            tables.source.c.claim_expires_at < now,
                        ),
                        _readers_remain(),
                        funded(tables.source.c.workspace_id, self.own_key_slots),
                    )
                    .distinct()
                )
            ).all()
        return tuple(row.workspace_id for row in rows)

    async def run(self) -> None:
        claim = uuid4().hex
        sources = await self._claim_due(claim)
        renewals = [asyncio.create_task(self._renew_claim(source)) for source in sources]
        try:
            for source, renewal in zip(sources, renewals, strict=True):
                await self._run_with_lease(source, renewal)
        finally:
            for renewal in renewals:
                if not renewal.done():
                    renewal.cancel()
            await asyncio.gather(*renewals, return_exceptions=True)

    async def _run_with_lease(self, source: ClaimedSource, renewal: asyncio.Task[None]) -> None:
        sync = asyncio.create_task(self._sync_claimed(source))
        try:
            done, _pending = await asyncio.wait(
                (sync, renewal), return_when=asyncio.FIRST_COMPLETED
            )
            if sync not in done:
                if renewal.cancelled():
                    raise asyncio.CancelledError
                error = renewal.exception()
                if error is None:
                    raise RuntimeError("source claim renewal stopped")
                raise error
            await sync
        except _SourceClaimLost:
            log("source_sync.claim_lost", source_id=str(source.source_id), **_stream_tags(source))
        finally:
            for task in (sync, renewal):
                if not task.done():
                    task.cancel()
            await asyncio.gather(sync, renewal, return_exceptions=True)

    async def _sync_claimed(self, source: ClaimedSource) -> None:
        try:
            result = await self._fetch(source)
            await self._commit(source, result)
        except _SourceClaimLost:
            raise
        except StreamSkipped as skipped:
            with suppress(Exception):
                log(
                    "source_sync.skipped",
                    source_id=str(source.source_id),
                    **_stream_tags(source),
                    reason=skipped.reason,
                )
            await self._skip(source, skipped.reason, awaits_grant=skipped.awaits_grant)
        except Exception as error:
            if _database_unreachable(error):
                await self._defer(source, error)
                return
            cursor_reset = isinstance(error, CursorExpired)
            errors, next_sync_at = self._error_backoff(source, datetime.now(UTC))
            await self._report_failed(source, error, cursor_reset, errors, next_sync_at)
            await self._release(source, cursor_reset, errors, next_sync_at)

    async def _renew_claim(self, source: ClaimedSource) -> None:
        while True:
            await asyncio.sleep(CLAIM_REFRESH_SECONDS)
            await self._refresh_claim(source)

    async def _refresh_claim(self, source: ClaimedSource) -> None:
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            renewed = await connection.execute(
                sa.update(tables.source)
                .where(
                    tables.source.c.id == source.source_id,
                    tables.source.c.claimed_by == source.claim,
                )
                .values(
                    claim_expires_at=now + timedelta(seconds=CLAIM_LEASE_SECONDS),
                    updated_at=sa.func.now(),
                )
            )
        if renewed.rowcount != 1:
            raise _SourceClaimLost(str(source.source_id))

    async def _claim_due(self, claim: str) -> tuple[ClaimedSource, ...]:
        now = datetime.now(UTC)
        due = (
            sa.select(
                tables.source.c.id,
                tables.source.c.workspace_id,
                tables.source.c.backend,
                tables.source.c.config,
                tables.source.c.connection_id,
                tables.connection.c.account_id,
                tables.connection.c.base_url,
                tables.source.c.cursor,
                tables.source.c.consecutive_errors,
            )
            .select_from(_source_authority())
            .where(
                tables.source.c.next_sync_at <= now,
                sa.or_(
                    tables.source.c.claimed_by.is_(None),
                    tables.source.c.claim_expires_at < now,
                ),
                _readers_remain(),
                funded(tables.source.c.workspace_id, self.own_key_slots),
            )
            .limit(DUE_BATCH_MAX_SOURCES)
        )
        if self.postgres:
            due = due.with_for_update(skip_locked=True, of=tables.source)
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
                connection_id=row["connection_id"],
                account_id=row["account_id"],
                base_url=row["base_url"],
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
                else self.source_credentials.bind(source.connection_id)
            ),
            base_url=source.base_url,
            self_user_id=self_user_id,
        )
        return await backend.fetch(config, source.cursor, auth)

    async def _commit(self, source: ClaimedSource, result: SyncResult) -> None:
        await self._refresh_claim(source)
        prior, prior_by_identity = await self._prior_pages(source.source_id)
        resolved: dict[str, UUID] = {
            identity: value[2].id for identity, value in prior_by_identity.items()
        }
        claimed: dict[UUID, str] = {
            page_id: identity
            for page_id, value in prior.items()
            if (identity := value[2].source_identity) is not None
        }
        fetched: list[UUID] = []
        changed: list[ChangedPage] = []
        metadata: list[PageBrowse] = []
        written: list[str] = []
        try:
            for page in result.pages:
                source_identity = page.source_identity or page.source_ref
                fallback_id = page_id_for(source.source_id, page.source_ref)
                existing = prior_by_identity.get(source_identity)
                page_id = resolved.get(source_identity)
                if page_id is None:
                    fallback = prior.get(fallback_id)
                    owner = claimed.get(fallback_id)
                    if fallback is not None and owner is None:
                        existing = fallback
                        page_id = fallback_id
                    elif fallback is None and owner is None:
                        page_id = fallback_id
                    else:
                        page_id = uuid5(
                            NAMESPACE_URL,
                            f"{source.source_id}/page-identity/{source_identity}",
                        )
                        existing = prior.get(page_id)
                    resolved[source_identity] = page_id
                    claimed[page_id] = source_identity
                fetched.append(page_id)
                browse = PageBrowse(
                    id=page_id,
                    source_identity=source_identity,
                    stream=page.stream,
                    title=page.title,
                    record_created_at=page.created_at,
                    record_updated_at=page.updated_at,
                )
                if existing is None or existing[:2] != (page.digest, False):
                    body_ref = (
                        f"{SOURCE_BLOB_PREFIX}/{source.source_id}/{page_id}/{source.claim}/"
                        f"{page.digest.removeprefix('sha256:')}"
                    )
                    written.append(body_ref)
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
            deleted = [
                existing[2].id
                if (existing := prior_by_identity.get(ref)) is not None
                else page_id_for(source.source_id, ref)
                for ref in result.deletes
            ]
            try:
                tombstoned = await self._write(
                    source,
                    result.next_cursor,
                    changed,
                    metadata,
                    fetched,
                    deleted,
                    result.snapshot,
                )
            except asyncio.CancelledError:
                # `_opened` runs the commit to completion under `asyncio.shield` and re-raises the
                # cancellation, so rows naming these bodies may stand committed. A body no row
                # names is garbage the next write replaces; a body a row names and the store lacks
                # stops that source's page feed for every consumer.
                written.clear()
                raise
        except BaseException as error:
            results = await asyncio.gather(
                *(self.blob.delete(body_ref) for body_ref in written),
                return_exceptions=True,
            )
            failures = [result for result in results if isinstance(result, BaseException)]
            if failures:
                raise BaseExceptionGroup(
                    "source commit and blob cleanup failed", [error, *failures]
                ) from None
            raise
        await self._report_ok(
            source,
            len(result.pages),
            len(changed) + len(metadata),
            tombstoned,
            result.dropped,
        )

    async def _prior_pages(
        self, source_id: UUID
    ) -> tuple[
        dict[UUID, tuple[str, bool, PageBrowse]],
        dict[str, tuple[str, bool, PageBrowse]],
    ]:
        async with workspace_tx() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(
                            tables.page.c.id,
                            tables.page.c.source_identity,
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
        by_id = {
            row["id"]: (
                row["digest"],
                bool(row["tombstone"]),
                PageBrowse(
                    id=row["id"],
                    source_identity=row["source_identity"],
                    stream=row["stream"],
                    title=row["title"],
                    record_created_at=row["record_created_at"],
                    record_updated_at=row["record_updated_at"],
                ),
            )
            for row in rows
        }
        return by_id, {
            page.source_identity: prior
            for prior in by_id.values()
            if (page := prior[2]).source_identity is not None
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
        material changes for `PageFeed`.

        The authority read locks the connection row as well as the source, so a `set_shared` that
        would otherwise commit between this read and the page stamp below waits for it: with only
        the source locked, a run could read `shared` one way, the member flip it and restamp every
        page the other way, and this run's trailing stamp put them back — leaving shared pages on a
        connection the member just made private."""
        now = datetime.now(UTC)
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
            authority = (
                sa.select(tables.connection.c.shared, tables.connection.c.owner_member_id)
                .select_from(_source_authority())
                .where(
                    tables.source.c.id == source.source_id,
                    tables.source.c.workspace_id == workspace_id,
                    tables.source.c.claimed_by == source.claim,
                )
            )
            if connection.dialect.name == "postgresql":
                authority = authority.with_for_update(of=(tables.source, tables.connection))
            held = (await connection.execute(authority)).one_or_none()
            if held is None:
                raise _SourceClaimLost(str(source.source_id))
            subject = connection_subject(held.shared, held.owner_member_id)
            for changed_page in changed:
                updated = await connection.execute(
                    sa.update(tables.page)
                    .values(
                        source_identity=changed_page.browse.source_identity,
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
                            source_identity=changed_page.browse.source_identity,
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
                        source_identity=browse_page.source_identity,
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
            landed = bool(changed) or bool(deleted) or tombstoned > 0
            empty_runs = sa.literal(0) if landed else tables.source.c.consecutive_empty + 1
            never_landed = ~sa.exists(
                sa.select(sa.literal(1))
                .select_from(tables.page)
                .where(tables.page.c.source_id == source.source_id)
                .correlate()
            )
            idles = sa.and_(empty_runs >= SOURCE_EMPTY_IDLE_THRESHOLD, never_landed)
            await connection.execute(
                sa.update(tables.source)
                .values(
                    cursor=next_cursor,
                    next_sync_at=_rescheduled(
                        source,
                        sa.case(
                            (idles, now + timedelta(seconds=SOURCE_EMPTY_IDLE_SECONDS)),
                            else_=now + timedelta(seconds=SOURCE_SYNC_INTERVAL_SECONDS),
                        ),
                    ),
                    consecutive_errors=0,
                    consecutive_refusals=0,
                    consecutive_empty=empty_runs,
                    parked_at=None,
                    parked_reason=None,
                    claimed_by=None,
                    claim_expires_at=None,
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.source.c.id == source.source_id,
                    tables.source.c.claimed_by == source.claim,
                )
            )
        return tombstoned

    async def _report_ok(
        self, source: ClaimedSource, fetched: int, written: int, tombstoned: int, dropped: int
    ) -> None:
        """One stream's successful run: what it wrote and what it dropped, as a log, and the
        stream's state, as an OK service check. `pages_dropped` is what the backend could not
        represent: the drop already warns per record, and a warning is not a signal a health query
        selects, so the run's own success event carries the count too. The check is what ends an
        alert on that row — a counter says a failure happened and never that it stopped, so nothing
        but the passage of time took a recovered stream out of a window over one. Each emission is
        suppressed on its own, as on the failure path."""
        tags = _stream_tags(source)
        with suppress(Exception):
            log(
                "source_sync.ok",
                source_id=str(source.source_id),
                **tags,
                account_id=source.account_id,
                pages_fetched=fetched,
                pages_written=written,
                pages_tombstoned=tombstoned,
                pages_dropped=dropped,
            )
        with suppress(Exception):
            await emit_service_check(SOURCE_SYNC_CHECK, SERVICE_CHECK_OK, **_check_tags(source))

    def _error_backoff(self, source: ClaimedSource, now: datetime) -> tuple[int, datetime]:
        """A failing source's new error count and the moment its backoff lets the next run start:
        the base interval doubling per consecutive error, capped. Computed before `_release` so the
        failure event reports them even when the release is the transaction that cannot write."""
        errors = source.consecutive_errors + 1
        backoff = min(
            SOURCE_SYNC_INTERVAL_SECONDS * 2 ** (errors - 1), SOURCE_ERROR_BACKOFF_CAP_SECONDS
        )
        return errors, now + timedelta(seconds=backoff)

    async def _report_failed(
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
        status and URL of the request that drew it, query dropped, bounded, followed by the reason
        the response's own `errors` array names (`response_fault`, which reads that array and
        nothing else of the body) so a refused request says which part of it was refused; a
        `StreamFault` renders the reason the backend authored for it, a `ProxyError` the reason the
        proxying hop authored (a broker transport's, or httpx's own — the provider's payload never
        reaches either), and a rejected model renders the field paths and rules that rejected it,
        never the values; any other class is named by `error_class` alone. The log goes first and
        each emission is suppressed on its own: this
        sits on the failure path, where a telemetry fault would replace the error it exists to
        report and strand the claim the release is about to free, and where a fault reaching the
        collector would leave a count with nothing to search."""
        tags = _stream_tags(source)
        error_class = type(error).__name__
        with suppress(Exception):
            match error:
                case httpx.HTTPStatusError():
                    request = (
                        f"{error.response.status_code} {error.request.method} "
                        f"{error.request.url.copy_with(query=None)}"
                    )
                    reason = response_fault(error.response)
                    fault = f"{request}: {reason}" if reason else request
                case StreamFault():
                    fault = error.reason
                case httpx.ProxyError():
                    fault = str(error)
                case ValidationError():
                    fault = validation_fault(error)
                case _:
                    fault = ""
            log_error(
                "source_sync.failed",
                source_id=str(source.source_id),
                **tags,
                account_id=source.account_id,
                error_class=error_class,
                provider_fault=fault[:SYNC_PROVIDER_FAULT_MAX_CHARS],
                consecutive_errors=errors,
                next_sync_at=next_sync_at.isoformat(),
                cursor_reset=cursor_reset,
            )
        with suppress(Exception):
            emit_metric(SOURCE_SYNC_FAILED_METRIC, **tags, error_class=error_class)
        with suppress(Exception):
            await emit_service_check(
                SOURCE_SYNC_CHECK,
                SERVICE_CHECK_CRITICAL,
                f"{errors} consecutive failed runs, last {error_class}",
                **_check_tags(source),
            )

    async def _defer(self, source: ClaimedSource, error: Exception) -> None:
        """A run this deploy's database ended, recorded as the interruption it is rather than as a
        failure of the stream: `source_sync.deferred` carries the same fields as
        `source_sync.failed`, so one search reads both, and the error class names what the database
        raised. No `source_sync_failed_total` and no CRITICAL check, because the run says nothing
        about the provider — the database has its own monitor, and a sync page here would name the
        wrong subsystem to whoever answers it.

        The row keeps its error counter and comes back at the normal interval: the source did not
        fail, so neither the backoff nor the count it feeds has anything to measure. The release
        opens a transaction against the database that just failed, so it is suppressed and the claim
        lease is what frees the row when it cannot be written."""
        next_sync_at = datetime.now(UTC) + timedelta(seconds=SOURCE_SYNC_INTERVAL_SECONDS)
        with suppress(Exception):
            warn(
                "source_sync.deferred",
                source_id=str(source.source_id),
                **_stream_tags(source),
                account_id=source.account_id,
                error_class=type(error).__name__,
                provider_fault="",
                consecutive_errors=source.consecutive_errors,
                next_sync_at=next_sync_at.isoformat(),
                cursor_reset=False,
            )
        with suppress(Exception):
            await self._release(source, False, source.consecutive_errors, next_sync_at)

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
                )
            )

    async def _skip(self, source: ClaimedSource, reason: str, *, awaits_grant: bool) -> None:
        """A backend raised `StreamSkipped`: the source is intentionally unreadable this run (a
        missing scope, a plan gate), not failed. Free the claim and reschedule at the normal
        interval with the cursor held and the error counter reset — no pages committed, so snapshot
        delete-detection never runs and the source's existing pages stand.

        The refusal also counts, and at `SOURCE_REFUSAL_PARK_THRESHOLD` the row parks, with the two
        marks naming why. How far the park holds it is the one thing the raiser decides: a refusal
        that can lift on its own waits `SOURCE_PARK_RETRY_SECONDS`, and one that waits on a grant
        event waits `SOURCE_PARK_HOLD_SECONDS`, because the event is what wakes it and an hourly
        request would only ask a question already answered elsewhere. A grant
        does not widen between two attempts, so retrying a refused stream every interval costs a
        request a minute for as long as nobody re-grants the scope; three refusals absorb a token
        that momentarily failed to refresh and end that loop within about five minutes.

        A park slows a stream, it never stops one. Whether a refusal will clear by itself is the
        provider's business and not always legible here — a throttle Google spells `403`, a
        rate-limited org, a plan gate lifted an hour later — so the driver keeps reading a parked
        row, and the run that finally succeeds clears the marks and the counter in `_write`. That is
        the recovery for every refusal alike, and it needs neither a member nor an operator. The
        reconnect and the resync that unpark only make it immediate instead of hourly. A park held
        until someone acted would put every stream a provider ever throttles behind a member's
        attention, on a reason naming a scope that was never missing — and would owe an alert, which
        a row that reads itself back every hour does not.

        The count is read and the park decided inside the one statement, off the stored counter
        rather than the value this claim was taken on. An unpark lands while a claim is held — a
        reconnect of the account, a resync — and it writes that counter back to zero without
        touching the claim, so a park computed from the claimed value would discard the act the
        member just made and push the row an hour out with nobody left to tell. Reading the counter
        here makes their zero the base this refusal counts from. The row the statement answers with
        is therefore what parked, or nothing at all."""
        now = datetime.now(UTC)
        counted = tables.source.c.consecutive_refusals + 1
        parks = counted >= SOURCE_REFUSAL_PARK_THRESHOLD
        held = SOURCE_PARK_HOLD_SECONDS if awaits_grant else SOURCE_PARK_RETRY_SECONDS
        async with workspace_tx() as connection:
            row = (
                await connection.execute(
                    sa.update(tables.source)
                    .values(
                        next_sync_at=_rescheduled(
                            source,
                            sa.case(
                                (parks, now + timedelta(seconds=held)),
                                else_=now + timedelta(seconds=SOURCE_SYNC_INTERVAL_SECONDS),
                            ),
                        ),
                        consecutive_errors=0,
                        consecutive_refusals=counted,
                        parked_at=sa.case((parks, now), else_=None),
                        parked_reason=sa.case((parks, reason), else_=None),
                        claimed_by=None,
                        claim_expires_at=None,
                        updated_at=sa.func.now(),
                    )
                    .where(
                        tables.source.c.id == source.source_id,
                        tables.source.c.claimed_by == source.claim,
                    )
                    .returning(tables.source.c.parked_at, tables.source.c.consecutive_refusals)
                )
            ).one_or_none()
        if row is not None and row.parked_at is not None:
            await self._report_parked(source, reason, row.consecutive_refusals)

    async def _report_parked(self, source: ClaimedSource, reason: str, refusals: int) -> None:
        """A run that parked a refused stream, as a record rather than a page: a warning log naming
        the reason the backend authored and the row it belongs to, plus the park counter.

        A park raises nobody at 3am. The stream is refused, and what ends that is a member widening
        a grant — an operator woken for it has nothing to do, and the row is already reading itself
        back every hour without either of them. So the park submits no service-check status at
        all: warning severity is where a sweep for streams that stopped earning their interval
        looks, and the counter carries the same fact as a rate. `ufo.source_sync` stays what it was,
        the failure path's own signal, and a stale CRITICAL on a row that stopped failing is
        resolved by that monitor's own timeout rather than by a status this path invents.

        A parked row that stays refused re-parks each hour, so both the log and the counter read as
        a cadence. Reported after the write, so a transaction that could not park the row says
        nothing. Each emission is suppressed on its own, as on the failure path."""
        tags = _stream_tags(source)
        with suppress(Exception):
            warn(
                "source_sync.parked",
                source_id=str(source.source_id),
                **tags,
                consecutive_refusals=refusals,
                reason=reason,
            )
        with suppress(Exception):
            emit_metric(SOURCE_SYNC_PARKED_METRIC, **tags)


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

    blob: WorkspaceBlobStore

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
