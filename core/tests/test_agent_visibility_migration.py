import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR


def _update_statement() -> sa.TextClause:
    spec = importlib.util.spec_from_file_location(
        "migration_0105", MIGRATIONS_DIR / "versions" / "0105_agent_visibility.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MAIN_VISIBILITY_UPDATE


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def test_0105_births_visibility_private_and_widens_main(tmp_path: Path) -> None:
    database_path = tmp_path / "agent-visibility.db"
    config = _config(database_path)
    command.upgrade(config, "0103")
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
    command.upgrade(config, "0105")
    with engine.connect() as connection:
        rows = connection.execute(sa.text("select id, visibility from agent order by id")).all()
    engine.dispose()
    assert rows == [("a", "workspace"), ("b", "private")]


def test_0105_update_runs_on_postgres(database_url: str) -> None:
    if not database_url.startswith("postgresql"):
        pytest.skip("Postgres migration proof runs on the Postgres database parameter")
    engine = sa.create_engine(database_url.replace("+asyncpg", "+psycopg"))
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "create temporary table agent (is_main boolean, visibility text) on commit drop"
            )
        )
        connection.execute(
            sa.text(
                "insert into agent (is_main, visibility) values "
                "(true, 'private'), (false, 'private')"
            )
        )
        connection.execute(_update_statement())
        rows = connection.execute(
            sa.text("select is_main, visibility from agent order by is_main")
        ).all()
        connection.commit()
    engine.dispose()
    assert rows == [(False, "private"), (True, "workspace")]
