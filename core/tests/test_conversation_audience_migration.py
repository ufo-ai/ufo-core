from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR


def test_conversation_audience_migration_fails_before_ddl_on_unclassified_slack_history(
    tmp_path: Path,
) -> None:
    path = tmp_path / "conversation-audience.db"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    command.upgrade(config, "0054")

    workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
    shared_id, private_id, cli_id, slack_turn_id, cli_turn_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    moment = datetime(2026, 7, 27, tzinfo=UTC)
    engine = sa.create_engine(f"sqlite:///{path}")
    metadata = sa.MetaData()
    metadata.reflect(engine, only=("workspace", "member", "agent", "conversation", "turn"))

    def stored(value: UUID) -> str:
        return value.hex

    with engine.connect() as connection:
        connection.execute(
            metadata.tables["workspace"]
            .insert()
            .values(id=stored(workspace_id), created_at=moment, updated_at=moment)
        )
        connection.execute(
            metadata.tables["member"]
            .insert()
            .values(
                id=stored(member_id),
                workspace_id=stored(workspace_id),
                email="member@example.com",
                created_at=moment,
                updated_at=moment,
            )
        )
        connection.execute(
            metadata.tables["agent"]
            .insert()
            .values(
                id=stored(agent_id),
                workspace_id=stored(workspace_id),
                name="assistant",
                prompt="p",
                model="m",
                internet_access_allowed=True,
                created_at=moment,
                updated_at=moment,
            )
        )
        for conversation_id, queue_key, row_member in (
            (shared_id, "shared", None),
            (private_id, "private", stored(member_id)),
            (cli_id, "cli", None),
        ):
            connection.execute(
                metadata.tables["conversation"]
                .insert()
                .values(
                    id=stored(conversation_id),
                    workspace_id=stored(workspace_id),
                    agent_id=stored(agent_id),
                    surface="cli" if conversation_id == cli_id else "slack",
                    queue_key=queue_key,
                    member_id=row_member,
                    created_at=moment,
                    updated_at=moment,
                )
            )
        for turn_id, conversation_id in (
            (slack_turn_id, shared_id),
            (cli_turn_id, cli_id),
        ):
            connection.execute(
                metadata.tables["turn"]
                .insert()
                .values(
                    id=stored(turn_id),
                    workspace_id=stored(workspace_id),
                    conversation_id=stored(conversation_id),
                    agent_id=stored(agent_id),
                    seq=1,
                    status="done",
                    inbound="history",
                    admission_source="member",
                    terminal={"status": "done"},
                    created_at=moment,
                    updated_at=moment,
                )
            )
        connection.commit()

    with pytest.raises(
        RuntimeError,
        match="existing memberless Slack conversation history has no provable disclosure audience",
    ):
        command.upgrade(config, "0055")
    assert "audience" not in {
        column["name"] for column in sa.inspect(engine).get_columns("conversation")
    }

    with engine.connect() as connection:
        connection.execute(
            metadata.tables["turn"]
            .delete()
            .where(metadata.tables["turn"].c.id == stored(slack_turn_id))
        )
        connection.commit()
    command.upgrade(config, "0055")
    with engine.connect() as connection:
        rows = dict(
            connection.execute(
                sa.text("select queue_key, audience from conversation order by queue_key")
            ).all()
        )
    engine.dispose()

    assert rows == {
        "cli": "shared",
        "private": f"member:{member_id}",
        "shared": "shared",
    }
