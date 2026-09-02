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
from ufo_ext_coding.manifest import CODING_MODEL, CODING_PROFILE

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
from ufo.runtime.authority import WORKSPACE_AUTHORITY, MemberAuthority
from ufo.runtime.ext.manifest import SubagentProfile
from ufo.runtime.profiles import CORE_SUBAGENT_PROFILES
from ufo.runtime.subagents import SubagentRegistry
from ufo.runtime.tools.context import SPAWN_CONNECT_PATH, SpawnNeedsOwnModelKey
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
        skill = spawn_catalog_skill(await spawn_targets(registry, MemberAuthority(admin)))

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
        skill = spawn_catalog_skill(await spawn_targets(registry, MemberAuthority(admin)))

    assert "| `scout` | profile |" in skill.instructions
    assert "| `agent:scout` | agent |" in skill.instructions


async def test_a_profile_on_the_members_own_key_shadows_their_agent(db: None) -> None:
    """A profile the member cannot spawn yet is still a name they cannot spawn bare: the resolver
    reads the whole registry and refuses a bare name two records answer to, so the agent is listed
    under the qualified form that resolver accepts."""
    workspace_id = await _workspace_with_agents({"coding": None})
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    registry = SubagentRegistry((replace(_profile("coding"), needs_own_model_key=True),))

    with ws(workspace_id):
        keyless = await _catalog_member(workspace_id, admin=True)
        listed = spawn_catalog_skill(await spawn_targets(registry, MemberAuthority(keyless)))
        assert "| `coding` | profile |" in listed.instructions
        assert "| `agent:coding` | agent |" in listed.instructions
        assert "| `coding` | agent |" not in listed.instructions


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
        member_view = spawn_catalog_skill(await spawn_targets(registry, MemberAuthority(mine)))
        admin_view = spawn_catalog_skill(await spawn_targets(registry, MemberAuthority(admin)))

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
        catalog = spawn_catalog_skill(
            await spawn_targets(SubagentRegistry(CORE_SUBAGENT_PROFILES), WORKSPACE_AUTHORITY)
        )
    registry = skill_registry((), (catalog,))
    assert [ref.card.name for ref in registry.closure(SPAWN_CATALOG_SKILL_NAME)] == [
        SPAWN_CATALOG_SKILL_NAME
    ]
    assert (SPAWN_CATALOG_SKILL_NAME, SPAWN_CATALOG_DESCRIPTION) in registry.index()


async def test_a_profile_on_the_members_own_key_is_listed_whether_or_not_they_connected(
    db: None,
) -> None:
    """Connecting a provider is a skippable onboarding step, and the coding subagent is what
    skipping costs — so the member who skipped is exactly the one who has to find out. Withholding
    the target hides the capability: the model would never name coding, and nothing would tell them
    it exists or why it is off. It is listed either way, and the spawn refusal is what names the
    account it needs and the screen that connects one."""
    workspace_id = await _workspace_with_agents({})
    member_id = await _catalog_member(workspace_id)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    registry = SubagentRegistry(
        (
            _profile("research"),
            replace(_profile("coding"), needs_own_model_key=True),
        )
    )

    with ws(workspace_id):
        skipped = spawn_catalog_skill(await spawn_targets(registry, MemberAuthority(member_id)))
        assert "`research`" in skipped.instructions
        assert "`coding`" in skipped.instructions

        await store.put(
            workspace_id, member_slot(ANTHROPIC_KEY_SLOT, member_id), "sk-ant-connected"
        )
        connected = spawn_catalog_skill(await spawn_targets(registry, MemberAuthority(member_id)))
        assert "`research`" in connected.instructions
        assert "`coding`" in connected.instructions


async def test_either_provider_is_enough_to_earn_the_profile(db: None) -> None:
    """The step asks for one of the two, so either satisfies it — and a key the workspace or an
    admin holds is not the member's own: the subagent spends the account of the person who
    connected it, so a deploy key cannot stand in for a member who skipped."""
    workspace_id = await _workspace_with_agents({})
    admin_id = await _catalog_member(workspace_id, admin=True)
    member_id = await _catalog_member(workspace_id)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)

    with ws(workspace_id):
        assert not await ws_current().member_holds_own_model_key(MemberAuthority(member_id))

        await store.put(workspace_id, ANTHROPIC_KEY_SLOT, "sk-ant-workspace")
        await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, admin_id), "sk-admin")
        assert not await ws_current().member_holds_own_model_key(MemberAuthority(member_id))

        await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, member_id), "sk-member")
        assert await ws_current().member_holds_own_model_key(MemberAuthority(member_id))


async def test_the_provider_a_member_connected_is_the_one_they_are_read_as(db: None) -> None:
    """Which provider they connected, not merely whether they did — a profile that runs on the
    member's own account has to know which account that is, so the model it picks is one that
    account can serve."""
    workspace_id = await _workspace_with_agents({})
    member_id = await _catalog_member(workspace_id)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)

    with ws(workspace_id):
        assert await ws_current().member_model_provider(MemberAuthority(member_id)) is None

        await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, member_id), "sk-openai")
        assert (
            await ws_current().member_model_provider(MemberAuthority(member_id)) == PROVIDER_OPENAI
        )

        await store.put(workspace_id, member_slot(ANTHROPIC_KEY_SLOT, member_id), "sk-ant")
        assert (
            await ws_current().member_model_provider(MemberAuthority(member_id))
            == PROVIDER_ANTHROPIC
        )


def test_a_member_key_outranks_the_profiles_own_model_pin() -> None:
    """The coding subagent is gated on the member having connected an account, so it must also RUN
    on that account. A pinned model naming some third backend would spend the deploy's key on the
    very work the member's key was asked for — which is what happened before this: the spawn passed
    the gate and then died on a backend nobody had connected."""
    profile = replace(
        _profile("coding"),
        model="anthropic.claude-opus-5",
        needs_own_model_key=True,
        own_key_models={PROVIDER_ANTHROPIC: "claude-opus-5", PROVIDER_OPENAI: "gpt-5.6-sol"},
    )

    assert profile.own_key_models.get(PROVIDER_ANTHROPIC) == "claude-opus-5"
    assert profile.own_key_models.get(PROVIDER_OPENAI) == "gpt-5.6-sol"
    assert (profile.own_key_models.get(PROVIDER_ANTHROPIC) or profile.model) == "claude-opus-5"
    assert (profile.own_key_models.get("") or profile.model) == "anthropic.claude-opus-5"


def test_the_shipped_coding_profile_runs_only_on_a_members_own_account() -> None:
    """Both ends of the rule, on the profile that actually ships: it declares a model for each
    provider a member can connect, and one of its own for the deploys that can hold no member
    account at all — a pack shipping this profile and no extension that connects one."""
    assert CODING_PROFILE.needs_own_model_key
    assert CODING_PROFILE.model == CODING_MODEL
    assert set(CODING_PROFILE.own_key_models) == {PROVIDER_ANTHROPIC, PROVIDER_OPENAI}


def test_the_refusal_hands_over_the_address_that_satisfies_it() -> None:
    """The first run offers to connect an account, but a member whose workspace already exists
    never sees that screen again — so the refusal is where most members meet the requirement. It
    names the connect screen, because "connect one from the portal" is not something a member can
    click, and one screen takes either account — a second address would only ask them to pick a
    provider before they have seen what each one is."""
    named = str(SpawnNeedsOwnModelKey("coding", "https://ufo.example/"))
    assert f"https://ufo.example{SPAWN_CONNECT_PATH}" in named
    assert "coding" in named
    assert "ChatGPT or Claude" in named
    assert "OpenAI" not in named and "Anthropic" not in named

    unconfigured = str(SpawnNeedsOwnModelKey("coding"))
    assert "the portal" in unconfigured
    assert "surface/web" not in unconfigured


async def test_the_payload_description_names_every_targets_keys(db: None) -> None:
    """The keys reach the field that takes them, not only the document a caller may skip. The
    Code app's own prompt forbids loading the catalog, so for that agent the description is the
    only place the contract appears — and a caller that cannot see `objective` sends a spawn
    without it."""
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
        admin = await _catalog_member(workspace_id, admin=True)
        described = spawn_payload_description(await spawn_targets(registry, MemberAuthority(admin)))

    assert "coding takes `objective`, `extended_context` (optional)" in described
    assert "support takes `ticket`, `notes` (optional)" in described
    assert described.startswith("Arguments matching the target's input schema")
    assert spawn_payload_description(()) == "Arguments matching the target's input schema."
