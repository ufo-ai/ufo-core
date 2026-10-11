import asyncio
import gc
import hashlib
import os
import shutil
import socket
import sqlite3
import threading
import warnings
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager, suppress
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
from alembic.config import Config
from alembic.script import ScriptDirectory
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
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
from ufo.runtime.sources.sync import feed_handle_for
from ufo.runtime.workspace import ws
from ufo.schema import tables


class _UndefinedColumn(Exception):
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
    pytest process and again by every per-test fixture that migrates a database of its own."""
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
    """The key is a digest of every migration file that would run, so an edited or added
    migration is never served the schema it replaced."""
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
        await connection.execute(
            sa.text(
                "insert into chunk (workspace_id, chunk_digest, owner_kind, owner_id, "
                "subject, ordinal, text) values (:workspace_id, 'sha256:probe', 'memory', "
                ":owner, 'shared', 0, 'probe row')"
            ).bindparams(sa.bindparam("workspace_id", type_=sa.Uuid)),
            {"workspace_id": workspace_id, "owner": str(uuid4())},
        )
        if sqlite:
            await connection.execute(
                sa.text(
                    "insert into chunk_fts (workspace_id, chunk_digest, text) "
                    "values (:workspace_id, 'sha256:probe', 'probe row')"
                ).bindparams(sa.bindparam("workspace_id", type_=sa.Uuid)),
                {"workspace_id": workspace_id},
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
                    "insert into chunk_fts (workspace_id, chunk_digest, text) "
                    "values (:workspace_id, 'sha256:after', 'still writable')"
                ).bindparams(sa.bindparam("workspace_id", type_=sa.Uuid)),
                {"workspace_id": workspace_id},
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
    written and not yet committed blocked the wipe and had its rows removed once it committed."""
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
    """A cycle has no child-first order, and every order a wipe could pick trips one of its keys."""
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
    """`delete` leaves a sequence where `truncate` restarted it."""
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
    those revisions reach."""
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
    core_head = _core_migration_head()
    core_revisions = _reached({core_head})
    for head in heads:
        if head == core_head:
            continue
        revision = scripts.get_revision(head)
        assert revision.down_revision is None
        assert revision.dependencies in core_revisions
    assert {
        core_head,
        "index_default_0003",
        "research_0001",
        "20260925191145",
        "20260924160318",
        "scheduled_tasks_0004",
        "sources_0011",
        "monitors_0004",
        "skill_create_0004",
        "coding_0004",
        "sites_0008",
        "report_digest_0002",
        "workspace_credential_slot_0001",
    } <= set(heads)
    assert len(heads) == 13


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


def test_migrate_command_brings_the_schema_to_head(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    """The boot check the TCP-probed deployments rest on."""
    init_db(REFUSED_DSN)
    try:
        with pytest.raises(ConnectionRefusedError):
            await verify_db_reachable()
    finally:
        await dispose_db()


async def test_verify_db_reachable_checks_the_owner_url_too(db: None) -> None:
    init_owner_db(REFUSED_DSN)
    with pytest.raises(ConnectionRefusedError):
        await verify_db_reachable()


async def test_verify_db_reachable_requires_init() -> None:
    with pytest.raises(RuntimeError, match="db not initialized"):
        await verify_db_reachable()


async def test_verify_db_reachable_publishes_no_engine(db: None) -> None:
    before = (set(ufo.db._APP.engines), set(ufo.db._OWNER.engines))
    await verify_db_reachable()
    assert (set(ufo.db._APP.engines), set(ufo.db._OWNER.engines)) == before


async def test_workspace_tx_requires_init() -> None:
    with pytest.raises(RuntimeError):
        async with workspace_tx():
            pass


async def test_a_transaction_that_never_opens_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The connect that never lands is invisible to the database — it never receives it — so this
    count is the only place the failure exists."""
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
    back out of SQLAlchemy as a database error."""

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
    o11y.emit_metric("db_tx_unavailable_total", path="workspace", error_class="TimeoutError")
    o11y.emit_metric("db_pool_exhausted_total", path="workspace")
    o11y.emit_histogram("db_tx_acquire_ms", 1, path="workspace")


async def test_a_saturated_pool_is_counted_apart_from_a_lost_dial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    """THE pooling safety invariant: `set_config(..., true)` is `SET LOCAL`, so a pooled
    connection handed to the next checkout carries no residual workspace GUC."""
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
    """What keeps a throwaway boot loop from abandoning a live connection."""
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
    beside the test's own loop."""
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
    ever finish."""
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
    def is_closed(self) -> bool:
        return False

    def call_soon_threadsafe(self, *_: object) -> None:
        raise RuntimeError("Event loop is closed")


async def test_a_loop_that_closes_mid_teardown_neither_raises_nor_holds_the_rest(
    db: None,
) -> None:
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
    """The url is half the registry key, and this is what fails if it leaves."""
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
    """`ufoctl ingress`, `ufoctl proxy`, and every one-shot verb open one URL, never an owner
    one."""
    await _touch()
    mine = _current_engine()
    async with owner_tx() as connection:
        await connection.execute(sa.text("select 1"))
    loop = asyncio.get_running_loop()
    assert not any(held is loop for held, _ in ufo.db._OWNER.engines)
    assert [key for key in ufo.db._APP.engines if key[0] is loop] == [(loop, ufo.db._app_url)]
    assert _current_engine() is mine


async def test_an_owner_url_gets_its_own_smaller_pool(db: None, database_url: str) -> None:
    """On the fleet `serve` sets both URLs, and then the owner pool is a second ceiling on the
    same instance."""
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
    whole of #834 — so the dial a replacement checkout drives is bounded next to the connect."""
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
    the scheme), and `serve` opens asyncpg."""
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
    """A migrated sqlite file is left in the mode its engines already use, and holds all its
    rows."""
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
    """The owner DSN arrives from a secret store in libpq form."""
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
