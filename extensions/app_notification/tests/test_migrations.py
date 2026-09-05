"""The extension's migrations carry each release's declaration onto the `notification` agent rows a
workspace already holds: a provisioning pass writes setup and purpose alone, so without this the
prompt a release declares reaches new workspaces only. A row moves where it still says what the
release that created it said; a member's own wording, an archived row, and another extension's row
stand."""

from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from ufo_ext_app_notification.manifest import NAME, NOTIFICATION_AGENT_PROMPT, VERSION

from ufo.db import MIGRATIONS_DIR
from ufo.host.ext.loader import migration_locations

CORE_HEAD = (MIGRATIONS_DIR / "versions" / "HEAD").read_text().strip()
RELEASED = "notification_0001"
TRIAGE = "notification_0002"


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        "\n".join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "newline")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def _revision(config: Config, revision: str) -> ModuleType:
    return ScriptDirectory.from_config(config).get_revision(revision).module


TRIAGE_MODULE = _revision(_config(Path("unused.db")), TRIAGE)
RELEASED_PROMPT: str = TRIAGE_MODULE.RELEASED_PROMPT
RELEASED_VERSION: str = TRIAGE_MODULE.RELEASED_VERSION


def _seed(database_path: Path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite:///{database_path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) values "
                "('w', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('x', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('y', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, prompt, model, is_main, visibility, "
                "provisioned_by, provisioned_name, provisioned_version, created_at, updated_at) "
                "values (:id, :ws, :name, :prompt, 'auto', 0, 'workspace', :by, :declared, "
                ":version, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            [
                {
                    "id": "shipped",
                    "ws": "w",
                    "name": "notification",
                    "prompt": RELEASED_PROMPT,
                    "by": NAME,
                    "declared": "notification",
                    "version": RELEASED_VERSION,
                },
                {
                    "id": "edited",
                    "ws": "x",
                    "name": "notification-app-notification",
                    "prompt": "only tell me about churn",
                    "by": NAME,
                    "declared": "notification",
                    "version": RELEASED_VERSION,
                },
                {
                    "id": "other",
                    "ws": "w",
                    "name": "radar",
                    "prompt": RELEASED_PROMPT,
                    "by": "app_radar",
                    "declared": "radar",
                    "version": RELEASED_VERSION,
                },
            ],
        )
        connection.execute(
            sa.text(
                "insert into agent (id, workspace_id, name, archived_name, archived_at, prompt, "
                "model, is_main, visibility, provisioned_by, provisioned_name, "
                "provisioned_version, created_at, updated_at) values "
                "('archived', 'y', '~archived-a', 'notification', CURRENT_TIMESTAMP, :prompt, "
                "'auto', 0, 'workspace', :by, 'notification', :version, CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            ),
            {"prompt": RELEASED_PROMPT, "by": NAME, "version": RELEASED_VERSION},
        )
        connection.commit()
    return engine


def _rows(engine: sa.Engine) -> dict[str, tuple[str, str]]:
    with engine.connect() as connection:
        return {
            row.id: (row.prompt, row.provisioned_version)
            for row in connection.execute(
                sa.text("select id, prompt, provisioned_version from agent")
            ).all()
        }


def test_the_triage_prompt_reaches_the_shipped_row_and_no_other(tmp_path: Path) -> None:
    database_path = tmp_path / "notification.db"
    config = _config(database_path)
    command.upgrade(config, CORE_HEAD)
    command.upgrade(config, RELEASED)
    engine = _seed(database_path)
    command.upgrade(config, TRIAGE)
    after = _rows(engine)
    command.downgrade(config, RELEASED)
    restored = _rows(engine)

    assert after["shipped"] == (NOTIFICATION_AGENT_PROMPT, VERSION)
    assert after["edited"] == ("only tell me about churn", RELEASED_VERSION)
    assert after["other"] == (RELEASED_PROMPT, RELEASED_VERSION)
    assert after["archived"] == (RELEASED_PROMPT, RELEASED_VERSION)
    assert restored["shipped"] == (RELEASED_PROMPT, RELEASED_VERSION)
