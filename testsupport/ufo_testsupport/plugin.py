"""The one pytest plugin every test directory shares, registered once via a pytest11 entry point.

DBOS is a process singleton — it cannot launch twice in a process. Loading this as an entry-point
plugin means pytest imports and registers it exactly once at startup, so there is exactly one
`dbos_launched` FixtureDef across core/tests and every `extensions/*/tests`: one launch per database
param, never a double-launch. (A per-directory conftest would create a second FixtureDef the moment
two test roots are collected together — the wedge this plugin exists to prevent.)

It also stamps every test under a `tests/integration/` path `integration` + `serial`, so `-m
integration` selects the whole live suite wherever it lives and each live module declares only the
dependency gate it needs.

And it records where a run's wall-clock went: one row per test and one run summary per invocation,
written once at session end under `--timings-dir` (see `_TimingsRecorder`), which CI uploads per job
so `.github/scripts/ci_timings_report.py` can merge many runs."""

import asyncio
import csv
import hashlib
import json
import os
import platform
import shutil
import socket
import sys
import time
from collections import Counter
from collections.abc import AsyncIterator, Generator, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import asyncpg
import pytest
from dbos import DBOS, DBOSClient
from sqlalchemy.engine import make_url

from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.db import apply_migrations, dispose_db, init_db, workspace_tx
from ufo.schema.records import DBOS_APP_NAME, DBOS_APP_VERSION
from ufo.workspace import init_workspace_credentials
from ufo_testsupport.migrations import apply_cached_migrations
from ufo_testsupport.tables import reset_workspace_data
from ufo_testsupport.workflows import drain_workflows

POSTGRES_TEST_URL = os.environ.get(
    "UFO_TEST_POSTGRES_URL",
    "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo_test",
)
INTEGRATION_REQUIRED_ENV = "UFO_INTEGRATION_REQUIRED"
TIMINGS_DIR_ENV = "UFO_TEST_TIMINGS_DIR"
DEFAULT_TIMINGS_DIR = ".pytest-timings"
TIMINGS_SCHEMA = 1
SLOWEST_IN_SUMMARY = 10
ROW_FIELDS = (
    "nodeid",
    "file",
    "directory",
    "outcome",
    "rerun",
    "database",
    "param_id",
    "worker",
    "setup_seconds",
    "call_seconds",
    "teardown_seconds",
    "total_seconds",
    "start",
    "stop",
)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--shard", help="Run one deterministic INDEX/COUNT shard")
    parser.addoption(
        "--timings-dir",
        default=os.environ.get(TIMINGS_DIR_ENV, DEFAULT_TIMINGS_DIR),
        help=(
            "Where this run writes its per-test timing artifacts, relative to the invocation "
            f"directory (default {DEFAULT_TIMINGS_DIR}, or ${TIMINGS_DIR_ENV}); empty writes none"
        ),
    )


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


async def _drain_dbos(database_url: str) -> None:
    client = DBOSClient(system_database_url=DatabaseConfig(url=database_url).system_url)
    try:
        await drain_workflows(client)
    finally:
        await asyncio.to_thread(client.destroy)


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
        apply_cached_migrations(url)
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
async def db(
    database_url: str, tmp_path: Path, request: pytest.FixtureRequest
) -> AsyncIterator[None]:
    """One initialized engine per test over the test's own database, disposed however the test
    ends (`init_db` guards a process global, so the dispose is in a `finally`).

    SQLite holds one writer slot per file, so the session file would couple every test to every
    background writer the process still carries — a turn workflow a previous test left running on
    the session DBOS worker holds that slot across this test's `begin immediate`, and its late
    rows land in tables the wipe just reset. Each test instead gets its own copy of the session's
    migrated template: the copy's writer population is this test alone, and no wipe is needed.
    Postgres (MVCC, one database per xdist worker) keeps the shared database and the wipe."""
    if database_url.startswith("sqlite"):
        private = tmp_path / "private.db"
        shutil.copy(make_url(database_url).database, private)
        init_db(f"sqlite+aiosqlite:///{private}")
        try:
            yield
        finally:
            try:
                if "dbos_launched" in request.fixturenames:
                    await _drain_dbos(database_url)
            finally:
                await dispose_db()
        return
    init_db(database_url)
    try:
        async with workspace_tx() as connection:
            await reset_workspace_data(connection)
        yield
    finally:
        try:
            if "dbos_launched" in request.fixturenames:
                await _drain_dbos(database_url)
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


@dataclass
class _Attempt:
    """One run of one test: the three phase durations plus the wall window they occupied."""

    nodeid: str
    file: str
    worker: str
    rerun: bool
    database: str = ""
    param_id: str = ""
    outcome: str = ""
    setup: float = 0.0
    call: float = 0.0
    teardown: float = 0.0
    start: float = 0.0
    stop: float = 0.0

    @property
    def total(self) -> float:
        return self.setup + self.call + self.teardown

    def row(self) -> dict[str, Any]:
        phases = [round(self.setup, 6), round(self.call, 6), round(self.teardown, 6)]
        return {
            "nodeid": self.nodeid,
            "file": self.file,
            "directory": str(PurePosixPath(self.file).parent) if self.file else "",
            "outcome": self.outcome or "passed",
            "rerun": self.rerun,
            "database": self.database,
            "param_id": self.param_id,
            "worker": self.worker,
            "setup_seconds": phases[0],
            "call_seconds": phases[1],
            "teardown_seconds": phases[2],
            # the sum of the columns as published, so a reader's own addition matches this one
            "total_seconds": round(sum(phases), 6),
            "start": round(self.start, 6),
            "stop": round(self.stop, 6),
        }


@dataclass
class _FixtureCost:
    """What one fixture cost this process, summed over its setups — for a session fixture, the once
    it paid. It times the fixture's own body, so a fixture that resolves another lazily carries that
    one too; the cost a test paid for the whole tree is its row's `setup_seconds`."""

    setups: int = 0
    seconds: float = 0.0
    slowest: float = 0.0
    errors: int = 0


def _phase_outcome(report: pytest.TestReport) -> str | None:
    """pytest's headline for one phase, or None when the phase says nothing — a plain passing setup
    or teardown. A failure outside `call` is an error, the same distinction the terminal draws."""
    if hasattr(report, "wasxfail"):
        return "xpassed" if report.passed else "xfailed"
    if report.skipped:
        return "skipped"
    if report.failed:
        return "failed" if report.when == "call" else "error"
    return None


def _pull_request_number() -> str:
    ref = os.environ.get("GITHUB_REF", "")
    parts = ref.split("/")
    return parts[2] if len(parts) > 3 and parts[1] == "pull" else ""


class _TimingsRecorder:
    """Where a run's wall-clock went, written once at session end.

    Rows are written by whichever process ran the tests — each xdist worker writes its own
    `tests-gwN.*` and `fixtures-gwN.json`, so no two writers share a file and per-worker gaps and
    the idle tail stay computable — while the process that owns the session writes the single
    `run-summary.json`. Under `-n` the controller sees every worker's reports, so its summary is the
    whole invocation; without `-n` one process does both.

    Every write is best-effort: instrumentation that cannot land degrades to a warning, never to a
    failed run.
    """

    def __init__(self, config: pytest.Config, directory: Path) -> None:
        self._config = config
        self._directory = directory
        workerinput = getattr(config, "workerinput", None)
        self._worker = str(workerinput["workerid"]) if workerinput else "master"
        self._is_worker = workerinput is not None
        self._processes = int(config.getoption("numprocesses", default=None) or 0)
        self._collected: dict[str, tuple[str, str, str]] = {}
        self._open: dict[str, _Attempt] = {}
        self._attempts: list[_Attempt] = []
        self._seen: set[str] = set()
        self._fixtures: dict[tuple[str, str], _FixtureCost] = {}
        self._session_start = 0.0
        self._session_stop = 0.0

    def pytest_sessionstart(self) -> None:
        self._session_start = time.time()

    def pytest_runtest_setup(self, item: pytest.Item) -> None:
        """The file a test lives in and how it was parametrized are properties of the item, not of
        its reports: read them here, so a row carries a path relative to the checkout (a report's
        own path is relative to whichever rootdir the invocation found) and separates the sqlite
        pass from the postgres one."""
        callspec = getattr(item, "callspec", None)
        database = str(callspec.params.get("database_url", "")) if callspec is not None else ""
        param_id = str(callspec.id) if callspec is not None else ""
        self._collected[item.nodeid] = (self._relative_file(item), database, param_id)

    def _relative_file(self, item: pytest.Item) -> str:
        path = getattr(item, "path", None)
        if path is None:
            return ""
        try:
            return str(path.relative_to(self._config.invocation_params.dir))
        except ValueError:
            return str(path)

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        attempt = self._open.get(report.nodeid)
        if attempt is None:
            file, database, param_id = self._collected.pop(
                report.nodeid, (report.location[0], "", "")
            )
            attempt = _Attempt(
                nodeid=report.nodeid,
                file=file,
                worker=self._worker,
                rerun=report.nodeid in self._seen,
                database=database,
                param_id=param_id,
                start=float(getattr(report, "start", 0.0)),
            )
            self._open[report.nodeid] = attempt
        duration = float(getattr(report, "duration", 0.0))
        if report.when == "setup":
            attempt.setup = duration
        elif report.when == "call":
            attempt.call = duration
        elif report.when == "teardown":
            attempt.teardown = duration
        attempt.start = min(attempt.start, float(getattr(report, "start", attempt.start)))
        attempt.stop = max(attempt.stop, float(getattr(report, "stop", 0.0)))
        attempt.outcome = attempt.outcome or (_phase_outcome(report) or "")
        if report.when == "teardown":
            del self._open[report.nodeid]
            self._seen.add(report.nodeid)
            self._attempts.append(attempt)

    @pytest.hookimpl(wrapper=True)
    def pytest_fixture_setup(
        self, fixturedef: pytest.FixtureDef[Any]
    ) -> Generator[None, object, object]:
        started = time.perf_counter()
        cost = self._fixtures.setdefault(
            (fixturedef.argname, str(fixturedef.scope)), _FixtureCost()
        )
        try:
            result = yield
        except BaseException:
            cost.errors += 1
            raise
        finally:
            elapsed = time.perf_counter() - started
            cost.setups += 1
            cost.seconds += elapsed
            cost.slowest = max(cost.slowest, elapsed)
        return result

    def pytest_sessionfinish(self, exitstatus: int | pytest.ExitCode) -> None:
        self._session_stop = time.time()
        try:
            self._write(int(exitstatus))
        except Exception as error:  # instrumentation never fails a run
            self._warn(f"timing artifacts not written: {error!r}")

    def _write(self, exitstatus: int) -> None:
        self._directory.mkdir(parents=True, exist_ok=True)
        if self._is_worker or not self._processes:
            rows = [attempt.row() for attempt in self._attempts]
            with (self._directory / f"tests-{self._worker}.jsonl").open("w") as stream:
                for row in rows:
                    stream.write(f"{json.dumps(row)}\n")
            with (self._directory / f"tests-{self._worker}.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(ROW_FIELDS))
                writer.writeheader()
                writer.writerows(rows)
            (self._directory / f"fixtures-{self._worker}.json").write_text(
                json.dumps(self._fixture_costs(), indent=2) + "\n"
            )
        if not self._is_worker:
            (self._directory / "run-summary.json").write_text(
                json.dumps(self._summary(exitstatus), indent=2) + "\n"
            )

    def _fixture_costs(self) -> dict[str, Any]:
        return {
            "schema": TIMINGS_SCHEMA,
            "worker": self._worker,
            "fixtures": sorted(
                (
                    {
                        "name": name,
                        "scope": scope,
                        "setups": cost.setups,
                        "total_seconds": round(cost.seconds, 6),
                        "slowest_seconds": round(cost.slowest, 6),
                        "errors": cost.errors,
                    }
                    for (name, scope), cost in self._fixtures.items()
                ),
                key=lambda entry: entry["total_seconds"],
                reverse=True,
            ),
        }

    def _summary(self, exitstatus: int) -> dict[str, Any]:
        counts = Counter(attempt.outcome or "passed" for attempt in self._attempts)
        slowest = sorted(self._attempts, key=lambda attempt: attempt.total, reverse=True)
        return {
            "schema": TIMINGS_SCHEMA,
            "sha": os.environ.get("GITHUB_SHA", ""),
            "ref": os.environ.get("GITHUB_REF", ""),
            "ref_name": os.environ.get("GITHUB_REF_NAME", ""),
            "pull_request": _pull_request_number(),
            "workflow": os.environ.get("GITHUB_WORKFLOW", ""),
            "job": os.environ.get("GITHUB_JOB", ""),
            "run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            "shard": self._config.getoption("--shard") or "",
            "runner_os": os.environ.get("RUNNER_OS") or platform.system(),
            "python": platform.python_version(),
            "workers": self._processes or 1,
            "exit_status": exitstatus,
            "session_start": round(self._session_start, 6),
            "session_stop": round(self._session_stop, 6),
            "wall_seconds": round(self._session_stop - self._session_start, 6),
            "tests": len(self._attempts),
            "counts": dict(sorted(counts.items())),
            "slowest": [
                {"nodeid": attempt.nodeid, "total_seconds": round(attempt.total, 6)}
                for attempt in slowest[:SLOWEST_IN_SUMMARY]
            ],
        }

    def _warn(self, message: str) -> None:
        reporter = self._config.pluginmanager.get_plugin("terminalreporter")
        if reporter is None or self._is_worker:
            print(f"ufo-timings: {message}", file=sys.stderr)
            return
        reporter.write_line(f"ufo-timings: {message}", yellow=True)


def pytest_configure(config: pytest.Config) -> None:
    value = str(config.getoption("--timings-dir") or "")
    if not value:
        return
    directory = Path(value)
    if not directory.is_absolute():
        # the invocation directory, not the rootdir: `control` roots its own pytest config, and one
        # directory per checkout is what a CI job can name as a single upload path.
        directory = config.invocation_params.dir / directory
    config.pluginmanager.register(_TimingsRecorder(config, directory), "ufo-timings")
