import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR
from ufo.ext.loader import migration_locations

NOW = datetime(2026, 8, 19, tzinfo=UTC)


def _config(path: Path) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option(
        "version_locations",
        os.pathsep.join((str(MIGRATIONS_DIR / "versions"), *migration_locations())),
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    return config


def test_application_editions_remove_only_the_provisioned_daily_brief(tmp_path: Path) -> None:
    path = tmp_path / "daily-brief-application.db"
    config = _config(path)
    command.upgrade(config, "sweep_0001")
    engine = sa.create_engine(f"sqlite:///{path}")
    metadata = sa.MetaData()
    metadata.reflect(engine)
    workspace_id, member_id = uuid4(), uuid4()
    provisioned_agent_id, member_agent_id = uuid4(), uuid4()
    provisioned_conversation_id, member_conversation_id = uuid4(), uuid4()
    provisioned_turn_id, member_turn_id = uuid4(), uuid4()
    provisioned_ledger_id, member_ledger_id = uuid4(), uuid4()

    def stored(value: UUID) -> str:
        return value.hex

    with engine.connect() as connection:
        connection.execute(
            metadata.tables["workspace"]
            .insert()
            .values(id=stored(workspace_id), created_at=NOW, updated_at=NOW)
        )
        connection.execute(
            metadata.tables["member"]
            .insert()
            .values(
                id=stored(member_id),
                workspace_id=stored(workspace_id),
                email="member@example.com",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            metadata.tables["agent"].insert(),
            (
                {
                    "id": stored(provisioned_agent_id),
                    "workspace_id": stored(workspace_id),
                    "name": "daily-brief-sweep",
                    "prompt": "Prepare a brief.",
                    "model": "auto",
                    "provisioned_by": "sweep",
                    "provisioned_name": "daily-brief",
                    "provisioned_version": "0.1.0",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": stored(member_agent_id),
                    "workspace_id": stored(workspace_id),
                    "name": "daily-brief",
                    "prompt": "Member application.",
                    "model": "auto",
                    "provisioned_by": None,
                    "provisioned_name": None,
                    "provisioned_version": None,
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ),
        )
        connection.execute(
            metadata.tables["conversation"].insert(),
            (
                {
                    "id": stored(provisioned_conversation_id),
                    "workspace_id": stored(workspace_id),
                    "agent_id": stored(provisioned_agent_id),
                    "surface": "extension:sweep",
                    "queue_key": f"daily-brief:{member_id}:2026-08-19",
                    "member_id": stored(member_id),
                    "audience": f"member:{member_id}",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": stored(member_conversation_id),
                    "workspace_id": stored(workspace_id),
                    "agent_id": stored(member_agent_id),
                    "surface": "web",
                    "queue_key": "member-daily-brief",
                    "member_id": stored(member_id),
                    "audience": f"member:{member_id}",
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ),
        )
        connection.execute(
            metadata.tables["turn"].insert(),
            (
                {
                    "id": stored(provisioned_turn_id),
                    "workspace_id": stored(workspace_id),
                    "conversation_id": stored(provisioned_conversation_id),
                    "agent_id": stored(provisioned_agent_id),
                    "seq": 1,
                    "status": "done",
                    "inbound": "Prepare today's private daily brief.",
                    "terminal": {"status": "done", "text": "Brief."},
                    "created_at": NOW,
                    "updated_at": NOW,
                },
                {
                    "id": stored(member_turn_id),
                    "workspace_id": stored(workspace_id),
                    "conversation_id": stored(member_conversation_id),
                    "agent_id": stored(member_agent_id),
                    "seq": 1,
                    "status": "done",
                    "inbound": "Prepare my application.",
                    "terminal": {"status": "done", "text": "Application."},
                    "created_at": NOW,
                    "updated_at": NOW,
                },
            ),
        )
        connection.execute(
            metadata.tables["ledger"].insert(),
            tuple(
                {
                    "id": stored(ledger_id),
                    "workspace_id": stored(workspace_id),
                    "turn_id": stored(turn_id),
                    "dimension": "tokens",
                    "amount": 1,
                    "prompt_tokens": 1,
                    "cache_read_tokens": 0,
                    "priced_micro_usd": 1,
                    "model": "test",
                    "created_at": NOW,
                    "updated_at": NOW,
                }
                for ledger_id, turn_id in (
                    (provisioned_ledger_id, provisioned_turn_id),
                    (member_ledger_id, member_turn_id),
                )
            ),
        )
        connection.execute(
            metadata.tables["shared_artifact"]
            .insert()
            .values(
                turn_id=stored(provisioned_turn_id),
                blob_key="brief.md",
                id=stored(uuid4()),
                workspace_id=stored(workspace_id),
                filename="brief.md",
                media_type="text/markdown",
                size_bytes=5,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            metadata.tables["sweep_edition"]
            .insert()
            .values(
                workspace_id=stored(workspace_id),
                member_id=stored(member_id),
                local_date="2026-08-19",
                timezone="America/Los_Angeles",
                status="completed",
                attempt=1,
                conversation_id=stored(provisioned_conversation_id),
                turn_id=stored(provisioned_turn_id),
                candidate_cursor=NOW,
                candidate_input_keys=["conversation:one"],
                candidate_finding_keys=["finding:one"],
                completed_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        connection.execute(
            metadata.tables["ext_store"].insert(),
            tuple(
                {
                    "workspace_id": stored(workspace_id),
                    "extension": extension,
                    "key": key,
                    "value": {},
                    "created_at": NOW,
                    "updated_at": NOW,
                }
                for extension, key in (
                    ("todos", f"todo/{provisioned_conversation_id}"),
                    ("todos", f"todo/{member_conversation_id}"),
                    ("web", f"chat/{provisioned_conversation_id}"),
                    ("web", f"chat/{member_conversation_id}"),
                    ("web", f"homepage-seed/{provisioned_agent_id}"),
                    ("web", f"homepage-seed/{member_agent_id}"),
                    ("web", f"audience/{provisioned_agent_id}/reader@example.com"),
                    ("web", f"audience/{member_agent_id}/reader@example.com"),
                )
            ),
        )
        connection.commit()

    command.upgrade(config, "sweep_0002")
    metadata = sa.MetaData()
    metadata.reflect(engine)
    with engine.connect() as connection:
        agents = connection.execute(sa.select(metadata.tables["agent"].c.id)).scalars().all()
        conversations = (
            connection.execute(sa.select(metadata.tables["conversation"].c.id)).scalars().all()
        )
        turns = connection.execute(sa.select(metadata.tables["turn"].c.id)).scalars().all()
        ledger = dict(
            connection.execute(
                sa.select(metadata.tables["ledger"].c.id, metadata.tables["ledger"].c.turn_id)
            ).all()
        )
        artifacts = (
            connection.execute(sa.select(metadata.tables["shared_artifact"].c.turn_id))
            .scalars()
            .all()
        )
        edition = connection.execute(sa.select(metadata.tables["sweep_edition"])).one()
        ext_store_keys = set(
            connection.execute(
                sa.select(
                    metadata.tables["ext_store"].c.extension,
                    metadata.tables["ext_store"].c.key,
                )
            ).all()
        )
    engine.dispose()

    assert agents == [stored(member_agent_id)]
    assert conversations == [stored(member_conversation_id)]
    assert turns == [stored(member_turn_id)]
    assert ledger == {
        stored(provisioned_ledger_id): None,
        stored(member_ledger_id): stored(member_turn_id),
    }
    assert artifacts == []
    assert edition.status == "completed"
    assert edition.turn_id is None
    assert edition.candidate_input_keys == ["conversation:one"]
    assert edition.candidate_finding_keys == ["finding:one"]
    assert ext_store_keys == {
        ("todos", f"todo/{member_conversation_id}"),
        ("web", f"chat/{member_conversation_id}"),
        ("web", f"homepage-seed/{member_agent_id}"),
        ("web", f"audience/{member_agent_id}/reader@example.com"),
    }
    assert "attempt" not in metadata.tables["sweep_edition"].c
    assert "conversation_id" not in metadata.tables["sweep_edition"].c
    assert set(metadata.tables["sweep_application"].c.keys()) == {
        "workspace_id",
        "conversation_id",
        "member_id",
        "agent_id",
        "created_at",
        "updated_at",
    }
