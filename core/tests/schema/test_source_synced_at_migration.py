from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR, core_migration_head

BEFORE = "20260913044546"
REVISION = "20260913055555"


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _source_columns(engine: sa.Engine) -> dict[str, bool]:
    with engine.connect() as connection:
        return {
            row[1]: bool(row[3]) for row in connection.execute(sa.text("pragma table_info(source)"))
        }


def test_a_child_can_distinguish_an_empty_parent_from_one_that_has_not_synced(
    tmp_path: Path,
) -> None:
    database = tmp_path / "source_synced_at.db"
    config = _config(database)
    command.upgrade(config, BEFORE)
    engine = sa.create_engine(f"sqlite:///{database}")
    before = _source_columns(engine)
    assert "synced_at" not in before

    command.upgrade(config, REVISION)

    assert _source_columns(engine) == {**before, "synced_at": False}
    command.downgrade(config, BEFORE)
    assert _source_columns(engine) == before
    assert core_migration_head() == REVISION
