"""`skill_create_0003` projects each existing row's routing card out of its stored content: the
frontmatter description and depends land in their own columns, a row whose bundle no longer parses
gets the empty card rather than bricking the migration, and every row starts unpinned and unindexed
so the interval job derives its chunks."""

import base64
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from pytest import fixture
from sqlalchemy.engine import make_url

from ufo.db import MIGRATIONS_DIR
from ufo.host.ext.loader import migration_locations

SKILL_MD = (
    "---\n"
    "name: greet\n"
    "description: greets people\n"
    "metadata:\n"
    "  depends:\n"
    "    - tone\n"
    "---\n"
    "Follow the steps.\n"
)


def _alembic(migration_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", migration_url)
    return config


@fixture
def migration_urls(database_url: str, tmp_path: Path) -> Iterator[tuple[str, str]]:
    source = make_url(database_url)
    if source.get_backend_name() == "sqlite":
        path = tmp_path / "routing-cards.db"
        yield f"sqlite+aiosqlite:///{path}", f"sqlite:///{path}"
        return
    database = f"{source.database}_routing_cards_{uuid4().hex[:8]}"
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


def test_routing_cards_migration_backfills_from_stored_content(
    migration_urls: tuple[str, str],
) -> None:
    migration_url, sync_url = migration_urls
    config = _alembic(migration_url)
    command.upgrade(config, "skill_create_0002")

    workspace_id, agent_id = uuid4(), uuid4()
    now = datetime(2026, 8, 19, tzinfo=UTC)
    valid_content = json.dumps(
        {"files": {"SKILL.md": base64.b64encode(SKILL_MD.encode()).decode()}}
    )
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
            {"id": workspace_id.hex, "now": now},
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, created_at, updated_at) "
                "values (:id, :workspace_id, 'assistant', 'p', 'claude-opus-4-8', :now, :now)"
            ),
            {"id": agent_id.hex, "workspace_id": workspace_id.hex, "now": now},
        )
        for name, content in (("greet", valid_content), ("broken", "{ not valid json")):
            connection.execute(
                sa.text(
                    "insert into user_skill "
                    "(workspace_id, agent_id, name, digest, content, created_at, updated_at) "
                    "values (:workspace_id, :agent_id, :name, 'sha256:seed', :content, :now, :now)"
                ),
                {
                    "workspace_id": workspace_id.hex,
                    "agent_id": agent_id.hex,
                    "name": name,
                    "content": content,
                    "now": now,
                },
            )
        connection.commit()
    engine.dispose()

    command.upgrade(config, "skill_create_0003")
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        rows = {
            row.name: row
            for row in connection.execute(
                sa.text("select name, description, depends, pinned, indexed_digest from user_skill")
            ).all()
        }
    engine.dispose()
    assert rows["greet"].description == "greets people"
    assert json.loads(rows["greet"].depends) == ["tone"]
    assert (rows["broken"].description, rows["broken"].depends) == ("", "[]")
    for row in rows.values():
        assert not row.pinned
        assert row.indexed_digest is None

    command.downgrade(config, "skill_create_0002")
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        columns = {column["name"] for column in sa.inspect(connection).get_columns("user_skill")}
        names = set(connection.execute(sa.text("select name from user_skill")).scalars())
    engine.dispose()
    assert {"description", "depends", "pinned", "indexed_digest"}.isdisjoint(columns)
    assert names == {"greet", "broken"}
