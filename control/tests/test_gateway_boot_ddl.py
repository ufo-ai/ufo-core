"""Boot-time DDL against a real Postgres that holds none of it yet.

Every gateway replica issues the same `create ... if not exists` statements on startup, and Postgres
does not make those atomic against a concurrent creator: without the boot-DDL advisory lock the
losers raise a `pg_class`/`pg_namespace` unique violation and their startup dies. A fleet restart is
exactly this shape, so the proof is the shape too — N first-time boots at once, then the schema they
all agreed on.
"""

import asyncio
from collections.abc import AsyncIterator

import asyncpg
import pytest

from ufo_control.gateway_invite import InviteCodes
from ufo_control.gateway_slack_connect import DUE_INDEX, ensure_delivery_table
from ufo_control.gateway_store import ACTIVE_INDEX, SCHEMA, OnboardStore

BOOTS = 8
BOOT_DATABASE = "ufo_boot_ddl"
LIVE_INVITE_INDEX = "invite_code_live_object"
EXPECTED_TABLES = frozenset({"onboard_claim", "invite_code", "slack_connect_delivery"})
EXPECTED_INDEXES = frozenset({ACTIVE_INDEX, LIVE_INVITE_INDEX, DUE_INDEX})


@pytest.fixture
async def empty_database(gateway_postgres: str) -> AsyncIterator[str]:
    """A database of its own, so first-time creation is genuinely first-time and the session's
    prepared schema is never dropped out from under the other tests."""
    dsn = f"{gateway_postgres.rsplit('/', 1)[0]}/{BOOT_DATABASE}"
    await _recreate_database(gateway_postgres)
    try:
        yield dsn
    finally:
        await _drop_database(gateway_postgres)


async def test_concurrent_first_boots_all_shape_the_control_schema(empty_database: str) -> None:
    await asyncio.gather(*(_boot(empty_database) for _ in range(BOOTS)))

    connection = await asyncpg.connect(empty_database)
    try:
        tables = await connection.fetch(
            "select table_name from information_schema.tables where table_schema = $1", SCHEMA
        )
        indexes = await connection.fetch(
            "select indexname from pg_indexes where schemaname = $1", SCHEMA
        )
    finally:
        await connection.close()
    assert {row["table_name"] for row in tables} == EXPECTED_TABLES
    assert EXPECTED_INDEXES <= {row["indexname"] for row in indexes}


async def _boot(dsn: str) -> None:
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    try:
        await OnboardStore(pool=pool).ensure_table()
        await InviteCodes(pool=pool).ensure_table()
        await ensure_delivery_table(pool)
    finally:
        await pool.close()


async def _recreate_database(admin_dsn: str) -> None:
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute(f'drop database if exists "{BOOT_DATABASE}" with (force)')
        await connection.execute(f'create database "{BOOT_DATABASE}"')
    finally:
        await connection.close()


async def _drop_database(admin_dsn: str) -> None:
    connection = await asyncpg.connect(admin_dsn)
    try:
        await connection.execute(f'drop database if exists "{BOOT_DATABASE}" with (force)')
    finally:
        await connection.close()
