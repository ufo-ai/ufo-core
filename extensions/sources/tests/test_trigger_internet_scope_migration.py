import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from ufo_ext_sources.tools import trigger_name

from ufo.db import MIGRATIONS_DIR, workspace_tx
from ufo.host.ext.loader import migration_locations
from ufo.schema import tables
from ufo.sdk.grants import account_object_name


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


def test_existing_trigger_scope_backfills_to_inherit(tmp_path: Path) -> None:
    database = tmp_path / "source-trigger-internet-scope.db"
    config = _config(database)
    command.upgrade(config, "0085")
    command.upgrade(config, "sources_0007")
    engine = sa.create_engine(f"sqlite:///{database}")
    workspace_id, member_id, agent_id, conversation_id, connection_id, trigger_id, turn_id = (
        uuid4().hex for _ in range(7)
    )
    now = datetime.now(UTC)
    object_name = trigger_name(account_object_name("hub", "account"), UUID(conversation_id))
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
            {"id": workspace_id, "now": now},
        )
        connection.execute(
            sa.text(
                "insert into member (id, workspace_id, email, created_at, updated_at) "
                "values (:id, :workspace, 'speaker@example.com', :now, :now)"
            ),
            {"id": member_id, "workspace": workspace_id, "now": now},
        )
        connection.execute(
            sa.text(
                "insert into agent "
                "(id, workspace_id, name, prompt, model, created_at, updated_at) values "
                "(:id, :workspace, 'assistant', 'p', 'claude-opus-4-8', :now, :now)"
            ),
            {"id": agent_id, "workspace": workspace_id, "now": now},
        )
        connection.execute(
            sa.text(
                "insert into conversation "
                "(id, workspace_id, agent_id, surface, queue_key, created_at, updated_at) values "
                "(:id, :workspace, :agent, 'cli', :id, :now, :now)"
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
                "insert into connection "
                "(id, workspace_id, provider, account_id, host, owner_member_id, "
                "shared, created_at, updated_at) values "
                "(:id, :workspace, 'hub', 'account', 'api.hub.test', :member, 1, :now, :now)"
            ),
            {
                "id": connection_id,
                "workspace": workspace_id,
                "member": member_id,
                "now": now,
            },
        )
        connection.execute(
            sa.text(
                "insert into turn "
                "(id, workspace_id, conversation_id, agent_id, seq, status, inbound, "
                "admission_source, speaker_member_id, created_refs, created_at, updated_at) values "
                "(:id, :workspace, :conversation, :agent, 1, 'running', 'watch this feed', "
                "'member', :member, :created_refs, :now, :now)"
            ),
            {
                "id": turn_id,
                "workspace": workspace_id,
                "conversation": conversation_id,
                "agent": agent_id,
                "member": member_id,
                "created_refs": json.dumps([{"kind": "source_trigger", "name": object_name}]),
                "now": now,
            },
        )
        connection.execute(
            sa.text(
                "insert into source_trigger "
                "(id, workspace_id, conversation_id, agent_id, connection_id, resource, streams, "
                "delivery, created_by_member_id, created_at, updated_at) values "
                "(:id, :workspace, :conversation, :agent, :connection, '', '', 'current', "
                ":member, :now, :now)"
            ),
            {
                "id": trigger_id,
                "workspace": workspace_id,
                "conversation": conversation_id,
                "agent": agent_id,
                "connection": connection_id,
                "member": member_id,
                "now": now,
            },
        )
        connection.commit()
    command.upgrade(config, "sources_0009")
    with engine.connect() as connection:
        internet_access, requesting_message_ref = connection.execute(
            sa.text(
                "select internet_access, requesting_message_ref from source_trigger where id = :id"
            ),
            {"id": trigger_id},
        ).one()
        with pytest.raises(sa.exc.IntegrityError):
            connection.execute(
                sa.text(
                    "insert into source_trigger "
                    "(id, workspace_id, conversation_id, agent_id, connection_id, resource, "
                    "streams, delivery, created_by_member_id, created_at, updated_at) values "
                    "(:id, :workspace, :conversation, :agent, :connection, 'second', '', "
                    "'current', :member, :now, :now)"
                ),
                {
                    "id": uuid4().hex,
                    "workspace": workspace_id,
                    "conversation": conversation_id,
                    "agent": agent_id,
                    "connection": connection_id,
                    "member": member_id,
                    "now": now,
                },
            )
        connection.rollback()
    engine.dispose()
    assert internet_access == 1
    assert requesting_message_ref == turn_id


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["postgresql"], indirect=True)
async def test_omitted_trigger_scope_comes_from_the_active_member_turn(db: None) -> None:
    workspace_id, member_id, agent_id, connection_id = (uuid4() for _ in range(4))
    narrowed_conversation_id, ordinary_conversation_id, idle_conversation_id = (
        uuid4() for _ in range(3)
    )
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(id=workspace_id, created_at=now, updated_at=now)
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="trigger-speaker@example.com",
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.conversation),
            [
                {
                    "id": conversation_id,
                    "workspace_id": workspace_id,
                    "agent_id": agent_id,
                    "surface": "cli",
                    "queue_key": uuid4().hex,
                    "created_at": now,
                    "updated_at": now,
                }
                for conversation_id in (
                    narrowed_conversation_id,
                    ordinary_conversation_id,
                    idle_conversation_id,
                )
            ],
        )
        await connection.execute(
            sa.insert(tables.connection).values(
                id=connection_id,
                workspace_id=workspace_id,
                provider="hub",
                account_id="account",
                host="api.hub.test",
                owner_member_id=member_id,
                shared=True,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.turn),
            [
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "agent_id": agent_id,
                    "seq": 1,
                    "status": "running",
                    "inbound": "watch",
                    "admission_source": "member",
                    "speaker_member_id": member_id,
                    "runtime_config": runtime_config,
                    "created_at": now,
                    "updated_at": now,
                }
                for conversation_id, runtime_config in (
                    (narrowed_conversation_id, {"internet_access": False}),
                    (ordinary_conversation_id, None),
                )
            ],
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=uuid4(),
                workspace_id=workspace_id,
                conversation_id=narrowed_conversation_id,
                agent_id=agent_id,
                seq=2,
                status="running",
                inbound="newer unrestricted automatic work",
                admission_source="scheduled",
                speaker_member_id=None,
                runtime_config=None,
                created_at=now,
                updated_at=now,
            )
        )
        omitted = sa.table(
            "source_trigger",
            sa.column("id", sa.Uuid),
            sa.column("workspace_id", sa.Uuid),
            sa.column("conversation_id", sa.Uuid),
            sa.column("agent_id", sa.Uuid),
            sa.column("connection_id", sa.Uuid),
            sa.column("resource", sa.Text),
            sa.column("streams", sa.Text),
            sa.column("delivery", sa.Text),
            sa.column("created_by_member_id", sa.Uuid),
            sa.column("internet_access", sa.Boolean),
            sa.column("created_at", sa.DateTime(timezone=True)),
            sa.column("updated_at", sa.DateTime(timezone=True)),
        )
        ids = {
            conversation_id: uuid4()
            for conversation_id in (
                narrowed_conversation_id,
                ordinary_conversation_id,
                idle_conversation_id,
            )
        }
        await connection.execute(
            sa.insert(omitted),
            [
                {
                    "id": ids[conversation_id],
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "agent_id": agent_id,
                    "connection_id": connection_id,
                    "resource": f"resource-{index}",
                    "streams": "",
                    "delivery": "current",
                    "created_by_member_id": member_id,
                    "created_at": now,
                    "updated_at": now,
                }
                for index, conversation_id in enumerate(ids)
            ],
        )
        scopes = dict(
            (
                await connection.execute(
                    sa.select(omitted.c.id, omitted.c.internet_access).where(
                        omitted.c.id.in_(ids.values())
                    )
                )
            ).all()
        )
    assert scopes == {
        ids[narrowed_conversation_id]: False,
        ids[ordinary_conversation_id]: True,
        ids[idle_conversation_id]: False,
    }
