"""The tenancy boundary: module-private engines, workspace_tx as the only scoped session source.

Isolation is set per transaction from an ambient workspace, so one serve process safely serves many
workspaces: a request/turn/job sets `current_workspace` at its boundary, and `workspace_tx` pins the
RLS GUC (`app.workspace_id`) for that transaction. The serve role is an RLS *subject* with no pinned
default, so a transaction that never set the workspace fails closed — the policy's `current_setting`
errors on the unset GUC, never a leak. Pooled-connection reuse never carries a workspace across
checkouts: the GUC is pinned with `set_config(..., is_local=true)` — `SET LOCAL` — which Postgres
clears at transaction end.

`owner_tx` is the one exception: the RLS-bypassing read the cross-workspace background sweeps
enumerate through — never a scoped read, and the caller re-binds each row under `with ws(...)`.

Engines pool connections per event loop: an asyncpg connection binds to the loop that created it,
and surfaces and DBOS workflows run on different loops in the same process, so each loop lazily
builds and keeps its own engine for the process's life (the pattern `S3BlobStore._client` uses).
"""

import asyncio
import os
import sqlite3
import time
import warnings
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID
from weakref import WeakKeyDictionary

import sqlalchemy as sa
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import AsyncAdaptedQueuePool

MIGRATIONS_DIR = Path(__file__).parent / "schema" / "migrations"
SQLITE_BUSY_TIMEOUT_MS = 5_000
STATEMENT_LOG_MAX_CHARS = 2_000
WORKSPACE_GUC = "app.workspace_id"
# Every pool below is a ceiling one event loop can reach, and the fleet's total is what has to fit.
# Measured on the testing instance 2026-07-31: `max_connections` 400, `superuser_reserved` 3, so 397
# are the fleet's to spend. Serve's replica count is the `serve_replicas` template variable (4 on
# testing, 2 on prod — the arithmetic below takes testing's, the larger fleet); every other
# `hosted.yaml.tpl` deployment is the literal `replicas: 2`, and nothing autoscales, so the count
# has no user term. Every `init_db` root is counted, not just serve's:
#
#   ufo-serve           4 pods x 3 loops (uvicorn, DBOS, heartbeat) x (5 + 5)    = 120 app
#                       4 pods x 3 loops x (2 + 3)                               =  60 owner
#   ufo-ingress         2 pods x 1 loop x (5 + 5)                                =  20
#   ufo-sandbox-proxy   2 pods x 1 loop x (5 + 5)                                =  20
#   ufo-gateway         2 pods x 1 loop x (5 + 5) through this module            =  20
#                       2 pods x asyncpg `max_size` 4 (its own control-plane pool) =   8
#   DBOS executor       4 pods x 20 on `*_dbos`, same instance (`max_overflow` 0) =  80
#   DBOS client         4 pods x 5 on `*_dbos`                                    =  20
#                                                                       ceiling  = 348 of 397
#
# The owner registry is sized apart because its consumers are serial — the background sweeps'
# enumeration and the heartbeat, never a fan-out — and a pod with no owner DSN spends nothing on
# it at all: `owner_tx` resolves the app pool's engine rather than a second pool for one URL.
# 5 + 5 is not a bound on concurrent demand — turns fan out parallel tool calls, spawned children
# and prepared intents run on the express queue, and job workers share the loop. It is sized to
# how the pool is actually used: every checkout here is transaction-scoped and no transaction
# spans a non-database await (a scan the review gate keeps true), so a demand burst queues for
# milliseconds and drains. What the pool cannot absorb is a starved event loop stretching
# checkout-to-release cycles, and the turns queue's `TURN_WORKER_CONCURRENCY` in
# `ufo.runtime.queue` bounds that at its source by capping what one process claims to run at once.
#
# `db_connections_high` brackets these two numbers: it warns above what the fleet is entitled to and
# alerts below where Postgres refuses.
POOL_SIZE = 5
MAX_OVERFLOW = 5
OWNER_POOL_SIZE = 2
OWNER_MAX_OVERFLOW = 3
POOL_TIMEOUT_SECONDS = 10
POOL_RECYCLE_SECONDS = 1800
CONNECT_TIMEOUT_SECONDS = 10
ASYNCPG_DRIVER = "asyncpg"


@dataclass(frozen=True)
class _Pool:
    """One registry of per-loop engines and the ceiling each of them holds. `application_name` is
    what attributes a connection to its pool in `pg_stat_activity`, which is otherwise blind to
    which of a pod's registries opened it."""

    application_name: str
    size: int
    overflow: int
    engines: dict[tuple[asyncio.AbstractEventLoop, str], AsyncEngine] = field(default_factory=dict)


_APP = _Pool(application_name="ufo_app", size=POOL_SIZE, overflow=MAX_OVERFLOW)
_OWNER = _Pool(application_name="ufo_owner", size=OWNER_POOL_SIZE, overflow=OWNER_MAX_OVERFLOW)

_app_url: str | None = None
_owner_url: str | None = None
_disposing: set[asyncio.Task[None]] = set()
_SQLITE_TRANSACTION_LOCKS: WeakKeyDictionary[AsyncEngine, asyncio.Lock] = WeakKeyDictionary()

current_workspace: ContextVar[UUID | None] = ContextVar("current_workspace", default=None)


def _build_engine(url: str, pool: _Pool) -> AsyncEngine:
    """A pool for either backend — sqlite's single-writer semantics are the `begin immediate`
    listener below, not the absence of a pool, so nothing is bought by opening a fresh file handle
    and re-running three pragmas for every transaction. `pool_pre_ping` is what survives an RDS
    failover or an idle-killed connection, and the dial it replaces one with is bounded here:
    asyncpg's own default is 60 seconds, longer than any caller of this module will wait."""
    engine = create_async_engine(url, **_pool_kwargs(url, pool))
    if engine.dialect.name == "sqlite":
        sa.event.listen(engine.sync_engine, "connect", _sqlite_on_connect)
        sa.event.listen(engine.sync_engine, "begin", _sqlite_begin_immediate)
    return engine


def _pool_kwargs(url: str, pool: _Pool) -> dict[str, Any]:
    """sqlite keeps `pool.size` connections warm and refuses none: its overflow is unlimited, so a
    caller that wants a connection while every pooled one is checked out still gets one — the
    property `NullPool` gave, without paying for a dial it could have reused. A local file needs
    neither a pre-ping (nothing between the process and the file goes stale) nor a recycle."""
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        return {
            "poolclass": AsyncAdaptedQueuePool,
            "pool_size": pool.size,
            "max_overflow": -1,
            "pool_timeout": POOL_TIMEOUT_SECONDS,
        }
    return {
        "pool_size": pool.size,
        "max_overflow": pool.overflow,
        "pool_timeout": POOL_TIMEOUT_SECONDS,
        "pool_recycle": POOL_RECYCLE_SECONDS,
        "pool_pre_ping": True,
        **_driver_kwargs(parsed.get_driver_name(), pool),
    }


def _driver_kwargs(driver: str, pool: _Pool) -> dict[str, Any]:
    """Bounding the dial and naming the pool are one intent in two drivers' spellings, and a
    kwarg meant for the other driver is refused rather than ignored: psycopg rejects `timeout` as an
    unknown connection option, and `ufoctl proxy` and `ufoctl ingress` both open a psycopg DSN
    (`proxy_serve.owner_dsn`), so one shape for both would fail their every connection.

    Both drivers cache plans per connection — asyncpg 100 statements, psycopg after five uses — and
    a pooled connection carries them for `pool_recycle`. DDL arrives from outside this process, the
    `ufo-migrate` Job running alembic against a live fleet, and a plan that outlives its table is
    `InvalidCachedStatementError` on asyncpg and `FeatureNotSupported` on psycopg. psycopg discards
    its cache on rollback, which is why only a committed transaction shows it — the path `ufoctl
    proxy` and `ufoctl ingress` actually run. Each setting rides in `connect_args` because it is its
    driver's own argument, not a dialect one."""
    if driver == ASYNCPG_DRIVER:
        return {
            "connect_args": {
                "timeout": CONNECT_TIMEOUT_SECONDS,
                "server_settings": {"application_name": pool.application_name},
                "prepared_statement_cache_size": 0,
            }
        }
    return {
        "connect_args": {
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            "application_name": pool.application_name,
            "prepare_threshold": None,
        }
    }


def _engine_for(url: str, pool: _Pool) -> AsyncEngine:
    """The running loop's engine for `pool` and `url`, built on first touch. Sync throughout: there
    is no await between the lookup and the store, so two tasks on one loop cannot interleave here,
    and two loops write different keys — no lock is reachable by either. The url is part of the key
    so a re-`init_db` against a different database can never be served an engine still bound to the
    previous one. The sweep drops entries whose loop is gone but cannot dispose them: the loop that
    owns those sockets is the only thing that could close them and it is already closed, so
    `dispose_loop_engines` is what prevents that leak and this is only what keeps the registry
    bounded. It snapshots the keys because the serve, DBOS, and heartbeat loops run on different
    threads and insert here concurrently."""
    key = (asyncio.get_running_loop(), url)
    for stale in [held for held in list(pool.engines) if held[0].is_closed()]:
        pool.engines.pop(stale, None)
    engine = pool.engines.get(key)
    if engine is None:
        engine = pool.engines[key] = _build_engine(url, pool)
    return engine


def init_db(url: str) -> None:
    global _app_url
    if _app_url is not None:
        raise RuntimeError("db already initialized")
    _app_url = url


def init_owner_db(url: str) -> None:
    """The RLS-bypassing owner-role URL `owner_tx` enumerates through (`UFO_OWNER_DSN`, the same
    secret the shared proxy opens). Its role owns the tables and is never FORCEd RLS, so it reads
    across every workspace — the one cross-tenant path. `serve` always sets it; `ufoctl ingress`,
    `ufoctl proxy`, and one-shot verbs set no owner URL, so `owner_tx` falls to the app pool and
    that URL's own role scopes it.

    The driver is normalized here rather than by each caller. A secret store hands this DSN out in
    libpq form (`postgresql://`), which SQLAlchemy resolves to the sync psycopg2 dialect — a banned
    import that is not installed — so an engine built from it raises before any query runs. Doing it
    where the URL is registered leaves no caller holding the unusable form."""
    global _owner_url
    if _owner_url is not None:
        raise RuntimeError("owner db already initialized")
    _owner_url = url.replace("postgresql://", "postgresql+asyncpg://", 1)


async def verify_db_reachable() -> None:
    """Fail loud at boot on a database this process cannot reach. Engines build per loop on first
    touch, so nothing dials until the first request — and `ufo-sandbox-proxy` and `ufo-ingress` are
    TCP-probed rather than `/healthz`-probed (`hosted.yaml.tpl`), so a bound socket in front of an
    unreachable database passes readiness and then fails every request behind it.

    Awaited on the caller's loop rather than driven on a private one. `init_db`'s callers include
    `async def` composition roots, and a blocking dial there stalls every surface that loop carries:
    the proxy registers its SIGTERM handler before it opens the database, so a stalled loop is also
    a loop that cannot be shut down. The engine this dials with is disposed here and never published
    to a registry, so no pooled connection outlives the check."""
    opened = [(url, pool) for url, pool in ((_app_url, _APP), (_owner_url, _OWNER)) if url]
    if not opened:
        raise RuntimeError("db not initialized (init_db runs in the composition root)")
    for url, pool in opened:
        engine = _build_engine(url, pool)
        try:
            async with engine.connect():
                pass
        finally:
            await engine.dispose()


async def dispose_db() -> None:
    """Teardown — a CLI verb's `finally`, a test's fixture. The urls clear first, before anything
    can fail, so a teardown that cannot finish never leaves the next `init_db` refusing, and no
    caller sees an exception: seventeen of them are a bare `finally: await dispose_db()`, where a
    raise would replace whatever drove teardown.

    A connection can only be closed by the loop that opened it. This loop's engines are disposed
    here and awaited. Another loop's are handed to that loop and *not* awaited: waiting on a loop
    this one does not drive is a wait with no end. An entry whose loop is already closed is
    unreachable by anything and only its key goes. Each entry leaves the registry before its
    handoff, so the foreign loop is never runnable with an engine both registered and disposing."""
    global _app_url, _owner_url
    loop = asyncio.get_running_loop()
    _app_url = None
    _owner_url = None
    for pool in (_APP, _OWNER):
        for held in [key for key in list(pool.engines) if key[0] is loop]:
            engine = pool.engines.pop(held, None)
            if engine is not None:
                await engine.dispose()
        for held in list(pool.engines):
            engine = pool.engines.pop(held, None)
            if engine is not None and not held[0].is_closed():
                _hand_off(held[0], engine)


def _hand_off(loop: asyncio.AbstractEventLoop, engine: AsyncEngine) -> None:
    """Ask `loop` to dispose `engine`. `call_soon_threadsafe` raises exactly when that loop closed
    after the caller looked, which is the one race a check cannot remove — and a closed loop can no
    longer close anything, so there is nothing left to do about it."""
    try:
        loop.call_soon_threadsafe(_dispose_on_this_loop, engine)
    except RuntimeError:
        return


def _dispose_on_this_loop(engine: AsyncEngine) -> None:
    """Runs on the loop that owns `engine`'s connections, which is the only loop that can close
    them. Holding the engine as an argument is what keeps it reachable between `dispose_db` removing
    its registry entry and this running, and `_disposing` is what keeps the task itself from being
    collected before it finishes."""
    task = asyncio.ensure_future(engine.dispose())
    _disposing.add(task)
    task.add_done_callback(_disposing.discard)


async def dispose_loop_engines() -> None:
    """Dispose and drop the running loop's engines, keeping the urls initialized. The steps `serve`
    drives on throwaway `asyncio.run` loops call this before their loop closes, so no pooled
    connection is abandoned to a dead loop; the persistent loops keep theirs for the process's
    life."""
    loop = asyncio.get_running_loop()
    for pool in (_APP, _OWNER):
        for held in [key for key in list(pool.engines) if key[0] is loop]:
            engine = pool.engines.pop(held, None)
            if engine is not None:
                await engine.dispose()


def _stopping() -> bool:
    """Whether this task has been asked to stop. A cancellation delivered while the driver holds the
    greenlet comes back out of SQLAlchemy as a database error, so every `except SQLAlchemyError`
    over a transaction would read a stop as a blip the caller survives and keep working — a listener
    that can never be shut down. The request outlives the disguise: `cancelling` counts what was
    asked and only an `uncancel` takes it back."""
    task = asyncio.current_task()
    return task is not None and bool(task.cancelling())


async def _await_opening(
    opening: asyncio.Future[AsyncConnection],
) -> tuple[AsyncConnection, asyncio.CancelledError | None]:
    cancelled: asyncio.CancelledError | None = None
    while not opening.done():
        try:
            await asyncio.shield(opening)
        except asyncio.CancelledError as cancel:
            cancelled = cancel
        except Exception:
            break
    return opening.result(), cancelled


async def _await_close(close: asyncio.Future[bool | None]) -> asyncio.CancelledError | None:
    cancelled: asyncio.CancelledError | None = None
    while not close.done():
        try:
            await asyncio.shield(close)
        except asyncio.CancelledError as cancel:
            cancelled = cancel
    close.result()
    return cancelled


@asynccontextmanager
async def _opened(engine: AsyncEngine, path: str) -> AsyncIterator[AsyncConnection]:
    """Begin a transaction, timing the acquisition and counting the ones that never begin. A
    Postgres transaction waits twice before it runs — for a slot in this loop's pool, then for a
    dial if the pool has no warm connection to hand it. SQLite queues on its one local writer slot
    before checking out a connection; `begin immediate` keeps the same ordering across processes.
    The database sees none of the waits before checkout, so the count has to be taken here, where
    the wait happens. Only the acquisition is watched; a failure inside the caller's transaction is
    the caller's own.

    A pool exhausted at its ceiling raises `sqlalchemy.exc.TimeoutError`, whose class name is the
    bare `TimeoutError` a lost dial raises too — one is this fleet reaching its own ceiling, the
    other is the network, and nothing in the unavailable count's dimensions separates them. So
    saturation carries its own name.

    `emit_metric` is imported here because `o11y` reads this module's ambient workspace, the same
    cycle `apply_migrations` breaks the same way."""
    from ufo.harness.o11y import emit_histogram, emit_metric

    lock = None
    if engine.dialect.name == "sqlite":
        lock = _SQLITE_TRANSACTION_LOCKS.get(engine)
        if lock is None:
            lock = _SQLITE_TRANSACTION_LOCKS[engine] = asyncio.Lock()
    locked = False
    started = time.monotonic()
    try:
        try:
            if lock is not None:
                await lock.acquire()
                locked = True
            stack = AsyncExitStack()
            opening = asyncio.ensure_future(stack.enter_async_context(engine.begin()))
            try:
                connection, cancelled = await _await_opening(opening)
            except Exception as error:
                if isinstance(error, sa.exc.TimeoutError):
                    emit_metric("db_pool_exhausted_total", path=path)
                emit_metric("db_tx_unavailable_total", path=path, error_class=type(error).__name__)
                if _stopping():
                    raise asyncio.CancelledError from error
                raise
        finally:
            elapsed = round((time.monotonic() - started) * 1000)
            emit_histogram("db_tx_acquire_ms", elapsed, path=path)
        caught: BaseException | None = cancelled
        if caught is None:
            try:
                yield connection
            except BaseException as error:
                caught = error
        close = asyncio.ensure_future(
            stack.__aexit__(type(caught), caught, caught.__traceback__)
            if caught is not None
            else stack.__aexit__(None, None, None)
        )
        cancelled = await _await_close(close)
        if cancelled is not None:
            raise cancelled
        if caught is not None:
            if _stopping() and not isinstance(caught, asyncio.CancelledError):
                raise asyncio.CancelledError from caught
            raise caught
    finally:
        if locked and lock is not None:
            lock.release()


@asynccontextmanager
async def workspace_tx() -> AsyncIterator[AsyncConnection]:
    if _app_url is None:
        raise RuntimeError("db not initialized (init_db runs in the composition root)")
    async with _opened(_engine_for(_app_url, _APP), "workspace") as connection:
        workspace_id = current_workspace.get()
        if workspace_id is not None and connection.dialect.name == "postgresql":
            await connection.execute(
                sa.text("select set_config(:guc, :ws, true)"),
                {"guc": WORKSPACE_GUC, "ws": str(workspace_id)},
            )
        yield connection


def failed_statement(error: BaseException) -> dict[str, str]:
    """The driver's own account of a refused statement, as log fields: the SQL it refused and the
    SQLSTATE it answered with. Empty for anything that is not a database error.

    A `ProgrammingError` says only that the statement was refused — an unknown column or table, a
    parameter the query cannot bind — and which one it was is in the statement and the code. A
    failure log carrying neither names no defect at all: the schema the process met has moved on
    by the time anyone reads the line, so both fields are taken here, where the driver holds them.

    The bound parameters stay out, and so does the message the database returned beside them. The
    statement text is this repo's own SQL; the values bound into it are a workspace's rows, and a
    driver message quotes them back — `formatted_stack` withholds a message for the same reason."""
    if not isinstance(error, sa.exc.DBAPIError):
        return {}
    fields = {}
    if error.statement:
        fields["statement"] = error.statement[:STATEMENT_LOG_MAX_CHARS]
    sqlstate = getattr(error.orig, "sqlstate", None) or getattr(error.orig, "pgcode", None)
    if isinstance(sqlstate, str):
        fields["sqlstate"] = sqlstate
    return fields


@asynccontextmanager
async def owner_tx() -> AsyncIterator[AsyncConnection]:
    """The one cross-workspace read path: a transaction that pins NO workspace GUC, so it enumerates
    every workspace this deploy serves. The background sweeps find their work across workspaces
    through it, then re-scope each unit under `with ws(row.workspace_id)`. With an owner URL set
    (`serve`) it bypasses RLS through the owner role; without one it falls to the app pool — the
    same engine `workspace_tx` resolves, never a second pool for one URL — and that URL's own role
    scopes it. It threads no workspace and sets no GUC, so nothing it yields is a tenant boundary:
    never read a row's contents through it beyond the identifiers needed to re-bind that row's own
    workspace."""
    url, pool = (_app_url, _APP) if _owner_url is None else (_owner_url, _OWNER)
    if url is None:
        raise RuntimeError("db not initialized (init_db runs in the composition root)")
    async with _opened(_engine_for(url, pool), "owner") as connection:
        yield connection


def apply_migrations(url: str, pack: str | None = None) -> None:
    """Alembic owns the schema; runs off the loop (CLI startup, test fixtures). Core's version
    location and every active extension's are layered into one run, so `upgrade heads` brings the
    deploy to core's head plus each pinned extension's — one head per owner, each extension ordered
    after core by the `depends_on` its base declares. With `pack` set the active set narrows to that
    pack's bundle, so only its extensions' tables are created. The loader import is local to break
    the db↔loader↔context cycle (the loader reaches core through the same context that binds to this
    module).

    Two files claiming one revision id are one graph node, and a location with two heads is a fork
    that would stamp both and wedge every migrate after the fork is linearized — so the graph is
    validated before any DDL runs."""
    from ufo.host.ext.loader import migration_locations

    config = AlembicConfig()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    locations = (str(MIGRATIONS_DIR / "versions"), *migration_locations(pack))
    config.set_main_option("version_locations", os.pathsep.join(locations))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    scripts = ScriptDirectory.from_config(config)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            heads = scripts.get_heads()
    except UserWarning as error:
        raise RuntimeError("a duplicate migration revision collapses into one node") from error
    heads_by_location: dict[Path, list[str]] = {}
    for head in heads:
        heads_by_location.setdefault(Path(scripts.get_revision(head).path).parent, []).append(head)
    for location, revisions in heads_by_location.items():
        if len(revisions) > 1:
            raise RuntimeError(
                f"migration graph forked: {location} has heads {sorted(revisions)}; "
                "repoint down_revision at the branch head"
            )
    command.upgrade(config, "heads")
    if url.startswith("sqlite"):
        _seal_sqlite_journal(url)


def _seal_sqlite_journal(url: str) -> None:
    """Leave a migrated sqlite file in the journal mode every engine that opens it expects, with
    nothing left in a sidecar.

    Alembic builds its own engine, so `_sqlite_on_connect` never runs against it and the file it
    writes is left in the default rollback journal. The first engine to open that file then converts
    it, and the conversion needs an exclusive lock it cannot wait out: a connection that has to
    convert while another holds the file ends in `database is locked`. Converting here — once,
    before any engine or any copy of this file exists — means no later connection ever asks for that
    lock. Alembic commits every migrated row into the file itself and closing leaves no write-ahead
    log beside it, so a plain byte copy of it carries the whole database."""
    database = make_url(url).database
    if database is None:
        raise RuntimeError(f"sqlite url names no file: {url}")
    with sqlite3.connect(database) as connection:
        connection.execute("pragma journal_mode=wal")
    connection.close()


def core_migration_head() -> str:
    """Core's single head — the revision a new core migration chains onto. Core's version location
    alone, so an extension's branch head is never what comes back."""
    config = AlembicConfig()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    head = ScriptDirectory.from_config(config).get_current_head()
    if head is None:
        raise RuntimeError("core's migration graph has no head")
    return head


def _sqlite_on_connect(dbapi_connection: Any, _connection_record: Any) -> None:
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    cursor.execute("pragma journal_mode=wal")
    cursor.execute("pragma foreign_keys=on")
    cursor.execute(f"pragma busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    cursor.close()


def _sqlite_begin_immediate(connection: sa.Connection) -> None:
    """Claim the single writer slot up front: lock-upgrade deadlocks become queueing."""
    connection.exec_driver_sql("begin immediate")
