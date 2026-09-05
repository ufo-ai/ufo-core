"""The artifacts app's row takes the stacked-squares mark the chat sidebar draws for artifacts.

The provisioning code writes a shipped row once and then leaves it to the workspace, so a
declaration that changes the icon reaches new workspaces only. This migration carries the change to
the rows already there — and only where the row still holds the shipped `books`, so a member who
picked their own mark keeps it.
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

BEFORE = "20260903020306"
ICON = "20260905013000"


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _seed(database_path: Path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) values "
                "('w', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('x', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, icon, prompt, model, is_main, "
                "visibility, provisioned_by, provisioned_name, provisioned_version, created_at, "
                "updated_at) values "
                "('a', 'w', 'artifacts', 'books', 'p', 'auto', 0, 'workspace', "
                "'app_artifacts', 'artifacts', '0.1.0', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('b', 'x', 'shelf', 'hydria', 'p', 'auto', 0, 'workspace', "
                "'app_artifacts', 'artifacts', '0.1.0', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('c', 'w', 'wiki', 'book', 'p', 'auto', 0, 'workspace', "
                "'app_wiki', 'wiki', '0.1.0', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('d', 'w', 'library', 'books', 'p', 'auto', 0, 'private', "
                "null, null, null, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
    return engine


def _icons(engine: sa.Engine) -> list[tuple[str, str]]:
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(sa.text("select id, icon from agent order by id")).all()
        ]


def test_the_shipped_books_mark_becomes_the_stack_and_comes_back(tmp_path: Path) -> None:
    database_path = tmp_path / "icon.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    engine = _seed(database_path)

    command.upgrade(config, ICON)
    assert _icons(engine) == [
        ("a", "stack-2"),
        # A mark the workspace chose stands.
        ("b", "hydria"),
        # Another app's mark stands.
        ("c", "book"),
        # A member's own agent is no app's row.
        ("d", "books"),
    ]

    command.downgrade(config, BEFORE)
    assert _icons(engine) == [("a", "books"), ("b", "hydria"), ("c", "book"), ("d", "books")]
    engine.dispose()
