"""The tenancy boundary: module-private engine, workspace_tx as the only session source."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

MIGRATIONS_DIR = Path(__file__).parent / "schema" / "migrations"
SQLITE_BUSY_TIMEOUT_MS = 5_000

_engine: AsyncEngine | None = None


def init_db(url: str) -> None:
    """NullPool: a pooled connection binds to one event loop, and surfaces and DBOS
    workflows run on different loops in the same process."""
    global _engine
    if _engine is not None:
        raise RuntimeError("db already initialized")
    engine = create_async_engine(url, poolclass=NullPool)
    if engine.dialect.name == "sqlite":
        sa.event.listen(engine.sync_engine, "connect", _sqlite_on_connect)
        sa.event.listen(engine.sync_engine, "begin", _sqlite_begin_immediate)
    _engine = engine


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
        yield connection


def apply_migrations(url: str) -> None:
    """Alembic owns the schema; runs off the loop (CLI startup, test fixtures)."""
    config = AlembicConfig()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")


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
