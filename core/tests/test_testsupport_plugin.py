"""The shared plugin's own guarantees, driven through a nested pytest run — the `pytester` shape
`test_test_sharding.py` uses for the same plugin's sharding hook.

The `db` fixture binds a process global, so what it does when its own setup fails is a property of
the fixture rather than of any test using it: it can only be asserted from outside, by running a
session that fails that way and reading what the next test in that process sees. The nested run is a
subprocess, so its globals are its own and this file's session never inherits them."""

import pytest

pytest_plugins = ("pytester",)

LEAKING_SESSION = """
import asyncio

import pytest

import ufo_testsupport.plugin as plugin
from ufo.db import dispose_db, init_db


async def _locked(connection):
    raise RuntimeError("database is locked")


plugin.reset_workspace_data = _locked


@pytest.fixture(scope="session")
def database_url(tmp_path_factory):
    return f"sqlite+aiosqlite:///{tmp_path_factory.mktemp('leak') / 'leak.db'}"


async def test_its_wipe_cannot_take_the_write_lock(db):
    raise AssertionError("the fixture raises in setup, so this body never runs")


def test_the_next_test_sees_no_bound_global(database_url):
    init_db(database_url)
    asyncio.run(dispose_db())
"""


def test_the_db_fixture_disposes_when_its_own_wipe_raises(pytester: pytest.Pytester) -> None:
    """A wipe that raises between `init_db` and the `yield` still disposes. Without that the next
    test in the worker fails on `init_db`'s own "db already initialized" instead of its own subject,
    so one contended wipe reads as a shard of unrelated failures."""
    pytester.makepyfile(LEAKING_SESSION)

    result = pytester.runpytest_subprocess("-q", "-o", "asyncio_mode=auto")

    outcomes = result.parseoutcomes()
    assert outcomes.get("errors") == 1, result.stdout.str()
    assert outcomes.get("passed") == 1, result.stdout.str()
    result.stdout.fnmatch_lines(["*database is locked*"])
    assert "db already initialized" not in result.stdout.str()
