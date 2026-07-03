from uuid import uuid4

import pytest
from sqlalchemy import text

from selfhost.db import apply_migrations, workspace_tx


def test_migrations_are_idempotent(database_url: str) -> None:
    apply_migrations(database_url)
    apply_migrations(database_url)


async def test_workspace_tx_round_trip(db: None) -> None:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            text("insert into workspace (id, created_at, updated_at) values (:id, now(), now())"),
            {"id": workspace_id},
        )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                text("select id from workspace where id = :id"), {"id": workspace_id}
            )
        ).one()
    assert row.id == workspace_id


async def test_workspace_tx_requires_init() -> None:
    with pytest.raises(RuntimeError):
        async with workspace_tx():
            pass


async def test_turn_terminal_consistency_enforced(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4(), uuid4()
        await connection.execute(
            text("insert into workspace (id, created_at, updated_at) values (:id, now(), now())"),
            {"id": workspace_id},
        )
        await connection.execute(
            text(
                "insert into member (id, workspace_id, email, created_at, updated_at)"
                " values (:id, :ws, 'a@b.c', now(), now())"
            ),
            {"id": member_id, "ws": workspace_id},
        )
        await connection.execute(
            text(
                "insert into agent (id, workspace_id, name, prompt, model, created_at, updated_at)"
                " values (:id, :ws, 'assistant', 'p', 'm', now(), now())"
            ),
            {"id": agent_id, "ws": workspace_id},
        )
        await connection.execute(
            text(
                "insert into conversation"
                " (id, workspace_id, surface, queue_key, member_id, created_at, updated_at)"
                " values (:id, :ws, 'cli', 'session', :member, now(), now())"
            ),
            {"id": conversation_id, "ws": workspace_id, "member": member_id},
        )
        with pytest.raises(Exception, match=r"turn.*check"):
            await connection.execute(
                text(
                    "insert into turn (id, workspace_id, conversation_id, agent_id, seq, status,"
                    " inbound, terminal, created_at, updated_at)"
                    " values (:id, :ws, :conv, :agent, 1, 'done', 'hi', null, now(), now())"
                ),
                {"id": uuid4(), "ws": workspace_id, "conv": conversation_id, "agent": agent_id},
            )
