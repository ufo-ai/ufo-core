import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from ufo.db import MIGRATIONS_DIR
from ufo.host.ext.loader import migration_locations

UNWRITTEN = "20260909204543"
REVISION = "20260909233938"
NOW = "2026-09-09 06:30:00+00:00"
CONTENT_ID_COLUMNS = {
    "source": {"id"},
    "page": {"id", "source_id"},
    "memory_item": {"created_from_page_id", "source_id"},
    "memory_source": {"source_id", "page_id"},
    "mem_page": {"page_id"},
}
CONTENT_ID_INDEXES = {
    "source": {"source_workspace_identity", "source_id"},
    "page": {"page_workspace_identity", "page_source_id", "page_id"},
    "memory_source": {"memory_source_item_page_id"},
    "mem_page": {"mem_page_page_id"},
}
PARTITIONED_ONLY_INDEXES = {"source_id", "page_id"}
OLD_CONSTRAINTS = {
    "source_old": {
        "source_pkey_old",
        "source_workspace_identity_old",
        "source_workspace_uid_old",
        "source_feed_handle_old",
        "source_workspace_id_fkey_old",
        "source_authority_fkey_old",
    },
    "page_old": {
        "page_pkey_old",
        "page_workspace_uid_old",
        "page_subject_old",
        "page_workspace_id_fkey_old",
        "page_source_id_fkey_old",
    },
}
OLD_INDEXES = {
    "source_old": {"source_authority_old", "source_due_old"},
    "page_old": {"page_feed_old", "page_source_old", "page_source_identity_old"},
}


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
        path = tmp_path / "content-ids-dropped.db"
        yield f"sqlite+aiosqlite:///{path}", f"sqlite:///{path}"
        return
    database = f"{source.database}_dropped_{uuid4().hex[:8]}"
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


def _seed(connection: sa.Connection, ids: dict[str, str], postgres: bool) -> None:
    true, false = ("true", "false") if postgres else ("1", "0")
    connection.execute(
        sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
        {"id": ids["workspace"], "now": NOW},
    )
    connection.execute(
        sa.text(
            "insert into connection (id, workspace_id, provider, account_id, host, shared, "
            f"created_at, updated_at) values (:id, :ws, 'folder', '', '', {true}, :now, :now)"
        ),
        {"id": ids["connection"], "ws": ids["workspace"], "now": NOW},
    )
    connection.execute(
        sa.text(
            "insert into source (uid, workspace_id, backend, config, feed_handle, connection_id, "
            "next_sync_at, created_at, updated_at) values "
            "(:uid, :ws, 'folder', '{}', '{}', :conn, :now, :now, :now)"
        ),
        {"uid": ids["source_uid"], "ws": ids["workspace"], "conn": ids["connection"], "now": NOW},
    )
    connection.execute(
        sa.text(
            "insert into page (uid, workspace_id, source_uid, source_identity, digest, body_ref, "
            "stream, title, subject, tombstone, created_at, updated_at) values "
            f"(:uid, :ws, :src_uid, 'notes/a.md', 'sha256:p', 'pages/p', 'notes', 'Page', "
            f"'shared', {false}, :now, :now)"
        ),
        {"uid": ids["page_uid"], "ws": ids["workspace"], "src_uid": ids["source_uid"], "now": NOW},
    )
    connection.execute(
        sa.text(
            "insert into memory_item (id, workspace_id, subject, body, item_class, memory_kind, "
            "confidence, created_from_page_uid, created_from_page_revision, source_uid, "
            "created_at, updated_at) values (:id, :ws, 'shared', 'a fact', 'fact', 'fact', 5, "
            ":page_uid, 1, :src_uid, :now, :now)"
        ),
        {
            "id": ids["item"],
            "ws": ids["workspace"],
            "page_uid": ids["page_uid"],
            "src_uid": ids["source_uid"],
            "now": NOW,
        },
    )
    connection.execute(
        sa.text(
            "insert into memory_source (workspace_id, memory_item_id, source_uid, page_uid, "
            "revision, created_at, updated_at) values "
            "(:ws, :item, :src_uid, :page_uid, 1, :now, :now)"
        ),
        {
            "ws": ids["workspace"],
            "item": ids["item"],
            "src_uid": ids["source_uid"],
            "page_uid": ids["page_uid"],
            "now": NOW,
        },
    )
    connection.execute(
        sa.text(
            "insert into mem_page (page_uid, workspace_id, subject, revision, created_at) "
            "values (:uid, :ws, 'shared', 1, :now)"
        ),
        {"uid": ids["page_uid"], "ws": ids["workspace"], "now": NOW},
    )


def _columns(inspector: sa.Inspector) -> dict[str, set[str]]:
    return {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in CONTENT_ID_COLUMNS
    }


def _indexes(inspector: sa.Inspector) -> dict[str, set[str]]:
    return {
        table: {str(index["name"]) for index in inspector.get_indexes(table)}
        | {str(unique["name"]) for unique in inspector.get_unique_constraints(table)}
        for table in CONTENT_ID_INDEXES
    }


def _old_shape(connection: sa.Connection) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    constraints = {
        table: set(
            connection.execute(
                sa.text(
                    "select conname from pg_constraint where conrelid = cast(:table as regclass)"
                ),
                {"table": table},
            ).scalars()
        )
        for table in OLD_CONSTRAINTS
    }
    indexes = {
        table: set(
            connection.execute(
                sa.text(
                    "select indexname from pg_indexes where tablename = :table "
                    "and indexname not in (select conname from pg_constraint)"
                ),
                {"table": table},
            ).scalars()
        )
        for table in OLD_INDEXES
    }
    return constraints, indexes


def test_content_ids_and_the_frozen_tables_go_and_the_downgrade_rebuilds_them(
    migration_urls: tuple[str, str],
) -> None:
    """Rows the replaced release landed by uid alone survive the drop with their keys, cascades and
    the revision trigger intact; the downgrade restores the release being replaced's shape — the
    columns nullable, their indexes, and on Postgres the pre-swap tables empty under the names the
    swap's downgrade strips — and the revision lands again over it."""
    migration_url, sync_url = migration_urls
    postgres = migration_url.startswith("postgresql")
    config = _alembic(migration_url)
    command.upgrade(config, UNWRITTEN)
    command.upgrade(config, "memory_0020")
    ids = {
        name: str(uuid4()) for name in ("workspace", "connection", "source_uid", "page_uid", "item")
    }
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        _seed(connection, ids, postgres)
        connection.commit()

    command.upgrade(config, "memory_0021")
    with engine.connect() as connection:
        if not postgres:
            connection.execute(sa.text("pragma foreign_keys = on"))
        inspector = sa.inspect(connection)
        dropped_columns = _columns(inspector)
        dropped_indexes = _indexes(inspector)
        tables_left = set(inspector.get_table_names())
        counts = connection.execute(
            sa.text(
                "select (select count(*) from source), (select count(*) from page), "
                "(select count(*) from memory_source), (select count(*) from mem_page)"
            )
        ).one()
        late_uid = str(uuid4())
        connection.execute(
            sa.text(
                "insert into page (uid, workspace_id, source_uid, source_identity, digest, "
                "body_ref, stream, title, subject, tombstone, created_at, updated_at) values "
                f"(:uid, :ws, :src_uid, 'notes/b.md', 'sha256:q', 'pages/q', 'notes', 'Late', "
                f"'shared', {'false' if postgres else '0'}, :now, :now)"
            ),
            {"uid": late_uid, "ws": ids["workspace"], "src_uid": ids["source_uid"], "now": NOW},
        )
        revisions = (
            connection.execute(sa.text("select revision from page order by revision"))
            .scalars()
            .all()
        )
        connection.execute(sa.text("delete from page where uid = :uid"), {"uid": ids["page_uid"]})
        connection.commit()
        mirrors_left = connection.execute(sa.text("select count(*) from mem_page")).scalar_one()
        connection.execute(sa.text("delete from page where uid = :uid"), {"uid": late_uid})
        connection.commit()

    command.downgrade(config, UNWRITTEN)
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        restored_columns = _columns(inspector)
        restored_indexes = _indexes(inspector)
        nullable = {
            (table, column["name"]): column["nullable"]
            for table in CONTENT_ID_COLUMNS
            for column in inspector.get_columns(table)
            if column["name"] in CONTENT_ID_COLUMNS[table]
        }
        if postgres:
            old_constraints, old_indexes = _old_shape(connection)
            old_rows = connection.execute(
                sa.text("select (select count(*) from source_old), (select count(*) from page_old)")
            ).one()
    command.upgrade(config, "memory_0021")
    with engine.connect() as connection:
        inspector = sa.inspect(connection)
        dropped_again = _columns(inspector)
    engine.dispose()

    for table, columns in CONTENT_ID_COLUMNS.items():
        assert not dropped_columns[table] & columns, table
        assert not dropped_again[table] & columns, table
        assert restored_columns[table] >= columns, table
    for table, indexes in CONTENT_ID_INDEXES.items():
        assert not dropped_indexes[table] & indexes, table
        assert restored_indexes[table] >= indexes - (
            set() if postgres else PARTITIONED_ONLY_INDEXES
        )
    assert all(nullable.values())
    assert not tables_left & {"source_old", "page_old"}
    assert tuple(counts) == (1, 1, 1, 1)
    assert len(revisions) == 2 and revisions[1] > revisions[0] >= 1
    assert mirrors_left == 0
    if postgres:
        assert old_constraints == OLD_CONSTRAINTS
        assert old_indexes == OLD_INDEXES
        assert tuple(old_rows) == (0, 0)
