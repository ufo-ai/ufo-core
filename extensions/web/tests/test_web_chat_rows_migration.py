import json
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
AGENT = uuid4()
MEMBER = uuid4()
TYPED = uuid4()
SLASHED = uuid4()
BARE = uuid4()
CASED = uuid4()
LOCAL_PART = uuid4()
MINTED = uuid4()
SLACK = uuid4()
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
        path = tmp_path / "chat-rows.db"
        yield f"sqlite+aiosqlite:///{path}", f"sqlite:///{path}"
        return
    database = f"{source.database}_chat_rows_{uuid4().hex[:8]}"
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
    connection.execute(
        sa.text("insert into workspace (id, created_at, updated_at) values (:id, :m, :m)"),
        {"id": WORKSPACE.hex, "m": MOMENT},
    )
    for member_id, email in (
        (MEMBER, "owner@example.com"),
        (TYPED, "Owner@Example.net"),
        (SLASHED, "a/b@example.org"),
    ):
        connection.execute(
            sa.text(
                "insert into member (id, workspace_id, email, created_at, updated_at) "
                "values (:id, :ws, :email, :m, :m)"
            ),
            {"id": member_id.hex, "ws": WORKSPACE.hex, "email": email, "m": MOMENT},
        )
    connection.execute(
        sa.text(
            "insert into agent (id, workspace_id, name, prompt, model, created_at, updated_at) "
            "values (:id, :ws, 'assistant', 'be useful', 'claude-opus-4-8', :m, :m)"
        ),
        {"id": AGENT.hex, "ws": WORKSPACE.hex, "m": MOMENT},
    )
    for conversation_id, member_id, surface, queue_key in (
        (BARE, MEMBER, "web", f"{AGENT}/owner@example.com"),
        (CASED, TYPED, "web", f"{AGENT}/owner@example.net"),
        (LOCAL_PART, SLASHED, "web", f"{AGENT}/a/b@example.org"),
        (MINTED, MEMBER, "web", f"{AGENT}/owner@example.com/{uuid4().hex}"),
        (SLACK, MEMBER, "slack", "C42:1723.0"),
    ):
        connection.execute(
            sa.text(
                "insert into conversation "
                "(id, workspace_id, agent_id, surface, queue_key, member_id, audience, "
                "created_at, updated_at) "
                "values (:id, :ws, :agent, :surface, :key, :member, :audience, :m, :m)"
            ),
            {
                "id": conversation_id.hex,
                "ws": WORKSPACE.hex,
                "agent": AGENT.hex,
                "surface": surface,
                "key": queue_key,
                "member": member_id.hex,
                "audience": f"member:{member_id}",
                "m": MOMENT,
            },
        )
    connection.execute(
        sa.text(
            "insert into ext_store (workspace_id, extension, key, value, created_at, updated_at) "
            "values (:ws, 'web', :key, '{}', :m, :m)"
        ),
        {"ws": WORKSPACE.hex, "key": f"audience/{AGENT}/peer@example.com", "m": MOMENT},
    )


def _rows(connection: sa.Connection) -> dict[str, object]:
    listed = connection.execute(
        sa.text("select key, value from ext_store where extension = 'web' order by key")
    ).all()
    return {
        row.key: row.value if isinstance(row.value, dict) else json.loads(row.value)
        for row in listed
    }


def test_chat_rows_backfill_covers_bare_keys_and_downgrade_removes_only_them(
    migration_urls: tuple[str, str],
) -> None:
    migration_url, sync_url = migration_urls
    config = _config(migration_url)
    command.upgrade(config, "0067")
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        _seed(connection)
        connection.commit()

    command.upgrade(config, "web_0001")

    with engine.connect() as connection:
        rows = _rows(connection)
    assert set(rows) == {
        f"audience/{AGENT}/peer@example.com",
        f"chat/{BARE}",
        f"chat/{CASED}",
        f"chat/{LOCAL_PART}",
    }
    assert rows[f"chat/{BARE}"] == {
        "agent_id": str(AGENT),
        "email": "owner@example.com",
        "title": "assistant",
    }
    assert rows[f"chat/{CASED}"] == {
        "agent_id": str(AGENT),
        "email": "owner@example.net",
        "title": "assistant",
    }
    assert rows[f"chat/{LOCAL_PART}"] == {
        "agent_id": str(AGENT),
        "email": "a/b@example.org",
        "title": "assistant",
    }

    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into ext_store (workspace_id, extension, key, value, created_at, "
                "updated_at) values (:ws, 'web', :key, :value, :m, :m)"
            ),
            {
                "ws": WORKSPACE.hex,
                "key": f"chat/{MINTED}",
                "value": json.dumps(
                    {"agent_id": str(AGENT), "email": "owner@example.com", "title": "Opened here"}
                ),
                "m": MOMENT,
            },
        )
        connection.commit()

    command.downgrade(config, "web@base")

    with engine.connect() as connection:
        remaining = _rows(connection)
    engine.dispose()
    assert set(remaining) == {f"audience/{AGENT}/peer@example.com", f"chat/{MINTED}"}


def test_web_0002_carries_each_stored_title_onto_the_conversation_and_strikes_it(
    migration_urls: tuple[str, str],
) -> None:
    """The name a portal chat was kept under moves onto the conversation it names, and leaves the
    store row holding only the binding this surface gates on. It stands over the opening words
    `0086` filled the column with: a summary is the better name, and the one the member has been
    reading on every row."""
    migration_url, sync_url = migration_urls
    config = _config(migration_url)
    command.upgrade(config, "0085")
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        _seed(connection)
        connection.execute(
            sa.text(
                "insert into turn (id, workspace_id, conversation_id, agent_id, seq, status, "
                "inbound, created_at, updated_at) "
                "values (:id, :ws, :conversation, :agent, 1, 'queued', :inbound, :m, :m)"
            ),
            {
                "id": uuid4().hex,
                "ws": WORKSPACE.hex,
                "conversation": BARE.hex,
                "agent": AGENT.hex,
                "inbound": "the words it opened with",
                "m": MOMENT,
            },
        )
        connection.commit()

    command.upgrade(config, "0086")
    with engine.connect() as connection:
        opened = connection.execute(
            sa.text("select title from conversation where id = :id"), {"id": BARE.hex}
        ).scalar_one()
    assert opened == "the words it opened with"

    command.upgrade(config, "web_0002")

    with engine.connect() as connection:
        rows = _rows(connection)
        named = connection.execute(
            sa.text("select title from conversation where id = :id"), {"id": BARE.hex}
        ).scalar_one()
    engine.dispose()

    assert named == "assistant"
    assert rows[f"chat/{BARE}"] == {"agent_id": str(AGENT), "email": "owner@example.com"}
