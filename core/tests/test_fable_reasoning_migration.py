import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR


def _update_statement() -> sa.TextClause:
    spec = importlib.util.spec_from_file_location(
        "migration_0102", MIGRATIONS_DIR / "versions" / "0102_fable_reasoning.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FABLE_REASONING_UPDATE


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def test_0102_updates_fable_reasoning_and_preserves_auto_model(tmp_path: Path) -> None:
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
    command.upgrade(config, "0102")
    with engine.connect() as connection:
        rows = connection.execute(sa.text("select model, reasoning from agent order by id")).all()
    engine.dispose()
    assert rows == [
        ("claude-fable-5", "low"),
        ("anthropic.claude-fable-5", "low"),
        ("auto", "off"),
    ]


def test_0102_update_runs_on_postgres(database_url: str) -> None:
    if not database_url.startswith("postgresql"):
        pytest.skip("Postgres migration proof runs on the Postgres database parameter")
    engine = sa.create_engine(database_url.replace("+asyncpg", "+psycopg"))
    with engine.connect() as connection:
        connection.execute(
            sa.text("create temporary table agent (model text, reasoning text) on commit drop")
        )
        connection.execute(
            sa.text(
                "insert into agent (model, reasoning) values "
                "('claude-fable-5', 'off'), ('anthropic.claude-fable-5', 'low'), "
                "('auto', 'off')"
            )
        )
        connection.execute(_update_statement())
        rows = connection.execute(
            sa.text("select model, reasoning from agent order by model")
        ).all()
        connection.commit()
    engine.dispose()
    assert rows == [
        ("anthropic.claude-fable-5", "low"),
        ("auto", "off"),
        ("claude-fable-5", "low"),
    ]
