import os
from collections.abc import AsyncIterator

import psycopg
import pytest
from sqlalchemy import text

from selfhost.db import apply_migrations, dispose_db, init_db, workspace_tx

TEST_DATABASE_URL = os.environ.get(
    "SELFHOST_TEST_DATABASE_URL",
    "postgresql+psycopg://selfhost:selfhost@127.0.0.1:5541/selfhost_test",
)
ALL_TABLES = "workspace, member, surface_identity, agent, conversation, turn, ledger"


@pytest.fixture(scope="session")
def database_url() -> str:
    base, _, dbname = TEST_DATABASE_URL.rpartition("/")
    admin_dsn = f"{base}/selfhost".replace("postgresql+psycopg://", "postgresql://", 1)
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        connection.execute(f"drop database if exists {dbname}")
        connection.execute(f"create database {dbname}")
    apply_migrations(TEST_DATABASE_URL)
    return TEST_DATABASE_URL


@pytest.fixture
async def db(database_url: str) -> AsyncIterator[None]:
    init_db(database_url)
    async with workspace_tx() as connection:
        await connection.execute(text(f"truncate {ALL_TABLES} cascade"))
    yield
    await dispose_db()
