from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

BEFORE = "20260826235718"
CHAT = "20260827015500"

WORKSPACES = ("ordinary", "renamed", "name-taken", "put-away", "no-chat-app")

AGENTS = (
    ("ordinary-main", "ordinary", "assistant", True, "ufo", None),
    ("ordinary-chat", "ordinary", "chat", False, "message-circle", ("app_chat", "chat")),
    ("ordinary-radar", "ordinary", "radar", False, "radar", ("app_radar", "radar")),
    ("renamed-main", "renamed", "ufo", True, "propylon", None),
    ("renamed-chat", "renamed", "talk", False, "message-circle", ("app_chat", "chat")),
    ("taken-main", "name-taken", "assistant", True, "ufo", None),
    ("taken-mine", "name-taken", "chat", False, "kalyx", None),
    ("taken-chat", "name-taken", "chat-app-chat", False, "message-circle", ("app_chat", "chat")),
    ("away-main", "put-away", "assistant", True, "ufo", None),
    ("bare-main", "no-chat-app", "assistant", True, "ufo", None),
)
AWAY_CHAT = "away-chat"


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _archived(agent_id: UUID) -> str:
    return f"~archived-{agent_id.hex}"


def _seed(database_path: Path) -> tuple[sa.Engine, dict[str, UUID]]:
    ids = {label: uuid4() for label in (*WORKSPACES, *(row[0] for row in AGENTS), AWAY_CHAT)}
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        for workspace in WORKSPACES:
            connection.execute(
                sa.text(
                    "insert into workspace (id, created_at, updated_at) "
                    "values (:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": ids[workspace].hex},
            )
        for label, workspace, name, is_main, icon, provision in AGENTS:
            provisioned_by, provisioned_name = provision or (None, None)
            connection.execute(
                sa.text(
                    "insert into agent (id, workspace_id, name, icon, prompt, model, is_main, "
                    "visibility, provisioned_by, provisioned_name, provisioned_version, "
                    "created_at, updated_at) values "
                    "(:id, :workspace, :name, :icon, 'be useful', 'auto', :main, 'workspace', "
                    ":by, :declared, :version, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": ids[label].hex,
                    "workspace": ids[workspace].hex,
                    "name": name,
                    "icon": icon,
                    "main": is_main,
                    "by": provisioned_by,
                    "declared": provisioned_name,
                    "version": None if provision is None else "0.1.0",
                },
            )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, archived_name, archived_at, icon, "
                "prompt, model, is_main, visibility, provisioned_by, provisioned_name, "
                "provisioned_version, created_at, updated_at) values "
                "(:id, :workspace, :name, 'chat', CURRENT_TIMESTAMP, 'message-circle', "
                "'be useful', 'auto', 0, 'workspace', 'app_chat', 'chat', '0.1.0', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {
                "id": ids[AWAY_CHAT].hex,
                "workspace": ids["put-away"].hex,
                "name": _archived(ids[AWAY_CHAT]),
            },
        )
        connection.commit()
    return engine, ids


def _state(
    engine: sa.Engine, ids: dict[str, UUID]
) -> dict[str, tuple[str, str | None, bool, str | None, str | None]]:
    labels = {ids[label].hex: label for label in ids}
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "select id, name, archived_name, is_main, provisioned_by, provisioned_name "
                "from agent"
            )
        ).all()
    return {
        labels[row.id]: (
            row.name,
            row.archived_name,
            bool(row.is_main),
            row.provisioned_by,
            row.provisioned_name,
        )
        for row in rows
    }


def test_the_main_agent_becomes_the_chat_app_and_the_shipped_row_is_put_away(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "chat-main.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    engine, ids = _seed(database_path)

    command.upgrade(config, CHAT)
    assert _state(engine, ids) == {
        "ordinary-main": ("chat", None, True, "app_chat", "chat"),
        "ordinary-chat": (_archived(ids["ordinary-chat"]), "chat", False, None, None),
        "ordinary-radar": ("radar", None, False, "app_radar", "radar"),
        "renamed-main": ("ufo", None, True, "app_chat", "chat"),
        "renamed-chat": (_archived(ids["renamed-chat"]), "talk", False, None, None),
        "taken-main": ("assistant", None, True, "app_chat", "chat"),
        "taken-mine": ("chat", None, False, None, None),
        "taken-chat": (_archived(ids["taken-chat"]), "chat-app-chat", False, None, None),
        "away-main": ("chat", None, True, "app_chat", "chat"),
        "away-chat": (_archived(ids[AWAY_CHAT]), "chat", False, None, None),
        "bare-main": ("assistant", None, True, None, None),
    }
    with engine.connect() as connection:
        held = connection.execute(
            sa.text(
                "select prompt, icon, visibility, provisioned_version from agent where id = :id"
            ),
            {"id": ids["ordinary-main"].hex},
        ).one()
    assert tuple(held) == ("be useful", "ufo", "workspace", "0.2.0")
    engine.dispose()


def test_a_downgrade_leaves_the_app_on_the_main_agent_and_a_rerun_changes_nothing(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "chat-main-down.db"
    config = _config(database_path)
    command.upgrade(config, BEFORE)
    engine, ids = _seed(database_path)

    command.upgrade(config, CHAT)
    moved = _state(engine, ids)
    command.downgrade(config, BEFORE)
    assert _state(engine, ids) == moved
    command.upgrade(config, CHAT)
    assert _state(engine, ids) == moved
    engine.dispose()
