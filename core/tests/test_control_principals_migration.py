from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR


def _config(path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    return config


def test_control_principals_migration_names_one_admin_and_main_agent(tmp_path: Path) -> None:
    path = tmp_path / "control-principals.db"
    config = _config(path)
    command.upgrade(config, "0055")
    engine = sa.create_engine(f"sqlite:///{path}")
    workspace_id, first_member, second_member, first_agent, second_agent = (
        uuid4() for _ in range(5)
    )
    early = datetime(2026, 7, 1, tzinfo=UTC)
    late = datetime(2026, 7, 2, tzinfo=UTC)
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) "
                "values (:id, :created, :created)"
            ),
            {"id": workspace_id.hex, "created": early},
        )
        for member_id, email, created in (
            (second_member, "second@example.com", late),
            (first_member, "first@example.com", early),
        ):
            connection.execute(
                sa.text(
                    "insert into member (id, workspace_id, email, created_at, updated_at) "
                    "values (:id, :workspace, :email, :created, :created)"
                ),
                {
                    "id": member_id.hex,
                    "workspace": workspace_id.hex,
                    "email": email,
                    "created": created,
                },
            )
        connection.commit()
        for agent_id, name, created in (
            (second_agent, "second", late),
            (first_agent, "first", early),
        ):
            connection.execute(
                sa.text(
                    "insert into agent "
                    "(id, workspace_id, name, prompt, model, internet_access_allowed, "
                    "created_at, updated_at) "
                    "values (:id, :workspace, :name, 'p', 'm', true, :created, :created)"
                ),
                {
                    "id": agent_id.hex,
                    "workspace": workspace_id.hex,
                    "name": name,
                    "created": created,
                },
            )
        connection.commit()

    command.upgrade(config, "0056")
    with engine.connect() as connection:
        members = dict(connection.execute(sa.text("select email, is_admin from member")).all())
        agents = dict(connection.execute(sa.text("select name, is_main from agent")).all())
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text("update agent set is_main = true where id = :id"),
                {"id": second_agent.hex},
            )
    engine.dispose()

    assert members == {"first@example.com": 1, "second@example.com": 0}
    assert agents == {"first": 1, "second": 0}


def test_control_principals_migration_fails_before_ddl_without_principals(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing-principals.db"
    config = _config(path)
    command.upgrade(config, "0055")
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) "
                "values (:id, :created, :created)"
            ),
            {
                "id": uuid4().hex,
                "created": datetime(2026, 7, 1, tzinfo=UTC),
            },
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="has no control principals"):
        command.upgrade(config, "0056")
    assert "is_admin" not in {column["name"] for column in sa.inspect(engine).get_columns("member")}
    assert "is_main" not in {column["name"] for column in sa.inspect(engine).get_columns("agent")}
    engine.dispose()
