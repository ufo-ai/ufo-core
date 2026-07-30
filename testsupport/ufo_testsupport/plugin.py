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
import hashlib
import os
import socket
from collections.abc import AsyncIterator, Iterator

import asyncpg
import pytest
from dbos import DBOS
from sqlalchemy.engine import make_url

from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.db import apply_migrations, dispose_db, init_db, workspace_tx
from ufo.schema.records import DBOS_APP_NAME, DBOS_APP_VERSION
from ufo.workspace import init_workspace_credentials
from ufo_testsupport.tables import reset_workspace_data

POSTGRES_TEST_URL = os.environ.get(
    "UFO_TEST_POSTGRES_URL",
    "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo_test",
)
INTEGRATION_REQUIRED_ENV = "UFO_INTEGRATION_REQUIRED"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--shard", help="Run one deterministic INDEX/COUNT shard")


def postgres_reachable() -> bool:
    url = make_url(POSTGRES_TEST_URL)
    try:
        with socket.create_connection((url.host, url.port), timeout=0.5):
            return True
    except OSError:
        return False


def integration_dependency_available(available: bool, reason: str) -> bool:
    if not available and os.environ.get(INTEGRATION_REQUIRED_ENV):
        raise RuntimeError(f"required integration dependency unavailable: {reason}")
    return available


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
    if not integration_dependency_available(
        postgres_reachable(), "Postgres service is not reachable"
    ):
        pytest.skip("Postgres service is not reachable")
    base = make_url(POSTGRES_TEST_URL)
    url = base.set(database=f"{base.database}_{worker_id}")
    dsn = url.render_as_string(hide_password=False)
    asyncio.run(reset_postgres_database(url.database))
    apply_migrations(dsn)
    return dsn


@pytest.fixture(autouse=True)
def _reset_workspace_credentials() -> Iterator[None]:
    """The workspace credential store is a process global installed once at boot; a test that
    installs one must not leak it into the next — especially a test that never inits the db, where a
    leaked store drives `ws_current().credential` into `workspace_tx` and fails with 'db not
    initialized' rather than resolving the platform env default. Reset around every test so
    credential resolution falls to env unless a test explicitly installs a store."""
    init_workspace_credentials(None)
    yield
    init_workspace_credentials(None)


@pytest.fixture
async def db(database_url: str) -> AsyncIterator[None]:
    """One initialized engine per test, disposed however the test ends: `init_db` guards a process
    global, so the dispose is in a `finally`. The wipe between it and the `yield` can raise — a
    SQLite `begin immediate` that cannot take the write lock inside its busy timeout does — and a
    setup that raised without disposing would poison every later test in the process with 'db
    already initialized', one flaky failure amplified into a red shard."""
    init_db(database_url)
    try:
        async with workspace_tx() as connection:
            await reset_workspace_data(connection)
        yield
    finally:
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


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Stamp every test under a `tests/integration/` path `integration` + `serial` so `-m
    integration` selects the whole live suite and `-m "not integration"` excludes it, without each
    module repeating the two markers — the per-module `pytestmark` then adds only the dependency
    gate it actually needs."""
    for item in items:
        if "tests/integration/" in item.nodeid or "tests\\integration\\" in item.nodeid:
            item.add_marker(pytest.mark.integration)
            item.add_marker(pytest.mark.serial)
    shard = config.getoption("--shard")
    if shard is None:
        return
    try:
        index_text, count_text = shard.split("/", maxsplit=1)
        index = int(index_text)
        count = int(count_text)
    except (AttributeError, ValueError) as error:
        raise pytest.UsageError("--shard must be INDEX/COUNT") from error
    if count < 2 or index < 1 or index > count:
        raise pytest.UsageError(
            "--shard must be INDEX/COUNT with COUNT >= 2 and 1 <= INDEX <= COUNT"
        )
    selected: list[pytest.Item] = []
    deselected: list[pytest.Item] = []
    for item in items:
        digest = int.from_bytes(hashlib.sha256(item.nodeid.encode()).digest())
        (selected if digest % count == index - 1 else deselected).append(item)
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)
