"""An app whose screen the portal draws itself leaves the roster, and its rows leave with it.

The agent row is archived rather than deleted, so the conversations it held stay readable and the
name it held is free again. It keeps its provision identity: the outgoing image still declares both
apps and runs a provisioning pass that reads an archived row as present, where a row stripped of
that identity would send the pass to mint a second agent beside the one this put away. An agent a
member made and named `radar` is not a provision and is left alone.
"""

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


def test_both_app_provisions_are_archived_and_their_store_rows_deleted(tmp_path: Path) -> None:
    database_path = tmp_path / "archive-radar-artifacts.db"
    config = _config(database_path)
    command.upgrade(config, "20260910194926")
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) values "
                "('w', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('v', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, reasoning, "
                "visibility, provisioned_by, provisioned_name, provisioned_version, "
                "created_at, updated_at) values "
                "('a', 'w', 'radar', 'p', 'auto', 'auto', 'workspace', 'app_radar', 'radar', "
                "'0.1.0', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('b', 'w', 'artifacts', 'p', 'auto', 'auto', 'workspace', 'app_artifacts', "
                "'artifacts', '0.1.0', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('c', 'w', 'wiki', 'p', 'auto', 'auto', 'workspace', 'app_wiki', 'wiki', "
                "'0.1.0', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('d', 'v', 'radar', 'p', 'auto', 'auto', 'workspace', NULL, NULL, NULL, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into ext_store (workspace_id, extension, key, value, "
                "created_at, updated_at) values "
                "('w', 'app_radar', 'cursor', '1', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('w', 'app_artifacts', 'cursor', '2', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('w', 'app_wiki', 'cursor', '3', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
    command.upgrade(config, "20260910224332")
    with engine.connect() as connection:
        agents = connection.execute(
            sa.text(
                "select id, name, archived_name, archived_at is not null, provisioned_by "
                "from agent order by id"
            )
        ).all()
        stored = connection.execute(
            sa.text("select extension from ext_store order by extension")
        ).all()
    engine.dispose()
    assert agents == [
        ("a", "~archived-a", "radar", 1, "app_radar"),
        ("b", "~archived-b", "artifacts", 1, "app_artifacts"),
        ("c", "wiki", None, 0, "app_wiki"),
        ("d", "radar", None, 0, None),
    ]
    assert stored == [("app_wiki",)]
