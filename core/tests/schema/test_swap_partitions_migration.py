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

SHADOWED = "20260909193737"
REVISION = "20260909195911"
NOW = "2026-09-09 06:30:00+00:00"


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
        path = tmp_path / "swap-partitions.db"
        yield f"sqlite+aiosqlite:///{path}", f"sqlite:///{path}"
        return
    database = f"{source.database}_swap_{uuid4().hex[:8]}"
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
    true = "true" if postgres else "1"
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
            "insert into source (id, uid, workspace_id, backend, config, feed_handle, "
            "connection_id, next_sync_at, created_at, updated_at) values "
            "(:id, :uid, :ws, 'folder', '{}', '{}', :conn, :now, :now, :now)"
        ),
        {
            "id": ids["source"],
            "uid": ids["source_uid"],
            "ws": ids["workspace"],
            "conn": ids["connection"],
            "now": NOW,
        },
    )
    connection.execute(
        sa.text(
            "insert into page (id, uid, workspace_id, source_id, digest, body_ref, stream, "
            "title, subject, tombstone, created_at, updated_at) values "
            f"(:id, :uid, :ws, :src, 'sha256:p', 'pages/p', 'notes', 'Page', 'shared', "
            f"{'false' if postgres else '0'}, :now, :now)"
        ),
        {
            "id": ids["page"],
            "uid": ids["page_uid"],
            "ws": ids["workspace"],
            "src": ids["source"],
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


def test_cutover_keeps_rows_keys_triggers_and_inbound_cascades(
    migration_urls: tuple[str, str],
) -> None:
    """The row landed before the cutover is still there under the new key; a page landed after it
    takes a revision from the recreated trigger; the mirror row follows its page through the
    re-pointed foreign key; and on Postgres the live names now belong to the partitioned tables
    while the old ones stand aside, with nothing left mirroring into them."""
    migration_url, sync_url = migration_urls
    postgres = migration_url.startswith("postgresql")
    config = _alembic(migration_url)
    command.upgrade(config, SHADOWED)
    command.upgrade(config, "memory_0019")
    ids = {
        name: str(uuid4())
        for name in ("workspace", "connection", "source", "source_uid", "page", "page_uid")
    }
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        _seed(connection, ids, postgres)
        connection.commit()
    command.upgrade(config, REVISION)

    late = {"page": str(uuid4()), "uid": str(uuid4())}
    with engine.connect() as connection:
        if not postgres:
            connection.execute(sa.text("pragma foreign_keys = on"))
        connection.execute(
            sa.text(
                "insert into page (id, uid, workspace_id, source_id, source_uid, digest, body_ref, "
                "stream, title, subject, tombstone, created_at, updated_at) values "
                f"(:id, :uid, :ws, :src, :src_uid, 'sha256:q', 'pages/q', 'notes', 'Late', "
                f"'shared', {'false' if postgres else '0'}, :now, :now)"
            ),
            {
                "id": late["page"],
                "uid": late["uid"],
                "ws": ids["workspace"],
                "src": ids["source"],
                "src_uid": ids["source_uid"],
                "now": NOW,
            },
        )
        connection.execute(
            sa.text(
                "insert into mem_page (page_uid, workspace_id, subject, revision, created_at) "
                "values (:uid, :ws, 'shared', 1, :now)"
            ),
            {"uid": late["uid"], "ws": ids["workspace"], "now": NOW},
        )
        connection.commit()
        revisions = {
            str(r.uid).replace("-", ""): r.revision
            for r in connection.execute(sa.text("select uid, revision from page"))
        }
        connection.execute(sa.text("delete from page where id = :id"), {"id": late["page"]})
        connection.commit()
        mirrors = {
            str(r).replace("-", "")
            for r in connection.execute(sa.text("select page_uid from mem_page")).scalars()
        }
        if postgres:
            kinds = dict(
                connection.execute(
                    sa.text(
                        "select relname, relkind::text from pg_class "
                        "where relname in ('source', 'page', 'source_old', 'page_old')"
                    )
                ).all()
            )
            old_rows = connection.execute(
                sa.text("select (select count(*) from page_old), (select count(*) from source_old)")
            ).one()
            mirrors_left = connection.execute(
                sa.text("select count(*) from pg_trigger where tgname like '%_mirror'")
            ).scalar_one()
            pkey = connection.execute(
                sa.text(
                    "select pg_get_constraintdef(oid) from pg_constraint "
                    "where conrelid = 'page'::regclass and conname = 'page_pkey'"
                )
            ).scalar_one()
            mem_fk = connection.execute(
                sa.text(
                    "select confrelid::regclass::text from pg_constraint "
                    "where conname = 'mem_page_page_uid_fkey'"
                )
            ).scalar_one()
            with pytest.raises(sa.exc.ProgrammingError):
                connection.execute(sa.text("select 1 from pg_constraint where conname = 'x'"))
                connection.execute(
                    sa.text(
                        "insert into source (id, uid, workspace_id, backend, config, feed_handle, "
                        "connection_id, next_sync_at, created_at, updated_at) values "
                        "(:id, :uid, :ws, 'folder', '{}', 'dup', :conn, :now, :now, :now) "
                        "on conflict (id) do nothing"
                    ),
                    {
                        "id": str(uuid4()),
                        "uid": str(uuid4()),
                        "ws": ids["workspace"],
                        "conn": ids["connection"],
                        "now": NOW,
                    },
                )
            connection.rollback()

    command.downgrade(config, SHADOWED)
    with engine.connect() as connection:
        back = {
            str(r.uid).replace("-", ""): r.title
            for r in connection.execute(sa.text("select uid, title from page"))
        }
        if postgres:
            shadow_rows = connection.execute(sa.text("select count(*) from page_new")).scalar_one()
            mirrors_back = connection.execute(
                sa.text("select count(*) from pg_trigger where tgname like '%_mirror'")
            ).scalar_one()
    engine.dispose()

    early_uid = ids["page_uid"].replace("-", "")
    assert back == {early_uid: "Page"}
    if postgres:
        assert (shadow_rows, mirrors_back) == (1, 2)
    assert set(revisions) == {early_uid, late["uid"].replace("-", "")}
    assert revisions[late["uid"].replace("-", "")] > revisions[early_uid] >= 1
    assert mirrors == {early_uid}
    if postgres:
        assert kinds == {"source": "p", "page": "p", "source_old": "r", "page_old": "r"}
        assert tuple(old_rows) == (1, 1)
        assert mirrors_left == 0
        assert pkey == "PRIMARY KEY (workspace_id, uid)"
        assert mem_fk == "page"
