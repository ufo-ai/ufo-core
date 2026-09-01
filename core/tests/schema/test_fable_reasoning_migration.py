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


def test_0103_updates_fable_reasoning_and_preserves_auto_model(tmp_path: Path) -> None:
    database_path = tmp_path / "fable-reasoning.db"
    config = _config(database_path)
    command.upgrade(config, "0099")
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
                "insert into agent (id, workspace_id, name, prompt, model, reasoning, "
                "created_at, updated_at) values "
                "('a', 'w', 'fable', 'p', 'claude-fable-5', 'off', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP), "
                "('b', 'w', 'bedrock-fable', 'p', 'anthropic.claude-fable-5', 'off', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('c', 'w', 'auto', 'p', 'auto', 'off', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
    command.upgrade(config, "0103")
    with engine.connect() as connection:
        rows = connection.execute(sa.text("select model, reasoning from agent order by id")).all()
    engine.dispose()
    assert rows == [
        ("claude-fable-5", "low"),
        ("anthropic.claude-fable-5", "low"),
        ("auto", "off"),
    ]
