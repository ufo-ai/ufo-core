"""Stored agent allowlists hold the Slack and iMessage setup tools by wire name again: the restore
migration turns each canonical surface action id back into the name this image registers, leaves
every other entry as it was, and its downgrade re-applies the ids. A fleet's rows — a SQL NULL, a
provisioned app's JSON `null`, an empty list, and mixed arrays — all survive both directions."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR
from ufo.schema import tables

BEFORE = "20260827153512"
SURFACE_ACTIONS = "20260828011033"
RESTORE = "20260828045120"
STAMP = datetime(2026, 8, 28, tzinfo=UTC)


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _seed(database_path: Path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite:///{database_path}")
    workspace_id = uuid4()
    with engine.connect() as connection:
        connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=STAMP, updated_at=STAMP)
        )
        for name, tools in (
            ("mixed", ["bash", "slack_connect", "slack_channels", "imessage_connect"]),
            ("manifest-only", ["slack_app_manifest"]),
            ("plain", ["bash", "read"]),
            ("empty", []),
            ("provisioned", None),
        ):
            connection.execute(
                sa.insert(tables.agent).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    name=name,
                    prompt="p",
                    model="auto",
                    tools=tools,
                    created_at=STAMP,
                    updated_at=STAMP,
                )
            )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, is_main, created_at, "
                "updated_at) values (:id, :workspace_id, 'unset', 'p', 'auto', 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": uuid4().hex, "workspace_id": workspace_id.hex},
        )
        connection.commit()
    return engine


def _stored_tools(engine: sa.Engine) -> dict[str, str | None]:
    with engine.connect() as connection:
        rows = connection.execute(sa.text("select name, tools from agent order by name")).all()
    return {row.name: row.tools for row in rows}


def test_the_restore_returns_stored_allowlists_to_the_wire_names(tmp_path: Path) -> None:
    database_path = tmp_path / "restore-surface-names.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    engine = _seed(database_path)
    seeded = _stored_tools(engine)

    command.upgrade(config, SURFACE_ACTIONS)
    rewritten = _stored_tools(engine)
    assert json.loads(rewritten["mixed"]) == [
        "bash",
        "action:surface:slack_connect",
        "action:surface:slack_channels",
        "action:surface:imessage_connect",
    ]

    command.upgrade(config, RESTORE)
    assert _stored_tools(engine) == seeded

    command.downgrade(config, SURFACE_ACTIONS)
    assert _stored_tools(engine) == rewritten
    assert _stored_tools(engine)["provisioned"] == "null"
    assert _stored_tools(engine)["unset"] is None
    engine.dispose()
