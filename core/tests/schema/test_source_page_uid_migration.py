import json
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from ufo.db import MIGRATIONS_DIR

BEFORE = "20260909042656"
REVISION = "20260909062725"
NOW = "2026-09-09 06:00:00+00:00"


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def test_source_and_page_gain_a_distinct_uid_unique_with_the_workspace(tmp_path: Path) -> None:
    """Rows written before the column exist get a uid the backfill mints; every row has one, no two
    share one, and a second row claiming a workspace's existing uid is refused."""
    database = tmp_path / "uid.db"
    config = _config(database)
    command.upgrade(config, BEFORE)
    workspace_id, connection_id = uuid4(), uuid4()
    source_ids, page_ids = [uuid4() for _ in range(2)], [uuid4() for _ in range(3)]
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
            {"id": workspace_id.hex, "now": NOW},
        )
        connection.execute(
            sa.text(
                "insert into connection (id, workspace_id, provider, account_id, host, shared, "
                "created_at, updated_at) values (:id, :ws, 'folder', '', '', 1, :now, :now)"
            ),
            {"id": connection_id.hex, "ws": workspace_id.hex, "now": NOW},
        )
        for n, source_id in enumerate(source_ids):
            connection.execute(
                sa.text(
                    "insert into source (id, workspace_id, backend, config, feed_handle, "
                    "connection_id, next_sync_at, created_at, updated_at) values "
                    "(:id, :ws, 'folder', :config, :handle, :conn, :now, :now, :now)"
                ),
                {
                    "id": source_id.hex,
                    "ws": workspace_id.hex,
                    "config": json.dumps({"root": f"/{n}"}),
                    "handle": json.dumps({"root": f"/{n}"}),
                    "conn": connection_id.hex,
                    "now": NOW,
                },
            )
        for n, page_id in enumerate(page_ids):
            connection.execute(
                sa.text(
                    "insert into page (id, workspace_id, source_id, digest, body_ref, stream, "
                    "title, "
                    "subject, tombstone, created_at, updated_at) values "
                    "(:id, :ws, :src, :digest, 'pages/p', 'notes', 'Page', 'shared', 0, :now, :now)"
                ),
                {
                    "id": page_id.hex,
                    "ws": workspace_id.hex,
                    "src": source_ids[n % 2].hex,
                    "digest": f"sha256:{n}",
                    "now": NOW,
                },
            )
        connection.commit()

    command.upgrade(config, REVISION)

    with engine.connect() as connection:
        source_uids = [r[0] for r in connection.execute(sa.text("select uid from source"))]
        page_uids = [r[0] for r in connection.execute(sa.text("select uid from page"))]
    assert len(source_uids) == 2 and all(source_uids) and len(set(source_uids)) == 2
    assert len(page_uids) == 3 and all(page_uids) and len(set(page_uids)) == 3

    with engine.connect() as connection, pytest.raises(IntegrityError):
        connection.execute(
            sa.text(
                "insert into page (id, uid, workspace_id, source_id, digest, body_ref, stream, "
                "title, subject, tombstone, created_at, updated_at) values "
                "(:id, :uid, :ws, :src, 'sha256:dup', 'pages/p', 'notes', 'Page', 'shared', 0, "
                ":now, :now)"
            ),
            {
                "id": uuid4().hex,
                "uid": page_uids[0],
                "ws": workspace_id.hex,
                "src": source_ids[0].hex,
                "now": NOW,
            },
        )
    engine.dispose()
