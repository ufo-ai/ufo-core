from uuid import uuid4

import pytest
import sqlalchemy as sa

from selfhost.db import apply_migrations, workspace_tx
from selfhost.schema import tables


def test_migrations_are_idempotent(database_url: str) -> None:
    apply_migrations(database_url)
    apply_migrations(database_url)


async def test_workspace_tx_round_trip(db: None) -> None:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.workspace.c.id).where(tables.workspace.c.id == workspace_id)
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
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="a@b.c",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="m",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="cli",
                queue_key="session",
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        with pytest.raises(sa.exc.IntegrityError):
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    seq=1,
                    status="done",
                    inbound="hi",
                    terminal=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
