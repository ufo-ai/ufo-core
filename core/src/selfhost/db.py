"""The tenancy boundary: module-private engine, workspace_tx as the only session source."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import psycopg
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

SCHEMA_DIR = Path(__file__).parent / "schema"

_engine: AsyncEngine | None = None


def init_db(url: str) -> None:
    global _engine
    if _engine is not None:
        raise RuntimeError("db already initialized")
    _engine = create_async_engine(url)


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
    """Sync by design: migrations run off the loop, at CLI startup."""
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://", 1)) as connection:
        for sql_file in sorted(SCHEMA_DIR.glob("*.sql")):
            connection.execute(sql_file.read_text())
        connection.commit()
