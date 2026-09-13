import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.turns.audience import SHARED_AUDIENCE, conversation_audience
from ufo.runtime.workspace import ws
from ufo.schema import tables

FOUNDED_AT = datetime(2026, 8, 4, tzinfo=UTC)


async def _workspace_with_agent() -> tuple[UUID, UUID]:
    workspace_id, agent_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                is_main=True,
                visibility="workspace",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id


async def _member(workspace_id: UUID) -> UUID:
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{member_id.hex[:8]}@x.test",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id


async def _shared_conversation(
    workspace_id: UUID, agent_id: UUID, founder: UUID
) -> tuple[UUID, UUID]:
    conversation_id, turn_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=conversation_id.hex,
                member_id=None,
                audience=str(SHARED_AUDIENCE),
                created_at=FOUNDED_AT,
                updated_at=FOUNDED_AT,
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="running",
                inbound="Ship the plan",
                admission_source="member",
                speaker_member_id=founder,
                created_at=FOUNDED_AT,
                updated_at=FOUNDED_AT,
            )
        )
    return conversation_id, turn_id


def _folded_arrival(
    workspace_id: UUID, conversation_id: UUID, turn_id: UUID, speaker: UUID
) -> sa.Insert:
    return sa.insert(tables.inbound_message).values(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        seq=1,
        body="and a designer",
        admission_source="member",
        speaker_member_id=speaker,
        admitted_turn_id=turn_id,
        created_at=datetime(2026, 8, 4, 0, 5, tzinfo=UTC),
    )


async def _audience_of(conversation_id: UUID) -> tuple[str, UUID | None]:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.conversation.c.audience, tables.conversation.c.member_id).where(
                    tables.conversation.c.id == conversation_id
                )
            )
        ).one()
    return row.audience, row.member_id


def _ext() -> ExtensionContext:
    return context_for("test", frozenset())


async def test_narrowing_rejects_a_folded_second_speaker(db: None) -> None:
    workspace_id, agent_id = await _workspace_with_agent()
    with ws(workspace_id):
        alice, bob = await _member(workspace_id), await _member(workspace_id)
        conversation_id, turn_id = await _shared_conversation(workspace_id, agent_id, alice)
        async with workspace_tx() as connection:
            await connection.execute(_folded_arrival(workspace_id, conversation_id, turn_id, bob))

        changed = await _ext().change_conversation_audience(
            conversation_id,
            SHARED_AUDIENCE,
            conversation_audience(alice),
            sole_speaker=alice,
        )

        assert changed is False
        assert await _ext().conversation_speakers(conversation_id) == {alice, bob}
        assert await _audience_of(conversation_id) == (str(SHARED_AUDIENCE), None)


async def test_narrowing_waits_for_an_admitting_second_speaker(db: None, database_url: str) -> None:
    if database_url.startswith("sqlite"):
        pytest.skip("a row lock is Postgres's; SQLite serializes writers whole")
    workspace_id, agent_id = await _workspace_with_agent()
    with ws(workspace_id):
        alice, bob = await _member(workspace_id), await _member(workspace_id)
        conversation_id, turn_id = await _shared_conversation(workspace_id, agent_id, alice)
        async with workspace_tx() as admitting:
            await admitting.execute(
                sa.select(tables.conversation.c.id)
                .where(tables.conversation.c.id == conversation_id)
                .with_for_update()
            )
            narrowing = asyncio.create_task(
                _ext().change_conversation_audience(
                    conversation_id,
                    SHARED_AUDIENCE,
                    conversation_audience(alice),
                    sole_speaker=alice,
                )
            )
            finished, _pending = await asyncio.wait({narrowing}, timeout=1.0)
            assert not finished
            await admitting.execute(_folded_arrival(workspace_id, conversation_id, turn_id, bob))

        assert await narrowing is False
        assert await _audience_of(conversation_id) == (str(SHARED_AUDIENCE), None)
