import asyncio
import os
import socket
from collections.abc import AsyncIterator

import asyncpg
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from selfhost.db import apply_migrations, dispose_db, init_db, workspace_tx
from selfhost.schema import tables

POSTGRES_TEST_URL = os.environ.get(
    "SELFHOST_TEST_POSTGRES_URL",
    "postgresql+asyncpg://selfhost:selfhost@127.0.0.1:5541/selfhost_test",
)
DELETE_ORDER = (
    tables.ledger,
    tables.turn,
    tables.conversation,
    tables.surface_identity,
    tables.agent,
    tables.ext_store,
    tables.credential,
    tables.member,
    tables.workspace,
)


def postgres_reachable() -> bool:
    url = make_url(POSTGRES_TEST_URL)
    try:
        with socket.create_connection((url.host, url.port), timeout=0.5):
            return True
    except OSError:
        return False


async def reset_postgres_database(name: str) -> None:
    url = make_url(POSTGRES_TEST_URL)
    admin = await asyncpg.connect(
        host=url.host, port=url.port, user=url.username, password=url.password, database="selfhost"
    )
    try:
        await admin.execute(f'drop database if exists "{name}"')
        await admin.execute(f'create database "{name}"')
    finally:
        await admin.close()


@pytest.fixture(scope="session", params=["sqlite", "postgres"])
def database_url(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> str:
    if request.param == "sqlite":
        url = f"sqlite+aiosqlite:///{tmp_path_factory.mktemp('db') / 'selfhost_test.db'}"
        apply_migrations(url)
        return url
    if not postgres_reachable():
        pytest.skip("postgres service not reachable")
    asyncio.run(reset_postgres_database(make_url(POSTGRES_TEST_URL).database))
    apply_migrations(POSTGRES_TEST_URL)
    return POSTGRES_TEST_URL


@pytest.fixture
async def db(database_url: str) -> AsyncIterator[None]:
    init_db(database_url)
    async with workspace_tx() as connection:
        for table in DELETE_ORDER:
            await connection.execute(sa.delete(table))
    yield
    await dispose_db()
