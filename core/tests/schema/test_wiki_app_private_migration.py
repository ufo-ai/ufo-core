"""The wiki app is private on the rows a workspace already holds, and stays private on a downgrade.

The release that shipped the app declared `workspace`, so the rows in the fleet are visible to every
member and no provisioning pass would ever narrow them: a pass writes setup and purpose alone.
"""

from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

BEFORE = "20260827015500"
PRIVATE = "20260827161500"


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
                "('a', 'w', 'wiki', 'be the wiki', 'auto', 0, 'workspace', "
                "'app_wiki', 'wiki', '0.1.0', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('b', 'x', 'wiki', 'be the wiki', 'auto', 0, 'private', "
                "'app_wiki', 'wiki', '0.1.0', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('c', 'w', 'radar', 'be the radar', 'auto', 0, 'workspace', "
                "'app_radar', 'radar', '0.1.0', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('d', 'y', 'notes', 'mine', 'auto', 0, 'workspace', "
                "null, null, null, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, archived_name, archived_at, prompt, "
                "model, is_main, visibility, provisioned_by, provisioned_name, "
                "provisioned_version, created_at, updated_at) values "
                "('e', 'y', '~archived-e', 'wiki', CURRENT_TIMESTAMP, 'be the wiki', "
                "'auto', 0, 'workspace', 'app_wiki', 'wiki', '0.1.0', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
    return engine


def _visibility(engine: sa.Engine) -> list[tuple[str, str]]:
    with engine.connect() as connection:
        return [
            tuple(row)
            for row in connection.execute(
                sa.text("select id, visibility from agent order by id")
            ).all()
        ]


def test_the_shipped_wiki_rows_become_private(tmp_path: Path) -> None:
    """Every row the old declaration left at `workspace` narrows, the archived one with them — an
    agent nobody's roster holds loses nothing today, and the workspace that restores it gets the app
    the product ships. A row a member already narrowed stands, and no other extension's rows and no
    agent a member made themselves are touched."""
    database_path = tmp_path / "wiki-private.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    engine = _seed(database_path)

    command.upgrade(config, PRIVATE)
    assert _visibility(engine) == [
        ("a", "private"),
        ("b", "private"),
        ("c", "workspace"),
        ("d", "workspace"),
        ("e", "private"),
    ]
    with engine.connect() as connection:
        held = connection.execute(
            sa.text("select name, prompt, provisioned_version from agent where id = 'a'")
        ).one()
    assert held == ("wiki", "be the wiki", "0.1.0")
    engine.dispose()


def test_a_downgrade_widens_nothing_back(tmp_path: Path) -> None:
    """A row reading `private` after the upgrade is either one this narrowed or one a member
    narrowed themselves, and nothing on the row tells the two apart. The old image reads a private
    wiki agent the way it reads any other, so nothing has to be rescued."""
    database_path = tmp_path / "wiki-private-down.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    engine = _seed(database_path)

    command.upgrade(config, PRIVATE)
    command.downgrade(config, BEFORE)
    assert _visibility(engine) == [
        ("a", "private"),
        ("b", "private"),
        ("c", "workspace"),
        ("d", "workspace"),
        ("e", "private"),
    ]
    engine.dispose()
