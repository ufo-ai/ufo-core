"""The one pytest plugin every test directory shares, registered once via a pytest11 entry point.

DBOS is a process singleton — it cannot launch twice in a process. Loading this as an entry-point
plugin means pytest imports and registers it exactly once at startup, so there is exactly one
`dbos_launched` FixtureDef across core/tests and every `extensions/*/tests`: one launch per database
param, never a double-launch. (A per-directory conftest would create a second FixtureDef the moment
two test roots are collected together — the wedge this plugin exists to prevent.)

It also stamps every test under a `tests/integration/` path `integration` + `serial`, so `-m
integration` selects the whole live suite wherever it lives and each live module declares only the
dependency gate it needs."""

import asyncio
import os
import socket
from collections.abc import AsyncIterator, Iterator

import asyncpg
import pytest
import sqlalchemy as sa
from dbos import DBOS
from sqlalchemy.engine import make_url

from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.db import apply_migrations, dispose_db, init_db, workspace_tx
from ufo.schema.records import DBOS_APP_NAME, DBOS_APP_VERSION
from ufo_testsupport.tables import DELETE_ORDER

POSTGRES_TEST_URL = os.environ.get(
    "UFO_TEST_POSTGRES_URL",
    "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo_test",
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
        host=url.host, port=url.port, user=url.username, password=url.password, database="ufo"
    )
    try:
        await admin.execute(f'drop database if exists "{name}"')
        await admin.execute(f'create database "{name}"')
    finally:
        await admin.close()


@pytest.fixture(scope="session", params=["sqlite", "postgres"])
def database_url(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory, worker_id: str
) -> str:
    if request.param == "sqlite":
        url = f"sqlite+aiosqlite:///{tmp_path_factory.mktemp('db') / 'ufo_test.db'}"
        apply_migrations(url)
        return url
    if not postgres_reachable():
        pytest.skip("postgres service not reachable")
    base = make_url(POSTGRES_TEST_URL)
    url = base.set(database=f"{base.database}_{worker_id}")
    dsn = url.render_as_string(hide_password=False)
    asyncio.run(reset_postgres_database(url.database))
    apply_migrations(dsn)
    return dsn


@pytest.fixture
async def db(database_url: str) -> AsyncIterator[None]:
    init_db(database_url)
    async with workspace_tx() as connection:
        for table in DELETE_ORDER:
            await connection.execute(sa.delete(table))
    yield
    await dispose_db()


@pytest.fixture(scope="session")
def dbos_launched(database_url: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Config]:
    """The one DBOS instance per session: DBOS is a process singleton that cannot launch twice, so
    every DBOS-driving test module shares this launch."""
    config = Config(
        database=DatabaseConfig(url=database_url),
        blob=BlobConfig(backend="filesystem", root=tmp_path_factory.mktemp("blobs")),
    )
    system_url = config.database.system_url
    if system_url.startswith("postgresql"):
        asyncio.run(reset_postgres_database(make_url(system_url).database))
    DBOS(
        config={
            "name": DBOS_APP_NAME,
            "application_version": DBOS_APP_VERSION,
            "system_database_url": system_url,
            "run_admin_server": False,
            "scheduler_polling_interval_sec": 1.0,
        }
    )
    DBOS.launch()
    yield config
    DBOS.destroy()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Stamp every test under a `tests/integration/` path `integration` + `serial` so `-m
    integration` selects the whole live suite and `-m "not integration"` excludes it, without each
    module repeating the two markers — the per-module `pytestmark` then adds only the dependency
    gate it actually needs."""
    for item in items:
        if "tests/integration/" in item.nodeid or "tests\\integration\\" in item.nodeid:
            item.add_marker(pytest.mark.integration)
            item.add_marker(pytest.mark.serial)
