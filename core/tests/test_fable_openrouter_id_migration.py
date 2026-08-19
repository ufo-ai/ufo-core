"""0110 repoints agents off both stranded fable ids onto the router slug that serves the model."""

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


def test_0110_repoints_both_stranded_ids_and_leaves_every_other_model(tmp_path: Path) -> None:
    database_path = tmp_path / "fable-openrouter-id.db"
    config = _config(database_path)
    command.upgrade(config, "0109")
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
                "('a', 'w', 'dated', 'p', 'claude-5-fable-20260609', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP), "
                "('b', 'w', 'original', 'p', 'claude-fable-5', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP), "
                "('c', 'w', 'bedrock-fable', 'p', 'anthropic.claude-fable-5', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('d', 'w', 'auto', 'p', 'auto', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.commit()
    command.upgrade(config, "0110")
    with engine.connect() as connection:
        rows = connection.execute(sa.text("select model from agent order by id")).all()
    engine.dispose()
    assert rows == [
        ("anthropic/claude-fable-5",),
        ("anthropic/claude-fable-5",),
        ("anthropic.claude-fable-5",),
        ("auto",),
    ]
    command.downgrade(config, "0109")
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        reverted = connection.execute(sa.text("select model from agent order by id")).all()
    engine.dispose()
    assert reverted == [
        ("claude-5-fable-20260609",),
        ("claude-5-fable-20260609",),
        ("anthropic.claude-fable-5",),
        ("auto",),
    ]
