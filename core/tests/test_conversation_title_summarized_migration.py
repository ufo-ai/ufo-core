from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR


def test_title_summarized_migration_leaves_every_conversation_awaiting_a_summary(
    tmp_path: Path,
) -> None:
    """Which conversations still await a summarized title was the web extension's own
    bookkeeping: a `chat/<id>` row per portal chat it opened, and a `chat_title_pending/<id>` row
    beside it until the job had summarized that chat. Every conversation that exists stands as
    awaiting a summary, whatever those rows say — a portal chat the job already named is named once
    more, which costs one summary, while a conversation wrongly recorded as summarized would keep
    its raw first message for good. The pending rows go, because the column is the state they held,
    and nothing else would ever delete them."""
    path = tmp_path / "title-summarized.db"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    command.upgrade(config, "0095")

    workspace_id, agent_id = uuid4(), uuid4()
    summarized_id, pending_id, slack_id = uuid4(), uuid4(), uuid4()
    moment = datetime(2026, 8, 16, tzinfo=UTC)
    engine = sa.create_engine(f"sqlite:///{path}")
    metadata = sa.MetaData()
    metadata.reflect(engine, only=("workspace", "agent", "conversation", "ext_store"))

    def stored(value: UUID) -> str:
        return value.hex

    with engine.connect() as connection:
        connection.execute(
            metadata.tables["workspace"]
            .insert()
            .values(id=stored(workspace_id), created_at=moment, updated_at=moment)
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
        for conversation_id, surface, title in (
            (summarized_id, "web", "Restock the depot"),
            (pending_id, "web", "order more pallets before"),
            (slack_id, "slack", "Review this Slack message"),
        ):
            connection.execute(
                metadata.tables["conversation"]
                .insert()
                .values(
                    id=stored(conversation_id),
                    workspace_id=stored(workspace_id),
                    agent_id=stored(agent_id),
                    surface=surface,
                    queue_key=stored(conversation_id),
                    title=title,
                    created_at=moment,
                    updated_at=moment,
                )
            )
        for key in (
            f"chat/{summarized_id}",
            f"chat/{pending_id}",
            f"chat_title_pending/{pending_id}",
            "audience/someone@example.com",
        ):
            connection.execute(
                metadata.tables["ext_store"]
                .insert()
                .values(
                    workspace_id=stored(workspace_id),
                    extension="web",
                    key=key,
                    value={},
                    created_at=moment,
                    updated_at=moment,
                )
            )
        connection.commit()

    command.upgrade(config, "0096")
    with engine.connect() as connection:
        awaiting = dict(
            connection.execute(
                sa.text("select queue_key, title_summarized from conversation")
            ).all()
        )
        keys = {
            key
            for (key,) in connection.execute(
                sa.text("select key from ext_store where extension = 'web'")
            ).all()
        }
    engine.dispose()

    assert awaiting == {
        stored(summarized_id): 0,
        stored(pending_id): 0,
        stored(slack_id): 0,
    }
    assert keys == {
        "audience/someone@example.com",
        f"chat/{pending_id}",
        f"chat/{summarized_id}",
    }
