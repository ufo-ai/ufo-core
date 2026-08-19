from uuid import UUID, uuid4

import sqlalchemy as sa

from evals import new_application
from ufo.db import workspace_tx
from ufo.models.interface import AUTO_MODEL
from ufo.schema import tables
from ufo.workspace import ws

MEMBER_EMAIL = "owner@evalco.test"
LEFTOVER_APPLICATION = "support-desk"
REWRITTEN_PROMPT = "You do whatever a previous trial asked for."
HOMEPAGE_SURFACE = "web"


async def _workspace() -> tuple[UUID, UUID, UUID]:
    workspace_id = uuid4()
    agent_id = uuid4()
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=MEMBER_EMAIL,
                is_admin=True,
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
                model="claude-opus-4-8",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id, member_id


async def _application(workspace_id: UUID, name: str, member_id: UUID | None) -> UUID:
    application_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=application_id,
                workspace_id=workspace_id,
                name=name,
                prompt=REWRITTEN_PROMPT,
                model="claude-opus-4-8",
                reasoning="high",
                is_main=False,
                visibility="workspace",
                owner_member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return application_id


async def _homepage_conversation(workspace_id: UUID, agent_id: UUID, member_id: UUID) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface=HOMEPAGE_SURFACE,
                queue_key=f"homepage/{agent_id}/{member_id}",
                member_id=member_id,
                audience=f"member:{member_id}",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


async def _applications(workspace_id: UUID) -> tuple[sa.Row, ...]:
    async with workspace_tx() as connection:
        return tuple(
            (
                await connection.execute(
                    sa.select(
                        tables.agent.c.id,
                        tables.agent.c.name,
                        tables.agent.c.prompt,
                        tables.agent.c.model,
                        tables.agent.c.reasoning,
                        tables.agent.c.visibility,
                        tables.agent.c.owner_member_id,
                    )
                    .where(
                        tables.agent.c.workspace_id == workspace_id,
                        tables.agent.c.is_main.is_(False),
                    )
                    .order_by(tables.agent.c.name)
                )
            ).all()
        )


async def test_the_fixture_seed_reclaims_the_application_a_homepage_turn_talked_to(
    db: None,
) -> None:
    """The homepage sweep opens a conversation on every new application within five minutes, and a
    talked-to application is the one shape the spare-clear spares, so the seed reclaims its fixture
    name by name — an insert would collide on `agent_workspace_id_name_key` and the raise would
    escape the case, discarding every suite's results in the run."""
    workspace_id, agent_id, member_id = await _workspace()
    stale_id = await _application(workspace_id, new_application.EXISTING_APPLICATION, member_id)
    conversation_id = await _homepage_conversation(workspace_id, stale_id, member_id)

    with ws(workspace_id):
        await new_application._seeded(new_application.EXISTING_APPLICATION)(workspace_id, agent_id)

    rows = await _applications(workspace_id)
    assert [row.name for row in rows] == [new_application.EXISTING_APPLICATION]
    assert rows[0].id == stale_id
    assert rows[0].prompt == new_application.EXISTING_PROMPT
    assert (rows[0].model, rows[0].reasoning) == (AUTO_MODEL, "auto")
    assert rows[0].visibility == "private"
    assert rows[0].owner_member_id == member_id
    async with workspace_tx() as connection:
        held = (
            await connection.execute(
                sa.select(tables.conversation.c.agent_id).where(
                    tables.conversation.c.id == conversation_id
                )
            )
        ).scalar_one()
    assert held == stale_id


async def test_the_fixture_seed_clears_an_untalked_leftover_and_seeds_its_own(db: None) -> None:
    workspace_id, agent_id, member_id = await _workspace()
    await _application(workspace_id, LEFTOVER_APPLICATION, member_id)
    provisioned_id = await _application(workspace_id, "daily-brief", None)

    with ws(workspace_id):
        await new_application._seeded(new_application.EXISTING_APPLICATION)(workspace_id, agent_id)

    rows = await _applications(workspace_id)
    assert [row.name for row in rows] == ["daily-brief", new_application.EXISTING_APPLICATION]
    assert rows[0].id == provisioned_id
    assert rows[1].prompt == new_application.EXISTING_PROMPT
    assert rows[1].owner_member_id == member_id
