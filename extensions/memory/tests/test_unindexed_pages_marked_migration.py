import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR
from ufo.host.ext.loader import migration_locations

CORE_HEAD = "20260910114436"
BEFORE = "memory_0021"
REVISION = "memory_0022"
MARKER_KEY = "unindexed_pages_drain"
NOW = "2026-09-10 12:00:00+00:00"


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


def _seed_workspace(connection: sa.Connection, indexed: bool, mirrored: bool) -> UUID:
    workspace_id, connection_id, source_uid, page_uid = uuid4(), uuid4(), uuid4(), uuid4()
    connection.execute(
        sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
        {"id": workspace_id.hex, "now": NOW},
    )
    connection.execute(
        sa.text(
            "insert into connection (id, workspace_id, provider, account_id, host, shared, "
            "created_at, updated_at) values (:id, :ws, 'github', '', '', 1, :now, :now)"
        ),
        {"id": connection_id.hex, "ws": workspace_id.hex, "now": NOW},
    )
    connection.execute(
        sa.text(
            "insert into source (uid, workspace_id, backend, config, feed_handle, connection_id, "
            "next_sync_at, created_at, updated_at) values "
            "(:uid, :ws, 'github', '{}', '{}', :conn, :now, :now, :now)"
        ),
        {"uid": source_uid.hex, "ws": workspace_id.hex, "conn": connection_id.hex, "now": NOW},
    )
    connection.execute(
        sa.text(
            "insert into page (uid, workspace_id, source_uid, digest, body_ref, stream, title, "
            "subject, tombstone, indexed, created_at, updated_at) values "
            "(:uid, :ws, :src, 'sha256:p', 'pages/p', 'workflow_runs', 'Page', 'shared', 0, "
            ":indexed, :now, :now)"
        ),
        {
            "uid": page_uid.hex,
            "ws": workspace_id.hex,
            "src": source_uid.hex,
            "indexed": int(indexed),
            "now": NOW,
        },
    )
    if mirrored:
        connection.execute(
            sa.text(
                "insert into mem_page (page_uid, workspace_id, subject, revision, created_at) "
                "values (:uid, :ws, 'shared', 1, :now)"
            ),
            {"uid": page_uid.hex, "ws": workspace_id.hex, "now": NOW},
        )
    return workspace_id


def _markers(engine: sa.Engine) -> dict[str, object]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "select workspace_id, value from ext_store "
                "where extension = 'memory' and key = :key"
            ),
            {"key": MARKER_KEY},
        ).all()
    return {row[0]: json.loads(row[1]) for row in rows}


def test_only_workspaces_mirroring_an_unindexed_page_are_marked_for_the_drain(
    tmp_path: Path,
) -> None:
    """A marker names a workspace with chunks to drain: one whose `mem_page` mirrors a page that
    does not reach memory. A workspace whose mirrored pages all reach memory, and one whose
    unindexed page was never mirrored, hold nothing to drain and get no marker; the downgrade takes
    the markers back."""
    database = tmp_path / "unindexed-marked.db"
    config = _config(database)
    command.upgrade(config, CORE_HEAD)
    command.upgrade(config, BEFORE)
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        marked = _seed_workspace(connection, indexed=False, mirrored=True)
        _seed_workspace(connection, indexed=True, mirrored=True)
        _seed_workspace(connection, indexed=False, mirrored=False)
        connection.commit()

    command.upgrade(config, REVISION)

    assert _markers(engine) == {marked.hex: {"after": None}}

    command.downgrade(config, BEFORE)

    assert _markers(engine) == {}


def test_a_second_run_over_a_marker_already_written_changes_nothing(tmp_path: Path) -> None:
    """The migrate Job can die between the marker insert and the version stamp, and the next Job
    runs the revision again over the row it left. The insert lands on the primary key and does
    nothing, so the run keeps the one marker — the drain's cursor included — and raises nothing."""
    database = tmp_path / "unindexed-marked-twice.db"
    config = _config(database)
    command.upgrade(config, CORE_HEAD)
    command.upgrade(config, BEFORE)
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        marked = _seed_workspace(connection, indexed=False, mirrored=True)
        connection.execute(
            sa.text(
                "insert into ext_store (workspace_id, extension, key, value, created_at, "
                "updated_at) values (:ws, 'memory', :key, :value, :now, :now)"
            ),
            {
                "ws": marked.hex,
                "key": MARKER_KEY,
                "value": json.dumps({"after": marked.hex}),
                "now": NOW,
            },
        )
        connection.commit()

    command.upgrade(config, REVISION)

    assert _markers(engine) == {marked.hex: {"after": marked.hex}}
