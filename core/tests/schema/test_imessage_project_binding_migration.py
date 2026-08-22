"""0109 leaves surface_installation as the one iMessage project binding."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

NOW = datetime(2026, 8, 18, tzinfo=UTC)


def _config(path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    return config


def test_only_the_duplicate_imessage_project_row_goes(tmp_path: Path) -> None:
    database = tmp_path / "imessage-project.db"
    config = _config(database)
    command.upgrade(config, "0108")
    workspace_id = uuid4()
    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
            {"id": str(workspace_id), "now": NOW},
        )
        for extension, key in (
            ("imessage", "project"),
            ("imessage", "phone/+14155550123"),
            ("memory", "project"),
        ):
            connection.execute(
                sa.text(
                    "insert into ext_store "
                    "(workspace_id, extension, key, value, created_at, updated_at) "
                    "values (:workspace_id, :extension, :key, :value, :now, :now)"
                ),
                {
                    "workspace_id": str(workspace_id),
                    "extension": extension,
                    "key": key,
                    "value": '"value"',
                    "now": NOW,
                },
            )
        connection.commit()
    engine.dispose()

    command.upgrade(config, "0109")

    engine = sa.create_engine(f"sqlite:///{database}")
    with engine.connect() as connection:
        store = connection.execute(sa.text("select extension, key from ext_store")).all()
    engine.dispose()

    assert set(store) == {
        ("imessage", "phone/+14155550123"),
        ("memory", "project"),
    }
