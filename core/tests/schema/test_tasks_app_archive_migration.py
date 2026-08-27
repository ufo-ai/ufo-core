"""The tasks app tear-out archives the shipped agent and touches nothing else.

The row keeps its provision identity so the outgoing image's provisioning pass matches it and
mints no second tasks agent during the rollout, and a member's own agent that happens to be named
`tasks` is not the shipped one and stands untouched.
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

BEFORE = "20260825044912"
ARCHIVE = "20260826235718"


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
                "('x', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('y', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, is_main, visibility, "
                "provisioned_by, provisioned_name, provisioned_version, created_at, updated_at) "
                "values "
                "('a', 'w', 'tasks', 'p', 'auto', 0, 'workspace', "
                "'app_tasks', 'tasks', '0.1.0', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('b', 'w', 'radar', 'p', 'auto', 0, 'workspace', "
                "'app_radar', 'radar', '0.1.0', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('c', 'x', 'tasks-renamed', 'p', 'auto', 0, 'workspace', "
                "'app_tasks', 'tasks', '0.1.0', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('e', 'x', 'tasks', 'mine', 'auto', 0, 'private', "
                "null, null, null, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, archived_name, archived_at, prompt, "
                "model, is_main, visibility, provisioned_by, provisioned_name, "
                "provisioned_version, created_at, updated_at) values "
                "('d', 'y', '~archived-d', 'tasks', '2026-08-20 12:00:00', 'p', "
                "'auto', 0, 'workspace', 'app_tasks', 'tasks', '0.1.0', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
    return engine


def _rows(engine: sa.Engine) -> list[tuple[str, str, str | None, str | None, str | None, bool]]:
    with engine.connect() as connection:
        return [
            (
                row.id,
                row.name,
                row.archived_name,
                row.provisioned_by,
                row.provisioned_name,
                row.archived_at is not None,
            )
            for row in connection.execute(
                sa.text(
                    "select id, name, archived_name, provisioned_by, provisioned_name, "
                    "archived_at from agent order by id"
                )
            ).all()
        ]


def test_the_shipped_tasks_agent_is_archived_with_its_identity_kept(tmp_path: Path) -> None:
    database_path = tmp_path / "archive.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    engine = _seed(database_path)

    command.upgrade(config, ARCHIVE)
    assert _rows(engine) == [
        # The shipped row leaves the roster under the archive name and keeps its identity.
        ("a", "~archived-a", "tasks", "app_tasks", "tasks", True),
        # Another app's row stands.
        ("b", "radar", None, "app_radar", "radar", False),
        # A workspace's rename rides into `archived_name`, so a restore hands it back.
        ("c", "~archived-c", "tasks-renamed", "app_tasks", "tasks", True),
        # One a workspace archived itself is already what this states.
        ("d", "~archived-d", "tasks", "app_tasks", "tasks", True),
        # A member's own agent named tasks is not the shipped one.
        ("e", "tasks", None, None, None, False),
    ]
    with engine.connect() as connection:
        kept = connection.execute(
            sa.text("select archived_at from agent where id = 'd'")
        ).scalar_one()
    assert kept == "2026-08-20 12:00:00"
    engine.dispose()
