import os
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR
from ufo.host.ext.loader import migration_locations

BEFORE = "20260909195911"
REVISION = "20260909204543"
NOW = "2026-09-09 06:30:00+00:00"


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _seed(connection: sa.Connection, identity: str | None) -> dict[str, str]:
    ids = {k: uuid4().hex for k in ("ws", "conn", "src", "src_uid", "page", "page_uid")}
    connection.execute(
        sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
        {"id": ids["ws"], "now": NOW},
    )
    connection.execute(
        sa.text(
            "insert into connection (id, workspace_id, provider, account_id, host, shared, "
            "created_at, updated_at) values (:id, :ws, 'folder', '', '', 1, :now, :now)"
        ),
        {"id": ids["conn"], "ws": ids["ws"], "now": NOW},
    )
    connection.execute(
        sa.text(
            "insert into source (id, uid, workspace_id, backend, config, feed_handle, "
            "connection_id, next_sync_at, created_at, updated_at) values "
            "(:id, :uid, :ws, 'folder', '{}', '{}', :conn, :now, :now, :now)"
        ),
        {"id": ids["src"], "uid": ids["src_uid"], "ws": ids["ws"], "conn": ids["conn"], "now": NOW},
    )
    connection.execute(
        sa.text(
            "insert into page (id, uid, workspace_id, source_id, source_uid, source_identity, "
            "digest, body_ref, stream, title, subject, tombstone, created_at, updated_at) values "
            "(:id, :uid, :ws, :src, :src_uid, :identity, 'sha256:p', 'pages/p', 'notes', 'Page', "
            "'shared', 0, :now, :now)"
        ),
        {
            "id": ids["page"],
            "uid": ids["page_uid"],
            "ws": ids["ws"],
            "src": ids["src"],
            "src_uid": ids["src_uid"],
            "identity": identity,
            "now": NOW,
        },
    )
    connection.commit()
    return ids


def test_content_ids_become_optional_and_a_new_row_needs_none(tmp_path: Path) -> None:
    database = tmp_path / "content-ids.db"
    config = _config(database)
    command.upgrade(config, BEFORE)
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        ids = _seed(connection, "notes/a.md")
    command.upgrade(config, REVISION)
    with engine.connect() as connection:
        connection.execute(sa.text("pragma foreign_keys = on"))
        connection.execute(
            sa.text(
                "insert into source (uid, workspace_id, backend, config, feed_handle, "
                "connection_id, next_sync_at, created_at, updated_at) values "
                "(:uid, :ws, 'folder', '{}', 'second', :conn, :now, :now, :now)"
            ),
            {"uid": uuid4().hex, "ws": ids["ws"], "conn": ids["conn"], "now": NOW},
        )
        connection.execute(
            sa.text(
                "insert into page (uid, workspace_id, source_uid, source_identity, digest, "
                "body_ref, stream, title, subject, tombstone, created_at, updated_at) values "
                "(:uid, :ws, :src_uid, 'notes/b.md', 'sha256:q', 'pages/q', 'notes', 'B', "
                "'shared', 0, :now, :now)"
            ),
            {"uid": uuid4().hex, "ws": ids["ws"], "src_uid": ids["src_uid"], "now": NOW},
        )
        connection.commit()
        revisions = (
            connection.execute(sa.text("select revision from page order by revision"))
            .scalars()
            .all()
        )
    engine.dispose()
    assert len(revisions) == 2 and revisions[1] > revisions[0]
