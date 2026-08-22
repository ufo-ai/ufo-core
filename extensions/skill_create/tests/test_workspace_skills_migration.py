"""`skill_create_0004` moves the workspace's skills onto one row per name: the newest save of each
name becomes the workspace's one row, the rows it shadowed are dropped, and every survivor starts
with a fresh generation and no targeting. A survivor keeps the name it was saved under, so its
stored `SKILL.md` frontmatter still matches the `name` column and the skill loads after the
upgrade."""

import base64
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from ufo.db import MIGRATIONS_DIR
from ufo.ext.loader import migration_locations
from ufo.skills.runtime import parse_skill_content

MOMENT = datetime(2026, 8, 22, tzinfo=UTC)
WORKSPACE = uuid4()
FIRST_AGENT, SECOND_AGENT = sorted((uuid4(), uuid4()), key=lambda agent_id: agent_id.hex)


def _config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture
def migration_urls(database_url: str, tmp_path: Path) -> Iterator[tuple[str, str]]:
    source = make_url(database_url)
    if source.get_backend_name() == "sqlite":
        path = tmp_path / "workspace-skills.db"
        yield f"sqlite+aiosqlite:///{path}", f"sqlite:///{path}"
        return
    database = f"{source.database}_workspace_skills_{uuid4().hex[:8]}"
    admin = sa.create_engine(
        source.set(drivername="postgresql+psycopg", database="ufo"),
        isolation_level="AUTOCOMMIT",
    )
    with admin.connect() as connection:
        connection.exec_driver_sql(f'create database "{database}"')
    try:
        yield (
            source.set(database=database).render_as_string(hide_password=False),
            source.set(drivername="postgresql+psycopg", database=database).render_as_string(
                hide_password=False
            ),
        )
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'drop database "{database}"')
        admin.dispose()


def _stored(name: str) -> str:
    """One row's `content` column: the skill's files as the store persists them, the frontmatter
    name matching the row's own name the way a save wrote it."""
    document = f'---\nname: {name}\ndescription: "how {name} reads"\n---\n\nWrite tersely.\n'
    return json.dumps({"files": {"SKILL.md": base64.b64encode(document.encode()).decode()}})


def _materialized(content: str, name: str) -> str:
    stored = json.loads(content)
    files = {path: base64.b64decode(payload) for path, payload in stored["files"].items()}
    return parse_skill_content(name, files).name


def _seed(
    connection: sa.Connection, workspace_id: UUID, rows: tuple[tuple[UUID, str, datetime], ...]
) -> None:
    connection.execute(
        sa.text("insert into workspace (id, created_at, updated_at) values (:id, :m, :m)"),
        {"id": workspace_id.hex, "m": MOMENT},
    )
    for agent_id in sorted({agent_id for agent_id, _, _ in rows}):
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, created_at, updated_at) "
                "values (:id, :workspace_id, :name, 'p', 'claude-opus-4-8', :m, :m)"
            ),
            {
                "id": agent_id.hex,
                "workspace_id": workspace_id.hex,
                "name": f"agent-{agent_id.hex[:8]}",
                "m": MOMENT,
            },
        )
    for agent_id, name, updated_at in rows:
        connection.execute(
            sa.text(
                "insert into user_skill (workspace_id, agent_id, name, digest, content, "
                "description, depends, pinned, indexed_digest, created_at, updated_at) values "
                "(:workspace_id, :agent_id, :name, :digest, :content, :name, '[]', "
                "false, 'sha256:indexed', :m, :updated_at)"
            ),
            {
                "workspace_id": workspace_id.hex,
                "agent_id": agent_id.hex,
                "name": name,
                "digest": f"sha256:{name}-{agent_id.hex[:8]}",
                "content": _stored(name),
                "m": MOMENT,
                "updated_at": updated_at,
            },
        )
    connection.commit()


def test_workspace_skills_migration_keeps_the_newest_row_of_each_name(
    migration_urls: tuple[str, str],
) -> None:
    migration_url, sync_url = migration_urls
    config = _config(migration_url)
    command.upgrade(config, "skill_create_0003")
    command.upgrade(config, "20260822090000")

    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        _seed(
            connection,
            WORKSPACE,
            (
                (FIRST_AGENT, "greet", MOMENT),
                (SECOND_AGENT, "greet", MOMENT + timedelta(minutes=1)),
                (FIRST_AGENT, "solo", MOMENT),
            ),
        )
    engine.dispose()

    command.upgrade(config, "skill_create_0004")

    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        rows = {
            row.name: row
            for row in connection.execute(
                sa.text(
                    "select name, generation, agents, digest, content, indexed_digest "
                    "from user_skill"
                )
            ).all()
        }
        columns = {column["name"] for column in sa.inspect(connection).get_columns("user_skill")}
        foreign_keys = sa.inspect(connection).get_foreign_keys("user_skill")
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "insert into user_skill (workspace_id, name, generation, digest, content, "
                    "description, depends, agents, pinned, created_at, updated_at) values "
                    "(:workspace_id, 'greet', :generation, 'sha256:clash', '{\"files\": {}}', "
                    "'', '[]', '[]', false, :m, :m)"
                ),
                {"workspace_id": WORKSPACE.hex, "generation": uuid4().hex, "m": MOMENT},
            )
    engine.dispose()

    assert set(rows) == {"greet", "solo"}
    assert rows["greet"].digest == f"sha256:greet-{SECOND_AGENT.hex[:8]}"
    assert all(_materialized(row.content, name) == name for name, row in rows.items())
    assert all(row.generation is not None for row in rows.values())
    assert len({str(row.generation) for row in rows.values()}) == len(rows)
    assert all(row.agents == "[]" for row in rows.values())
    assert all(row.indexed_digest is None for row in rows.values())
    assert "agent_id" not in columns
    assert all(
        foreign_key["referred_table"] != "agent"
        and foreign_key["name"] != "user_skill_agent_id_fkey"
        for foreign_key in foreign_keys
    )

    command.downgrade(config, "skill_create_0003")
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        restored = {
            row.name: row
            for row in connection.execute(sa.text("select name, agent_id from user_skill")).all()
        }
        columns = {column["name"] for column in sa.inspect(connection).get_columns("user_skill")}
    engine.dispose()
    assert set(restored) == {"greet", "solo"}
    assert all(row.agent_id is not None for row in restored.values())
    assert "generation" not in columns
    assert "agents" not in columns


def test_workspace_skills_migration_collapses_per_workspace(
    migration_urls: tuple[str, str],
) -> None:
    migration_url, sync_url = migration_urls
    config = _config(migration_url)
    command.upgrade(config, "skill_create_0003")
    command.upgrade(config, "20260822090000")

    crowd = uuid4()
    neighbour = uuid4()
    first, second, third, fourth = sorted(uuid4() for _ in range(4))
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        _seed(
            connection,
            crowd,
            (
                (first, "triage", MOMENT),
                (second, "triage", MOMENT + timedelta(minutes=1)),
                (third, "triage", MOMENT + timedelta(minutes=3)),
                (fourth, "triage", MOMENT + timedelta(minutes=2)),
                (third, "solo", MOMENT),
            ),
        )
        _seed(connection, neighbour, ((uuid4(), "triage", MOMENT),))
    engine.dispose()

    command.upgrade(config, "skill_create_0004")

    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        rows = {
            row.name: row
            for row in connection.execute(
                sa.text("select name, digest from user_skill where workspace_id = :workspace_id"),
                {"workspace_id": crowd.hex},
            ).all()
        }
        untouched = set(
            connection.execute(
                sa.text("select name from user_skill where workspace_id = :workspace_id"),
                {"workspace_id": neighbour.hex},
            ).scalars()
        )
    engine.dispose()

    assert set(rows) == {"triage", "solo"}
    assert rows["triage"].digest == f"sha256:triage-{third.hex[:8]}"
    assert untouched == {"triage"}
