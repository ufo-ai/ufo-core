"""The per-turn spawn read serves both its surfaces from the same registry and agent rows a spawn
dispatches against — both-ends for docs: every registered profile and every workspace agent
appears with its payload keys in the catalog skill, and in `spawn`'s own `payload` description, so
a caller has the keys whether or not it loads anything."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import BaseModel
from ufo_ext_coding.manifest import CODING_MODELS, CODING_PROFILE

from ufo.db import workspace_tx
from ufo.harness.models.catalog import ANTHROPIC_KEY_SLOT, OPENAI_KEY_SLOT
from ufo.harness.models.interface import PROVIDER_ANTHROPIC, PROVIDER_OPENAI
from ufo.host.ext.loader import skill_registry
from ufo.host.spawn_catalog import (
    SPAWN_CATALOG_DESCRIPTION,
    SPAWN_CATALOG_SKILL_NAME,
    spawn_catalog_skill,
    spawn_payload_description,
    spawn_targets,
)
from ufo.runtime.access.credentials import CredentialStore, member_slot
from ufo.runtime.ext.manifest import SubagentProfile
from ufo.runtime.profiles import CORE_SUBAGENT_PROFILES
from ufo.runtime.subagents import SubagentRegistry
from ufo.runtime.turns.audience import SHARED_AUDIENCE, conversation_audience
from ufo.runtime.workspace import init_workspace_credentials, ws, ws_current
from ufo.schema import tables


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


pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]


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
                    visibility="workspace",
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
        skill = spawn_catalog_skill(await spawn_targets(registry, SHARED_AUDIENCE))

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
        skill = spawn_catalog_skill(await spawn_targets(registry, SHARED_AUDIENCE))

    assert "| `scout` | profile |" in skill.instructions
    assert "| `agent:scout` | agent |" in skill.instructions


async def test_a_profile_with_a_model_route_shadows_their_agent(db: None) -> None:
    workspace_id = await _workspace_with_agents({"coding": None})
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    registry = SubagentRegistry(
        (replace(_profile("coding"), models=("claude-opus-5", "z-ai/glm-5.3")),)
    )

    with ws(workspace_id):
        listed = spawn_catalog_skill(await spawn_targets(registry, SHARED_AUDIENCE))
        assert "| `coding` | profile |" in listed.instructions
        assert "| `agent:coding` | agent |" in listed.instructions
        assert "| `coding` | agent |" not in listed.instructions


async def test_the_catalog_derives_private_visibility_from_its_audience(db: None) -> None:
    workspace_id = await _workspace_with_agents({"shared": None})
    with ws(workspace_id):
        mine = await _catalog_member(workspace_id)
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
        member_catalog = spawn_catalog_skill(
            await spawn_targets(registry, conversation_audience(mine))
        )
        shared_catalog = spawn_catalog_skill(await spawn_targets(registry, SHARED_AUDIENCE))

    assert "`shared`" in member_catalog.instructions
    assert "`mine`" in member_catalog.instructions
    for name in ("theirs", "retired"):
        assert name not in member_catalog.instructions
    assert "`shared`" in shared_catalog.instructions
    for name in ("mine", "theirs", "retired"):
        assert name not in shared_catalog.instructions


async def test_the_catalog_stands_on_its_own_in_the_index(db: None) -> None:
    """A folder skill cannot `depends` on a per-turn one — `CORE_SKILL_REGISTRY` resolves at
    import, before the registry exists."""
    workspace_id = await _workspace_with_agents({})
    with ws(workspace_id):
        catalog = spawn_catalog_skill(
            await spawn_targets(SubagentRegistry(CORE_SUBAGENT_PROFILES), SHARED_AUDIENCE)
        )
    registry = skill_registry((), (catalog,))
    assert [ref.card.name for ref in registry.closure(SPAWN_CATALOG_SKILL_NAME)] == [
        SPAWN_CATALOG_SKILL_NAME
    ]
    assert (SPAWN_CATALOG_SKILL_NAME, SPAWN_CATALOG_DESCRIPTION) in registry.index()


async def test_a_profile_with_a_model_route_is_listed_whether_or_not_they_connected(
    db: None,
) -> None:
    """Connecting a provider is a skippable onboarding step, and the coding subagent is what
    skipping costs — so the member who skipped is exactly the one who has to find out."""
    workspace_id = await _workspace_with_agents({})
    member_id = await _catalog_member(workspace_id)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    registry = SubagentRegistry(
        (
            _profile("research"),
            replace(_profile("coding"), models=("claude-opus-5", "z-ai/glm-5.3")),
        )
    )

    with ws(workspace_id):
        skipped = spawn_catalog_skill(
            await spawn_targets(registry, conversation_audience(member_id))
        )
        assert "`research`" in skipped.instructions
        assert "`coding`" in skipped.instructions

        await store.put(
            workspace_id, member_slot(ANTHROPIC_KEY_SLOT, member_id), "sk-ant-connected"
        )
        connected = spawn_catalog_skill(
            await spawn_targets(registry, conversation_audience(member_id))
        )
        assert "`research`" in connected.instructions
        assert "`coding`" in connected.instructions


async def test_either_provider_is_enough_to_earn_the_profile(db: None) -> None:
    workspace_id = await _workspace_with_agents({})
    admin_id = await _catalog_member(workspace_id, admin=True)
    member_id = await _catalog_member(workspace_id)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)

    with ws(workspace_id):
        assert await ws_current().member_model_accounts(member_id) == ()

        await store.put(workspace_id, ANTHROPIC_KEY_SLOT, "sk-ant-workspace")
        await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, admin_id), "sk-admin")
        assert await ws_current().member_model_accounts(member_id) == ()

        await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, member_id), "sk-member")
        assert await ws_current().member_model_accounts(member_id) == (
            (PROVIDER_OPENAI, member_slot(OPENAI_KEY_SLOT, member_id)),
        )


async def test_the_provider_a_member_connected_is_the_one_they_are_read_as(db: None) -> None:
    workspace_id = await _workspace_with_agents({})
    member_id = await _catalog_member(workspace_id)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)

    with ws(workspace_id):
        assert await ws_current().member_model_accounts(member_id) == ()

        await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, member_id), "sk-openai")
        assert await ws_current().member_model_accounts(member_id) == (
            (PROVIDER_OPENAI, member_slot(OPENAI_KEY_SLOT, member_id)),
        )

        await store.put(workspace_id, member_slot(ANTHROPIC_KEY_SLOT, member_id), "sk-ant")
        assert await ws_current().member_model_accounts(member_id) == (
            (PROVIDER_ANTHROPIC, member_slot(ANTHROPIC_KEY_SLOT, member_id)),
            (PROVIDER_OPENAI, member_slot(OPENAI_KEY_SLOT, member_id)),
        )


def test_a_profile_model_route_preserves_priority_order() -> None:
    profile = replace(
        _profile("coding"),
        models=("claude-opus-5", "gpt-5.6-sol", "z-ai/glm-5.3"),
    )

    assert profile.models == ("claude-opus-5", "gpt-5.6-sol", "z-ai/glm-5.3")


def test_the_shipped_coding_profile_prefers_connected_accounts_then_glm() -> None:
    assert (
        CODING_PROFILE.models
        == CODING_MODELS
        == (
            "claude-opus-5-5",
            "gpt-5.6-sol",
            "z-ai/glm-5.3",
        )
    )


async def test_the_payload_description_names_every_targets_keys(db: None) -> None:
    """The keys reach the field that takes them, not only the document a caller may skip."""
    workspace_id = await _workspace_with_agents(
        {
            "support": {
                "type": "object",
                "properties": {"ticket": {"type": "string"}, "notes": {"type": "string"}},
                "required": ["ticket"],
            }
        }
    )
    registry = SubagentRegistry((CODING_PROFILE, *CORE_SUBAGENT_PROFILES))
    with ws(workspace_id):
        described = spawn_payload_description(await spawn_targets(registry, SHARED_AUDIENCE))

    assert "coding takes `objective`, `extended_context` (optional)" in described
    assert "support takes `ticket`, `notes` (optional)" in described
    assert described.startswith("Arguments matching the target's input schema")
    assert spawn_payload_description(()) == "Arguments matching the target's input schema."
