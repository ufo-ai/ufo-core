import asyncio
import gc
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import threading
import warnings
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager, nullcontext, suppress
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5
from weakref import ref

import aiosqlite
import pytest
import sqlalchemy as sa
import sqlalchemy.ext.asyncio.engine as sqlalchemy_async_engine
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from pydantic import BaseModel
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from ufo_testsupport import migrations
from ufo_testsupport.migrations import TEMPLATE_CACHE_OFF_ENV, apply_cached_migrations
from ufo_testsupport.plugin import drop_postgres_database, reset_postgres_database
from ufo_testsupport.tables import POSTGRES_TABLES, reset_workspace_data

import ufo.db
from ufo.db import (
    MIGRATIONS_DIR,
    STATEMENT_LOG_MAX_CHARS,
    WORKSPACE_GUC,
    _build_engine,
    _opened,
    apply_migrations,
    dispose_db,
    failed_statement,
    init_db,
    init_owner_db,
    owner_tx,
    verify_db_reachable,
    workspace_tx,
)
from ufo.harness import o11y
from ufo.host.ext.loader import migration_locations
from ufo.runtime.sources.sync import feed_handle, feed_handle_for
from ufo.runtime.workspace import ws
from ufo.schema import tables


class _UndefinedColumn(Exception):
    """A driver error carrying the SQLSTATE a Postgres refusal arrives with, standing in for
    `asyncpg.exceptions.UndefinedColumnError`: SQLSTATE reaches a log through the driver exception
    the DBAPI error wraps, and no SQLite refusal carries one."""

    sqlstate = "42703"


@dataclass
class _RefusingEngine:
    """An engine whose `begin()` refuses to open, standing in for a Postgres that never answers the
    connect. The real engine cannot be made to fail this way without a database to take away."""

    error: Exception | None

    @property
    def dialect(self) -> SimpleNamespace:
        return SimpleNamespace(name="postgresql")

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[object]:
        if self.error is not None:
            raise self.error
        yield object()


def test_migrations_are_idempotent(database_url: str) -> None:
    apply_migrations(database_url)
    apply_migrations(database_url)


def _sqlite_shape(database: Path) -> tuple[frozenset[tuple[str, str]], frozenset[str]]:
    with sqlite3.connect(database) as connection:
        schema = frozenset(
            (row[0], row[1] or "")
            for row in connection.execute("select name, sql from sqlite_master")
        )
        heads = frozenset(
            row[0] for row in connection.execute("select version_num from alembic_version")
        )
    return schema, heads


def _refuse_migration(url: str, pack: str | None = None) -> None:
    raise AssertionError(f"a cached template should have served {url}")


def test_a_migrated_sqlite_template_is_cached_across_pytest_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`upgrade heads` over core's chain plus every extension branch costs ~2 s, paid by every
    pytest process and again by every per-test fixture that migrates a database of its own. The
    first call fills the cache and the second is the byte copy a sealed migrated file already
    allows — proved by taking the real migration away before it."""
    monkeypatch.setattr(migrations, "SQLITE_TEMPLATE_DIR", tmp_path / "templates")
    first = tmp_path / "first.db"
    apply_cached_migrations(f"sqlite+aiosqlite:///{first}")
    monkeypatch.setattr(migrations, "apply_migrations", _refuse_migration)

    second = tmp_path / "second.db"
    apply_cached_migrations(f"sqlite+aiosqlite:///{second}")

    assert _sqlite_shape(second) == _sqlite_shape(first)


def test_a_changed_migration_set_misses_the_cached_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The key is a digest of every migration file that would run, so an edited or added migration
    is never served the schema it replaced. Dropping the extension locations stands in for that
    edit: the digest moves, the real migration runs again, and both templates are kept."""
    templates = tmp_path / "templates"
    monkeypatch.setattr(migrations, "SQLITE_TEMPLATE_DIR", templates)
    apply_cached_migrations(f"sqlite+aiosqlite:///{tmp_path / 'first.db'}")
    migrated: list[str] = []
    real = migrations.apply_migrations

    def recording(url: str, pack: str | None = None) -> None:
        migrated.append(url)
        real(url, pack)

    monkeypatch.setattr(migrations, "migration_locations", lambda pack: ())
    monkeypatch.setattr(migrations, "apply_migrations", recording)
    second = tmp_path / "second.db"

    apply_cached_migrations(f"sqlite+aiosqlite:///{second}")

    assert migrated == [f"sqlite+aiosqlite:///{second}"]
    assert len(list(templates.glob("*.db"))) == 2


def test_the_sqlite_template_cache_switches_off_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    templates = tmp_path / "templates"
    monkeypatch.setattr(migrations, "SQLITE_TEMPLATE_DIR", templates)
    monkeypatch.setenv(TEMPLATE_CACHE_OFF_ENV, "1")
    database = tmp_path / "uncached.db"

    apply_cached_migrations(f"sqlite+aiosqlite:///{database}")

    _, heads = _sqlite_shape(database)
    assert heads
    assert not templates.exists()


def test_a_database_that_already_exists_is_migrated_rather_than_copied_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`apply_migrations` over a database already at heads is a no-op — `test_migrations_are_
    idempotent` asserts it — and the cached path stands in for it at every call site, so it has to
    keep that. A byte copy would replace the file instead, taking the rows and whatever a `-wal`
    beside it still holds with it."""
    monkeypatch.setattr(migrations, "SQLITE_TEMPLATE_DIR", tmp_path / "templates")
    database = tmp_path / "seeded.db"
    url = f"sqlite+aiosqlite:///{database}"
    apply_cached_migrations(url)
    seeding = sqlite3.connect(database)
    seeding.execute(
        "insert into workspace (id, created_at, updated_at) "
        "values (:id, '2026-01-01', '2026-01-01')",
        {"id": uuid4().hex},
    )
    seeding.commit()
    seeding.close()

    apply_cached_migrations(url)

    reader = sqlite3.connect(database)
    kept = next(reader.execute("select count(*) from workspace"))[0]
    reader.close()
    assert kept == 1


async def test_reset_wipes_every_application_table_and_keeps_the_stamp(
    db: None, database_url: str
) -> None:
    """`reset_workspace_data`'s own contract, asserted directly: a core row, an extension row, and
    an index chunk all vanish; the alembic stamp survives; and on sqlite the FTS5 virtual table is
    emptied through its own surface, leaving the index writable afterwards.

    Postgres deletes only the tables a test dirtied, and its characteristic failure is a table the
    probe does not account for, so the postgres arm ends by asserting the state the narrowing has
    to leave: every application table in the schema empty, not only the three seeded here."""
    workspace_id = uuid4()
    sqlite = database_url.startswith("sqlite")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.text(
                "insert into memory_item "
                "(id, workspace_id, subject, body, body_digest, item_class, memory_kind, "
                "confidence, created_at, updated_at) values "
                "(:id, :workspace_id, 'shared', 'probe', :digest, 'fact', 'fact', 5, :now, :now)"
            ),
            {
                "id": uuid4().hex,
                "workspace_id": workspace_id.hex,
                "digest": hashlib.sha256(b"probe").hexdigest(),
                "now": datetime.now(UTC),
            },
        )
        if sqlite:
            await connection.execute(
                sa.text(
                    "insert into chunk (chunk_digest, owner_kind, owner_id, subject, ordinal, "
                    "text) values ('sha256:probe', 'memory', :owner, 'shared', 0, 'probe row')"
                ),
                {"owner": uuid4().hex},
            )
            await connection.execute(
                sa.text(
                    "insert into chunk_fts (chunk_digest, text) "
                    "values ('sha256:probe', 'probe row')"
                )
            )
        else:
            await connection.execute(
                sa.text(
                    "insert into chunk (workspace_id, chunk_digest, owner_kind, owner_id, "
                    "subject, ordinal, text) values (:workspace_id, 'sha256:probe', 'memory', "
                    ":owner, 'shared', 0, 'probe row')"
                ),
                {"workspace_id": str(workspace_id), "owner": str(uuid4())},
            )

    async with workspace_tx() as connection:
        await reset_workspace_data(connection)

    async with workspace_tx() as connection:
        for table in ("workspace", "memory_item", "chunk"):
            count = (
                await connection.execute(sa.text(f"select count(*) from {table}"))
            ).scalar_one()
            assert count == 0, table
        stamped = (
            await connection.execute(sa.text("select count(*) from alembic_version"))
        ).scalar_one()
        assert stamped >= 1
        if sqlite:
            fts = (await connection.execute(sa.text("select count(*) from chunk_fts"))).scalar_one()
            assert fts == 0
            await connection.execute(
                sa.text(
                    "insert into chunk_fts (chunk_digest, text) "
                    "values ('sha256:after', 'still writable')"
                )
            )
            matched = (
                await connection.execute(
                    sa.text("select count(*) from chunk_fts where chunk_fts match 'writable'")
                )
            ).scalar_one()
            assert matched == 1

    if not sqlite:
        async with workspace_tx() as connection:
            names = (await connection.execute(POSTGRES_TABLES)).scalars().all()
            probe = " union all ".join(
                f"(select '{name}' as name from \"{name}\" limit 1)" for name in names
            )
            left = (await connection.execute(sa.text(probe))).scalars().all()
        assert list(left) == []


async def test_the_wipe_deletes_the_tables_a_test_dirtied_children_first(
    db: None, database_url: str
) -> None:
    """`truncate table … cascade` over all 41 application tables rewrote 41 relfilenodes per test,
    and its median climbed from 59 ms at the start of a postgres session to 614 ms 900 tests in.
    One probe finds the tables a test actually dirtied and only those are deleted, in the
    child-first order the live foreign-key graph gives — `member` before the `workspace` its
    NO ACTION key points at, which is the order a delete has to take and truncate never needed."""
    if database_url.startswith("sqlite"):
        pytest.skip("deleting what is dirty is the postgres arm; sqlite copies a fresh file")
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=uuid4(),
                workspace_id=workspace_id,
                email="member@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    executed: list[str] = []

    def record(
        connection: sa.Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        executed.append(statement)

    async with workspace_tx() as connection:
        sa.event.listen(connection.sync_connection, "before_cursor_execute", record)
        try:
            await reset_workspace_data(connection)
        finally:
            sa.event.remove(connection.sync_connection, "before_cursor_execute", record)

    assert [line for line in executed if line.startswith("delete from")] == [
        'delete from "member"',
        'delete from "workspace"',
    ]
    async with workspace_tx() as connection:
        for table in ("member", "workspace"):
            count = (
                await connection.execute(sa.text(f"select count(*) from {table}"))
            ).scalar_one()
            assert count == 0, table


async def test_the_wipe_waits_out_a_writer_that_is_still_in_flight(
    db: None, database_url: str
) -> None:
    """`truncate table … cascade` took ACCESS EXCLUSIVE on every table, so a transaction that had
    written and not yet committed blocked the wipe and had its rows removed once it committed. A
    probe and a delete take no lock and see only their own snapshot: without the explicit lock the
    writer here is invisible, its table is judged clean, no statement is spent on it, and its row
    lands in the next test on a database every test on the worker shares. The harness has this
    shape for real — `drain_workflows` exists because a test can finish while its workflow still
    owns a transaction, and `test_cli_e2e` resets while servers run in threads."""
    if database_url.startswith("sqlite"):
        pytest.skip("the wipe is the postgres arm; sqlite copies a fresh file per test")
    written = asyncio.Event()

    async def writer() -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.workspace).values(
                    id=uuid4(), created_at=sa.func.now(), updated_at=sa.func.now()
                )
            )
            written.set()
            await asyncio.sleep(0.5)

    in_flight = asyncio.create_task(writer())
    await written.wait()
    async with workspace_tx() as connection:
        await reset_workspace_data(connection)
    await in_flight

    async with workspace_tx() as connection:
        left = (await connection.execute(sa.text("select count(*) from workspace"))).scalar_one()
    assert left == 0


async def test_a_foreign_key_cycle_leaves_no_delete_order_and_is_refused(
    db: None, database_url: str
) -> None:
    """A cycle has no child-first order, and every order a wipe could pick trips one of its keys.
    The order is derived from the live catalog so a new migration needs no change here — and one
    that closes a cycle names it in the raise instead of failing as an arbitrary delete. The two
    tables exist only inside this transaction, which the raise rolls back."""
    if database_url.startswith("sqlite"):
        pytest.skip("deleting what is dirty is the postgres arm; sqlite copies a fresh file")
    with pytest.raises(RuntimeError, match="foreign key cycle has no delete order: cycle_"):
        async with workspace_tx() as connection:
            await connection.execute(sa.text("create table cycle_head (id uuid primary key)"))
            await connection.execute(
                sa.text(
                    "create table cycle_tail (id uuid primary key, "
                    "head_id uuid references cycle_head (id))"
                )
            )
            await connection.execute(
                sa.text("alter table cycle_head add column tail_id uuid references cycle_tail (id)")
            )
            await reset_workspace_data(connection)


async def test_no_table_carries_a_sequence_the_delete_wipe_would_leave_unreset(
    db: None, database_url: str
) -> None:
    """`delete` leaves a sequence where `truncate` restarted it. Every key in this schema is a
    caller-assigned uuid, so no sequence exists for a wipe to reset — a migration that adds a serial
    or identity column fails here rather than leaking its counter into the next test. The one
    counter the schema does carry is `workspace.page_revision`, which the `assign_page_revision`
    trigger increments; it returns to zero because the workspace row holding it is deleted too."""
    if database_url.startswith("sqlite"):
        pytest.skip("postgres owns the sequences truncate would have restarted")
    async with workspace_tx() as connection:
        sequences = (
            (
                await connection.execute(
                    sa.text("select sequencename from pg_sequences where schemaname = 'public'")
                )
            )
            .scalars()
            .all()
        )
    assert list(sequences) == []


DUPLICATE_PROBE = """\"\"\"duplicate revision probe\"\"\"

revision: str = "probe_dup"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
"""


def _core_migration_head() -> str:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    head = ScriptDirectory.from_config(config).get_current_head()
    assert head is not None
    return head


def _reached(stamped: set[str]) -> set[str]:
    """Every revision a stamped database has run, which is what the stamp names plus everything
    those revisions reach. A revision another head declares a dependency on is not stamped
    separately — alembic records the head that absorbs it — so a chain is read as applied through
    whatever depends on it, never by looking for its own row."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    scripts = ScriptDirectory.from_config(config)
    return {revision.revision for revision in scripts.iterate_revisions(stamped, "base")}


def test_apply_migrations_rejects_duplicate_revision_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two files claiming one revision id are one graph node, so the losing DDL would be skipped."""
    probe = tmp_path / "probe_versions"
    probe.mkdir()
    for name in ("first", "second"):
        (probe / f"{name}.py").write_text(DUPLICATE_PROBE)
    monkeypatch.setattr("ufo.host.ext.loader.migration_locations", lambda pack: (str(probe),))
    with pytest.raises(RuntimeError, match="collapses into one node"):
        apply_migrations(f"sqlite+aiosqlite:///{tmp_path / 'probe.db'}")


FORK_PROBE = """\"\"\"fork probe\"\"\"

revision: str = "{revision}"
down_revision: str | None = {down_revision}
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
"""


def test_apply_migrations_rejects_forked_heads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two revisions chained onto one parent fork the branch: `upgrade heads` would stamp both,
    and once the fork is later linearized every migrate fails on the overlapping stamp rows."""
    probe = tmp_path / "probe_versions"
    probe.mkdir()
    (probe / "base.py").write_text(FORK_PROBE.format(revision="probe_base", down_revision="None"))
    for name in ("probe_a", "probe_b"):
        (probe / f"{name}.py").write_text(
            FORK_PROBE.format(revision=name, down_revision='"probe_base"')
        )
    monkeypatch.setattr("ufo.host.ext.loader.migration_locations", lambda pack: (str(probe),))
    with pytest.raises(RuntimeError, match="forked"):
        apply_migrations(f"sqlite+aiosqlite:///{tmp_path / 'probe.db'}")


def test_extension_migration_forms_one_head_per_owner(database_url: str) -> None:
    """The migration seam's schema invariant: each extension's version location layers over
    core's — `apply_migrations` ran clean in the fixture — and the graph has exactly one head per
    owner (core's chain plus each extension branch), so `upgrade heads` is deterministic,
    core-first.

    Every revision id is unique: two files claiming one id collapse into a single graph node, so a
    deploy already stamped with that id plans nothing and the losing file's DDL is skipped while
    `upgrade heads` still reports success. Alembic detects the collision as a warning, which this
    raises."""
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        scripts = ScriptDirectory.from_config(config)
        heads = scripts.get_heads()
    assert scripts.get_revision("memory_0008").dependencies == "0049"
    assert {
        _core_migration_head(),
        "index_default_0002",
        "objectives_0002",
        "memory_0026",
        "sample_ext_note_0001",
        "scheduled_tasks_0004",
        "sources_0009",
        "monitors_0004",
        "skill_create_0004",
        "coding_0004",
        "eval_env_0001",
        "sites_0008",
        "web_0003",
        "report_digest_0002",
        "enrichment_0002",
        "notification_0008",
        "workspace_credential_slot_0001",
        "lifecycle_email_0004",
    } <= set(heads)
    assert len(heads) == 19


def test_coding_only_pack_migrates_without_sources(tmp_path: Path) -> None:
    database_path = tmp_path / "coding-only.db"
    apply_migrations(
        f"sqlite+aiosqlite:///{database_path}",
        pack="gdpval_documents",
    )
    with sqlite3.connect(database_path) as connection:
        names = {row[0] for row in connection.execute("select name from sqlite_master")}
    assert "source_trigger" not in names
    assert "coding_review_inbox" not in names
    assert "coding_review_run" not in names


@pytest.mark.parametrize("graph_installed", [False, True])
def test_one_memory_surface_advances_both_old_heads(tmp_path: Path, graph_installed: bool) -> None:
    database_path = tmp_path / f"graph-revision-{graph_installed}.db"
    url = f"sqlite+aiosqlite:///{database_path}"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0051")
    if graph_installed:
        command.upgrade(config, "knowledge_graph_0001")

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("select name from sqlite_master where type = 'table'")
        }
    if graph_installed:
        assert {"graph_entity", "graph_edge"} <= tables
    else:
        assert not {"graph_entity", "graph_edge"} & tables

    apply_migrations(url)

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("select name from sqlite_master where type = 'table'")
        }
        revisions = {
            row[0] for row in connection.execute("select version_num from alembic_version")
        }
    assert not {"graph_entity", "graph_edge"} & tables
    assert _core_migration_head() in _reached(revisions)
    assert "knowledge_graph_0001" not in revisions


def test_shared_artifact_id_backfills_every_existing_row(tmp_path: Path) -> None:
    """0061 gives already-shared files their row identity: the column arrives non-null and unique,
    so files a deploy shared before the paging cursor existed page by it afterwards — two files one
    turn shared in the same instant included, which is the tie the cursor cannot break without
    this column."""
    database_path = tmp_path / "artifact-id.db"
    url = f"sqlite+aiosqlite:///{database_path}"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0060")
    workspace_id, agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime(2026, 7, 29, tzinfo=UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "insert into workspace (id, created_at, updated_at) values (?, ?, ?)",
            (workspace_id.hex, now, now),
        )
        connection.execute(
            "insert into agent (id, workspace_id, name, prompt, model, is_main, created_at, "
            "updated_at) values (?, ?, 'assistant', 'p', 'auto', 1, ?, ?)",
            (agent_id.hex, workspace_id.hex, now, now),
        )
        connection.execute(
            "insert into conversation (id, workspace_id, agent_id, surface, queue_key, "
            "created_at, updated_at) values (?, ?, ?, 'web', 'a/b', ?, ?)",
            (conversation_id.hex, workspace_id.hex, agent_id.hex, now, now),
        )
        connection.execute(
            "insert into turn (id, workspace_id, conversation_id, agent_id, seq, status, "
            "inbound, created_at, updated_at) values (?, ?, ?, ?, 1, 'queued', '{}', ?, ?)",
            (turn_id.hex, workspace_id.hex, conversation_id.hex, agent_id.hex, now, now),
        )
        for name in ("chart.png", "notes.txt"):
            connection.execute(
                "insert into shared_artifact (turn_id, blob_key, workspace_id, filename, "
                "media_type, size_bytes, created_at, updated_at) "
                "values (?, ?, ?, ?, 'text/plain', 3, ?, ?)",
                (turn_id.hex, f"artifacts/x/{name}", workspace_id.hex, name, now, now),
            )
    command.upgrade(config, "0061")
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute("select filename, id from shared_artifact").fetchall()
    identities = {name: value for name, value in rows}
    assert set(identities) == {"chart.png", "notes.txt"}
    assert all(value for value in identities.values())
    assert len(set(identities.values())) == 2


def test_intent_admission_downgrade_rewrites_to_internal(tmp_path: Path) -> None:
    """0060's downgrade parks intent turns on the inert source: 'internal' matches no member-turn
    projection and no seat gate, so a machine envelope never renders as a member's message."""
    database_path = tmp_path / "intent-downgrade.db"
    url = f"sqlite+aiosqlite:///{database_path}"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0060")
    workspace_id, agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4(), uuid4()
    now = datetime(2026, 7, 29, tzinfo=UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "insert into workspace (id, created_at, updated_at) values (?, ?, ?)",
            (workspace_id.hex, now, now),
        )
        connection.execute(
            "insert into agent (id, workspace_id, name, prompt, model, is_main, created_at, "
            "updated_at) values (?, ?, 'assistant', 'p', 'auto', 1, ?, ?)",
            (agent_id.hex, workspace_id.hex, now, now),
        )
        connection.execute(
            "insert into conversation (id, workspace_id, agent_id, surface, queue_key, "
            "created_at, updated_at) values (?, ?, ?, 'web', 'intent/a/b', ?, ?)",
            (conversation_id.hex, workspace_id.hex, agent_id.hex, now, now),
        )
        connection.execute(
            "insert into turn (id, workspace_id, conversation_id, agent_id, seq, status, "
            "inbound, admission_source, created_at, updated_at) "
            "values (?, ?, ?, ?, 1, 'queued', '{}', 'intent', ?, ?)",
            (turn_id.hex, workspace_id.hex, conversation_id.hex, agent_id.hex, now, now),
        )
    command.downgrade(config, "0058")
    with sqlite3.connect(database_path) as connection:
        source = connection.execute(
            "select admission_source from turn where id = ?", (turn_id.hex,)
        ).fetchone()[0]
        refused = False
        try:
            connection.execute(
                "insert into turn (id, workspace_id, conversation_id, agent_id, seq, status, "
                "inbound, admission_source, created_at, updated_at) "
                "values (?, ?, ?, ?, 2, 'queued', '{}', 'intent', ?, ?)",
                (uuid4().hex, workspace_id.hex, conversation_id.hex, agent_id.hex, now, now),
            )
        except sqlite3.IntegrityError:
            refused = True
    assert source == "internal"
    assert refused


def test_source_trigger_migration_carries_every_live_subscription(tmp_path: Path) -> None:
    """The sources extension's own migration moves its subscriber maps into rows of its own. Every
    entry has to arrive: the map is deleted in the same migration and nothing else could ever
    restore it, so a conversation being woken before the upgrade is still woken after it. A
    conversation that has since gone takes its entry with it rather than stranding a row no foreign
    key would accept, and a member's own conversation names that member so the trigger stays
    visible to the one person who could have subscribed it.

    Carried across means across the whole chain, so this runs to the sources head rather than to the
    revision that writes the rows. `sources_0004` deletes a trigger whose binding names no
    connection, which is the one thing that can lose a subscription this migration promised to keep,
    and a run stopping short of it would assert the safe half and stay silent on that. The binding
    is derived from the connection the subscription authenticated as rather than written as a
    literal, because a literal names no connection and would be deleted for the right reason."""
    database_path = tmp_path / "source-trigger.db"
    url = f"sqlite+aiosqlite:///{database_path}"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "heads")
    workspace_id, member_id = uuid4(), uuid4()
    agent_id, other_agent_id = uuid4(), uuid4()
    shared_conversation, private_conversation, gone = uuid4(), uuid4(), uuid4()
    connection_id, account_id = uuid4(), "acct-7"
    digest = hashlib.sha256(
        json.dumps(
            {"account": account_id, "base_url": None, "provider": "asana"}, sort_keys=True
        ).encode()
    ).hexdigest()[:8]
    binding = f"asana-{digest}"
    now = datetime(2026, 8, 14, tzinfo=UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute("delete from source_trigger")
        connection.execute(
            "insert into workspace (id, created_at, updated_at) values (?, ?, ?)",
            (workspace_id.hex, now, now),
        )
        connection.execute(
            "insert into member (id, workspace_id, email, created_at, updated_at) "
            "values (?, ?, 'who@example.com', ?, ?)",
            (member_id.hex, workspace_id.hex, now, now),
        )
        for identity, name in ((agent_id, "assistant"), (other_agent_id, "scout")):
            connection.execute(
                "insert into agent (id, workspace_id, name, prompt, model, is_main, created_at, "
                "updated_at) values (?, ?, ?, 'p', 'auto', 0, ?, ?)",
                (identity.hex, workspace_id.hex, name, now, now),
            )
        connection.execute(
            "insert into conversation (id, workspace_id, agent_id, surface, queue_key, audience, "
            "created_at, updated_at) values (?, ?, ?, 'web', 'a/b', 'shared', ?, ?)",
            (shared_conversation.hex, workspace_id.hex, agent_id.hex, now, now),
        )
        connection.execute(
            "insert into conversation (id, workspace_id, agent_id, surface, queue_key, member_id, "
            "audience, created_at, updated_at) values (?, ?, ?, 'cli', 'c/d', ?, ?, ?, ?)",
            (
                private_conversation.hex,
                workspace_id.hex,
                other_agent_id.hex,
                member_id.hex,
                f"member:{member_id}",
                now,
                now,
            ),
        )
        connection.execute(
            "insert into connection (id, workspace_id, provider, account_id, host, "
            "owner_member_id, shared, created_at, updated_at) "
            "values (?, ?, 'asana', ?, '', ?, 0, ?, ?)",
            (connection_id.hex, workspace_id.hex, account_id, member_id.hex, now, now),
        )
        for key, value in (
            (
                f"subscribers:{binding}",
                json.dumps(
                    {
                        shared_conversation.hex: agent_id.hex,
                        private_conversation.hex: other_agent_id.hex,
                        gone.hex: agent_id.hex,
                    }
                ),
            ),
            (f"cursor:{binding}", json.dumps({"seq": 7})),
        ):
            connection.execute(
                "insert into ext_store (workspace_id, extension, key, value, created_at, "
                "updated_at) values (?, 'sources', ?, ?, ?, ?)",
                (workspace_id.hex, key, value, now, now),
            )
    command.downgrade(config, "sources@base")
    command.upgrade(config, "sources@head")
    with sqlite3.connect(database_path) as connection:
        carried = connection.execute(
            "select conversation_id, agent_id, connection_id, resource, delivery, "
            "created_by_member_id from source_trigger"
        ).fetchall()
        left = connection.execute(
            "select key from ext_store where extension = 'sources'"
        ).fetchall()
    assert sorted(carried) == sorted(
        [
            (shared_conversation.hex, agent_id.hex, connection_id.hex, "", "current", None),
            (
                private_conversation.hex,
                other_agent_id.hex,
                connection_id.hex,
                "",
                "current",
                member_id.hex,
            ),
        ]
    )
    assert [key for (key,) in left] == [f"cursor:{binding}"]


def test_a_feed_naming_no_account_takes_the_handle_core_derives(tmp_path: Path) -> None:
    """A feed that authenticates as no broker account — a repository, a folder root — takes its
    config's identity JSON as its connection's `account_id`, and the revision that mints that
    connection spells the handle inline while `register_sources` and an extension's object kind
    spell it through `feed_handle`. Two copies of one rule drift without a word of warning: they
    would simply mint different handles, the boot path would look for a connection it never finds,
    and it would create a second one beside every feed this migration moved. So the handles are
    compared here byte for byte.

    Each feed also has to land on a connection of its own. One handle shared across two roots would
    make deleting either cascade the other's sources and pages away, which is the loss the identity
    handle exists to prevent."""

    class GitOrigin(BaseModel):
        repo: str
        branch: str | None = None

    class FolderOrigin(BaseModel):
        root: str

    origins = {
        "gbrain_git": GitOrigin(repo="octo/wiki"),
        "gbrain_folder": FolderOrigin(root="/srv/notes"),
    }
    database_path = tmp_path / "feed-handle.db"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    command.upgrade(config, "20260906225812")
    command.upgrade(config, "sources_0003")
    workspace_id, member_id = uuid4(), uuid4()
    now = datetime(2026, 9, 7, tzinfo=UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "insert into workspace (id, created_at, updated_at) values (?, ?, ?)",
            (workspace_id.hex, now, now),
        )
        connection.execute(
            "insert into member (id, workspace_id, email, created_at, updated_at) "
            "values (?, ?, 'who@example.com', ?, ?)",
            (member_id.hex, workspace_id.hex, now, now),
        )
        for backend, origin in origins.items():
            payload = origin.model_dump(mode="json")
            connection.execute(
                "insert into source (id, workspace_id, backend, config, subject, owner_member_id, "
                "next_sync_at, created_at, updated_at) "
                "values (?, ?, ?, ?, 'shared', ?, ?, ?, ?)",
                (
                    uuid4().hex,
                    workspace_id.hex,
                    backend,
                    json.dumps(payload),
                    member_id.hex,
                    now,
                    now,
                    now,
                ),
            )
    command.upgrade(config, "heads")
    with sqlite3.connect(database_path) as connection:
        minted = dict(
            connection.execute(
                "select s.backend, c.account_id from source s "
                "join connection c on c.id = s.connection_id"
            ).fetchall()
        )
    assert minted == {backend: feed_handle(origin) for backend, origin in origins.items()}
    assert len(set(minted.values())) == len(origins)


def test_a_stream_row_naming_no_account_refuses_the_upgrade(tmp_path: Path) -> None:
    """A row whose config names a stream is connector-backed, so it authenticates as an account and
    a row storing none is dead data — the image being replaced cannot load it either, since
    `ConnectorSourceConfig` requires the account. Minting it a connection would invent an authority
    for it: the identity-JSON handle a repository or folder root takes, ownerless and shared under a
    connector provider, which the broker would then be handed as an account id. The upgrade refuses
    instead, naming the row so it can be repaired or removed.

    The pair is the point. The same row with an account migrates, so the refusal is the missing
    account and not the presence of a stream."""
    for account, expected in (
        ({}, pytest.raises(RuntimeError, match="names a stream and no account")),
        ({"account": "acct-3"}, nullcontext()),
    ):
        database_path = tmp_path / f"stream-no-account-{len(account)}.db"
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_DIR))
        config.set_main_option(
            "version_locations",
            os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
        )
        config.set_main_option("path_separator", "os")
        config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
        command.upgrade(config, "20260906225812")
        command.upgrade(config, "sources_0003")
        workspace_id, member_id = uuid4(), uuid4()
        now = datetime(2026, 9, 7, tzinfo=UTC).isoformat()
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                "insert into workspace (id, created_at, updated_at) values (?, ?, ?)",
                (workspace_id.hex, now, now),
            )
            connection.execute(
                "insert into member (id, workspace_id, email, created_at, updated_at) "
                "values (?, ?, 'who@example.com', ?, ?)",
                (member_id.hex, workspace_id.hex, now, now),
            )
            connection.execute(
                "insert into source (id, workspace_id, backend, config, subject, owner_member_id, "
                "connection_id, next_sync_at, created_at, updated_at) "
                "values (?, ?, 'github', ?, 'shared', ?, null, ?, ?, ?)",
                (
                    uuid4().hex,
                    workspace_id.hex,
                    json.dumps({"stream": "issues", **account}),
                    member_id.hex,
                    now,
                    now,
                    now,
                ),
            )
        with expected:
            command.upgrade(config, "heads")


def test_the_trigger_tables_merge_onto_one_keyed_by_resource(tmp_path: Path) -> None:
    """A trigger narrowed to one resource of a source is a `source_trigger` row carrying that
    resource, so the resource is part of the table's unique key and `source_resource_watch` is
    gone. The wide key is deliberate: it is what lets one conversation hold the whole binding and
    each resource of it as rows of one table, and narrowing it back to (workspace_id,
    conversation_id, binding) would make the second of those rows unwritable.

    Every narrowed row crosses over keeping its own id — the generation an object edit checks
    itself against. Both rows seeded here land under one conversation and one binding, which the
    narrow key could not hold. The next revision makes every row current and gives the retained
    column a default for the outgoing image."""
    database_path = tmp_path / "trigger-resource.db"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    command.upgrade(config, "heads")
    command.downgrade(config, "sources_0003")
    workspace_id, member_id, agent_id, conversation_id = (uuid4() for _ in range(4))
    whole_id, narrowed_id, connection_id = uuid4(), uuid4(), uuid4()
    binding = "github-82cba16e"  # what sources_0004 maps onto this connection
    resource = "https://github.com/metalcraftai/ufo/pull/1684"
    now = datetime(2026, 9, 7, tzinfo=UTC).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "insert into workspace (id, created_at, updated_at) values (?, ?, ?)",
            (workspace_id.hex, now, now),
        )
        connection.execute(
            "insert into member (id, workspace_id, email, created_at, updated_at) "
            "values (?, ?, 'who@example.com', ?, ?)",
            (member_id.hex, workspace_id.hex, now, now),
        )
        connection.execute(
            "insert into agent (id, workspace_id, name, prompt, model, is_main, created_at, "
            "updated_at) values (?, ?, 'assistant', 'p', 'auto', 1, ?, ?)",
            (agent_id.hex, workspace_id.hex, now, now),
        )
        connection.execute(
            "insert into conversation (id, workspace_id, agent_id, surface, queue_key, audience, "
            "created_at, updated_at) values (?, ?, ?, 'cli', 'a/b', 'shared', ?, ?)",
            (conversation_id.hex, workspace_id.hex, agent_id.hex, now, now),
        )
        connection.execute(
            "insert into connection (id, workspace_id, provider, account_id, host, "
            "owner_member_id, shared, created_at, updated_at) "
            "values (?, ?, 'github', 'metalcraftai', 'github.com', ?, 1, ?, ?)",
            (connection_id.hex, workspace_id.hex, member_id.hex, now, now),
        )
        connection.execute(
            "insert into source_trigger (id, workspace_id, conversation_id, agent_id, binding, "
            "delivery, created_by_member_id, created_at, updated_at) "
            "values (?, ?, ?, ?, ?, 'current', ?, ?, ?)",
            (
                whole_id.hex,
                workspace_id.hex,
                conversation_id.hex,
                agent_id.hex,
                binding,
                member_id.hex,
                now,
                now,
            ),
        )
        connection.execute(
            "insert into source_resource_watch (id, workspace_id, conversation_id, agent_id, "
            "binding, resource, delivery, created_by_member_id, created_at, updated_at) "
            "values (?, ?, ?, ?, ?, ?, 'per_page', ?, ?, ?)",
            (
                narrowed_id.hex,
                workspace_id.hex,
                conversation_id.hex,
                agent_id.hex,
                binding,
                resource,
                member_id.hex,
                now,
                now,
            ),
        )
    command.upgrade(config, "sources_0004")
    engine = sa.create_engine("sqlite:///" + str(database_path))
    try:
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            trigger_keys = {
                tuple(unique["column_names"])
                for unique in inspector.get_unique_constraints("source_trigger")
            }
            table_names = set(inspector.get_table_names())
    finally:
        engine.dispose()
    with sqlite3.connect(database_path) as connection:
        carried = connection.execute(
            "select id, conversation_id, connection_id, resource, delivery, created_by_member_id "
            "from source_trigger order by resource"
        ).fetchall()
    assert trigger_keys == {("workspace_id", "conversation_id", "connection_id", "resource")}
    assert "source_resource_watch" not in table_names
    assert carried == [
        (whole_id.hex, conversation_id.hex, connection_id.hex, "", "current", member_id.hex),
        (
            narrowed_id.hex,
            conversation_id.hex,
            connection_id.hex,
            resource,
            "per_page",
            member_id.hex,
        ),
    ]
    command.upgrade(config, "sources_0006")
    defaulted_id = uuid4()
    with sqlite3.connect(database_path) as connection:
        normalized = connection.execute(
            "select id, delivery from source_trigger order by id"
        ).fetchall()
        connection.execute(
            "insert into source_trigger (id, workspace_id, conversation_id, agent_id, "
            "connection_id, resource, created_by_member_id, created_at, updated_at) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                defaulted_id.hex,
                workspace_id.hex,
                conversation_id.hex,
                agent_id.hex,
                connection_id.hex,
                "https://github.com/metalcraftai/ufo/pull/1685",
                member_id.hex,
                now,
                now,
            ),
        )
        defaulted = connection.execute(
            "select delivery from source_trigger where id = ?", (defaulted_id.hex,)
        ).fetchone()
    assert {delivery for _, delivery in normalized} == {"current"}
    assert defaulted == ("current",)
    command.upgrade(config, "sources_0007")
    running_id = uuid4()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "insert into source_trigger (id, workspace_id, conversation_id, agent_id, "
            "connection_id, resource, created_by_member_id, created_at, updated_at) "
            "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                running_id.hex,
                workspace_id.hex,
                conversation_id.hex,
                agent_id.hex,
                connection_id.hex,
                "https://github.com/metalcraftai/ufo/pull/3509",
                member_id.hex,
                now,
                now,
            ),
        )
        paused = connection.execute("select paused from source_trigger").fetchall()
    assert {state for (state,) in paused} == {0}
    command.upgrade(config, "sources_0008")
    with sqlite3.connect(database_path) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "insert into source_trigger (id, workspace_id, conversation_id, agent_id, "
                "connection_id, resource, created_by_member_id, created_at, updated_at) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid4().hex,
                    workspace_id.hex,
                    conversation_id.hex,
                    agent_id.hex,
                    connection_id.hex,
                    "https://github.com/metalcraftai/ufo/pull/3592",
                    member_id.hex,
                    now,
                    now,
                ),
            )


SOURCE_AUTHORITY_REVISION = "20260907150257"
SOURCE_AUTHORITY_DOWN_REVISION = "20260906225812"
CONNECTOR_NON_IDENTITY_KEYS = frozenset({"backfill_days", "backfill_after"})


def _source_row_id(
    workspace_id: UUID,
    backend: str,
    config: Mapping[str, object],
    *,
    connection_id: UUID,
    non_identity_keys: frozenset[str] = frozenset(),
) -> UUID:
    handle = feed_handle_for(config, non_identity_keys)
    return uuid5(
        NAMESPACE_URL, f"{workspace_id}/source/{backend}/{handle}/connection/{connection_id}"
    )


SOURCE_WITH_ITS_OWN_AUTHORITY = sa.table(
    "source",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("backend", sa.Text()),
    sa.column("config", sa.JSON()),
    sa.column("subject", sa.Text()),
    sa.column("owner_member_id", sa.Uuid()),
    sa.column("connection_id", sa.Uuid()),
    sa.column("next_sync_at", sa.DateTime(timezone=True)),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)

CONNECTION_WITH_CONVERSATION = sa.table(
    "connection",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("provider", sa.Text()),
    sa.column("account_id", sa.Text()),
    sa.column("host", sa.Text()),
    sa.column("owner_member_id", sa.Uuid()),
    sa.column("conversation_id", sa.Uuid()),
    sa.column("shared", sa.Boolean()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


@contextmanager
def _own_database(database_url: str, tmp_path: Path) -> Iterator[str]:
    """A migrated database of the fixture's dialect that one test may downgrade and reseed without
    touching the session's: sqlite a file of its own, postgres a database of its own, dropped on the
    way out."""
    if database_url.startswith("sqlite"):
        url = f"sqlite+aiosqlite:///{tmp_path / 'own.db'}"
        apply_migrations(url)
        yield url
        return
    base = make_url(database_url)
    name = f"{base.database}_{uuid4().hex[:8]}"
    asyncio.run(reset_postgres_database(name))
    url = base.set(database=name).render_as_string(hide_password=False)
    try:
        apply_migrations(url)
        yield url
    finally:
        asyncio.run(drop_postgres_database(name))


def _alembic(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _execute(url: str, *statements: sa.Executable) -> list[sa.RowMapping]:
    """Run the statements in one transaction on the engine `serve` would build for the url — foreign
    keys enforced by its connect hook — answering the rows of the last one where it returns any."""

    async def run() -> list[sa.RowMapping]:
        engine = _build_engine(url, ufo.db._APP)
        try:
            async with engine.connect() as connection:
                result = None
                for statement in statements:
                    result = await connection.execute(statement)
                rows = (
                    []
                    if result is None or not result.returns_rows
                    else list(result.mappings().all())
                )
                await connection.commit()
                return rows
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _workspace_rows(
    workspace_id: UUID, member_id: UUID, agent_id: UUID, conversation_id: UUID, now: datetime
) -> tuple[sa.Executable, ...]:
    return (
        sa.insert(tables.workspace).values(id=workspace_id, created_at=now, updated_at=now),
        sa.insert(tables.member).values(
            id=member_id,
            workspace_id=workspace_id,
            email="who@example.com",
            created_at=now,
            updated_at=now,
        ),
        sa.insert(tables.agent).values(
            id=agent_id,
            workspace_id=workspace_id,
            name="assistant",
            prompt="p",
            model="auto",
            is_main=True,
            created_at=now,
            updated_at=now,
        ),
        sa.insert(tables.conversation).values(
            id=conversation_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            surface="cli",
            queue_key="a/b",
            audience="shared",
            created_at=now,
            updated_at=now,
        ),
    )


PAGE_BEFORE_UID = sa.table(
    "page",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("source_id", sa.Uuid()),
    sa.column("digest", sa.Text()),
    sa.column("body_ref", sa.Text()),
    sa.column("stream", sa.Text()),
    sa.column("title", sa.Text()),
    sa.column("subject", sa.Text()),
    sa.column("tombstone", sa.Boolean()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def _page_row(
    page_id: UUID, workspace_id: UUID, source_id: UUID, subject: str, now: datetime
) -> sa.Executable:
    return sa.insert(PAGE_BEFORE_UID).values(
        id=page_id,
        workspace_id=workspace_id,
        source_id=source_id,
        digest="sha256:0",
        body_ref=f"source/{source_id}/{page_id}/0",
        stream="issues",
        title="t",
        subject=subject,
        tombstone=False,
        created_at=now,
        updated_at=now,
    )


def test_source_authority_unshares_a_connection_carrying_private_streams(
    database_url: str, tmp_path: Path
) -> None:
    """A connection's streams read what it discloses once `source.subject` is gone, so a connection
    a member shared while its streams stayed private — every connected account, since the registrar
    stamped each stream `member:<owner>` and sharing never restamped — would disclose those streams
    to the workspace on the first sync after the roll. The revision unshares it instead, leaves a
    connection with no private stream alone, and keeps every page's subject. The downgrade returns
    every subject and id exactly and, unable to tell this connection from one never shared, leaves
    it unshared."""
    workspace_id, member_id, agent_id, conversation_id = (uuid4() for _ in range(4))
    narrowed, idle, open_, page_id = (uuid4() for _ in range(4))
    private = f"member:{member_id}"
    now = datetime(2026, 9, 7, tzinfo=UTC)
    stored = {
        stream: {
            "account": account,
            "stream": stream,
            "base_url": None,
            "backfill_days": 30,
            "backfill_after": "2026-08-08T00:00:00+00:00",
        }
        for stream, account in (
            ("issues", "metalcraftai"),
            ("pulls", "metalcraftai"),
            ("notes", "open-org"),
        )
    }
    before = {
        stream: _source_row_id(
            workspace_id,
            "github",
            stored[stream],
            connection_id=connection_id,
            non_identity_keys=CONNECTOR_NON_IDENTITY_KEYS,
        )
        for stream, connection_id in (("issues", narrowed), ("pulls", narrowed), ("notes", open_))
    }
    after = {
        stream: _source_row_id(
            workspace_id,
            "github",
            {
                key: value
                for key, value in stored[stream].items()
                if key not in ("account", "base_url")
            },
            connection_id=connection_id,
            non_identity_keys=CONNECTOR_NON_IDENTITY_KEYS,
        )
        for stream, connection_id in (("issues", narrowed), ("pulls", narrowed), ("notes", open_))
    }
    with _own_database(database_url, tmp_path) as url:
        config = _alembic(url)
        command.downgrade(config, SOURCE_AUTHORITY_DOWN_REVISION)
        _execute(
            url,
            *_workspace_rows(workspace_id, member_id, agent_id, conversation_id, now),
            *(
                sa.insert(CONNECTION_WITH_CONVERSATION).values(
                    id=connection_id,
                    workspace_id=workspace_id,
                    provider="github",
                    account_id=account,
                    host="github.com",
                    owner_member_id=member_id,
                    conversation_id=conversation_id,
                    shared=True,
                    created_at=now,
                    updated_at=now,
                )
                for connection_id, account in (
                    (narrowed, "metalcraftai"),
                    (idle, "idle-org"),
                    (open_, "open-org"),
                )
            ),
            *(
                sa.insert(SOURCE_WITH_ITS_OWN_AUTHORITY).values(
                    id=before[stream],
                    workspace_id=workspace_id,
                    backend="github",
                    config=stored[stream],
                    subject=subject,
                    owner_member_id=member_id,
                    connection_id=connection_id,
                    next_sync_at=now,
                    created_at=now,
                    updated_at=now,
                )
                for stream, subject, connection_id in (
                    ("issues", private, narrowed),
                    ("pulls", private, narrowed),
                    ("notes", "shared", open_),
                )
            ),
            _page_row(page_id, workspace_id, before["issues"], private, now),
        )
        command.upgrade(config, SOURCE_AUTHORITY_REVISION)
        connections = {
            row["id"]: (row["shared"], row["owner_member_id"])
            for row in _execute(
                url,
                sa.select(
                    tables.connection.c.id,
                    tables.connection.c.shared,
                    tables.connection.c.owner_member_id,
                ),
            )
        }
        streams = {
            row["id"]: row["connection_id"]
            for row in _execute(
                url,
                sa.select(
                    SOURCE_WITH_ITS_OWN_AUTHORITY.c.id,
                    SOURCE_WITH_ITS_OWN_AUTHORITY.c.connection_id,
                ),
            )
        }
        pages = [
            (row["source_id"], row["subject"])
            for row in _execute(
                url, sa.select(PAGE_BEFORE_UID.c.source_id, PAGE_BEFORE_UID.c.subject)
            )
        ]
        command.downgrade(config, SOURCE_AUTHORITY_DOWN_REVISION)
        restored = {
            row["id"]: (row["subject"], row["connection_id"])
            for row in _execute(
                url,
                sa.select(
                    SOURCE_WITH_ITS_OWN_AUTHORITY.c.id,
                    SOURCE_WITH_ITS_OWN_AUTHORITY.c.subject,
                    SOURCE_WITH_ITS_OWN_AUTHORITY.c.connection_id,
                ),
            )
        }
        sharing = {
            row["id"]: row["shared"]
            for row in _execute(
                url,
                sa.select(CONNECTION_WITH_CONVERSATION.c.id, CONNECTION_WITH_CONVERSATION.c.shared),
            )
        }
    assert connections == {
        narrowed: (False, member_id),
        idle: (True, member_id),
        open_: (True, member_id),
    }
    assert streams == {after["issues"]: narrowed, after["pulls"]: narrowed, after["notes"]: open_}
    assert pages == [(after["issues"], private)]
    assert restored == {
        before["issues"]: (private, narrowed),
        before["pulls"]: (private, narrowed),
        before["notes"]: ("shared", open_),
    }
    assert sharing == {narrowed: False, idle: True, open_: True}


def test_source_authority_mints_one_connection_per_repository(
    database_url: str, tmp_path: Path
) -> None:
    """A feed naming no account and no member takes its config's identity JSON as its connection's
    account handle, so two repositories of one backend are two connections: deleting one cascades
    to its own stream and pages and leaves the other's standing. The downgrade returns the stream
    that remains to the direct path under its old id and deletes the connection minted for it."""
    workspace_id, member_id, agent_id, conversation_id = (uuid4() for _ in range(4))
    ufo_page, docs_page = uuid4(), uuid4()
    now = datetime(2026, 9, 7, tzinfo=UTC)
    repos = {
        "ufo": {"repo": "metalcraftai/ufo", "branch": None},
        "docs": {"repo": "metalcraftai/docs", "branch": "main"},
    }
    before = {
        name: uuid5(
            NAMESPACE_URL,
            f"{workspace_id}/source/gbrain_git/{json.dumps(config, sort_keys=True)}",
        )
        for name, config in repos.items()
    }
    with _own_database(database_url, tmp_path) as url:
        config = _alembic(url)
        command.downgrade(config, SOURCE_AUTHORITY_DOWN_REVISION)
        _execute(
            url,
            *_workspace_rows(workspace_id, member_id, agent_id, conversation_id, now),
            *(
                sa.insert(SOURCE_WITH_ITS_OWN_AUTHORITY).values(
                    id=before[name],
                    workspace_id=workspace_id,
                    backend="gbrain_git",
                    config=repos[name],
                    subject="shared",
                    owner_member_id=None,
                    connection_id=None,
                    next_sync_at=now,
                    created_at=now,
                    updated_at=now,
                )
                for name in repos
            ),
            _page_row(ufo_page, workspace_id, before["ufo"], "shared", now),
            _page_row(docs_page, workspace_id, before["docs"], "shared", now),
        )
        command.upgrade(config, SOURCE_AUTHORITY_REVISION)
        minted = {
            row["account_id"]: (row["id"], row["owner_member_id"], row["shared"])
            for row in _execute(
                url,
                sa.select(
                    tables.connection.c.account_id,
                    tables.connection.c.id,
                    tables.connection.c.owner_member_id,
                    tables.connection.c.shared,
                ).where(tables.connection.c.provider == "gbrain_git"),
            )
        }
        handles = {name: json.dumps(repos[name], sort_keys=True) for name in repos}
        assert set(minted) == set(handles.values())
        connection_of = {name: minted[handles[name]][0] for name in repos}
        after = {
            name: _source_row_id(
                workspace_id, "gbrain_git", repos[name], connection_id=connection_of[name]
            )
            for name in repos
        }
        streams = {
            row["id"]: row["connection_id"]
            for row in _execute(
                url,
                sa.select(
                    SOURCE_WITH_ITS_OWN_AUTHORITY.c.id,
                    SOURCE_WITH_ITS_OWN_AUTHORITY.c.connection_id,
                ),
            )
        }
        _execute(
            url,
            sa.delete(tables.connection).where(tables.connection.c.id == connection_of["ufo"]),
        )
        left_sources = [
            row["id"] for row in _execute(url, sa.select(SOURCE_WITH_ITS_OWN_AUTHORITY.c.id))
        ]
        left_pages = [row["id"] for row in _execute(url, sa.select(PAGE_BEFORE_UID.c.id))]
        command.downgrade(config, SOURCE_AUTHORITY_DOWN_REVISION)
        restored = {
            row["id"]: (row["subject"], row["connection_id"], row["owner_member_id"])
            for row in _execute(
                url,
                sa.select(
                    SOURCE_WITH_ITS_OWN_AUTHORITY.c.id,
                    SOURCE_WITH_ITS_OWN_AUTHORITY.c.subject,
                    SOURCE_WITH_ITS_OWN_AUTHORITY.c.connection_id,
                    SOURCE_WITH_ITS_OWN_AUTHORITY.c.owner_member_id,
                ),
            )
        }
        left_connections = _execute(
            url,
            sa.select(CONNECTION_WITH_CONVERSATION.c.id).where(
                CONNECTION_WITH_CONVERSATION.c.provider == "gbrain_git"
            ),
        )
    assert {handle: held[1:] for handle, held in minted.items()} == {
        handle: (None, True) for handle in handles.values()
    }
    assert streams == {after["ufo"]: connection_of["ufo"], after["docs"]: connection_of["docs"]}
    assert left_sources == [after["docs"]]
    assert left_pages == [docs_page]
    assert restored == {before["docs"]: ("shared", None, None)}
    assert left_connections == []


def test_memory_as_of_migration_repairs_page_derived_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "memory-as-of.db"
    url = f"sqlite+aiosqlite:///{database_path}"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0048")
    command.upgrade(config, "memory_0006")
    command.upgrade(config, "memory_0007")

    engine = sa.create_engine(f"sqlite:///{database_path}")
    workspace_id, source_id, page_id, memory_id, manual_id = (uuid4() for _ in range(5))
    ingested = datetime(2026, 7, 24, tzinfo=UTC)
    source_as_of = datetime(2025, 7, 24, tzinfo=UTC)
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) "
                "values (:id, :ingested, :ingested)"
            ),
            {"id": workspace_id.hex, "ingested": ingested},
        )
        connection.execute(
            sa.text(
                "insert into source "
                "(id, workspace_id, backend, config, next_sync_at, created_at, updated_at) "
                "values (:id, :workspace_id, 'folder', '{}', :ingested, :ingested, :ingested)"
            ),
            {"id": source_id.hex, "workspace_id": workspace_id.hex, "ingested": ingested},
        )
        connection.execute(
            sa.text(
                "insert into page "
                "(id, workspace_id, source_id, digest, body_ref, subject, tombstone, "
                "stream, title, source_created_at, source_updated_at, "
                "created_at, updated_at) values "
                "(:id, :workspace_id, :source_id, 'sha256:page', 'pages/page', 'shared', false, "
                "'issues', 'Old issue', :source_as_of, null, :ingested, :ingested)"
            ),
            {
                "id": page_id.hex,
                "workspace_id": workspace_id.hex,
                "source_id": source_id.hex,
                "source_as_of": source_as_of.isoformat(),
                "ingested": ingested,
            },
        )
        for item_id, source_ref in (
            (memory_id, str(page_id)),
            (manual_id, "member-authored"),
        ):
            connection.execute(
                sa.text(
                    "insert into memory_item "
                    "(id, workspace_id, subject, body, item_class, memory_kind, confidence, "
                    "source_ref, created_at, updated_at) values "
                    "(:id, :workspace_id, 'shared', 'fact', 'fact', 'fact', 5, "
                    ":source_ref, :ingested, :ingested)"
                ),
                {
                    "id": item_id.hex,
                    "workspace_id": workspace_id.hex,
                    "source_ref": source_ref,
                    "ingested": ingested,
                },
            )
        connection.commit()

    command.upgrade(config, "0049")
    command.upgrade(config, "memory_0008")
    with engine.connect() as connection:
        repaired = connection.execute(
            sa.text("select as_of from memory_item where id = :id"), {"id": memory_id.hex}
        ).scalar_one()
        untouched = connection.execute(
            sa.text("select as_of from memory_item where id = :id"), {"id": manual_id.hex}
        ).scalar_one()
    engine.dispose()

    assert datetime.fromisoformat(repaired).replace(tzinfo=UTC) == source_as_of
    assert untouched is None


def test_memory_provenance_migration_backfills_page_derived_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "memory-provenance.db"
    url = f"sqlite+aiosqlite:///{database_path}"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0050")
    command.upgrade(config, "memory_0008")

    engine = sa.create_engine(f"sqlite:///{database_path}")
    workspace_id, source_id, page_id = (uuid4() for _ in range(3))
    derived_id, manual_id, dangling_id = (uuid4() for _ in range(3))
    ingested = datetime(2026, 7, 24, tzinfo=UTC)
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) "
                "values (:id, :ingested, :ingested)"
            ),
            {"id": workspace_id.hex, "ingested": ingested},
        )
        connection.execute(
            sa.text(
                "insert into source "
                "(id, workspace_id, backend, config, next_sync_at, created_at, updated_at) "
                "values (:id, :workspace_id, 'folder', '{}', :ingested, :ingested, :ingested)"
            ),
            {"id": source_id.hex, "workspace_id": workspace_id.hex, "ingested": ingested},
        )
        connection.execute(
            sa.text(
                "insert into page "
                "(id, workspace_id, source_id, digest, body_ref, subject, tombstone, "
                "stream, title, created_at, updated_at) values "
                "(:id, :workspace_id, :source_id, 'sha256:page', 'pages/page', 'shared', false, "
                "'issues', 'Old issue', :ingested, :ingested)"
            ),
            {
                "id": page_id.hex,
                "workspace_id": workspace_id.hex,
                "source_id": source_id.hex,
                "ingested": ingested,
            },
        )
        for item_id, source_ref in (
            (derived_id, str(page_id)),
            (manual_id, "member-authored"),
            (dangling_id, str(uuid4())),
        ):
            connection.execute(
                sa.text(
                    "insert into memory_item "
                    "(id, workspace_id, subject, body, item_class, memory_kind, confidence, "
                    "source_ref, created_at, updated_at) values "
                    "(:id, :workspace_id, 'shared', 'fact', 'fact', 'fact', 5, "
                    ":source_ref, :ingested, :ingested)"
                ),
                {
                    "id": item_id.hex,
                    "workspace_id": workspace_id.hex,
                    "source_ref": source_ref,
                    "ingested": ingested,
                },
            )
        connection.commit()

    command.upgrade(config, "memory_0009")
    with engine.connect() as connection:
        derived, manual, dangling = (
            connection.execute(
                sa.text("select created_from_page_id, source_ref from memory_item where id = :id"),
                {"id": item_id.hex},
            ).one()
            for item_id in (derived_id, manual_id, dangling_id)
        )
    engine.dispose()

    assert derived.created_from_page_id == page_id.hex
    assert derived.source_ref is None
    assert manual.created_from_page_id is None and manual.source_ref == "member-authored"
    assert dangling.created_from_page_id is None and dangling.source_ref is not None


def test_agent_binding_migration_backfills_the_earliest_agent(tmp_path: Path) -> None:
    database_path = tmp_path / "agent-bindings.db"
    url = f"sqlite+aiosqlite:///{database_path}"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0050")

    engine = sa.create_engine(f"sqlite:///{database_path}")
    workspace_id, later_agent, earliest_agent, conversation_id = (uuid4() for _ in range(4))
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) values (:id, :moment, :moment)"
            ),
            {"id": workspace_id.hex, "moment": datetime(2026, 7, 1, tzinfo=UTC)},
        )
        for agent_id, name, created in (
            (later_agent, "exec", datetime(2026, 7, 5, tzinfo=UTC)),
            (earliest_agent, "assistant", datetime(2026, 7, 2, tzinfo=UTC)),
        ):
            connection.execute(
                sa.text(
                    "insert into agent "
                    "(id, workspace_id, name, prompt, model, internet_access_allowed, "
                    "created_at, updated_at) "
                    "values (:id, :workspace_id, :name, 'p', 'eval', true, :created, :created)"
                ),
                {
                    "id": agent_id.hex,
                    "workspace_id": workspace_id.hex,
                    "name": name,
                    "created": created,
                },
            )
        connection.execute(
            sa.text(
                "insert into surface_installation "
                "(workspace_id, surface, installation_id, created_at, updated_at) "
                "values (:workspace_id, 'slack', 'team:T1', :moment, :moment)"
            ),
            {"workspace_id": workspace_id.hex, "moment": datetime(2026, 7, 3, tzinfo=UTC)},
        )
        connection.execute(
            sa.text(
                "insert into conversation "
                "(id, workspace_id, surface, queue_key, created_at, updated_at) "
                "values (:id, :workspace_id, 'cli', 'session', :moment, :moment)"
            ),
            {
                "id": conversation_id.hex,
                "workspace_id": workspace_id.hex,
                "moment": datetime(2026, 7, 3, tzinfo=UTC),
            },
        )
        connection.commit()

    command.upgrade(config, "0051")
    with engine.connect() as connection:
        installation_agent = connection.execute(
            sa.text("select agent_id from surface_installation where workspace_id = :id"),
            {"id": workspace_id.hex},
        ).scalar_one()
        conversation_agent = connection.execute(
            sa.text("select agent_id from conversation where id = :id"),
            {"id": conversation_id.hex},
        ).scalar_one()
    engine.dispose()

    assert installation_agent == earliest_agent.hex
    assert conversation_agent == earliest_agent.hex


def test_user_skill_migration_backfills_within_each_workspace_and_enforces_agent(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "agent-skills.db"
    url = f"sqlite+aiosqlite:///{database_path}"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0051")
    command.upgrade(config, "skill_create_0001")

    engine = sa.create_engine(f"sqlite:///{database_path}")
    first_workspace, second_workspace = uuid4(), uuid4()
    first_earliest, first_later, second_earliest, second_later = (uuid4() for _ in range(4))
    moment = datetime(2026, 7, 1, tzinfo=UTC)
    with engine.connect() as connection:
        for workspace_id in (first_workspace, second_workspace):
            connection.execute(
                sa.text(
                    "insert into workspace (id, created_at, updated_at) "
                    "values (:id, :moment, :moment)"
                ),
                {"id": workspace_id.hex, "moment": moment},
            )
        for agent_id, workspace_id, name, created in (
            (first_later, first_workspace, "first-later", datetime(2026, 7, 5, tzinfo=UTC)),
            (
                first_earliest,
                first_workspace,
                "first-earliest",
                datetime(2026, 7, 2, tzinfo=UTC),
            ),
            (
                second_later,
                second_workspace,
                "second-later",
                datetime(2026, 7, 3, tzinfo=UTC),
            ),
            (
                second_earliest,
                second_workspace,
                "second-earliest",
                datetime(2026, 7, 1, tzinfo=UTC),
            ),
        ):
            connection.execute(
                sa.text(
                    "insert into agent "
                    "(id, workspace_id, name, prompt, model, internet_access_allowed, "
                    "created_at, updated_at) "
                    "values (:id, :workspace_id, :name, 'p', 'eval', true, :created, :created)"
                ),
                {
                    "id": agent_id.hex,
                    "workspace_id": workspace_id.hex,
                    "name": name,
                    "created": created,
                },
            )
        for workspace_id in (first_workspace, second_workspace):
            connection.execute(
                sa.text(
                    "insert into user_skill "
                    "(workspace_id, name, digest, content, created_at, updated_at) "
                    "values (:workspace_id, 'greet', 'sha256:old', "
                    "'{\"files\": {}}', :moment, :moment)"
                ),
                {"workspace_id": workspace_id.hex, "moment": moment},
            )
        connection.commit()

    command.upgrade(config, "skill_create_0002")
    with engine.connect() as connection:
        connection.exec_driver_sql("pragma foreign_keys = on")
        assert connection.exec_driver_sql("pragma foreign_keys").scalar_one() == 1
        rows = connection.execute(
            sa.text("select workspace_id, agent_id from user_skill order by workspace_id")
        ).all()
        foreign_keys = sa.inspect(connection).get_foreign_keys("user_skill")
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "insert into user_skill "
                    "(workspace_id, agent_id, name, digest, content, created_at, updated_at) "
                    "values (:workspace_id, :agent_id, 'orphan', 'sha256:orphan', "
                    "'{\"files\": {}}', :moment, :moment)"
                ),
                {
                    "workspace_id": first_workspace.hex,
                    "agent_id": uuid4().hex,
                    "moment": moment,
                },
            )
            connection.commit()
    engine.dispose()

    assert {(row.workspace_id, row.agent_id) for row in rows} == {
        (first_workspace.hex, first_earliest.hex),
        (second_workspace.hex, second_earliest.hex),
    }
    assert any(
        foreign_key["name"] == "user_skill_agent_id_fkey"
        and foreign_key["referred_table"] == "agent"
        and foreign_key["constrained_columns"] == ["agent_id"]
        and foreign_key["referred_columns"] == ["id"]
        for foreign_key in foreign_keys
    )


def test_migrate_command_brings_the_schema_to_head(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operator verb behind the extension-migration seam: `ufoctl migrate` applies core's
    schema plus every active extension's branch, so `ext install` → `migrate` → `serve` actually
    creates a table-owning extension's tables. Idempotent, so it is safe to re-run."""
    from click.testing import CliRunner

    from ufo.cli import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "ufo.toml").write_text(
        '[database]\nurl = "sqlite+aiosqlite:///app.db"\n'
        '[blob]\nbackend = "filesystem"\nroot = "./blobs"\n'
    )
    runner = CliRunner()
    assert runner.invoke(main, ["migrate"]).exit_code == 0
    assert runner.invoke(main, ["migrate"]).exit_code == 0

    engine = sa.create_engine("sqlite:///" + str(tmp_path / "app.db"))
    try:
        with engine.connect() as connection:
            inspector = sa.inspect(connection)
            names = set(inspector.get_table_names())
            trigger_columns = {column["name"] for column in inspector.get_columns("source_trigger")}
    finally:
        engine.dispose()
    assert "workspace" in names
    assert "user_skill" in names
    assert "sample_ext_note" in names
    assert "delivery" in trigger_columns
    assert "coding_review_inbox" not in names
    assert "coding_review_run" not in names


def test_transcript_reads_names_disclosures_newest_first_and_pages_on_the_operator_s_limit(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The operator's read of the disclosure record, which is the only read of it: `ufoctl
    transcript-reads` names who opened whose conversation and when, newest first, and the operator
    asking sets how far back the page reaches."""
    from click.testing import CliRunner

    from ufo.cli import TRANSCRIPT_READS_LIMIT, main

    monkeypatch.chdir(tmp_path)
    (tmp_path / "ufo.toml").write_text(
        '[database]\nurl = "sqlite+aiosqlite:///app.db"\n'
        '[blob]\nbackend = "filesystem"\nroot = "./blobs"\n'
    )
    runner = CliRunner()
    assert runner.invoke(main, ["migrate"]).exit_code == 0

    database_path = tmp_path / "app.db"
    workspace_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4()
    admin_id, subject_id = uuid4(), uuid4()
    now = datetime(2026, 7, 31, 9, 0, tzinfo=UTC)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "insert into workspace (id, created_at, updated_at) values (?, ?, ?)",
            (workspace_id.hex, now.isoformat(), now.isoformat()),
        )
        for member_id, email, admin in (
            (admin_id, "boss@example.com", 1),
            (subject_id, "m@example.com", 0),
        ):
            connection.execute(
                "insert into member (id, workspace_id, email, is_admin, created_at, updated_at) "
                "values (?, ?, ?, ?, ?, ?)",
                (member_id.hex, workspace_id.hex, email, admin, now.isoformat(), now.isoformat()),
            )
        connection.execute(
            "insert into agent (id, workspace_id, name, prompt, model, is_main, created_at, "
            "updated_at) values (?, ?, 'assistant', 'p', 'auto', 1, ?, ?)",
            (agent_id.hex, workspace_id.hex, now.isoformat(), now.isoformat()),
        )
        connection.execute(
            "insert into conversation (id, workspace_id, agent_id, surface, queue_key, "
            "created_at, updated_at) values (?, ?, ?, 'web', 'a/b', ?, ?)",
            (conversation_id.hex, workspace_id.hex, agent_id.hex, now.isoformat(), now.isoformat()),
        )
        for index in range(TRANSCRIPT_READS_LIMIT + 1):
            connection.execute(
                "insert into transcript_access (id, workspace_id, conversation_id, "
                "reader_member_id, subject_member_id, created_at) values (?, ?, ?, ?, ?, ?)",
                (
                    uuid4().hex,
                    workspace_id.hex,
                    conversation_id.hex,
                    admin_id.hex,
                    subject_id.hex,
                    (now + timedelta(minutes=index)).isoformat(),
                ),
            )

    listed = runner.invoke(main, ["transcript-reads"])
    assert listed.exit_code == 0
    assert "boss@example.com" in listed.output
    assert "m@example.com" in listed.output
    assert str(conversation_id) in listed.output
    assert len(listed.output.strip().splitlines()) == TRANSCRIPT_READS_LIMIT
    assert "2026-07-31 09:00" not in listed.output

    deeper = runner.invoke(main, ["transcript-reads", "--limit", str(TRANSCRIPT_READS_LIMIT + 1)])
    assert deeper.exit_code == 0
    assert "2026-07-31 09:00" in deeper.output

    assert runner.invoke(main, ["transcript-reads", "--limit", "0"]).exit_code != 0


async def test_workspace_tx_round_trip(db: None) -> None:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.workspace.c.id).where(tables.workspace.c.id == workspace_id)
            )
        ).one()
    assert row.id == workspace_id


async def test_conversation_and_memory_audiences_are_constrained(db: None) -> None:
    workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
    memory_item = sa.table(
        "memory_item",
        sa.column("id", sa.Uuid()),
        sa.column("workspace_id", sa.Uuid()),
        sa.column("subject", sa.Text()),
        sa.column("body", sa.Text()),
        sa.column("body_digest", sa.Text()),
        sa.column("item_class", sa.Text()),
        sa.column("memory_kind", sa.Text()),
        sa.column("confidence", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="member@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="m",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        invalid = (
            ("invalid", None),
            (f"member:{member_id}", None),
            ("shared", member_id),
        )
        for index, (audience, row_member_id) in enumerate(invalid):
            with pytest.raises(sa.exc.IntegrityError):
                async with connection.begin_nested():
                    await connection.execute(
                        sa.insert(tables.conversation).values(
                            id=uuid4(),
                            workspace_id=workspace_id,
                            agent_id=agent_id,
                            surface="test",
                            queue_key=f"invalid-{index}",
                            member_id=row_member_id,
                            audience=audience,
                            created_at=sa.func.now(),
                            updated_at=sa.func.now(),
                        )
                    )
        with pytest.raises(sa.exc.IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    sa.insert(memory_item).values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        subject="invalid",
                        body="probe",
                        body_digest=hashlib.sha256(b"probe").hexdigest(),
                        item_class="fact",
                        memory_kind="fact",
                        confidence=5,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )


REFUSED_DSN = "postgresql+asyncpg://ufo:ufo@127.0.0.1:1/ufo_test"


async def test_verify_db_reachable_fails_loud_on_a_database_it_cannot_reach() -> None:
    """The boot check the TCP-probed deployments rest on. A privileged port is used because nothing
    in this suite can bind it, so the refusal is deterministic — an ephemeral port is free for a
    parallel worker to take between the release and the connect."""
    init_db(REFUSED_DSN)
    try:
        with pytest.raises(ConnectionRefusedError):
            await verify_db_reachable()
    finally:
        await dispose_db()


async def test_verify_db_reachable_checks_the_owner_url_too(db: None) -> None:
    """`ufoctl proxy` and `ufoctl ingress` open one url, but `serve` opens two and reaches the
    database through the owner one — a check that only dialled the app url would pass a boot whose
    owner database is unreachable."""
    init_owner_db(REFUSED_DSN)
    with pytest.raises(ConnectionRefusedError):
        await verify_db_reachable()


async def test_verify_db_reachable_requires_init() -> None:
    with pytest.raises(RuntimeError, match="db not initialized"):
        await verify_db_reachable()


async def test_verify_db_reachable_publishes_no_engine(db: None) -> None:
    """It dials with its own engine and disposes it, so the check never leaves a pooled connection
    behind — a boot verb that seeded the registry would hand the first request an engine built on
    whatever loop happened to run the check."""
    before = (set(ufo.db._APP.engines), set(ufo.db._OWNER.engines))
    await verify_db_reachable()
    assert (set(ufo.db._APP.engines), set(ufo.db._OWNER.engines)) == before


async def test_workspace_tx_requires_init() -> None:
    with pytest.raises(RuntimeError):
        async with workspace_tx():
            pass


async def test_a_transaction_that_never_opens_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The connect that never lands is invisible to the database — it never receives it — so this
    count is the only place the failure exists. It has to be the acquisition alone: counting the
    whole block would report every failing query as a database that could not be reached."""
    counted: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        o11y, "emit_metric", lambda name, **dimensions: counted.append((name, dimensions))
    )

    with pytest.raises(TimeoutError):
        async with _opened(_RefusingEngine(TimeoutError()), "workspace"):
            pass
    async with _opened(_RefusingEngine(None), "workspace"):
        pass
    with pytest.raises(ValueError):
        async with _opened(_RefusingEngine(None), "workspace"):
            raise ValueError("the caller's own failure")

    assert counted == [
        ("db_tx_unavailable_total", {"path": "workspace", "error_class": "TimeoutError"})
    ]


async def test_a_refused_connect_counts_under_the_class_the_driver_really_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`path` is this counter's only other dimension, so `error_class` is its whole information
    content, and the emitter folds a class no allowlist entry names into `other` — which makes the
    exact name load-bearing. Nothing in the code names these: they are whatever the driver raises
    through the dialect, so both are taken from the real stack rather than guessed at as the
    `OperationalError` a SQLAlchemy-shaped guess would reach for.

    A pooled engine builds without dialing, so the dial is driven here: `connect()` on the engine
    this deployment builds runs the whole path — pool, dialect, driver — which answers what a bare
    driver connect cannot, whether the dialect re-wraps the driver's `OSError` on the way out. It
    does not, and a release that started to would fail here rather than send production to `other`.
    A pool is what makes this counter rare rather than impossible: a warm connection answers most
    transactions, and the ones that still dial — a loop's first touch, a recycled connection, the
    replacement `pool_pre_ping` opens for one the database dropped — are where the incident it
    reports lives. Both harvested classes then travel the rest of the way, through `_opened` to an
    exact attribute dict, so neither entry in the allowlist is held by a name mirrored between two
    lists.

    The refusal is taken from a privileged port, which nothing in this suite can bind: an ephemeral
    port picked by binding and releasing is free for any parallel worker to take between the release
    and the connect, and a worker that binds it as a server answers the TCP handshake and then never
    speaks Postgres, which hangs the read rather than refusing it. A port that cannot be bound at
    all has no such window."""

    async def dial(url: str) -> None:
        engine = _build_engine(url, ufo.db._APP)
        try:
            async with engine.connect():
                pass
        finally:
            await engine.dispose()

    with pytest.raises(ConnectionRefusedError) as refused:
        await dial("postgresql+asyncpg://ufo:ufo@127.0.0.1:1/ufo_test")
    with pytest.raises(socket.gaierror) as unresolved:
        await dial("postgresql+asyncpg://ufo:ufo@no-such-host.invalid:5432/ufo_test")

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(o11y.metrics, "get_meter", provider.get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    for harvested in (refused.value, unresolved.value):
        with pytest.raises(type(harvested)):
            async with _opened(_RefusingEngine(type(harvested)()), "workspace"):
                pass
    assert [
        dict(point.attributes)
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "ufo.db_tx_unavailable_total"
        for point in metric.data.data_points
    ] == [
        {"path": "workspace", "error_class": "ConnectionRefusedError"},
        {"path": "workspace", "error_class": "gaierror"},
    ]


@pytest.fixture
def tx_engine(tmp_path: Path) -> AsyncEngine:
    url = f"sqlite+aiosqlite:///{tmp_path / 'transactions.db'}"
    apply_migrations(url)
    return _build_engine(url, ufo.db._APP)


async def _add_workspace(connection: AsyncConnection, workspace_id: UUID) -> None:
    await connection.execute(
        sa.insert(tables.workspace).values(
            id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
        )
    )


async def _committed_workspaces(engine: AsyncEngine) -> list[UUID]:
    async with _opened(engine, "workspace") as connection:
        return list((await connection.execute(sa.select(tables.workspace.c.id))).scalars())


async def test_a_body_that_raises_commits_nothing(tx_engine: AsyncEngine) -> None:
    workspace_id = uuid4()

    with pytest.raises(ValueError):
        async with _opened(tx_engine, "workspace") as connection:
            await _add_workspace(connection, workspace_id)
            raise ValueError("the caller's own failure")

    assert await _committed_workspaces(tx_engine) == []


async def test_a_cancel_inside_the_body_rolls_back_before_the_caller_sees_it(
    tx_engine: AsyncEngine,
) -> None:
    workspace_id = uuid4()
    written = asyncio.Event()
    opened: list[AsyncConnection] = []

    async def step() -> None:
        async with _opened(tx_engine, "workspace") as connection:
            opened.append(connection)
            await _add_workspace(connection, workspace_id)
            written.set()
            await asyncio.Event().wait()

    task = asyncio.ensure_future(step())
    await written.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert opened[0].closed
    assert await _committed_workspaces(tx_engine) == []


async def test_a_cancel_after_checkout_returns_the_connection_before_the_caller_sees_it(
    tx_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked_out = asyncio.Event()
    release_checkout = asyncio.Event()
    real_greenlet_spawn = sqlalchemy_async_engine.greenlet_spawn
    pause_next_checkout = True

    async def pause_after_checkout(*args: object, **kwargs: object) -> object:
        nonlocal pause_next_checkout
        result = await real_greenlet_spawn(*args, **kwargs)
        if pause_next_checkout:
            pause_next_checkout = False
            checked_out.set()
            await release_checkout.wait()
        return result

    monkeypatch.setattr(sqlalchemy_async_engine, "greenlet_spawn", pause_after_checkout)

    async def step() -> None:
        async with _opened(tx_engine, "workspace"):
            pass

    task = asyncio.create_task(step())
    await checked_out.wait()
    assert tx_engine.pool.checkedout() == 1
    task.cancel()
    release_checkout.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert tx_engine.pool.checkedout() == 0


async def test_a_cancelled_sqlite_contender_never_checks_out_a_connection(
    tx_engine: AsyncEngine,
) -> None:
    first_entered = asyncio.Event()
    release_first = asyncio.Event()
    second_entered = asyncio.Event()

    async def first() -> None:
        async with _opened(tx_engine, "workspace"):
            first_entered.set()
            await release_first.wait()

    async def second() -> None:
        async with _opened(tx_engine, "workspace"):
            second_entered.set()

    first_task = asyncio.create_task(first())
    await first_entered.wait()
    second_task = asyncio.create_task(second())
    for _ in range(3):
        await asyncio.sleep(0)
    queued_before_checkout = tx_engine.pool.checkedout() == 1 and not second_entered.is_set()
    second_task.cancel()
    release_first.set()
    await first_task
    with pytest.raises(asyncio.CancelledError):
        await second_task

    assert queued_before_checkout
    assert tx_engine.pool.checkedout() == 0
    async with _opened(tx_engine, "workspace"):
        pass


async def test_a_cancel_landing_inside_the_teardown_finishes_it_first(
    tx_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    opened: list[AsyncConnection] = []
    commit_started = asyncio.Event()
    release_commit = asyncio.Event()
    original_commit = aiosqlite.Connection.commit
    pause_next_commit = True

    async def paused_commit(connection: aiosqlite.Connection) -> None:
        nonlocal pause_next_commit
        if pause_next_commit:
            pause_next_commit = False
            commit_started.set()
            await release_commit.wait()
        await original_commit(connection)

    monkeypatch.setattr(aiosqlite.Connection, "commit", paused_commit)

    async def step() -> None:
        async with _opened(tx_engine, "workspace") as connection:
            opened.append(connection)
            await _add_workspace(connection, workspace_id)

    task = asyncio.ensure_future(step())
    await commit_started.wait()
    task.cancel()
    release_commit.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert asyncio.all_tasks() == {asyncio.current_task()}
    assert opened[0].closed
    assert await _committed_workspaces(tx_engine) == [workspace_id]


async def test_a_teardown_that_fails_raises_on_the_caller(tx_engine: AsyncEngine) -> None:
    def fail_the_commit(connection: sa.Connection) -> None:
        raise RuntimeError("the commit lost the race")

    sa.event.listen(tx_engine.sync_engine, "commit", fail_the_commit)

    with pytest.raises(RuntimeError, match="the commit lost the race"):
        async with _opened(tx_engine, "workspace") as connection:
            await _add_workspace(connection, uuid4())


async def test_a_teardown_that_fails_under_a_cancel_still_raises_on_the_caller(
    tx_engine: AsyncEngine,
) -> None:
    def fail_the_commit(connection: sa.Connection) -> None:
        task.cancel()
        raise RuntimeError("the commit lost the race")

    sa.event.listen(tx_engine.sync_engine, "commit", fail_the_commit)

    async def step() -> None:
        async with _opened(tx_engine, "workspace") as connection:
            await _add_workspace(connection, uuid4())

    task = asyncio.ensure_future(step())
    with pytest.raises(RuntimeError, match="the commit lost the race"):
        await task


async def test_a_teardown_that_failed_before_its_cancel_landed_still_raises_on_the_caller(
    tx_engine: AsyncEngine,
) -> None:
    def fail_the_commit(connection: sa.Connection) -> None:
        closing = asyncio.current_task()
        assert closing is not None
        closing.add_done_callback(lambda _: task.cancel())
        raise RuntimeError("the commit lost the race")

    sa.event.listen(tx_engine.sync_engine, "commit", fail_the_commit)

    async def step() -> None:
        async with _opened(tx_engine, "workspace") as connection:
            await _add_workspace(connection, uuid4())

    task = asyncio.ensure_future(step())
    with pytest.raises(RuntimeError, match="the commit lost the race"):
        await task


async def test_a_stop_the_acquisition_reported_as_a_database_error_still_ends_the_caller_cancelled(
    tx_engine: AsyncEngine,
) -> None:
    """A cancellation delivered while the driver holds the greenlet is swallowed there and comes
    back out of SQLAlchemy as a database error. A caller that survives database errors — the surface
    listener's claim tick — would read its own stop as a blip and poll on forever, so what the
    driver reports cannot outrank what was asked of the task."""

    def fail_the_begin(connection: sa.Connection) -> None:
        raise sa.exc.OperationalError("begin immediate", None, Exception("database is locked"))

    sa.event.listen(tx_engine.sync_engine, "begin", fail_the_begin)

    async def step() -> None:
        with suppress(asyncio.CancelledError):
            await asyncio.Event().wait()
        async with _opened(tx_engine, "workspace") as connection:
            await _add_workspace(connection, uuid4())

    task = asyncio.ensure_future(step())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


async def test_a_stop_the_body_reported_as_a_database_error_still_ends_the_caller_cancelled(
    tx_engine: AsyncEngine,
) -> None:
    def fail_the_statement(*_arguments: object) -> None:
        raise sa.exc.OperationalError("insert into workspace", None, Exception("disk I/O error"))

    async def step() -> None:
        with suppress(asyncio.CancelledError):
            await asyncio.Event().wait()
        async with _opened(tx_engine, "workspace") as connection:
            sa.event.listen(tx_engine.sync_engine, "before_cursor_execute", fail_the_statement)
            await _add_workspace(connection, uuid4())

    task = asyncio.ensure_future(step())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


def test_the_unavailable_count_is_a_registered_metric() -> None:
    """`_opened` emits from inside its except block, so a name `o11y` does not know would raise
    `unknown metric` there and leave the caller holding that instead of the connect failure the
    count exists to report — the instrumentation destroying the error it was added to surface. The
    test above mocks the emit away to read its arguments, so this is what holds the name."""
    o11y.emit_metric("db_tx_unavailable_total", path="workspace", error_class="TimeoutError")
    o11y.emit_metric("db_pool_exhausted_total", path="workspace")
    o11y.emit_histogram("db_tx_acquire_ms", 1, path="workspace")


async def test_a_saturated_pool_is_counted_apart_from_a_lost_dial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two failures share a class name — SQLAlchemy raises its own `TimeoutError` when a
    checkout waits out `pool_timeout`, and a lost dial raises the builtin one — so `error_class`
    cannot tell an operator whether the fleet hit its ceiling or the network dropped a packet. The
    saturation count is what separates them, and it is emitted beside the unavailable count rather
    than instead of it: a transaction that never opened is still a transaction that never opened."""
    counted: list[tuple[str, dict[str, str]]] = []
    monkeypatch.setattr(
        o11y, "emit_metric", lambda name, **dimensions: counted.append((name, dimensions))
    )

    with pytest.raises(sa.exc.TimeoutError):
        async with _opened(_RefusingEngine(sa.exc.TimeoutError("pool limit reached")), "workspace"):
            pass
    with pytest.raises(TimeoutError):
        async with _opened(_RefusingEngine(TimeoutError()), "owner"):
            pass

    assert counted == [
        ("db_pool_exhausted_total", {"path": "workspace"}),
        ("db_tx_unavailable_total", {"path": "workspace", "error_class": "TimeoutError"}),
        ("db_tx_unavailable_total", {"path": "owner", "error_class": "TimeoutError"}),
    ]


async def test_the_acquire_wait_is_recorded_whether_or_not_the_transaction_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pool that is filling shows up as a wait long before it shows up as a failure, and the wait
    that ends in a failure is the one worth the most — it is the queue at its longest, so the
    `finally` that records it is what the assertion below is really holding."""
    recorded: list[tuple[str, int, dict[str, str]]] = []
    monkeypatch.setattr(
        o11y,
        "emit_histogram",
        lambda name, value, **dimensions: recorded.append((name, value, dimensions)),
    )
    monkeypatch.setattr(o11y, "emit_metric", lambda name, **dimensions: None)
    clock = iter((10.0, 10.25, 20.0, 20.5))
    monkeypatch.setattr(ufo.db, "time", SimpleNamespace(monotonic=lambda: next(clock)))

    async with _opened(_RefusingEngine(None), "workspace"):
        pass
    with pytest.raises(TimeoutError):
        async with _opened(_RefusingEngine(TimeoutError()), "owner"):
            pass

    assert recorded == [
        ("db_tx_acquire_ms", 250, {"path": "workspace"}),
        ("db_tx_acquire_ms", 500, {"path": "owner"}),
    ]


def _current_engine() -> AsyncEngine:
    loop = asyncio.get_running_loop()
    return next(engine for (held, _), engine in ufo.db._APP.engines.items() if held is loop)


def _draining() -> list[asyncio.Task[None]]:
    return list(ufo.db._disposing.get(asyncio.get_running_loop(), ()))


async def _touch() -> None:
    async with workspace_tx() as connection:
        await connection.execute(sa.text("select 1"))


async def _touch_and_dispose() -> None:
    await _touch()
    await _current_engine().dispose()


async def test_rls_guc_does_not_leak_across_a_reused_connection(
    db: None, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE pooling safety invariant: `set_config(..., true)` is `SET LOCAL`, so a pooled connection
    handed to the next checkout carries no residual workspace GUC. A single-connection pool forces
    physical reuse (equal backend pids prove it), and a non-superuser probe role — the test user is
    a superuser, which bypasses RLS — proves rows pinned under one workspace are invisible under
    another and that an unbound read still fails closed."""
    if not database_url.startswith("postgresql"):
        pytest.skip("RLS is a postgres mechanism")
    url = make_url(database_url)
    role = f"rls_probe_{url.database}"
    probe_url = url.set(username=role, password="rls-probe").render_as_string(hide_password=False)
    predicate = f"workspace_id = current_setting('{WORKSPACE_GUC}')::uuid"
    async with workspace_tx() as connection:
        await connection.execute(sa.text("create table rls_probe (workspace_id uuid not null)"))
        await connection.execute(sa.text("alter table rls_probe enable row level security"))
        await connection.execute(
            sa.text(
                f"create policy rls_probe_ws on rls_probe "
                f"using ({predicate}) with check ({predicate})"
            )
        )
        await connection.execute(sa.text(f'drop role if exists "{role}"'))
        await connection.execute(sa.text(f"create role \"{role}\" login password 'rls-probe'"))
        await connection.execute(sa.text(f'grant select, insert on rls_probe to "{role}"'))
    await dispose_db()
    monkeypatch.setattr(ufo.db, "_APP", replace(ufo.db._APP, size=1, overflow=0, engines={}))
    init_db(probe_url)
    try:
        workspace_a, workspace_b = uuid4(), uuid4()
        with ws(workspace_a):
            async with workspace_tx() as connection:
                pid_a = (await connection.execute(sa.text("select pg_backend_pid()"))).scalar_one()
                await connection.execute(
                    sa.text("insert into rls_probe values (cast(:ws as uuid))"),
                    {"ws": str(workspace_a)},
                )
        with ws(workspace_b):
            async with workspace_tx() as connection:
                pid_b = (await connection.execute(sa.text("select pg_backend_pid()"))).scalar_one()
                rows = (await connection.execute(sa.text("select * from rls_probe"))).all()
        assert pid_a == pid_b
        assert rows == []
        with pytest.raises(sa.exc.DBAPIError):
            async with workspace_tx() as connection:
                await connection.execute(sa.text("select * from rls_probe"))
    finally:
        await dispose_db()
        init_db(database_url)
        async with workspace_tx() as connection:
            await connection.execute(sa.text("drop table rls_probe"))
            await connection.execute(sa.text(f'drop role "{role}"'))
        await dispose_db()
        init_db(database_url)


async def test_same_loop_resolves_the_same_engine(db: None) -> None:
    await _touch()
    first = _current_engine()
    await _touch()
    assert _current_engine() is first


async def test_engines_are_per_loop(db: None) -> None:
    await _touch()
    main_engine = _current_engine()
    seen: list[AsyncEngine] = []

    async def capture() -> None:
        await _touch()
        seen.append(_current_engine())
        await _current_engine().dispose()

    thread = threading.Thread(target=lambda: asyncio.run(capture()))
    thread.start()
    thread.join()
    assert seen[0] is not main_engine


async def test_dead_loop_entries_are_pruned(db: None) -> None:
    thread = threading.Thread(target=lambda: asyncio.run(_touch_and_dispose()))
    thread.start()
    thread.join()
    assert any(held.is_closed() for held, _ in ufo.db._APP.engines)
    await _touch()
    assert not any(held.is_closed() for held, _ in ufo.db._APP.engines)


async def test_dispose_loop_engines_closes_the_connection_not_just_the_key(db: None) -> None:
    """What keeps a throwaway boot loop from abandoning a live connection. Dropping the registry key
    is not the property — a pooled connection whose loop then closes is a socket nothing in the
    process can ever close — so this holds the *close*: disposal replaces the engine's pool, and
    the one the escaped engine's connection was checked into is gone. Remove the
    `await engine.dispose()` from `dispose_loop_engines` and this fails, where a key-only assertion
    passes."""
    await _touch()
    main_engine = _current_engine()
    escaped: list[tuple[AsyncEngine, object]] = []

    async def one_shot() -> None:
        await _touch()
        engine = _current_engine()
        escaped.append((engine, engine.pool))
        await ufo.db.dispose_loop_engines()

    thread = threading.Thread(target=lambda: asyncio.run(one_shot()))
    thread.start()
    thread.join()
    engine, pool_before = escaped[0]
    assert engine.pool is not pool_before
    assert not any(held.is_closed() for held, _ in ufo.db._APP.engines)
    assert _current_engine() is main_engine


async def test_dispose_db_closes_a_live_foreign_loops_engine(db: None) -> None:
    """A loop still running when teardown starts is the shape this suite has — a session worker
    beside the test's own loop. Only that loop can close its connections, so they close *through* it
    rather than dropped (which would strand them) or left open (which contends with whatever runs
    next). The handoff is scheduled on that loop rather than awaited from this one, so the foreign
    engine's pool is read after its loop has drained `_disposing`; both pools are asserted replaced,
    which is what disposal does and what popping a key alone does not."""
    await _touch()
    ready, release = threading.Event(), threading.Event()
    escaped: list[tuple[AsyncEngine, object]] = []

    async def hold() -> None:
        await _touch()
        engine = _current_engine()
        escaped.append((engine, engine.pool))
        ready.set()
        await asyncio.to_thread(release.wait)
        await asyncio.gather(*_draining())

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=lambda: loop.run_until_complete(hold()))
    thread.start()
    try:
        ready.wait()
        mine = _current_engine()
        mine_pool = mine.pool
        await dispose_db()
        assert mine.pool is not mine_pool
        assert not ufo.db._APP.engines
        assert ufo.db._app_url is None
    finally:
        release.set()
        thread.join()
        loop.close()
    foreign, foreign_pool = escaped[0]
    assert foreign.pool is not foreign_pool


def _strand_a_disposal_on_a_loop_that_stops(stranded: list[ref[AsyncEngine]]) -> None:
    url = ufo.db._app_url
    assert url is not None
    engine = ufo.db._build_engine(url, ufo.db._APP)
    stranded.append(ref(engine))
    stopped = asyncio.new_event_loop()
    stopped.call_soon(stopped.stop)
    ufo.db._hand_off(stopped, engine)
    stopped.run_forever()
    stopped.close()


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
async def test_a_closed_loops_disposal_never_reaches_the_loop_disposing_next(db: None) -> None:
    """A loop that stops with the disposal it was handed still pending leaves a task no loop can
    ever finish. A task belongs to one loop, so a later loop reading that entry cannot await it —
    it raises, and the disposal that loop was actually driving never completes. The strand is
    built the way one arises (a handoff to a loop that stops before the task runs), and what is
    asserted is the next loop's disposal: unaffected, and the strand released rather than held for
    the life of the process."""
    await _touch()
    stranded: list[ref[AsyncEngine]] = []
    stranding = threading.Thread(target=_strand_a_disposal_on_a_loop_that_stops, args=(stranded,))
    stranding.start()
    stranding.join()
    ready, release = threading.Event(), threading.Event()
    escaped: list[tuple[AsyncEngine, object]] = []

    async def hold() -> None:
        await _touch()
        engine = _current_engine()
        escaped.append((engine, engine.pool))
        ready.set()
        await asyncio.to_thread(release.wait)
        await asyncio.gather(*_draining())

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=lambda: loop.run_until_complete(hold()))
    thread.start()
    try:
        ready.wait()
        await dispose_db()
    finally:
        release.set()
        thread.join()
        loop.close()
    foreign, foreign_pool = escaped[0]
    assert foreign.pool is not foreign_pool
    gc.collect()
    assert stranded[0]() is None


class _LoopThatClosesInTheWindow:
    """An event loop that reports itself open and then refuses the handoff — the one interleaving a
    check cannot exclude, since the loop can close between `is_closed()` returning False and
    `call_soon_threadsafe` being reached. Standing in for the loop, never for the assertion: what is
    asserted is what `dispose_db` does when a handoff is refused."""

    def is_closed(self) -> bool:
        return False

    def call_soon_threadsafe(self, *_: object) -> None:
        raise RuntimeError("Event loop is closed")


async def test_a_loop_that_closes_mid_teardown_neither_raises_nor_holds_the_rest(
    db: None,
) -> None:
    """The failure has to stay inside teardown: seventeen callers are a bare
    `finally: await dispose_db()`, so a raise here replaces what drove the teardown, and an early
    exit would leave the pools after it untouched — the owner registry is iterated second, so its
    connections would stay open. The refused entry is dropped, because a loop that closed can no
    longer close anything."""
    await _touch()
    doomed = _LoopThatClosesInTheWindow()
    ufo.db._APP.engines[(doomed, "postgresql+asyncpg://doomed/app")] = _build_engine(
        "sqlite+aiosqlite:///doomed-app.db", ufo.db._APP
    )
    ufo.db._OWNER.engines[(doomed, "postgresql+asyncpg://doomed/owner")] = _build_engine(
        "sqlite+aiosqlite:///doomed-owner.db", ufo.db._OWNER
    )

    await dispose_db()

    assert not ufo.db._APP.engines
    assert not ufo.db._OWNER.engines
    assert ufo.db._app_url is None


async def test_dispose_db_clears_registries_and_reinit_works(db: None) -> None:
    """Re-initializes with the URL the fixture opened, not the `database_url` parameter: on sqlite
    that parameter is the session-scoped template the fixture handed this test a private copy of."""
    private = ufo.db._app_url
    assert private is not None
    await _touch()
    await dispose_db()
    assert not ufo.db._APP.engines
    assert not ufo.db._OWNER.engines
    with pytest.raises(RuntimeError):
        async with workspace_tx():
            pass
    init_db(private)
    await _touch()


async def test_one_loop_holds_one_engine_per_url(db: None, database_url: str) -> None:
    """The url is half the registry key, and this is what fails if it leaves. Two urls on one loop
    have to resolve two engines: an entry a foreign loop left behind would otherwise be handed to a
    caller that re-initialized against a different database, which would read and write the previous
    one. Both dialects, since the key is not dialect-specific — the second url is the same database
    reached through the other driver on postgres, a second file on sqlite."""
    first = ufo.db._app_url
    assert first is not None
    if first.startswith("sqlite"):
        second = f"sqlite+aiosqlite:///{Path(make_url(first).database or '').parent / 'other.db'}"
        shutil.copy(make_url(first).database or "", make_url(second).database or "")
    else:
        second = (
            make_url(first)
            .set(drivername="postgresql+psycopg")
            .render_as_string(hide_password=False)
        )
    mine = ufo.db._engine_for(first, ufo.db._APP)
    other = ufo.db._engine_for(second, ufo.db._APP)
    try:
        assert other is not mine
        assert ufo.db._engine_for(first, ufo.db._APP) is mine
    finally:
        await other.dispose()
        ufo.db._APP.engines.pop((asyncio.get_running_loop(), second), None)


async def test_owner_tx_without_an_owner_url_resolves_the_app_engine(db: None) -> None:
    """`ufoctl ingress`, `ufoctl proxy`, and every one-shot verb open one URL, never an owner one.
    Building a second pool for that same URL would double their connection ceiling for nothing, so
    the fallback is the app pool's own engine — the identity, not a copy of the sizing."""
    await _touch()
    mine = _current_engine()
    async with owner_tx() as connection:
        await connection.execute(sa.text("select 1"))
    loop = asyncio.get_running_loop()
    assert not any(held is loop for held, _ in ufo.db._OWNER.engines)
    assert [key for key in ufo.db._APP.engines if key[0] is loop] == [(loop, ufo.db._app_url)]
    assert _current_engine() is mine


async def test_an_owner_url_gets_its_own_smaller_pool(db: None, database_url: str) -> None:
    """On the fleet `serve` sets both URLs, and then the owner pool is a second ceiling on the same
    instance. Its consumers are serial — the sweeps' enumeration and the heartbeat — so it is sized
    apart, and the budget in `db.py` counts it apart."""
    if not database_url.startswith("postgresql"):
        pytest.skip("sqlite holds no pool to size")
    assert ufo.db._app_url is not None
    init_owner_db(ufo.db._app_url)
    async with owner_tx() as connection:
        await connection.execute(sa.text("select 1"))
    owner_engine = next(iter(ufo.db._OWNER.engines.values()))
    await _touch()
    assert owner_engine is not _current_engine()
    assert owner_engine.pool.size() == ufo.db.OWNER_POOL_SIZE
    assert _current_engine().pool.size() == ufo.db.POOL_SIZE


async def test_pool_class_matches_dialect(db: None, database_url: str) -> None:
    await _touch()
    pool = _current_engine().pool
    assert type(pool).__name__ == "AsyncAdaptedQueuePool"
    assert pool.size() == ufo.db.POOL_SIZE
    expected = -1 if database_url.startswith("sqlite") else ufo.db.MAX_OVERFLOW
    assert pool._max_overflow == expected


async def test_a_pooled_sqlite_connection_carries_nothing_into_the_next_transaction(
    db: None, database_url: str
) -> None:
    """THE sqlite pooling safety invariant: the handle a transaction hands back is reused
    physically, and the next transaction finds it the way a fresh dial would have left it — no rows
    from a body that raised, no write lock still held, the connect-time pragmas still on. Then the
    test's own teardown has to end the reuse: an engine that kept its connection past `dispose_db`
    would leave a write-ahead log beside the file every copy of it then misses. Self-contained in
    one test, because under `--dist load` two tests are not ordered."""
    if not database_url.startswith("sqlite"):
        pytest.skip("a reused postgres connection is the RLS-GUC invariant's subject")
    url = ufo.db._app_url
    assert url is not None
    await _touch()
    dials = 0

    def count_dial(*_: object) -> None:
        nonlocal dials
        dials += 1

    sa.event.listen(_current_engine().sync_engine, "connect", count_dial)
    committed, discarded = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=committed, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    with pytest.raises(RuntimeError, match="rolled back"):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.workspace).values(
                    id=discarded, created_at=sa.func.now(), updated_at=sa.func.now()
                )
            )
            raise RuntimeError("this body is rolled back")
    async with workspace_tx() as connection:
        held = set((await connection.execute(sa.select(tables.workspace.c.id))).scalars().all())
        foreign_keys = (await connection.execute(sa.text("pragma foreign_keys"))).scalar_one()
    assert dials == 0, "the pool dialed again instead of reusing the connection handed back to it"
    assert held == {committed}
    assert foreign_keys == 1

    engine = _current_engine()
    async with engine.connect(), engine.connect():
        assert dials == 1, "a second caller was handed the connection the first one was using"

    database = Path(make_url(url).database or "")
    await dispose_db()
    init_db(url)
    assert not database.with_name(f"{database.name}-wal").exists()
    assert not database.with_name(f"{database.name}-shm").exists()
    reader = sqlite3.connect(database)
    try:
        assert [row[0] for row in reader.execute("select id from workspace")] == [committed.hex]
    finally:
        reader.close()


def test_the_dial_is_bounded_and_the_pool_is_named() -> None:
    """asyncpg's default connect timeout is 60 seconds — longer than any turn will wait, and the
    whole of #834 — so the dial a replacement checkout drives is bounded next to the connect. The
    application name is what attributes a connection to its pool in `pg_stat_activity`, which is
    what the soak reads: without it both registries and DBOS's own pool arrive indistinguishable.
    Both spellings are asserted: a driver refuses the other's kwarg rather than ignoring it."""
    asyncpg = ufo.db._pool_kwargs("postgresql+asyncpg://ufo:ufo@localhost/ufo", ufo.db._OWNER)
    assert asyncpg["connect_args"] == {
        "timeout": ufo.db.CONNECT_TIMEOUT_SECONDS,
        "server_settings": {"application_name": "ufo_owner"},
        "prepared_statement_cache_size": 0,
    }
    assert asyncpg["pool_pre_ping"] is True
    assert asyncpg["pool_timeout"] == ufo.db.POOL_TIMEOUT_SECONDS

    psycopg = ufo.db._pool_kwargs("postgresql+psycopg://ufo:ufo@localhost/ufo", ufo.db._APP)
    assert psycopg["connect_args"] == {
        "connect_timeout": ufo.db.CONNECT_TIMEOUT_SECONDS,
        "application_name": "ufo_app",
        "prepare_threshold": None,
    }


def test_every_backend_bounds_the_wait_for_a_pooled_connection() -> None:
    for url in [
        "postgresql+asyncpg://ufo:ufo@localhost/ufo",
        "postgresql+psycopg://ufo:ufo@localhost/ufo",
        "sqlite+aiosqlite:///ufo.db",
    ]:
        assert ufo.db._pool_kwargs(url, ufo.db._APP)["pool_timeout"] == ufo.db.POOL_TIMEOUT_SECONDS


async def test_every_driver_a_composition_root_opens_can_actually_connect(
    database_url: str,
) -> None:
    """`ufoctl proxy` and `ufoctl ingress` open a psycopg DSN (`proxy_serve.owner_dsn` rewrites
    the scheme), and `serve` opens asyncpg. A connect kwarg is per-driver, and the
    wrong one is refused rather than ignored — psycopg rejects asyncpg's `timeout` as an unknown
    connection option — so asserting the kwarg dict alone would have left both those deployments
    unable to open a single connection. This drives a real connect through each driver instead."""
    if not database_url.startswith("postgresql"):
        pytest.skip("one postgres instance, two drivers")
    psycopg_url = make_url(database_url).set(drivername="postgresql+psycopg")
    for url in (database_url, psycopg_url.render_as_string(hide_password=False)):
        engine = _build_engine(url, ufo.db._APP)
        try:
            async with engine.connect() as connection:
                named = await connection.execute(
                    sa.text("select current_setting('application_name')")
                )
                assert named.scalar_one() == "ufo_app"
        finally:
            await engine.dispose()


async def test_a_warm_connection_survives_ddl_from_another_process(
    db: None, database_url: str
) -> None:
    """The regression pooling made reachable, driven end to end rather than asserted as a kwarg.
    asyncpg caches 100 prepared statements per connection and a pooled connection carries them for
    `pool_recycle`, while the `ufo-migrate` Job runs alembic against a live fleet — so a plan whose
    table changed underneath is `InvalidCachedStatementError` on the next execute. The DDL lands
    on a second connection, exactly as it does from outside the process, and the warm connection
    has to keep working.

    Two details are load-bearing. `select *` is what `add column` changes the result descriptor of,
    which is what a cached plan goes stale against — a select naming its columns survives the DDL
    with the cache at its default, so it would prove nothing. And the rollback is what lets the DDL
    take its lock: a `connect()` that has executed is inside a transaction until told otherwise, and
    asyncpg's plan cache is per DBAPI connection, so releasing the transaction does not release the
    cache this is about. The DDL connection takes a `lock_timeout` so a rollback that stops
    happening fails this test instead of wedging on the warm connection's lock."""
    if not database_url.startswith("postgresql"):
        pytest.skip("prepared statements are a postgres mechanism")
    engine = _build_engine(database_url, ufo.db._APP)
    table = "plan_cache_probe"
    try:
        async with engine.connect() as setup:
            await setup.execute(sa.text(f"create table {table} (id int)"))
            await setup.commit()
        async with engine.connect() as warm:
            for _ in range(8):
                await warm.execute(sa.text(f"select * from {table}"))
            await warm.rollback()
            async with engine.connect() as elsewhere:
                await elsewhere.execute(sa.text("set lock_timeout = '5s'"))
                await elsewhere.execute(sa.text(f"alter table {table} add column added int"))
                await elsewhere.commit()
            await warm.execute(sa.text(f"select * from {table}"))
        async with engine.connect() as teardown:
            await teardown.execute(sa.text(f"drop table {table}"))
            await teardown.commit()
    finally:
        await engine.dispose()


async def test_a_committed_psycopg_connection_survives_ddl_from_another_process(
    db: None, database_url: str
) -> None:
    """The psycopg half, which needs a committed transaction to show at all: psycopg discards its
    plan cache on rollback, so a rollback-shaped probe proves nothing here — and commit is the path
    `ufoctl proxy` and `ufoctl ingress` run. Eight committed selects, DDL from another connection,
    then the same statement: with the cache at its default this raises `FeatureNotSupported: cached
    plan must not change result type`."""
    if not database_url.startswith("postgresql"):
        pytest.skip("prepared statements are a postgres mechanism")
    url = make_url(database_url).set(drivername="postgresql+psycopg")
    engine = _build_engine(url.render_as_string(hide_password=False), ufo.db._APP)
    table = "psycopg_plan_cache_probe"
    try:
        async with engine.connect() as setup:
            await setup.execute(sa.text(f"create table {table} (id int)"))
            await setup.commit()
        async with engine.connect() as warm:
            for _ in range(8):
                await warm.execute(sa.text(f"select * from {table}"))
                await warm.commit()
            async with engine.connect() as elsewhere:
                await elsewhere.execute(sa.text("set lock_timeout = '5s'"))
                await elsewhere.execute(sa.text(f"alter table {table} add column added int"))
                await elsewhere.commit()
            await warm.execute(sa.text(f"select * from {table}"))
        async with engine.connect() as teardown:
            await teardown.execute(sa.text(f"drop table {table}"))
            await teardown.commit()
    finally:
        await engine.dispose()


async def test_dispose_loop_engines_closes_the_owner_pool_too(db: None) -> None:
    """Both of `_one_shot`'s production callers reach the database through `owner_tx`, so the
    engine a throwaway boot loop abandons is the owner one — skipping that registry is the leak
    this function exists to prevent, and the app pool's proof cannot see it.

    The owner url is the one the fixture opened, never the `database_url` parameter: on sqlite that
    parameter is the session template every later test copies, and binding a writer to it takes the
    one writer slot the fixture's private copy exists to keep separate."""
    private = ufo.db._app_url
    assert private is not None
    init_owner_db(private)
    escaped: list[tuple[AsyncEngine, object]] = []

    async def one_shot() -> None:
        async with owner_tx() as connection:
            await connection.execute(sa.text("select 1"))
        loop = asyncio.get_running_loop()
        engine = next(built for (held, _), built in ufo.db._OWNER.engines.items() if held is loop)
        escaped.append((engine, engine.pool))
        await ufo.db.dispose_loop_engines()

    thread = threading.Thread(target=lambda: asyncio.run(one_shot()))
    thread.start()
    thread.join()
    engine, pool_before = escaped[0]
    assert engine.pool is not pool_before
    assert not ufo.db._OWNER.engines


async def test_a_pool_at_its_ceiling_raises_the_class_the_saturation_count_branches_on(
    db: None, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The premise the saturation mechanism rests on, taken from SQLAlchemy rather than from a
    fake: `db_pool_exhausted_total` is emitted only where `_opened` sees SQLAlchemy's own
    `TimeoutError`, so a real pool at its ceiling raising anything else leaves the metric silent and
    the operator reading the fleet's ceiling as a network fault. A one-connection pool with checkout
    wait driven to zero reaches that ceiling on the second concurrent transaction."""
    if not database_url.startswith("postgresql"):
        pytest.skip("sqlite holds no pool to exhaust")
    counted: list[str] = []
    monkeypatch.setattr(o11y, "emit_metric", lambda name, **dimensions: counted.append(name))
    monkeypatch.setattr(ufo.db, "POOL_TIMEOUT_SECONDS", 0)
    pinned = replace(ufo.db._APP, size=1, overflow=0, engines={})
    monkeypatch.setattr(ufo.db, "_APP", pinned)
    try:
        async with workspace_tx():
            with pytest.raises(sa.exc.TimeoutError):
                async with workspace_tx():
                    pass
    finally:
        for engine in pinned.engines.values():
            await engine.dispose()

    assert counted == ["db_pool_exhausted_total", "db_tx_unavailable_total"]


async def test_agent_reasoning_is_constrained(db: None) -> None:
    """The column is the last gate on the effort a turn runs at: a value outside the enum is
    refused by the database, so a writer that bypasses the spec cannot seat `turbo` in a row the
    engine then hands the provider."""
    async with workspace_tx() as connection:
        workspace_id = uuid4()
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    sa.insert(tables.agent).values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        name="assistant",
                        prompt="p",
                        model="m",
                        reasoning="turbo",
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        seated = await connection.execute(
            sa.insert(tables.agent)
            .values(
                id=uuid4(),
                workspace_id=workspace_id,
                name="defaulted",
                prompt="p",
                model="m",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
            .returning(tables.agent.c.reasoning)
        )
        assert seated.scalar_one() == "auto"


async def test_turn_protocol_state_is_constrained(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4(), uuid4()
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="a@b.c",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="m",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key="session",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    sa.insert(tables.turn).values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        seq=1,
                        status="queued",
                        inbound="hi",
                        admission_source="typo",
                        terminal=None,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )
        with pytest.raises(sa.exc.IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    sa.insert(tables.turn).values(
                        id=uuid4(),
                        workspace_id=workspace_id,
                        conversation_id=conversation_id,
                        agent_id=agent_id,
                        seq=1,
                        status="done",
                        inbound="hi",
                        terminal=None,
                        created_at=sa.func.now(),
                        updated_at=sa.func.now(),
                    )
                )


def test_a_migrated_sqlite_file_needs_no_journal_conversion_from_its_readers(
    tmp_path: Path,
) -> None:
    """A migrated sqlite file is left in the mode its engines already use, and holds all its rows.

    Converting the journal mode takes an exclusive lock it cannot wait out: a connection that has to
    convert while another holds the file ends in `database is locked`. Leaving the migrated file
    converted means the engine every caller builds never asks for that lock — proved here by
    building one against a byte copy the way the test fixture copies its template, while a write is
    held open on it."""
    template = tmp_path / "template.db"
    apply_migrations(f"sqlite+aiosqlite:///{template}")
    assert not template.with_name(f"{template.name}-wal").exists()

    copy = tmp_path / "copy.db"
    shutil.copy(template, copy)
    with sqlite3.connect(copy) as seeded:
        assert seeded.execute("select count(*) from alembic_version").fetchone()[0] > 0

    holder = sqlite3.connect(copy, timeout=0.2)
    holder.execute("begin immediate")
    holder.execute(
        "insert into workspace (id, created_at, updated_at) values (?, ?, ?)",
        (str(uuid4()), "2026-01-01", "2026-01-01"),
    )
    try:
        _build_engine(f"sqlite+aiosqlite:///{copy}", ufo.db._APP)
    finally:
        holder.rollback()
        holder.close()


def test_a_refused_statement_reports_its_sql_and_its_sqlstate() -> None:
    """What a failure log gets to say about a rejected statement: the SQL and the SQLSTATE the
    database answered with, and neither the bound values nor the driver's message — a Postgres
    message quotes the row that broke the constraint, which is a workspace's data."""
    refused = sa.exc.ProgrammingError(
        "select agent_id from user_skill where workspace_id = $1",
        {"workspace_id": "0f9e2f4c-11c7-4a52-9c8e-6f1f4b0f2f77"},
        _UndefinedColumn(),
    )
    assert failed_statement(refused) == {
        "statement": "select agent_id from user_skill where workspace_id = $1",
        "sqlstate": "42703",
    }


def test_a_refused_statement_is_bounded_and_a_plain_failure_reports_none() -> None:
    long_statement = "select " + "x" * (STATEMENT_LOG_MAX_CHARS * 2) + " from workspace"
    bounded = failed_statement(sa.exc.ProgrammingError(long_statement, {}, _UndefinedColumn()))
    assert len(bounded["statement"]) == STATEMENT_LOG_MAX_CHARS
    assert failed_statement(TimeoutError("database connect timed out")) == {}


def test_a_libpq_owner_dsn_registers_as_the_async_driver() -> None:
    """The owner DSN arrives from a secret store in libpq form. SQLAlchemy resolves that to the sync
    psycopg2 dialect — a banned import that is not installed — so an engine built from it raises
    before any query runs, and every `owner_tx` caller fails at its first read. Normalizing where
    the URL is registered is what keeps a caller that passes the secret through verbatim working.
    """
    ufo.db._owner_url = None
    try:
        init_owner_db("postgresql://ufo_owner:secret@db.internal:5432/ufo")
        assert ufo.db._owner_url == "postgresql+asyncpg://ufo_owner:secret@db.internal:5432/ufo"
    finally:
        ufo.db._owner_url = None


def test_an_owner_dsn_that_already_names_its_driver_is_untouched() -> None:
    ufo.db._owner_url = None
    try:
        init_owner_db("postgresql+asyncpg://ufo_owner:secret@db.internal:5432/ufo")
        assert ufo.db._owner_url == "postgresql+asyncpg://ufo_owner:secret@db.internal:5432/ufo"
    finally:
        ufo.db._owner_url = None


def test_the_pause_migration_takes_its_rows_with_the_columns(tmp_path: Path) -> None:
    """The pause left core, so 0085 drops the columns that carried a member's wait and the rows that
    used them: a `@once` row left behind would be a task whose schedule no cron parser accepts, and
    nothing later would ever delete it. Recurring rows keep firing. Run over SQLite because that is
    the dialect where dropping a column rebuilds the table, taking the table's indexes with it."""
    database = tmp_path / "pause.db"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database}")
    command.upgrade(config, "0084")
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(database) as connection:
        for name, schedule, origin_seq in (
            ("@pause:one", "@once", 3),
            ("daily", "0 9 * * *", None),
        ):
            connection.execute(
                "insert into scheduled_task (id, workspace_id, conversation_id, agent_id, name, "
                "schedule, prompt, description, next_run_at, origin_seq, paused, created_at, "
                "updated_at) values (?, ?, ?, ?, ?, ?, 'resume', '', ?, ?, 0, ?, ?)",
                (
                    str(uuid4()),
                    str(uuid4()),
                    str(uuid4()),
                    str(uuid4()),
                    name,
                    schedule,
                    now,
                    origin_seq,
                    now,
                    now,
                ),
            )

    command.upgrade(config, "0085")

    with sqlite3.connect(database) as connection:
        surviving = [row[0] for row in connection.execute("select name from scheduled_task")]
        columns = {row[1] for row in connection.execute("pragma table_info(scheduled_task)")}
        indexes = {row[1] for row in connection.execute("pragma index_list(scheduled_task)")}
    assert surviving == ["daily"]
    assert not {"origin_seq", "resume_turn_id"} & columns
    assert "scheduled_task_pause" not in indexes
    assert "scheduled_task_due" in indexes


def test_the_conversation_title_backfill_names_every_conversation_that_has_spoken(
    tmp_path: Path,
) -> None:
    """0086 gives every existing conversation the name its listing row already showed: the member's
    own words out of the first turn, unfenced — the ambient digest a channel surface wrapped them
    in is not what the conversation is about — and cut to the same bound. A conversation no turn
    has opened has nothing to be called and stays unnamed, which is what its row already said."""
    database_path = tmp_path / "conversation-title.db"
    url = f"sqlite+aiosqlite:///{database_path}"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0085")
    workspace_id, agent_id = uuid4(), uuid4()
    spoke, quiet = uuid4(), uuid4()
    now = datetime(2026, 8, 14, tzinfo=UTC).isoformat()
    marker = "0f1e2d3c"
    fenced = (
        "<ambient_context_"
        + marker
        + ">\na bystander said something\n</ambient_context_"
        + marker
        + ">\n<member_message_"
        + marker
        + ">\nroll the warehouse plan forward\n</member_message_"
        + marker
        + ">"
    )
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "insert into workspace (id, created_at, updated_at) values (?, ?, ?)",
            (workspace_id.hex, now, now),
        )
        connection.execute(
            "insert into agent (id, workspace_id, name, prompt, model, is_main, created_at, "
            "updated_at) values (?, ?, 'assistant', 'p', 'auto', 1, ?, ?)",
            (agent_id.hex, workspace_id.hex, now, now),
        )
        for index, conversation_id in enumerate((spoke, quiet)):
            connection.execute(
                "insert into conversation (id, workspace_id, agent_id, surface, queue_key, "
                "created_at, updated_at) values (?, ?, ?, 'slack', ?, ?, ?)",
                (conversation_id.hex, workspace_id.hex, agent_id.hex, f"c{index}", now, now),
            )
        for seq, inbound in ((1, fenced), (2, "and one more thing")):
            connection.execute(
                "insert into turn (id, workspace_id, conversation_id, agent_id, seq, status, "
                "inbound, created_at, updated_at) values (?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                (uuid4().hex, workspace_id.hex, spoke.hex, agent_id.hex, seq, inbound, now, now),
            )
    command.upgrade(config, "0086")
    with sqlite3.connect(database_path) as connection:
        titles = dict(connection.execute("select id, title from conversation").fetchall())

    assert titles[spoke.hex] == "roll the warehouse plan forward"
    assert titles[quiet.hex] is None
