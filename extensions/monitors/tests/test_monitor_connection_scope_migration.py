import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from pytest import raises

from ufo.db import MIGRATIONS_DIR, workspace_tx
from ufo.host.ext.loader import migration_locations
from ufo.schema import tables
from ufo.schema.records import CONNECTION_SCOPE_MAX

NOW = "2026-09-13 12:00:00+00:00"


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


def _insert_monitor(
    connection: sa.Connection,
    *,
    monitor_id: str,
    workspace_id: str,
    conversation_id: str,
    agent_id: str,
    creator_id: str | None,
    name: str,
) -> None:
    connection.execute(
        sa.text(
            "insert into monitor "
            "(id, workspace_id, conversation_id, agent_id, name, audience, command, "
            "interval_minutes, deadline_at, reason, next_steps, user_description, "
            "created_by_member_id, baseline, next_probe_at, created_at, updated_at) values "
            "(:id, :workspace, :conversation, :agent, :name, 'shared', 'true', 5, :now, "
            "'CI', 'Report', 'CI', :creator, '', :now, :now, :now)"
        ),
        {
            "id": monitor_id,
            "workspace": workspace_id,
            "conversation": conversation_id,
            "agent": agent_id,
            "name": name,
            "creator": creator_id,
            "now": NOW,
        },
    )


def test_scope_backfill_and_outgoing_insert_fail_closed_by_workspace(tmp_path: Path) -> None:
    database = tmp_path / "monitor-connection-scope.db"
    config = _config(database)
    command.upgrade(config, "0085")
    command.upgrade(config, "monitors_0002")
    engine = sa.create_engine(f"sqlite:///{database}")
    workspace_id, other_workspace_id, creator_id, other_id, foreign_owner_id = (
        uuid4().hex for _ in range(5)
    )
    agent_id, other_agent_id, conversation_id, other_conversation_id = (
        uuid4().hex for _ in range(4)
    )
    owned_id, memberless_id, other_monitor_id, turn_id = (uuid4().hex for _ in range(4))
    own_id, shared_id, private_id, foreign_id = (uuid4().hex for _ in range(4))
    overflow_ids = tuple(uuid4().hex for _ in range(CONNECTION_SCOPE_MAX + 1))
    with engine.connect() as connection:
        for current_workspace in (workspace_id, other_workspace_id):
            connection.execute(
                sa.text(
                    "insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"
                ),
                {"id": current_workspace, "now": NOW},
            )
        for member_id, member_workspace, email in (
            (creator_id, workspace_id, "creator@x.test"),
            (other_id, workspace_id, "other@x.test"),
            (foreign_owner_id, other_workspace_id, "foreign@x.test"),
        ):
            connection.execute(
                sa.text(
                    "insert into member (id, workspace_id, email, created_at, updated_at) "
                    "values (:id, :workspace, :email, :now, :now)"
                ),
                {
                    "id": member_id,
                    "workspace": member_workspace,
                    "email": email,
                    "now": NOW,
                },
            )
        for current_agent, current_workspace, name in (
            (agent_id, workspace_id, "assistant"),
            (other_agent_id, other_workspace_id, "other"),
        ):
            connection.execute(
                sa.text(
                    "insert into agent "
                    "(id, workspace_id, name, prompt, model, created_at, updated_at) values "
                    "(:id, :workspace, :name, 'p', 'claude-opus-4-8', :now, :now)"
                ),
                {"id": current_agent, "workspace": current_workspace, "name": name, "now": NOW},
            )
        for current_conversation, current_workspace, current_agent in (
            (conversation_id, workspace_id, agent_id),
            (other_conversation_id, other_workspace_id, other_agent_id),
        ):
            connection.execute(
                sa.text(
                    "insert into conversation "
                    "(id, workspace_id, agent_id, surface, queue_key, audience, created_at, "
                    "updated_at) values (:id, :workspace, :agent, 'cli', :id, 'shared', :now, :now)"
                ),
                {
                    "id": current_conversation,
                    "workspace": current_workspace,
                    "agent": current_agent,
                    "now": NOW,
                },
            )
        connection.execute(
            sa.text(
                "insert into turn "
                "(id, workspace_id, conversation_id, agent_id, seq, status, inbound, "
                "admission_source, speaker_member_id, created_at, updated_at) values "
                "(:id, :workspace, :conversation, :agent, 1, 'running', 'watch this task', "
                "'member', :member, :now, :now)"
            ),
            {
                "id": turn_id,
                "workspace": workspace_id,
                "conversation": conversation_id,
                "agent": agent_id,
                "member": creator_id,
                "now": NOW,
            },
        )
        for connection_id, current_workspace, owner_id, shared, account in (
            (own_id, workspace_id, creator_id, 0, "own"),
            (shared_id, workspace_id, other_id, 1, "shared"),
            (private_id, workspace_id, other_id, 0, "private"),
            (foreign_id, other_workspace_id, foreign_owner_id, 1, "foreign"),
        ):
            connection.execute(
                sa.text(
                    "insert into connection "
                    "(id, workspace_id, provider, account_id, host, owner_member_id, "
                    "conversation_id, shared, created_at, updated_at) values "
                    "(:id, :workspace, 'hub', :account, 'api.hub.test', :owner, :conversation, "
                    ":shared, :now, :now)"
                ),
                {
                    "id": connection_id,
                    "workspace": current_workspace,
                    "account": account,
                    "owner": owner_id,
                    "conversation": (
                        conversation_id
                        if current_workspace == workspace_id
                        else other_conversation_id
                    ),
                    "shared": shared,
                    "now": NOW,
                },
            )
            connection.execute(
                sa.text(
                    "insert into connector_grant "
                    "(id, workspace_id, agent_id, connection_id, conversation_id, created_at, "
                    "updated_at) values "
                    "(:id, :workspace, :agent, :connection, :conversation, :now, :now)"
                ),
                {
                    "id": uuid4().hex,
                    "workspace": current_workspace,
                    "agent": agent_id if current_workspace == workspace_id else other_agent_id,
                    "connection": connection_id,
                    "conversation": (
                        conversation_id
                        if current_workspace == workspace_id
                        else other_conversation_id
                    ),
                    "now": NOW,
                },
            )
        for index, connection_id in enumerate(overflow_ids):
            connection.execute(
                sa.text(
                    "insert into connection "
                    "(id, workspace_id, provider, account_id, host, owner_member_id, "
                    "conversation_id, shared, created_at, updated_at) values "
                    "(:id, :workspace, 'hub', :account, 'api.hub.test', :owner, "
                    ":conversation, 1, :now, :now)"
                ),
                {
                    "id": connection_id,
                    "workspace": workspace_id,
                    "account": f"overflow-{index}",
                    "owner": other_id,
                    "conversation": conversation_id,
                    "now": NOW,
                },
            )
            connection.execute(
                sa.text(
                    "insert into connector_grant "
                    "(id, workspace_id, agent_id, connection_id, conversation_id, created_at, "
                    "updated_at) values "
                    "(:id, :workspace, :agent, :connection, :conversation, :now, :now)"
                ),
                {
                    "id": uuid4().hex,
                    "workspace": workspace_id,
                    "agent": agent_id,
                    "connection": connection_id,
                    "conversation": conversation_id,
                    "now": NOW,
                },
            )
        _insert_monitor(
            connection,
            monitor_id=owned_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            creator_id=creator_id,
            name="owned",
        )
        _insert_monitor(
            connection,
            monitor_id=memberless_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            creator_id=None,
            name="memberless",
        )
        _insert_monitor(
            connection,
            monitor_id=other_monitor_id,
            workspace_id=other_workspace_id,
            conversation_id=other_conversation_id,
            agent_id=other_agent_id,
            creator_id=None,
            name="foreign",
        )
        connection.commit()

    command.upgrade(config, "monitors_0004")
    with engine.connect() as connection:
        rows = {
            row.id: row
            for row in connection.execute(
                sa.text(
                    "select id, connections, internet_access, requesting_message_ref from monitor"
                )
            ).all()
        }
        with raises(sa.exc.IntegrityError):
            _insert_monitor(
                connection,
                monitor_id=uuid4().hex,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                creator_id=creator_id,
                name="omitted-scope",
            )
        connection.rollback()
    engine.dispose()

    owned_scope = json.loads(rows[owned_id].connections)
    memberless_scope = json.loads(rows[memberless_id].connections)
    assert (
        owned_scope
        == sorted(
            (str(UUID(own_id)), str(UUID(shared_id)), *(str(UUID(item)) for item in overflow_ids))
        )[:CONNECTION_SCOPE_MAX]
    )
    assert (
        memberless_scope
        == sorted((str(UUID(shared_id)), *(str(UUID(item)) for item in overflow_ids)))[
            :CONNECTION_SCOPE_MAX
        ]
    )
    assert json.loads(rows[other_monitor_id].connections) == [str(UUID(foreign_id))]
    assert all(row.internet_access == 1 for row in rows.values())
    assert rows[owned_id].requesting_message_ref == turn_id
    assert rows[memberless_id].requesting_message_ref is None


@pytest.mark.usefixtures("database_url")
@pytest.mark.parametrize("database_url", ["postgresql"], indirect=True)
async def test_omitted_monitor_scope_comes_from_the_active_member_turn(db: None) -> None:
    workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
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
                email="monitor-speaker@example.com",
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
            "monitor",
            sa.column("id", sa.Uuid),
            sa.column("workspace_id", sa.Uuid),
            sa.column("conversation_id", sa.Uuid),
            sa.column("agent_id", sa.Uuid),
            sa.column("name", sa.Text),
            sa.column("command", sa.Text),
            sa.column("interval_minutes", sa.Integer),
            sa.column("deadline_at", sa.DateTime(timezone=True)),
            sa.column("reason", sa.Text),
            sa.column("next_steps", sa.Text),
            sa.column("user_description", sa.Text),
            sa.column("created_by_member_id", sa.Uuid),
            sa.column("internet_access", sa.Boolean),
            sa.column("baseline", sa.Text),
            sa.column("next_probe_at", sa.DateTime(timezone=True)),
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
                    "name": f"watch-{index}",
                    "command": "true",
                    "interval_minutes": 5,
                    "deadline_at": now,
                    "reason": "CI",
                    "next_steps": "Report",
                    "user_description": "CI",
                    "created_by_member_id": member_id,
                    "baseline": "",
                    "next_probe_at": now,
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
