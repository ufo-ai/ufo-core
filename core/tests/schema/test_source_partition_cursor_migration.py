from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

BEFORE = "20260912192000"
REVISION = "20260913044546"


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


def test_a_tree_rows_map_gets_a_column_the_outgoing_image_never_reads(tmp_path: Path) -> None:
    """The revision adds `partition_cursor` nullable and backfills nothing — a tree row starts from
    an empty map — and leaves `cursor` exactly as it was, so the image still reading it as a
    watermark meets what it wrote. It is core's single head."""
    database = tmp_path / "partition_cursor.db"
    config = _config(database)
    command.upgrade(config, BEFORE)
    engine = sa.create_engine(f"sqlite:///{database}")
    before = _source_columns(engine)
    assert "partition_cursor" not in before

    command.upgrade(config, REVISION)

    after = _source_columns(engine)
    assert after == {**before, "partition_cursor": False}
    command.downgrade(config, BEFORE)
    assert _source_columns(engine) == before
