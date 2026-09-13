from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR

MOMENT = datetime(2026, 9, 13, tzinfo=UTC)
BEFORE = "20260913055555"
AFTER = "20260913095851"


def test_shared_artifact_member_backfills_the_speaker_and_stays_optional(
    tmp_path: Path,
) -> None:
    path = tmp_path / "shared-artifact-member.db"
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    command.upgrade(config, BEFORE)

    workspace_id, owner_id, speaker_id, agent_id = uuid4(), uuid4(), uuid4(), uuid4()
    conversation_id, turn_id, fallback_turn_id = uuid4(), uuid4(), uuid4()
    engine = sa.create_engine(f"sqlite:///{path}")
    before = sa.MetaData()
    before.reflect(
        engine, only=("workspace", "member", "agent", "conversation", "turn", "shared_artifact")
    )

    def stored(value: UUID) -> str:
        return value.hex

    with engine.connect() as connection:
        connection.execute(
            before.tables["workspace"]
            .insert()
            .values(id=stored(workspace_id), created_at=MOMENT, updated_at=MOMENT)
        )
        for member_id, email in (
            (owner_id, "owner@example.com"),
            (speaker_id, "speaker@example.com"),
        ):
            connection.execute(
                before.tables["member"]
                .insert()
                .values(
                    id=stored(member_id),
                    workspace_id=stored(workspace_id),
                    email=email,
                    created_at=MOMENT,
                    updated_at=MOMENT,
                )
            )
        connection.execute(
            before.tables["agent"]
            .insert()
            .values(
                id=stored(agent_id),
                workspace_id=stored(workspace_id),
                name="assistant",
                prompt="p",
                model="m",
                internet_access_allowed=True,
                created_at=MOMENT,
                updated_at=MOMENT,
            )
        )
        connection.execute(
            before.tables["conversation"]
            .insert()
            .values(
                id=stored(conversation_id),
                workspace_id=stored(workspace_id),
                agent_id=stored(agent_id),
                surface="web",
                queue_key="artifact-member",
                member_id=stored(owner_id),
                audience=f"member:{owner_id}",
                created_at=MOMENT,
                updated_at=MOMENT,
            )
        )
        connection.execute(
            before.tables["turn"]
            .insert()
            .values(
                id=stored(turn_id),
                workspace_id=stored(workspace_id),
                conversation_id=stored(conversation_id),
                agent_id=stored(agent_id),
                seq=1,
                status="done",
                inbound="seed",
                admission_source="member",
                speaker_member_id=stored(speaker_id),
                terminal='{"status": "done"}',
                created_at=MOMENT,
                updated_at=MOMENT,
            )
        )
        connection.execute(
            before.tables["shared_artifact"]
            .insert()
            .values(
                id=stored(uuid4()),
                turn_id=stored(turn_id),
                blob_key=f"artifacts/{uuid4()}/before.txt",
                workspace_id=stored(workspace_id),
                filename="before.txt",
                media_type="text/plain",
                size_bytes=3,
                created_at=MOMENT,
                updated_at=MOMENT,
            )
        )
        connection.execute(
            before.tables["turn"]
            .insert()
            .values(
                id=stored(fallback_turn_id),
                workspace_id=stored(workspace_id),
                conversation_id=stored(conversation_id),
                agent_id=stored(agent_id),
                seq=2,
                status="done",
                inbound="seed",
                admission_source="internal",
                terminal='{"status": "done"}',
                created_at=MOMENT,
                updated_at=MOMENT,
            )
        )
        connection.execute(
            before.tables["shared_artifact"]
            .insert()
            .values(
                id=stored(uuid4()),
                turn_id=stored(fallback_turn_id),
                blob_key=f"artifacts/{uuid4()}/fallback.txt",
                workspace_id=stored(workspace_id),
                filename="fallback.txt",
                media_type="text/plain",
                size_bytes=3,
                created_at=MOMENT,
                updated_at=MOMENT,
            )
        )
        connection.commit()

    command.upgrade(config, AFTER)
    after = sa.MetaData()
    after.reflect(engine, only=("shared_artifact",))
    with engine.connect() as connection:
        connection.execute(
            before.tables["shared_artifact"]
            .insert()
            .values(
                id=stored(uuid4()),
                turn_id=stored(turn_id),
                blob_key=f"artifacts/{uuid4()}/rolling.txt",
                workspace_id=stored(workspace_id),
                filename="rolling.txt",
                media_type="text/plain",
                size_bytes=3,
                created_at=MOMENT,
                updated_at=MOMENT,
            )
        )
        rows = dict(
            connection.execute(
                sa.select(
                    after.tables["shared_artifact"].c.filename,
                    after.tables["shared_artifact"].c.member_id,
                )
            ).all()
        )
        connection.commit()

    assert rows == {
        "before.txt": stored(speaker_id),
        "fallback.txt": stored(owner_id),
        "rolling.txt": None,
    }
