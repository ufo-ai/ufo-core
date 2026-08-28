"""Stored agent allowlists follow the Slack and iMessage setup tools onto the surface kind: each old
wire name becomes its canonical action id, every other entry stands, and the downgrade restores the
names the old image registers. The rows a fleet actually holds are all here — a provisioned app
whose declaration names no allowlist is written through the application's own insert as JSON
`null`, beside a SQL NULL, an empty list, and mixed arrays — and every one survives both
directions."""

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
    """The `tools` column as text — a JSON `null` reads as the string 'null', a SQL NULL as
    None, so the two rows a fleet holds stay distinguishable in the assertion."""
    with engine.connect() as connection:
        rows = connection.execute(sa.text("select name, tools from agent order by name")).all()
    return {row.name: row.tools for row in rows}


def test_stored_allowlists_move_to_the_canonical_action_ids_and_back(tmp_path: Path) -> None:
    database_path = tmp_path / "surface-actions.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    engine = _seed(database_path)
    before = _stored_tools(engine)
    assert before["provisioned"] == "null"
    assert before["unset"] is None

    command.upgrade(config, SURFACE_ACTIONS)
    after = _stored_tools(engine)
    assert json.loads(after["mixed"]) == [
        "bash",
        "action:surface:slack_connect",
        "action:surface:slack_channels",
        "action:surface:imessage_connect",
    ]
    assert json.loads(after["manifest-only"]) == ["action:surface:slack_app_manifest"]
    assert json.loads(after["plain"]) == ["bash", "read"]
    assert json.loads(after["empty"]) == []
    assert after["provisioned"] == "null"
    assert after["unset"] is None

    command.downgrade(config, BEFORE)
    assert _stored_tools(engine) == before
    engine.dispose()
