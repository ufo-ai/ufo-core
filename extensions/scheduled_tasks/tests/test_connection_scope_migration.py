import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from pytest import raises

from ufo.db import MIGRATIONS_DIR
from ufo.host.ext.loader import migration_locations
from ufo.sdk.context import CONNECTION_SCOPE_MAX, TurnRuntimeConfig

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


def _insert_task(
    connection: sa.Connection,
    *,
    task_id: str,
    workspace_id: str,
    agent_id: str,
    creator_id: str | None,
    name: str,
) -> None:
    connection.execute(
        sa.text(
            "insert into scheduled_task "
            "(id, workspace_id, conversation_id, agent_id, name, schedule, prompt, description, "
            "next_run_at, created_by_member_id, paused, created_at, updated_at) values "
            "(:id, :workspace, :conversation, :agent, :name, '0 9 * * *', 'run', '', :now, "
            ":creator, 0, :now, :now)"
        ),
        {
            "id": task_id,
            "workspace": workspace_id,
            "conversation": uuid4().hex,
            "agent": agent_id,
            "name": name,
            "creator": creator_id,
            "now": NOW,
        },
    )


def _insert_pause(connection: sa.Connection, workspace_id: str) -> str:
    pause_id = uuid4().hex
    connection.execute(
        sa.text(
            "insert into pause "
            "(id, workspace_id, conversation_id, agent_id, resume_at, origin_seq, "
            "origin_arrival_seq, prompt, user_description, created_at, updated_at) values "
            "(:id, :workspace, :conversation, :agent, :now, 1, 1, 'resume', 'resume', :now, :now)"
        ),
        {
            "id": pause_id,
            "workspace": workspace_id,
            "conversation": uuid4().hex,
            "agent": uuid4().hex,
            "now": NOW,
        },
    )
    return pause_id


def test_runtime_scope_migrations_accept_outgoing_inserts(tmp_path: Path) -> None:
    database = tmp_path / "scheduled-connection-scope.db"
    config = _config(database)
    command.upgrade(config, "0085")
    command.upgrade(config, "scheduled_tasks_0001")
    engine = sa.create_engine(f"sqlite:///{database}")
    workspace_id, creator_id, other_id = (uuid4().hex for _ in range(3))
    task_id, orphan_id, crowded_task_id, agent_id, crowded_agent_id = (
        uuid4().hex for _ in range(5)
    )
    own_id, shared_id, private_id = (uuid4().hex for _ in range(3))
    crowded_ids = tuple(sorted(uuid4().hex for _ in range(CONNECTION_SCOPE_MAX + 1)))
    with engine.connect() as connection:
        connection.execute(
            sa.text("insert into workspace (id, created_at, updated_at) values (:id, :now, :now)"),
            {"id": workspace_id, "now": NOW},
        )
        for member_id, email in ((creator_id, "creator@x.test"), (other_id, "other@x.test")):
            connection.execute(
                sa.text(
                    "insert into member (id, workspace_id, email, created_at, updated_at) "
                    "values (:id, :workspace, :email, :now, :now)"
                ),
                {
                    "id": member_id,
                    "workspace": workspace_id,
                    "email": email,
                    "now": NOW,
                },
            )
        for index, connection_id in enumerate(crowded_ids):
            connection.execute(
                sa.text(
                    "insert into connection "
                    "(id, workspace_id, provider, account_id, host, owner_member_id, "
                    "conversation_id, shared, created_at, updated_at) values "
                    "(:id, :workspace, 'hub', :account, 'api.hub.test', :owner, :conversation, "
                    "1, :now, :now)"
                ),
                {
                    "id": connection_id,
                    "workspace": workspace_id,
                    "account": f"crowded-{index}",
                    "owner": other_id,
                    "conversation": uuid4().hex,
                    "now": NOW,
                },
            )
            connection.execute(
                sa.text(
                    "insert into connector_grant "
                    "(id, workspace_id, agent_id, connection_id, conversation_id, "
                    "created_at, updated_at) values "
                    "(:id, :workspace, :agent, :connection, :conversation, :now, :now)"
                ),
                {
                    "id": uuid4().hex,
                    "workspace": workspace_id,
                    "agent": crowded_agent_id,
                    "connection": connection_id,
                    "conversation": uuid4().hex,
                    "now": NOW,
                },
            )
        for connection_id, owner_id, shared, account in (
            (own_id, creator_id, 0, "own"),
            (shared_id, other_id, 1, "shared"),
            (private_id, other_id, 0, "private"),
        ):
            connection.execute(
                sa.text(
                    "insert into connection "
                    "(id, workspace_id, provider, account_id, host, owner_member_id, "
                    "conversation_id, shared, "
                    "created_at, updated_at) values "
                    "(:id, :workspace, 'hub', :account, 'api.hub.test', :owner, :conversation, "
                    ":shared, :now, :now)"
                ),
                {
                    "id": connection_id,
                    "workspace": workspace_id,
                    "account": account,
                    "owner": owner_id,
                    "conversation": uuid4().hex,
                    "shared": shared,
                    "now": NOW,
                },
            )
            connection.execute(
                sa.text(
                    "insert into connector_grant "
                    "(id, workspace_id, agent_id, connection_id, conversation_id, "
                    "created_at, updated_at) values "
                    "(:id, :workspace, :agent, :connection, :conversation, :now, :now)"
                ),
                {
                    "id": uuid4().hex,
                    "workspace": workspace_id,
                    "agent": agent_id,
                    "connection": connection_id,
                    "conversation": uuid4().hex,
                    "now": NOW,
                },
            )
        _insert_task(
            connection,
            task_id=task_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            creator_id=creator_id,
            name="owned",
        )
        _insert_task(
            connection,
            task_id=orphan_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            creator_id=None,
            name="orphan",
        )
        _insert_task(
            connection,
            task_id=crowded_task_id,
            workspace_id=workspace_id,
            agent_id=crowded_agent_id,
            creator_id=creator_id,
            name="crowded",
        )
        pause_id = _insert_pause(connection, workspace_id)
        connection.commit()

    command.upgrade(config, "scheduled_tasks_0002")
    outgoing_task_id = uuid4().hex
    with engine.connect() as connection:
        rows = dict(
            connection.execute(
                sa.text(
                    "select id, connections from scheduled_task "
                    "where id in (:owned, :orphan, :crowded)"
                ),
                {"owned": task_id, "orphan": orphan_id, "crowded": crowded_task_id},
            ).all()
        )
        pause_scope = connection.execute(
            sa.text("select connections from pause where id = :id"), {"id": pause_id}
        ).scalar_one()
        _insert_task(
            connection,
            task_id=outgoing_task_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            creator_id=creator_id,
            name="outgoing",
        )
        outgoing_pause_id = _insert_pause(connection, workspace_id)
        outgoing = connection.execute(
            sa.text(
                "select "
                "(select connections from scheduled_task where id = :task), "
                "(select connections from pause where id = :pause)"
            ),
            {"task": outgoing_task_id, "pause": outgoing_pause_id},
        ).one()
        connection.commit()

    command.upgrade(config, "scheduled_tasks_0003")
    scoped_task_id = uuid4().hex
    with engine.connect() as connection:
        restricted = connection.execute(
            sa.text("select count(*) from scheduled_task where internet_access = 0")
        ).scalar_one()
        total = connection.execute(sa.text("select count(*) from scheduled_task")).scalar_one()
        _insert_task(
            connection,
            task_id=scoped_task_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            creator_id=creator_id,
            name="scoped-outgoing",
        )
        scoped_outgoing = connection.execute(
            sa.text("select connections, internet_access from scheduled_task where id = :task"),
            {"task": scoped_task_id},
        ).one()
        connection.execute(
            sa.text(
                "update pause set claimed_by = 'outgoing-runner', claim_expires_at = :expires "
                "where id = :id"
            ),
            {"expires": NOW, "id": pause_id},
        )
        connection.commit()

    command.upgrade(config, "scheduled_tasks_0004")
    replacement_pause_id = uuid4().hex
    with engine.connect() as connection:
        pause_unrestricted = connection.execute(
            sa.text("select count(*) from pause where internet_access = 1")
        ).scalar_one()
        pause_total = connection.execute(sa.text("select count(*) from pause")).scalar_one()
        connection.execute(
            sa.text("update pause set id = :replacement where id = :id"),
            {"replacement": replacement_pause_id, "id": pause_id},
        )
        replaced_pause_scope = connection.execute(
            sa.text("select internet_access from pause where id = :id"),
            {"id": replacement_pause_id},
        ).scalar_one()
        connection.commit()
        with raises(sa.exc.IntegrityError):
            _insert_pause(connection, workspace_id)
        connection.rollback()
        migrated_claim = connection.execute(
            sa.text("select claimed_by, claim_expires_at from pause where id = :id"),
            {"id": replacement_pause_id},
        ).one()

    command.downgrade(config, "scheduled_tasks_0003")
    with engine.connect() as connection:
        columns = {row[1] for row in connection.execute(sa.text("pragma table_info(pause)"))}
        trigger = connection.execute(
            sa.text(
                "select name from sqlite_master where type = 'trigger' "
                "and name = 'pause_generation_scope'"
            )
        ).scalar_one_or_none()
    engine.dispose()

    assert set(json.loads(rows[task_id])) == {str(UUID(own_id)), str(UUID(shared_id))}
    assert json.loads(rows[orphan_id]) == [str(UUID(shared_id))]
    crowded_scope = tuple(UUID(item) for item in json.loads(rows[crowded_task_id]))
    assert crowded_scope == tuple(UUID(item) for item in crowded_ids[:CONNECTION_SCOPE_MAX])
    assert TurnRuntimeConfig(connections=crowded_scope).connections == crowded_scope
    assert json.loads(pause_scope) == []
    assert tuple(outgoing) == (None, None)
    assert restricted == total == 4
    assert tuple(scoped_outgoing) == (None, 0)
    assert pause_unrestricted == pause_total == 2
    assert replaced_pause_scope == 1
    assert tuple(migrated_claim) == (None, None)
    assert "internet_access" not in columns
    assert trigger is None
