from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from ufo.db import MIGRATIONS_DIR
from ufo.ext.loader import migration_locations

WORKSPACE = uuid4()
OTHER_WORKSPACE = uuid4()
REBOUND_WORKSPACE = uuid4()
MAIN = uuid4()
OTHER_MAIN = uuid4()
REBOUND_MAIN = uuid4()
SPECIALIST = uuid4()
REBOUND_SPECIALIST = uuid4()
MAIN_HOME = uuid4()
SPECIALIST_HOME = uuid4()
UNBOUND = uuid4()
ALREADY_OPEN = uuid4()
HIJACKED = uuid4()
REBOUND_HOME = uuid4()
CREATOR = uuid4()
BINDER = uuid4()
OWNER = uuid4()
MOMENT = datetime(2026, 8, 1, tzinfo=UTC)


def _config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        "\n".join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "newline")
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture
def migration_urls(database_url: str, tmp_path: Path) -> Iterator[tuple[str, str]]:
    source = make_url(database_url)
    if source.get_backend_name() == "sqlite":
        path = tmp_path / "main-homepage.db"
        yield f"sqlite+aiosqlite:///{path}", f"sqlite:///{path}"
        return
    database = f"{source.database}_main_home_{uuid4().hex[:8]}"
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


def _seed(connection: sa.Connection) -> None:
    for workspace_id in (WORKSPACE, OTHER_WORKSPACE, REBOUND_WORKSPACE):
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :m, :m)"),
            {"id": workspace_id.hex, "m": MOMENT},
        )
    for agent_id, workspace_id, name, is_main in (
        (MAIN, WORKSPACE, "assistant", True),
        (SPECIALIST, WORKSPACE, "narrow", False),
        (OTHER_MAIN, OTHER_WORKSPACE, "assistant", True),
        (REBOUND_MAIN, REBOUND_WORKSPACE, "assistant", True),
        (REBOUND_SPECIALIST, REBOUND_WORKSPACE, "narrow", False),
    ):
        connection.execute(
            sa.text(
                "insert into agent "
                "(id, workspace_id, name, prompt, model, is_main, created_at, updated_at) "
                "values (:id, :ws, :name, 'be useful', 'claude-opus-4-8', :main, :m, :m)"
            ),
            {
                "id": agent_id.hex,
                "ws": workspace_id.hex,
                "name": name,
                "main": is_main,
                "m": MOMENT,
            },
        )
    for member_id, workspace_id, email in (
        (CREATOR, WORKSPACE, "creator@example.com"),
        (BINDER, OTHER_WORKSPACE, "binder@example.com"),
        (OWNER, REBOUND_WORKSPACE, "owner@example.com"),
    ):
        connection.execute(
            sa.text(
                "insert into member (id, workspace_id, email, created_at, updated_at) "
                "values (:id, :ws, :email, :m, :m)"
            ),
            {"id": member_id.hex, "ws": workspace_id.hex, "email": email, "m": MOMENT},
        )
    for conversation_id, workspace_id, agent_id, queue_key, member_id in (
        (MAIN_HOME, WORKSPACE, MAIN, f"homepage/{MAIN}/{CREATOR}", CREATOR),
        (HIJACKED, OTHER_WORKSPACE, OTHER_MAIN, f"homepage/{OTHER_MAIN}/{BINDER}", BINDER),
        (
            REBOUND_HOME,
            REBOUND_WORKSPACE,
            REBOUND_SPECIALIST,
            f"homepage/{REBOUND_SPECIALIST}/{OWNER}",
            OWNER,
        ),
    ):
        connection.execute(
            sa.text(
                "insert into conversation "
                "(id, workspace_id, agent_id, surface, queue_key, member_id, audience, "
                "created_at, updated_at) "
                "values (:id, :ws, :agent, 'web', :key, :member, :audience, :m, :m)"
            ),
            {
                "id": conversation_id.hex,
                "ws": workspace_id.hex,
                "agent": agent_id.hex,
                "key": queue_key,
                "member": member_id.hex,
                "audience": f"member:{member_id}",
                "m": MOMENT,
            },
        )
    for conversation_id, workspace_id, name, visibility, creator, bound in (
        (MAIN_HOME, WORKSPACE, "main-home", "private", CREATOR, MAIN),
        (SPECIALIST_HOME, WORKSPACE, "narrow-home", "private", CREATOR, SPECIALIST),
        (UNBOUND, WORKSPACE, "scratch", "private", CREATOR, None),
        (ALREADY_OPEN, WORKSPACE, "open-home", "workspace", CREATOR, None),
        (HIJACKED, OTHER_WORKSPACE, "hijacked-home", "private", CREATOR, OTHER_MAIN),
        (REBOUND_HOME, REBOUND_WORKSPACE, "rebound-home", "private", OWNER, REBOUND_MAIN),
    ):
        connection.execute(
            sa.text(
                "insert into hosted_site "
                "(workspace_id, conversation_id, name, port, visibility, creator_member_id, "
                "generation, homepage_agent_id, created_at, updated_at) "
                "values (:ws, :conversation, :name, 8000, :visibility, :creator, :generation, "
                ":bound, :m, :m)"
            ),
            {
                "ws": workspace_id.hex,
                "conversation": conversation_id.hex,
                "name": name,
                "visibility": visibility,
                "creator": creator.hex,
                "generation": uuid4().hex,
                "bound": None if bound is None else bound.hex,
                "m": MOMENT,
            },
        )


def test_sites_0004_widens_only_seed_shaped_main_homepages(
    migration_urls: tuple[str, str],
) -> None:
    """The migration widens exactly the rows whose binder it can prove was the creator — a main
    binding whose site lives in the seed room that same agent holds with the site's own creator. A
    private site bound to main from a room another member holds stays private, and so does one
    another agent's seed room holds: a migration has no speaker to make that disclosure."""
    migration_url, sync_url = migration_urls
    config = _config(migration_url)
    command.upgrade(config, "0056")
    command.upgrade(config, "sites_0003")
    engine = sa.create_engine(sync_url)
    try:
        with engine.connect() as connection:
            _seed(connection)
            before = {
                row.name: row.generation
                for row in connection.execute(
                    sa.text("select name, generation from hosted_site")
                ).all()
            }
            connection.commit()

        command.upgrade(config, "sites_0004")

        with engine.connect() as connection:
            rows = {
                row.name: row
                for row in connection.execute(
                    sa.text("select name, visibility, generation from hosted_site")
                ).all()
            }
    finally:
        engine.dispose()
    assert rows["main-home"].visibility == "workspace"
    assert rows["main-home"].generation != before["main-home"]
    assert rows["narrow-home"].visibility == "private"
    assert rows["narrow-home"].generation == before["narrow-home"]
    assert rows["scratch"].visibility == "private"
    assert rows["open-home"].visibility == "workspace"
    assert rows["open-home"].generation == before["open-home"]
    assert rows["hijacked-home"].visibility == "private"
    assert rows["hijacked-home"].generation == before["hijacked-home"]
    assert rows["rebound-home"].visibility == "private"
    assert rows["rebound-home"].generation == before["rebound-home"]
