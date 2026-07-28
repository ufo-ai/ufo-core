import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from ufo.db import MIGRATIONS_DIR


def _seed(path: Path, owners: tuple[UUID, UUID]) -> tuple[Config, UUID]:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", f"sqlite+aiosqlite:///{path}")
    command.upgrade(config, "0055")
    workspace_id, agent_a, agent_b, conversation_id = (uuid4() for _ in range(4))
    moment = datetime(2026, 7, 1, tzinfo=UTC)
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) values (:id, :moment, :moment)"
            ),
            {"id": workspace_id.hex, "moment": moment},
        )
        for index, member_id in enumerate(set(owners)):
            connection.execute(
                sa.text(
                    "insert into member "
                    "(id, workspace_id, email, created_at, updated_at) "
                    "values (:id, :workspace, :email, :moment, :moment)"
                ),
                {
                    "id": member_id.hex,
                    "workspace": workspace_id.hex,
                    "email": f"owner-{index}@x.test",
                    "moment": moment,
                },
            )
        for agent_id, name in ((agent_a, "assistant"), (agent_b, "exec")):
            connection.execute(
                sa.text(
                    "insert into agent "
                    "(id, workspace_id, name, prompt, model, internet_access_allowed, "
                    "created_at, updated_at) "
                    "values (:id, :workspace, :name, 'p', 'm', true, :moment, :moment)"
                ),
                {
                    "id": agent_id.hex,
                    "workspace": workspace_id.hex,
                    "name": name,
                    "moment": moment,
                },
            )
        connection.execute(
            sa.text(
                "insert into conversation "
                "(id, workspace_id, agent_id, surface, queue_key, member_id, audience, "
                "created_at, updated_at) "
                "values (:id, :workspace, :agent, 'cli', 'session', :member, :audience, "
                ":moment, :moment)"
            ),
            {
                "id": conversation_id.hex,
                "workspace": workspace_id.hex,
                "agent": agent_a.hex,
                "member": owners[0].hex,
                "audience": f"member:{owners[0]}",
                "moment": moment,
            },
        )
        for index, (agent_id, owner_id) in enumerate(((agent_a, owners[0]), (agent_b, owners[1]))):
            connection.execute(
                sa.text(
                    "insert into grant "
                    "(id, workspace_id, agent_id, provider, account_id, host, "
                    "grantor_member_id, conversation_id, shared, created_at, updated_at) "
                    "values (:id, :workspace, :agent, 'gmail', 'acct-1', 'gmail.test', "
                    ":owner, :conversation, :shared, :created, :updated)"
                ),
                {
                    "id": uuid4().hex,
                    "workspace": workspace_id.hex,
                    "agent": agent_id.hex,
                    "owner": owner_id.hex,
                    "conversation": conversation_id.hex,
                    "shared": index == 1,
                    "created": moment,
                    "updated": datetime(2026, 7, 2 + index, tzinfo=UTC),
                },
            )
        connection.execute(
            sa.text(
                "insert into source "
                "(id, workspace_id, backend, config, subject, owner_member_id, next_sync_at, "
                "consecutive_errors, created_at, updated_at) "
                "values (:id, :workspace, 'gmail', :config, :subject, :owner, :moment, 0, "
                ":moment, :moment)"
            ),
            {
                "id": uuid4().hex,
                "workspace": workspace_id.hex,
                "config": json.dumps({"account": "acct-1", "stream": "messages"}),
                "subject": f"member:{owners[0]}",
                "owner": owners[0].hex,
                "moment": moment,
            },
        )
        connection.commit()
    engine.dispose()
    return config, workspace_id


def test_connection_migration_collapses_one_account_into_two_agent_edges(tmp_path: Path) -> None:
    owner_id = uuid4()
    path = tmp_path / "connections.db"
    config, workspace_id = _seed(path, (owner_id, owner_id))
    command.upgrade(config, "0057")
    engine = sa.create_engine(f"sqlite:///{path}")
    inspector = sa.inspect(engine)
    assert "grant" not in inspector.get_table_names()
    assert any(
        foreign_key["constrained_columns"] == ["workspace_id", "connection_id"]
        and foreign_key["referred_columns"] == ["workspace_id", "id"]
        for foreign_key in inspector.get_foreign_keys("connector_grant")
    )
    assert any(
        foreign_key["constrained_columns"] == ["workspace_id", "connection_id", "owner_member_id"]
        and foreign_key["referred_columns"] == ["workspace_id", "id", "owner_member_id"]
        for foreign_key in inspector.get_foreign_keys("source")
    )
    assert {
        tuple(foreign_key["constrained_columns"])
        for table in ("connection", "connector_grant", "source")
        for foreign_key in inspector.get_foreign_keys(table)
    }.issuperset(
        {
            ("workspace_id", "owner_member_id"),
            ("workspace_id", "agent_id"),
            ("workspace_id", "conversation_id"),
            ("workspace_id", "connection_id"),
        }
    )
    with engine.connect() as connection:
        account = connection.execute(
            sa.text(
                "select id, provider, account_id, owner_member_id "
                "from connection where workspace_id = :workspace"
            ),
            {"workspace": workspace_id.hex},
        ).one()
        edges = connection.execute(
            sa.text(
                "select a.name, g.shared from connector_grant g "
                "join agent a on a.id = g.agent_id order by a.name"
            )
        ).all()
        source_connection_id = connection.execute(
            sa.text("select connection_id from source where workspace_id = :workspace"),
            {"workspace": workspace_id.hex},
        ).scalar_one()
    engine.dispose()
    assert account[1:] == ("gmail", "acct-1", owner_id.hex)
    assert source_connection_id == account.id
    assert edges == [("assistant", 0), ("exec", 1)]
    command.downgrade(config, "0056")
    engine = sa.create_engine(f"sqlite:///{path}")
    assert "connection_id" not in {
        column["name"] for column in sa.inspect(engine).get_columns("source")
    }
    with engine.connect() as connection:
        restored = connection.execute(
            sa.text(
                "select a.name, g.grantor_member_id, g.shared from grant g "
                "join agent a on a.id = g.agent_id order by a.name"
            )
        ).all()
    engine.dispose()
    assert restored == [("assistant", owner_id.hex, 0), ("exec", owner_id.hex, 1)]


def test_connection_migration_fails_before_ddl_when_ownership_conflicts(
    tmp_path: Path,
) -> None:
    path = tmp_path / "connection-owner-conflict.db"
    config, workspace_id = _seed(path, (uuid4(), uuid4()))
    with pytest.raises(RuntimeError) as raised:
        command.upgrade(config, "0057")
    assert str(workspace_id) in str(raised.value)
    assert "gmail/acct-1" in str(raised.value)
    assert "owned by different members" in str(raised.value)
    engine = sa.create_engine(f"sqlite:///{path}")
    tables = set(sa.inspect(engine).get_table_names())
    with engine.connect() as connection:
        grants = connection.execute(sa.text("select count(*) from grant")).scalar_one()
    engine.dispose()
    assert "connection" not in tables
    assert "connector_grant" not in tables
    assert grants == 2


def test_connection_migration_fails_before_ddl_when_hosts_conflict(tmp_path: Path) -> None:
    owner_id = uuid4()
    path = tmp_path / "connection-host-conflict.db"
    config, workspace_id = _seed(path, (owner_id, owner_id))
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                'update "grant" set host = :host where id = '
                '(select id from "grant" order by id limit 1)'
            ),
            {"host": "other.gmail.test"},
        )
        connection.commit()
    engine.dispose()

    with pytest.raises(RuntimeError) as raised:
        command.upgrade(config, "0057")
    assert str(workspace_id) in str(raised.value)
    assert "gmail/acct-1" in str(raised.value)
    assert "different hosts" in str(raised.value)
    engine = sa.create_engine(f"sqlite:///{path}")
    assert "connection" not in sa.inspect(engine).get_table_names()
    engine.dispose()


def test_connection_migration_refuses_cross_workspace_principals(tmp_path: Path) -> None:
    owner_id = uuid4()
    path = tmp_path / "connection-cross-workspace.db"
    config, workspace_id = _seed(path, (owner_id, owner_id))
    other_workspace, other_member, other_agent = uuid4(), uuid4(), uuid4()
    moment = datetime(2026, 7, 4, tzinfo=UTC)
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        connection.execute(
            sa.text(
                "insert into workspace (id, created_at, updated_at) values (:id, :moment, :moment)"
            ),
            {"id": other_workspace.hex, "moment": moment},
        )
        connection.execute(
            sa.text(
                "insert into member (id, workspace_id, email, created_at, updated_at) "
                "values (:id, :workspace, 'foreign@x.test', :moment, :moment)"
            ),
            {
                "id": other_member.hex,
                "workspace": other_workspace.hex,
                "moment": moment,
            },
        )
        connection.execute(
            sa.text(
                "insert into agent "
                "(id, workspace_id, name, prompt, model, internet_access_allowed, "
                "created_at, updated_at) "
                "values (:id, :workspace, 'foreign', 'p', 'm', true, :moment, :moment)"
            ),
            {
                "id": other_agent.hex,
                "workspace": other_workspace.hex,
                "moment": moment,
            },
        )
        connection.execute(
            sa.text('update "grant" set grantor_member_id = :member'),
            {"member": other_member.hex},
        )
        connection.commit()
    engine.dispose()

    with pytest.raises(RuntimeError) as raised:
        command.upgrade(config, "0057")
    assert "outside workspace" in str(raised.value)
    assert str(workspace_id) in str(raised.value)
    engine = sa.create_engine(f"sqlite:///{path}")
    assert "connection" not in sa.inspect(engine).get_table_names()
    engine.dispose()


def test_connection_migration_refuses_to_lose_an_ungranted_connection(tmp_path: Path) -> None:
    owner_id = uuid4()
    path = tmp_path / "connection-orphan.db"
    config, workspace_id = _seed(path, (owner_id, owner_id))
    command.upgrade(config, "0057")
    engine = sa.create_engine(f"sqlite:///{path}")
    with engine.connect() as connection:
        connection.execute(sa.text("delete from connector_grant"))
        connection.commit()
    engine.dispose()

    with pytest.raises(RuntimeError) as raised:
        command.downgrade(config, "0056")
    assert str(workspace_id) in str(raised.value)
    assert "gmail/acct-1" in str(raised.value)
    assert "grant schema cannot represent this connection" in str(raised.value)

    engine = sa.create_engine(f"sqlite:///{path}")
    tables = set(sa.inspect(engine).get_table_names())
    with engine.connect() as connection:
        connections = connection.execute(sa.text("select count(*) from connection")).scalar_one()
    engine.dispose()
    assert "grant" not in tables
    assert connections == 1
