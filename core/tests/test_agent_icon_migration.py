"""0107 gives every agent an icon and the main agent the workspace's mark."""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def test_0107_births_the_icon_and_marks_main(tmp_path: Path) -> None:
    database_path = tmp_path / "agent-icon.db"
    config = _config(database_path)
    command.upgrade(config, "0106")
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) values "
                "('w', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, is_main, "
                "created_at, updated_at) values "
                "('a', 'w', 'ufo', 'p', 'auto', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('b', 'w', 'research', 'p', 'auto', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
    command.upgrade(config, "0107")
    with engine.connect() as connection:
        rows = connection.execute(sa.text("select id, icon from agent order by id")).all()
    engine.dispose()
    assert rows == [("a", "ufo"), ("b", "robot")]
