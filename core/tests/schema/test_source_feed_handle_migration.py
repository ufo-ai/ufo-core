import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError

from ufo.db import MIGRATIONS_DIR

BEFORE = "20260907150257"
REVISION = "20260909042656"
NOW = "2026-09-09 04:00:00+00:00"


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _handle(config: dict[str, object], excluded: frozenset[str]) -> str:
    return json.dumps({k: v for k, v in config.items() if k not in excluded}, sort_keys=True)


def _row_id(workspace_id, backend: str, handle: str, connection_id) -> object:
    return uuid5(
        NAMESPACE_URL, f"{workspace_id}/source/{backend}/{handle}/connection/{connection_id}"
    )


def test_feed_handle_backfills_the_string_each_row_was_hashed_from(tmp_path: Path) -> None:
    """Two writers hashed with two exclusion sets: `register_source` drops the model's non-identity
    fields, the `[[sources]]` boot path drops none. The backfill stores, per row, the one candidate
    that hashes back to its own id — and a row it cannot reproduce stops the revision."""
    database = tmp_path / "feed-handle.db"
    config = _config(database)
    command.upgrade(config, BEFORE)

    workspace_id, connection_id = uuid4(), uuid4()
    windowed = {"root": "/shared", "backfill_days": 30}
    by_model = _row_id(
        workspace_id, "folder", _handle(windowed, frozenset({"backfill_days"})), connection_id
    )
    by_boot = _row_id(workspace_id, "folder", _handle(windowed, frozenset()), connection_id)
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
        for row_id in (by_model, by_boot):
            connection.execute(
                sa.text(
                    "insert into source (id, workspace_id, backend, config, connection_id, "
                    "next_sync_at, created_at, updated_at) values "
                    "(:id, :ws, 'folder', :config, :conn, :now, :now, :now)"
                ),
                {
                    "id": row_id.hex,
                    "ws": workspace_id.hex,
                    "config": json.dumps(windowed),
                    "conn": connection_id.hex,
                    "now": NOW,
                },
            )
        connection.commit()

    command.upgrade(config, REVISION)

    with engine.connect() as connection:
        handles = {
            row.id: row.feed_handle
            for row in connection.execute(sa.text("select id, feed_handle from source"))
        }
    assert handles == {
        by_model.hex: _handle(windowed, frozenset({"backfill_days"})),
        by_boot.hex: _handle(windowed, frozenset()),
    }

    with engine.connect() as connection, pytest.raises(IntegrityError):
        connection.execute(
            sa.text(
                "insert into source (id, workspace_id, backend, config, feed_handle, "
                "connection_id, next_sync_at, created_at, updated_at) values "
                "(:id, :ws, 'folder', :config, :handle, :conn, :now, :now, :now)"
            ),
            {
                "id": uuid4().hex,
                "ws": workspace_id.hex,
                "config": json.dumps(windowed),
                "handle": _handle(windowed, frozenset({"backfill_days"})),
                "conn": connection_id.hex,
                "now": NOW,
            },
        )
    engine.dispose()


def test_feed_handle_stops_on_a_row_it_cannot_reproduce(tmp_path: Path) -> None:
    database = tmp_path / "feed-handle-stray.db"
    config = _config(database)
    command.upgrade(config, BEFORE)
    workspace_id, connection_id = uuid4(), uuid4()
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
        connection.execute(
            sa.text(
                "insert into source (id, workspace_id, backend, config, connection_id, "
                "next_sync_at, created_at, updated_at) values "
                "(:id, :ws, 'folder', :config, :conn, :now, :now, :now)"
            ),
            {
                "id": uuid4().hex,
                "ws": workspace_id.hex,
                "config": json.dumps({"root": "/x"}),
                "conn": connection_id.hex,
                "now": NOW,
            },
        )
        connection.commit()
    engine.dispose()
    with pytest.raises(RuntimeError, match="does not hash back"):
        command.upgrade(config, REVISION)
