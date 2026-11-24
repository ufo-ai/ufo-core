import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR
from ufo.host.ext.loader import migration_locations


def _config(database_path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{database_path}")
    return config


def test_existing_tasks_default_to_parent_channel_delivery(tmp_path: Path) -> None:
    database = tmp_path / "scheduled-task-post-in-thread.db"
    config = _config(database)
    command.upgrade(config, "20260927025054")
    command.upgrade(config, "scheduled_tasks_0004")
    engine = sa.create_engine(f"sqlite:///{database}")
    workspace_id, agent_id, conversation_id, task_id = (uuid4().hex for _ in range(4))
    now = datetime.now(UTC)
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
            {"id": workspace_id, "now": now},
        )
        connection.execute(
            sa.text(
                "insert into agent "
                "(id, workspace_id, name, prompt, model, created_at, updated_at) values "
                "(:id, :workspace, 'assistant', 'p', 'claude-opus-5-5', :now, :now)"
            ),
            {"id": agent_id, "workspace": workspace_id, "now": now},
        )
        connection.execute(
            sa.text(
                "insert into conversation "
                "(id, workspace_id, agent_id, surface, queue_key, created_at, updated_at) values "
                "(:id, :workspace, :agent, 'slack', 'C1:100.0', :now, :now)"
            ),
            {
                "id": conversation_id,
                "workspace": workspace_id,
                "agent": agent_id,
                "now": now,
            },
        )
        connection.execute(
            sa.text(
                "insert into scheduled_task "
                "(id, workspace_id, conversation_id, agent_id, name, schedule, prompt, "
                "description, next_run_at, paused, internet_access, created_at, updated_at) values "
                "(:id, :workspace, :conversation, :agent, 'digest', '0 9 * * *', 'report', "
                "'Daily report', :now, 0, 0, :now, :now)"
            ),
            {
                "id": task_id,
                "workspace": workspace_id,
                "conversation": conversation_id,
                "agent": agent_id,
                "now": now,
            },
        )
        connection.commit()

    command.upgrade(config, "scheduled_tasks_0005")

    with engine.connect() as connection:
        post_in_thread = connection.scalar(
            sa.text("select post_in_thread from scheduled_task where id = :id"),
            {"id": task_id},
        )
    engine.dispose()
    assert post_in_thread == 0
