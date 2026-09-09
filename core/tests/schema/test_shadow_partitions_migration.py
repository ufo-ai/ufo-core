import os
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from ufo.db import MIGRATIONS_DIR
from ufo.host.ext.loader import migration_locations

BEFORE = "20260909062725"
REVISION = "20260909193737"
NOW = "2026-09-09 06:30:00+00:00"
PARTITIONS = 16


def _alembic(migration_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", migration_url)
    return config


@pytest.fixture
def migration_urls(database_url: str, tmp_path: Path) -> Iterator[tuple[str, str]]:
    source = make_url(database_url)
    if source.get_backend_name() == "sqlite":
        path = tmp_path / "shadow-partitions.db"
        yield f"sqlite+aiosqlite:///{path}", f"sqlite:///{path}"
        return
    database = f"{source.database}_shadow_{uuid4().hex[:8]}"
    admin = sa.create_engine(
        source.set(drivername="postgresql+psycopg", database="ufo"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as connection:
        connection.exec_driver_sql(f'create database "{database}"')
    try:
        yield (
            source.set(database=database).render_as_string(hide_password=False),
            source.set(drivername="postgresql+psycopg", database=database).render_as_string(
                hide_password=False
            ),
        )
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'drop database "{database}" with (force)')
        admin.dispose()


def _seed_feed(connection: sa.Connection, workspace: str, connection_id: str) -> None:
    connection.execute(
        sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
        {"id": workspace, "now": NOW},
    )
    connection.execute(
        sa.text(
            "insert into connection (id, workspace_id, provider, account_id, host, shared, "
            "created_at, updated_at) values (:id, :ws, 'folder', :acct, '', true, :now, :now)"
        ),
        {"id": connection_id, "ws": workspace, "acct": connection_id[:8], "now": NOW},
    )


def _seed_source(connection: sa.Connection, workspace: str, conn: str, key: str, uid: str) -> None:
    connection.execute(
        sa.text(
            "insert into source (id, uid, workspace_id, backend, config, feed_handle, "
            "connection_id, next_sync_at, created_at, updated_at) values "
            "(:id, :uid, :ws, 'folder', '{}', :handle, :conn, :now, :now, :now)"
        ),
        {"id": key, "uid": uid, "ws": workspace, "handle": key[:8], "conn": conn, "now": NOW},
    )


def _seed_page(
    connection: sa.Connection, workspace: str, source_key: str, key: str, uid: str
) -> None:
    connection.execute(
        sa.text(
            "insert into page (id, uid, workspace_id, source_id, digest, body_ref, stream, "
            "title, subject, tombstone, created_at, updated_at) values "
            "(:id, :uid, :ws, :src, 'sha256:p', 'pages/p', 'notes', 'Page', 'shared', false, "
            ":now, :now)"
        ),
        {"id": key, "uid": uid, "ws": workspace, "src": source_key, "now": NOW},
    )


def test_page_records_its_sources_uid(migration_urls: tuple[str, str]) -> None:
    migration_url, sync_url = migration_urls
    config = _alembic(migration_url)
    command.upgrade(config, BEFORE)
    workspace, conn, source_key, source_uid, page_key, page_uid = (str(uuid4()) for _ in range(6))
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        _seed_feed(connection, workspace, conn)
        _seed_source(connection, workspace, conn, source_key, source_uid)
        _seed_page(connection, workspace, source_key, page_key, page_uid)
        connection.commit()
    command.upgrade(config, REVISION)
    with engine.connect() as connection:
        recorded = connection.execute(sa.text("select source_uid from page")).scalar_one()
    engine.dispose()
    assert str(recorded).replace("-", "") == source_uid.replace("-", "")


def test_shadows_are_partitioned_mirrored_and_fenced(migration_urls: tuple[str, str]) -> None:
    """A row seeded before the revision is backfilled; one written after it is mirrored by the
    trigger, an update follows, and deleting the source cascades through the shadow's own key —
    on sixteen hash partitions under the live table's row-security policy."""
    migration_url, sync_url = migration_urls
    if not migration_url.startswith("postgresql"):
        pytest.skip("partitioning is a Postgres shape")
    config = _alembic(migration_url)
    command.upgrade(config, BEFORE)
    workspace, conn, early_key, early_uid, page_key, page_uid = (str(uuid4()) for _ in range(6))
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        _seed_feed(connection, workspace, conn)
        _seed_source(connection, workspace, conn, early_key, early_uid)
        _seed_page(connection, workspace, early_key, page_key, page_uid)
        connection.execute(
            sa.text(
                "alter table page enable row level security; "
                "create policy ufo_workspace_rls on page "
                "using (workspace_id = current_setting('app.workspace_id')::uuid) "
                "with check (workspace_id = current_setting('app.workspace_id')::uuid)"
            )
        )
        connection.commit()
    command.upgrade(config, REVISION)

    late_key, late_uid, late_page_key, late_page_uid = (str(uuid4()) for _ in range(4))
    with engine.connect() as connection:
        _seed_source(connection, workspace, conn, late_key, late_uid)
        _seed_page(connection, workspace, late_key, late_page_key, late_page_uid)
        connection.execute(
            sa.text("update page set title = 'Renamed' where id = :id"), {"id": late_page_key}
        )
        connection.commit()
        partitions = connection.execute(
            sa.text("select count(*) from pg_inherits where inhparent = 'page_new'::regclass")
        ).scalar_one()
        mirrored = {
            UUID(str(r.uid)): (UUID(str(r.source_uid)), r.title, r.revision)
            for r in connection.execute(
                sa.text("select uid, source_uid, title, revision from page_new")
            )
        }
        live = {
            UUID(str(r.uid)): (UUID(str(r.source_uid)), r.title, r.revision)
            for r in connection.execute(
                sa.text(
                    "select p.uid, coalesce(p.source_uid, s.uid) as source_uid, p.title, "
                    "p.revision from page p join source s on s.id = p.source_id"
                )
            )
        }
        policy = connection.execute(
            sa.text(
                "select relrowsecurity, (select count(*) from pg_policies "
                "where tablename = 'page_new' and policyname = 'ufo_workspace_rls') "
                "from pg_class where relname = 'page_new'"
            )
        ).one()
        connection.execute(sa.text("delete from source where id = :id"), {"id": late_key})
        connection.commit()
        after_delete = connection.execute(sa.text("select count(*) from page_new")).scalar_one()
        pkey = connection.execute(
            sa.text(
                "select pg_get_constraintdef(oid) from pg_constraint "
                "where conrelid = 'page_new'::regclass and contype = 'p'"
            )
        ).scalar_one()
    engine.dispose()

    assert partitions == PARTITIONS
    assert set(mirrored) == {UUID(page_uid), UUID(late_page_uid)} and mirrored == live
    assert mirrored[UUID(late_page_uid)][1:] == ("Renamed", live[UUID(late_page_uid)][2])
    assert tuple(policy) == (True, 1)
    assert after_delete == 1
    assert pkey == "PRIMARY KEY (workspace_id, uid)"
