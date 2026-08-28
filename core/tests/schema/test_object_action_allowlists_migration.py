import json
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR
from ufo.schema import tables

BEFORE = "20260828052547"
REVISION = "20260828010853"
OLD = (
    "slack_connect",
    "slack_app_manifest",
    "slack_channels",
    "imessage_connect",
    "skill_search",
    "monitor",
    "add_member",
    "restore_application",
    "request_credentials",
    "grant_web_access",
    "revoke_web_access",
    "read_private_transcript",
    "connect_github",
    "manage_billing",
    "rebuild_page_facts",
    "rebuild_report_digest",
    "deploy_website",
    "publish_website",
    "build_website",
    "build_ufo_application",
    "render_application_preview",
    "set_homepage",
    "generate_image",
    "generate_video",
)
CANONICAL = (
    "action:surface:slack_connect",
    "action:surface:slack_app_manifest",
    "action:surface:slack_channels",
    "action:surface:imessage_connect",
    "action:skill:skill_search",
    "action:monitor:monitor",
    "action:member:add_member",
    "action:agent:restore_application",
    "action:credential:request_credentials",
    "action:member:grant_web_access",
    "action:member:revoke_web_access",
    "action:conversation:read_private_transcript",
    "action:credential:connect_github",
    "action:workspace:manage_billing",
    "action:page:rebuild_page_facts",
    "action:report:rebuild_report_digest",
    "action:site:deploy_website",
    "action:site:publish_website",
    "action:site:build_website",
    "action:site:build_ufo_application",
    "action:site:render_application_preview",
    "action:agent:set_homepage",
    "action:artifact:generate_image",
    "action:artifact:generate_video",
)


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
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        for name, tools in (
            ("moved", ["bash", *OLD]),
            ("plain", ["bash", "read"]),
            ("json-null", None),
        ):
            connection.execute(
                sa.insert(tables.agent).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    name=name,
                    prompt="p",
                    model="auto",
                    is_main=False,
                    tools=tools,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, is_main, created_at, "
                "updated_at) values (:id, :workspace, 'sql-null', 'p', 'auto', 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": uuid4().hex, "workspace": workspace_id.hex},
        )
        for extension, key, value in (
            ("web", "homepage-seed/moved", "withheld-tools"),
            ("web", "homepage-seed/plain", "shipped"),
            ("sites", "homepage-seed/moved", "withheld-tools"),
        ):
            connection.execute(
                sa.insert(tables.ext_store).values(
                    workspace_id=workspace_id,
                    extension=extension,
                    key=key,
                    value=value,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        connection.commit()
    return engine


def _tools(engine: sa.Engine) -> dict[str, list[str] | None]:
    with engine.connect() as connection:
        rows = connection.execute(sa.text("select name, tools from agent order by name"))
        return {row.name: None if row.tools is None else json.loads(row.tools) for row in rows}


def _markers(engine: sa.Engine) -> list[tuple[str, str, str]]:
    with engine.connect() as connection:
        rows = connection.execute(
            sa.select(
                tables.ext_store.c.extension, tables.ext_store.c.key, tables.ext_store.c.value
            ).order_by(tables.ext_store.c.extension, tables.ext_store.c.key)
        )
        return [tuple(row) for row in rows]


def test_stored_allowlists_move_to_object_action_ids_and_back(tmp_path: Path) -> None:
    database_path = tmp_path / "object-action-allowlists.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    engine = _seed(database_path)

    command.upgrade(config, REVISION)
    assert _tools(engine) == {
        "json-null": None,
        "moved": ["bash", *CANONICAL],
        "plain": ["bash", "read"],
        "sql-null": None,
    }
    assert _markers(engine) == [
        ("sites", "homepage-seed/moved", "withheld-tools"),
        ("web", "homepage-seed/plain", "shipped"),
    ]

    command.downgrade(config, BEFORE)
    assert _tools(engine) == {
        "json-null": None,
        "moved": ["bash", *OLD],
        "plain": ["bash", "read"],
        "sql-null": None,
    }
    engine.dispose()
