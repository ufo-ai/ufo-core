from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

BEFORE = "20260913095851"
AFTER = "20260913112228"
INDEX = "shared_artifact_files"


def _indexes(engine: sa.Engine) -> dict[str, list[str]]:
    return {
        str(index["name"]): [str(column) for column in index["column_names"]]
        for index in sa.inspect(engine).get_indexes("shared_artifact")
    }


def test_shared_artifact_files_gain_the_listing_index(tmp_path: Path) -> None:
    path = tmp_path / "shared-artifact-listing-index.db"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    command.upgrade(config, BEFORE)
    engine = sa.create_engine(f"sqlite:///{path}")
    assert INDEX not in _indexes(engine)

    command.upgrade(config, AFTER)

    assert _indexes(engine)[INDEX] == [
        "workspace_id",
        "filename",
        "created_at",
        "blob_key",
    ]
    command.downgrade(config, BEFORE)
    assert INDEX not in _indexes(engine)
