"""0107 gives every agent an icon and the main agent the workspace's mark; 0112 moves the column's
default to the portal's own pack and leaves every row where it stands."""

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


def test_0112_moves_the_default_and_rewrites_no_row(tmp_path: Path) -> None:
    """Re-stamping rows is an operator's own step, so the revision that changes the default must not
    touch one. A row still holding a tabler slug keeps it and keeps drawing; only a row inserted
    without an icon takes the pack's mark."""
    database_path = tmp_path / "agent-icon-default.db"
    config = _config(database_path)
    command.upgrade(config, "0111")
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
                "insert into agent (id, workspace_id, name, prompt, model, is_main, icon, "
                "created_at, updated_at) values "
                "('a', 'w', 'ufo', 'p', 'auto', 1, 'ufo', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('b', 'w', 'research', 'p', 'auto', 0, 'robot', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP), "
                "('c', 'w', 'ops', 'p', 'auto', 0, 'anchor', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
    command.upgrade(config, "0112")
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, is_main, "
                "created_at, updated_at) values "
                "('d', 'w', 'digest', 'p', 'auto', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
        rows = connection.execute(sa.text("select id, icon from agent order by id")).all()
    engine.dispose()
    assert rows == [("a", "ufo"), ("b", "robot"), ("c", "anchor"), ("d", "propylon")]
