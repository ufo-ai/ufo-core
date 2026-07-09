"""The tenancy boundary: module-private engine, workspace_tx as the only session source.

Isolation is set per transaction from an ambient workspace, so one shared-serve process (one
connection pool) safely serves many workspaces: a request/turn/job sets `current_workspace` at its
boundary, and `workspace_tx` pins the RLS GUC (`app.workspace_id`) for that transaction. The
contextvar defaults to unset — then `workspace_tx` sets no GUC and the connection's own role
default scopes it: the single-workspace per-tenant (enterprise) deploy, unchanged.
The shared-serve role is an RLS *subject* with no pinned default, so a transaction that never set
the workspace fails closed — the policy's `current_setting` errors on the unset GUC, never a leak.
"""

import asyncio
import os
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

MIGRATIONS_DIR = Path(__file__).parent / "schema" / "migrations"
SQLITE_BUSY_TIMEOUT_MS = 5_000
WORKSPACE_GUC = "app.workspace_id"

_engine: AsyncEngine | None = None

current_workspace: ContextVar[UUID | None] = ContextVar("current_workspace", default=None)


def init_db(url: str) -> None:
    """NullPool: a pooled connection binds to one event loop, and surfaces and DBOS
    workflows run on different loops in the same process. The engine's one-time first-connect
    (dialect init, guarded by the pool's first-connect mutex held across async I/O) is completed
    here, single-threaded, before the engine is published — a first-connect driven concurrently
    from two loops deadlocks that mutex, so an engine is never shared until it is past it."""
    global _engine
    if _engine is not None:
        raise RuntimeError("db already initialized")
    engine = create_async_engine(url, poolclass=NullPool)
    if engine.dialect.name == "sqlite":
        sa.event.listen(engine.sync_engine, "connect", _sqlite_on_connect)
        sa.event.listen(engine.sync_engine, "begin", _sqlite_begin_immediate)
    _first_connect(engine)
    _engine = engine


def _first_connect(engine: AsyncEngine) -> None:
    """Force the engine's one-time dialect initialization on a private loop off any caller loop, so
    it is complete before the engine is driven concurrently from multiple event loops. Runs on its
    own thread — a fresh thread never has a running loop, so this is uniform whether init_db is
    called from a composition root or from within a running loop — and re-raises on the caller."""
    error: list[BaseException] = []

    def run() -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_open_and_close(engine))
        except BaseException as caught:
            error.append(caught)
        finally:
            loop.close()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if error:
        raise error[0]


async def _open_and_close(engine: AsyncEngine) -> None:
    async with engine.connect():
        pass


async def dispose_db() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


@asynccontextmanager
async def workspace_tx() -> AsyncIterator[AsyncConnection]:
    if _engine is None:
        raise RuntimeError("db not initialized (init_db runs in the composition root)")
    async with _engine.begin() as connection:
        workspace_id = current_workspace.get()
        if workspace_id is not None and connection.dialect.name == "postgresql":
            await connection.execute(
                sa.text("select set_config(:guc, :ws, true)"),
                {"guc": WORKSPACE_GUC, "ws": str(workspace_id)},
            )
        yield connection


def apply_migrations(url: str, pack: str | None = None) -> None:
    """Alembic owns the schema; runs off the loop (CLI startup, test fixtures). Core's version
    location and every active extension's are layered into one run, so `upgrade heads` brings the
    deploy to core's head plus each pinned extension's — one head per owner, each extension ordered
    after core by the `depends_on` its base declares. With `pack` set the active set narrows to that
    pack's bundle, so only its extensions' tables are created. The loader import is local to break
    the db↔loader↔context cycle (the loader reaches core through the same context that binds to this
    module)."""
    from ufo.ext.loader import migration_locations

    config = AlembicConfig()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    locations = (str(MIGRATIONS_DIR / "versions"), *migration_locations(pack))
    config.set_main_option("version_locations", os.pathsep.join(locations))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "heads")


def _sqlite_on_connect(dbapi_connection: Any, connection_record: Any) -> None:
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    cursor.execute("pragma journal_mode=wal")
    cursor.execute("pragma foreign_keys=on")
    cursor.execute(f"pragma busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    cursor.close()


def _sqlite_begin_immediate(connection: sa.Connection) -> None:
    """Claim the single writer slot up front: lock-upgrade deadlocks become queueing."""
    connection.exec_driver_sql("begin immediate")
