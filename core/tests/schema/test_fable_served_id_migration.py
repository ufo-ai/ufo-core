"""0108 repoints agents off the fable id no provider serves onto the dated one."""

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


def test_0108_repoints_fable_agents_and_leaves_every_other_model(tmp_path: Path) -> None:
    database_path = tmp_path / "fable-served-id.db"
    config = _config(database_path)
    command.upgrade(config, "0107")
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
                "insert into agent (id, workspace_id, name, prompt, model, "
                "created_at, updated_at) values "
                "('a', 'w', 'fable', 'p', 'claude-fable-5', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP), "
                "('b', 'w', 'bedrock-fable', 'p', 'anthropic.claude-fable-5', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('c', 'w', 'auto', 'p', 'auto', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
    command.upgrade(config, "0108")
    with engine.connect() as connection:
        rows = connection.execute(sa.text("select model from agent order by id")).all()
    engine.dispose()
    assert rows == [
        ("claude-5-fable-20260609",),
        ("anthropic.claude-fable-5",),
        ("auto",),
    ]
