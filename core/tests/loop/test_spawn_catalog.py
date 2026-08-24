"""The per-turn spawn-catalog skill reads the same registry and agent rows a spawn dispatches
against — both-ends for docs: every registered profile and every workspace agent appears with its
payload keys, so an agent that loads the catalog has the target names before its first call."""

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from pydantic import BaseModel

from ufo.db import workspace_tx
from ufo.ext.loader import skill_registry
from ufo.ext.manifest import SubagentProfile
from ufo.loop.profiles import CORE_SUBAGENT_PROFILES
from ufo.loop.spawn_catalog import (
    SPAWN_CATALOG_DESCRIPTION,
    SPAWN_CATALOG_SKILL_NAME,
    spawn_catalog_skill,
)
from ufo.loop.subagents import SubagentRegistry
from ufo.schema import tables
from ufo.workspace import ws


async def _catalog_member(workspace_id, admin: bool = False):
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{member_id.hex[:8]}@x.test",
                is_admin=admin,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id


class _Payload(BaseModel):
    question: str
    depth: int = 1


def _profile(name: str) -> SubagentProfile:
    return SubagentProfile(
        name=name, prompt="p", tool_names=(), input_model=_Payload, output_model=_Payload
    )


async def _workspace_with_agents(
    names_and_schemas: dict[str, dict[str, object] | None],
    owners: dict[str, object] | None = None,
):
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        for name, input_schema in names_and_schemas.items():
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    name=name,
                    prompt="p",
                    model="m",
                    input_schema=input_schema,
                    owner_member_id=(owners or {}).get(name),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return workspace_id


async def test_catalog_lists_profiles_and_agents_with_payload_keys(db: None) -> None:
    workspace_id = await _workspace_with_agents(
        {
            "support": {
                "type": "object",
                "properties": {"ticket": {"type": "string"}, "notes": {"type": "string"}},
                "required": ["ticket"],
            },
            "writer": None,
        }
    )
    registry = SubagentRegistry((_profile("scout"), *CORE_SUBAGENT_PROFILES))
    with ws(workspace_id):
        admin = await _catalog_member(workspace_id, admin=True)
        skill = await spawn_catalog_skill(registry, admin)

    assert skill.name == SPAWN_CATALOG_SKILL_NAME
    for profile in registry.profiles:
        assert f"`{profile.name}`" in skill.instructions
    assert "`question`" in skill.instructions
    assert "`depth` (optional)" in skill.instructions
    assert "`support`" in skill.instructions
    assert "`ticket`" in skill.instructions
    assert "`notes` (optional)" in skill.instructions
    assert "`writer`" in skill.instructions
    assert "`task`" in skill.instructions


async def test_an_agent_shadowed_by_a_profile_is_listed_qualified(db: None) -> None:
    workspace_id = await _workspace_with_agents({"scout": None})
    registry = SubagentRegistry((_profile("scout"),))
    with ws(workspace_id):
        admin = await _catalog_member(workspace_id, admin=True)
        skill = await spawn_catalog_skill(registry, admin)

    assert "| `scout` | profile |" in skill.instructions
    assert "| `agent:scout` | agent |" in skill.instructions


async def test_the_catalog_gives_a_member_their_own_agents_and_an_admin_all(db: None) -> None:
    workspace_id = await _workspace_with_agents({"shared": None})
    with ws(workspace_id):
        mine = await _catalog_member(workspace_id)
        admin = await _catalog_member(workspace_id, admin=True)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent),
            [
                {
                    "id": uuid4(),
                    "workspace_id": workspace_id,
                    "name": name,
                    "prompt": "p",
                    "model": "m",
                    "owner_member_id": owner,
                    "created_at": datetime(2026, 7, 9, tzinfo=UTC),
                    "updated_at": datetime(2026, 7, 9, tzinfo=UTC),
                }
                for name, owner in (("mine", mine), ("theirs", uuid4()), ("retired", mine))
            ],
        )
        await connection.execute(
            sa.update(tables.agent)
            .values(
                name="~archived-retired",
                archived_name=tables.agent.c.name,
                archived_at=sa.func.now(),
            )
            .where(tables.agent.c.name == "retired")
        )
    registry = SubagentRegistry(CORE_SUBAGENT_PROFILES)
    with ws(workspace_id):
        member_view = await spawn_catalog_skill(registry, mine)
        admin_view = await spawn_catalog_skill(registry, admin)

    assert "`mine`" in member_view.instructions
    assert "theirs" not in member_view.instructions
    assert "retired" not in member_view.instructions
    assert "shared" not in member_view.instructions
    for name in ("mine", "theirs", "shared"):
        assert f"`{name}`" in admin_view.instructions
    assert "retired" not in admin_view.instructions


async def test_the_catalog_stands_on_its_own_in_the_index(db: None) -> None:
    """A folder skill cannot `depends` on a per-turn one — `CORE_SKILL_REGISTRY` resolves at
    import, before the registry exists. So the catalog carries its own index entry and closes over
    nothing, which is what makes it reachable by name."""
    workspace_id = await _workspace_with_agents({})
    with ws(workspace_id):
        catalog = await spawn_catalog_skill(SubagentRegistry(CORE_SUBAGENT_PROFILES), None)
    registry = skill_registry((), (catalog,))
    assert [ref.card.name for ref in registry.closure(SPAWN_CATALOG_SKILL_NAME)] == [
        SPAWN_CATALOG_SKILL_NAME
    ]
    assert (SPAWN_CATALOG_SKILL_NAME, SPAWN_CATALOG_DESCRIPTION) in registry.index()
