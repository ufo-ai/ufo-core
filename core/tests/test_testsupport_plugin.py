"""The shared plugin's own guarantees, driven through a nested pytest run — the `pytester` shape
`test_test_sharding.py` uses for the same plugin's sharding hook.

The `db` fixture binds a process global, so what it does when a test or its own setup fails is a
property of the fixture rather than of any test using it: it can only be asserted from outside, by
running a session that fails that way and reading what the next test in that process sees. The
nested run is a subprocess, so its globals are its own and this file's session never inherits
them."""

import sqlite3

import pytest
from ufo_testsupport.plugin import postgres_reachable

pytest_plugins = ("pytester",)

SQLITE_SESSION = """
from uuid import uuid4

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.schema import tables


async def test_first_writes_then_fails(db):
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=uuid4(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    raise AssertionError("fail after committing, without disposing anything ourselves")


async def test_second_starts_clean(db):
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.workspace))
        ).scalar_one()
    assert rows == 0
"""


def test_a_failed_sqlite_test_leaves_the_next_a_clean_engine_and_a_clean_db(
    pytester: pytest.Pytester,
) -> None:
    """SQLite tests each run their own copy of the migrated template, so a failing test neither
    poisons the process global (the next `init_db` binds cleanly) nor leaks its rows into the next
    test's tables — the isolation the shared session file could not give, where a background
    writer a previous test left running holds the file's one writer slot across the next test's
    `begin immediate` and lands late rows in freshly reset tables."""
    pytester.makeini("[pytest]\nasyncio_mode = auto\n")
    pytester.makepyfile(inner_session=SQLITE_SESSION)

    result = pytester.runpytest_subprocess("-q", "-k", "sqlite", "inner_session.py")

    outcomes = result.parseoutcomes()
    assert outcomes.get("failed") == 1, result.stdout.str()
    assert outcomes.get("passed") == 1, result.stdout.str()
    assert "db already initialized" not in result.stdout.str()

    lone = pytester.runpytest_subprocess("-q", "-k", "first and sqlite", "inner_session.py")

    assert lone.parseoutcomes().get("failed") == 1, lone.stdout.str()
    templates = list(pytester.path.glob("**/ufo_test.db"))
    assert templates, "the inner sessions built no sqlite template"
    for template in templates:
        with sqlite3.connect(template) as connection:
            rows = connection.execute("select count(*) from workspace").fetchone()[0]
        assert rows == 0, f"a test's rows reached the shared template {template}"


LEAKING_WIPE_SESSION = """
import asyncio

import ufo_testsupport.plugin as plugin
from ufo.db import dispose_db, init_db


async def _locked(connection):
    raise RuntimeError("database is locked")


plugin.reset_workspace_data = _locked


async def test_its_wipe_cannot_take_the_write_lock(db):
    raise AssertionError("the fixture raises in setup, so this body never runs")


def test_the_next_test_sees_no_bound_global(database_url):
    init_db(database_url)
    asyncio.run(dispose_db())
"""


def test_the_db_fixture_disposes_when_its_own_wipe_raises(pytester: pytest.Pytester) -> None:
    """The postgres arm still wipes between `init_db` and the `yield`, and a wipe that raises must
    still dispose. Without that the next test in the worker fails on `init_db`'s own "db already
    initialized" instead of its own subject, so one contended wipe reads as a shard of unrelated
    failures."""
    if not postgres_reachable():
        pytest.skip("Postgres service is not reachable")
    pytester.makeini("[pytest]\nasyncio_mode = auto\n")
    pytester.makepyfile(inner_session=LEAKING_WIPE_SESSION)

    result = pytester.runpytest_subprocess("-q", "-k", "postgres", "inner_session.py")

    outcomes = result.parseoutcomes()
    assert outcomes.get("errors") == 1, result.stdout.str()
    assert outcomes.get("passed") == 1, result.stdout.str()
    result.stdout.fnmatch_lines(["*database is locked*"])
    assert "db already initialized" not in result.stdout.str()


WORKFLOW_DRAIN_SESSION = """
import asyncio

from dbos import DBOS

finished = []


@DBOS.workflow()
async def finish_after_test():
    await asyncio.sleep(0.1)
    finished.append(True)



async def test_runtime_test_finishes(db, dbos_launched):
    await DBOS.start_workflow_async(finish_after_test)


def test_the_next_test_sees_the_completed_drain(database_url):
    assert finished == [True]
"""


def test_a_runtime_test_drains_before_the_next_postgres_wipe(pytester: pytest.Pytester) -> None:
    if not postgres_reachable():
        pytest.skip("Postgres service is not reachable")
    pytester.makeini("[pytest]\nasyncio_mode = auto\n")
    pytester.makepyfile(inner_session=WORKFLOW_DRAIN_SESSION)

    result = pytester.runpytest_subprocess("-q", "-k", "postgres", "inner_session.py")

    result.assert_outcomes(passed=2)
