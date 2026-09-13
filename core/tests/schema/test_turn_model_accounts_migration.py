from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from ufo.db import MIGRATIONS_DIR
from ufo.schema.records import ModelAccountCapability, TurnRuntimeConfig

BEFORE = "20260913173609"
REVISION = "20260913183147"
NOW = datetime(2026, 9, 13, tzinfo=UTC)


def _alembic(migration_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("version_locations", str(MIGRATIONS_DIR / "versions"))
    config.set_main_option("path_separator", "os")
    config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))
    return config


@pytest.fixture
def migration_urls(database_url: str, tmp_path: Path) -> Iterator[tuple[str, str]]:
    source = make_url(database_url)
    if source.get_backend_name() == "sqlite":
        path = tmp_path / "turn-model-accounts.db"
        yield f"sqlite+aiosqlite:///{path}", f"sqlite:///{path}"
        return
    database = f"{source.database}_turn_accounts_{uuid4().hex[:8]}"
    admin = sa.create_engine(
        source.set(drivername="postgresql+psycopg", database="ufo"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as connection:
        connection.exec_driver_sql(f'create database "{database}"')
    try:
        yield (
            source.set(database=database).render_as_string(hide_password=False),
            source.set(drivername="postgresql+psycopg", database=database).render_as_string(
                hide_password=False
            ),
        )
    finally:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'drop database "{database}" with (force)')
        admin.dispose()


def _seed(connection: sa.Connection) -> dict[str, UUID]:
    metadata = sa.MetaData()
    tables = {
        name: sa.Table(name, metadata, autoload_with=connection)
        for name in (
            "workspace",
            "member",
            "agent",
            "conversation",
            "credential",
            "connection",
            "connector_grant",
            "turn",
        )
    }
    canonical_ids = {
        name: uuid4()
        for name in (
            "workspace",
            "member",
            "other_member",
            "agent",
            "conversation",
            "shared",
            "owned",
            "foreign",
            "ungranted",
            "narrow_turn",
            "full_turn",
            "embedded_turn",
        )
    }
    ids = (
        canonical_ids
        if connection.dialect.name == "postgresql"
        else {name: value.hex for name, value in canonical_ids.items()}
    )
    connection.execute(
        tables["workspace"].insert(),
        {"id": ids["workspace"], "created_at": NOW, "updated_at": NOW},
    )
    connection.execute(
        tables["member"].insert(),
        [
            {
                "id": ids["member"],
                "workspace_id": ids["workspace"],
                "email": "member@example.com",
                "created_at": NOW,
                "updated_at": NOW,
            },
            {
                "id": ids["other_member"],
                "workspace_id": ids["workspace"],
                "email": "other@example.com",
                "created_at": NOW,
                "updated_at": NOW,
            },
        ],
    )
    connection.execute(
        tables["agent"].insert(),
        {
            "id": ids["agent"],
            "workspace_id": ids["workspace"],
            "name": "main",
            "prompt": "p",
            "model": "auto",
            "reasoning": "auto",
            "visibility": "workspace",
            "created_at": NOW,
            "updated_at": NOW,
        },
    )
    connection.execute(
        tables["conversation"].insert(),
        {
            "id": ids["conversation"],
            "workspace_id": ids["workspace"],
            "agent_id": ids["agent"],
            "surface": "web",
            "queue_key": "migration",
            "audience": "shared",
            "created_at": NOW,
            "updated_at": NOW,
        },
    )
    member_id = str(canonical_ids["member"])
    connection.execute(
        tables["credential"].insert(),
        [
            {
                "workspace_id": ids["workspace"],
                "slot": f"anthropic_api_key:member:{member_id}",
                "ciphertext": b"a",
                "created_at": NOW,
                "updated_at": NOW,
            },
            {
                "workspace_id": ids["workspace"],
                "slot": f"openai_api_key:member:{member_id}",
                "ciphertext": b"o",
                "created_at": NOW,
                "updated_at": NOW,
            },
        ],
    )
    connection.execute(
        tables["connection"].insert(),
        [
            {
                "id": ids["shared"],
                "workspace_id": ids["workspace"],
                "provider": "shared",
                "account_id": "shared",
                "host": "shared.example.com",
                "owner_member_id": None,
                "shared": True,
                "created_at": NOW,
                "updated_at": NOW,
            },
            {
                "id": ids["owned"],
                "workspace_id": ids["workspace"],
                "provider": "owned",
                "account_id": "owned",
                "host": "owned.example.com",
                "owner_member_id": ids["member"],
                "shared": False,
                "created_at": NOW,
                "updated_at": NOW,
            },
            {
                "id": ids["foreign"],
                "workspace_id": ids["workspace"],
                "provider": "foreign",
                "account_id": "foreign",
                "host": "foreign.example.com",
                "owner_member_id": ids["other_member"],
                "shared": False,
                "created_at": NOW,
                "updated_at": NOW,
            },
            {
                "id": ids["ungranted"],
                "workspace_id": ids["workspace"],
                "provider": "ungranted",
                "account_id": "ungranted",
                "host": "ungranted.example.com",
                "owner_member_id": None,
                "shared": True,
                "created_at": NOW,
                "updated_at": NOW,
            },
        ],
    )
    connection.execute(
        tables["connector_grant"].insert(),
        [
            {
                "id": str(uuid4()),
                "workspace_id": ids["workspace"],
                "agent_id": ids["agent"],
                "connection_id": ids[name],
                "created_at": NOW,
                "updated_at": NOW,
            }
            for name in ("shared", "owned", "foreign")
        ],
    )
    forged = {
        "provider": "openai",
        "slot": f"openai_api_key:member:{canonical_ids['other_member']}",
    }
    embedded = {
        "provider": "anthropic",
        "slot": f"anthropic_api_key:member:{canonical_ids['member']}",
    }
    connection.execute(
        tables["turn"].insert(),
        [
            {
                "id": ids["narrow_turn"],
                "workspace_id": ids["workspace"],
                "conversation_id": ids["conversation"],
                "agent_id": ids["agent"],
                "seq": 1,
                "status": "queued",
                "inbound": "narrow",
                "on_behalf_of_member_id": ids["member"],
                "runtime_config": {
                    "internet_access": False,
                    "connections": [
                        str(canonical_ids["shared"]),
                        str(canonical_ids["foreign"]),
                    ],
                    "model_accounts": [forged],
                },
                "created_at": NOW,
                "updated_at": NOW,
            },
            {
                "id": ids["full_turn"],
                "workspace_id": ids["workspace"],
                "conversation_id": ids["conversation"],
                "agent_id": ids["agent"],
                "seq": 2,
                "status": "queued",
                "inbound": "full",
                "on_behalf_of_member_id": ids["member"],
                "runtime_config": None,
                "created_at": NOW,
                "updated_at": NOW,
            },
            {
                "id": ids["embedded_turn"],
                "workspace_id": ids["workspace"],
                "conversation_id": ids["conversation"],
                "agent_id": ids["agent"],
                "seq": 3,
                "status": "queued",
                "inbound": "embedded",
                "on_behalf_of_member_id": None,
                "runtime_config": {"internet_access": False, "model_accounts": [embedded]},
                "created_at": NOW,
                "updated_at": NOW,
            },
        ],
    )
    connection.commit()
    return canonical_ids


def _accounts(member_id: UUID) -> list[dict[str, str]]:
    return [
        {
            "provider": provider,
            "slot": f"{provider}_api_key:member:{member_id}",
        }
        for provider in ("anthropic", "openai")
    ]


def test_turn_capabilities_are_exact_across_the_schema_cutover(
    migration_urls: tuple[str, str],
) -> None:
    migration_url, sync_url = migration_urls
    postgres = migration_url.startswith("postgresql")
    config = _alembic(migration_url)
    command.upgrade(config, BEFORE)
    engine = sa.create_engine(sync_url)
    with engine.connect() as connection:
        ids = _seed(connection)
    command.upgrade(config, REVISION)

    metadata = sa.MetaData()
    with engine.connect() as connection:
        turn = sa.Table("turn", metadata, autoload_with=connection)
        rows = {
            UUID(str(row.id)): row
            for row in connection.execute(
                sa.select(
                    turn.c.id,
                    turn.c.runtime_config,
                    turn.c.model_accounts,
                    turn.c.on_behalf_of_member_id,
                )
            )
        }
        assert rows[ids["narrow_turn"]].model_accounts == _accounts(ids["member"])
        assert rows[ids["narrow_turn"]].runtime_config == {
            "internet_access": False,
            "connections": [str(ids["shared"])],
        }
        assert rows[ids["full_turn"]].model_accounts == _accounts(ids["member"])
        assert rows[ids["full_turn"]].runtime_config == {
            "connections": sorted([str(ids["owned"]), str(ids["shared"])])
        }
        assert rows[ids["embedded_turn"]].model_accounts == [_accounts(ids["member"])[0]]
        assert rows[ids["embedded_turn"]].runtime_config == {"internet_access": False}
        for row in rows.values():
            if row.runtime_config is not None:
                TurnRuntimeConfig.model_validate(row.runtime_config)
            tuple(ModelAccountCapability.model_validate(account) for account in row.model_accounts)

        if postgres:
            old_insert = uuid4()
            connection.execute(
                turn.insert().values(
                    id=old_insert,
                    workspace_id=ids["workspace"],
                    conversation_id=ids["conversation"],
                    agent_id=ids["agent"],
                    seq=4,
                    status="queued",
                    inbound="outgoing insert",
                    on_behalf_of_member_id=ids["member"],
                    runtime_config={
                        "connections": [str(ids["foreign"]), str(ids["shared"])],
                        "model_accounts": [_accounts(ids["other_member"])[1]],
                    },
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            inserted = connection.execute(
                sa.select(turn.c.runtime_config, turn.c.model_accounts).where(
                    turn.c.id == old_insert
                )
            ).one()
            assert inserted.runtime_config == {"connections": [str(ids["shared"])]}
            assert inserted.model_accounts == _accounts(ids["member"])
            connection.execute(
                sa.update(turn)
                .where(turn.c.id == old_insert)
                .values(runtime_config={"connections": None, "model_accounts": []})
            )
            updated = connection.execute(
                sa.select(turn.c.runtime_config, turn.c.model_accounts).where(
                    turn.c.id == old_insert
                )
            ).one()
            assert updated.runtime_config == {
                "connections": sorted([str(ids["owned"]), str(ids["shared"])])
            }
            assert updated.model_accounts == _accounts(ids["member"])
            connection.commit()
    engine.dispose()
