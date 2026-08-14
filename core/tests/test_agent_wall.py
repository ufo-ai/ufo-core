"""The agent wall's routing foundation: a surface installation binds to one agent, a conversation
binds permanently to its surface's agent at creation, and admission derives every turn's agent
from the conversation — a caller-supplied agent id is an assertion that fails closed on mismatch,
so a turn admitted through one installation can never execute as another agent."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import BaseModel
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    UNREACHED_AMBIENT_REPLY,
    UNREACHED_STOPPER,
    no_user_skills,
)

from ufo.audience import conversation_audience
from ufo.blob import FilesystemBlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.manifest import SubagentProfile
from ufo.ext.surface import SurfaceContext
from ufo.hub import InProcessHub
from ufo.loop.subagents import SubagentRegistry, Subagents
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint
from ufo.schema import tables
from ufo.schema.records import Turn
from ufo.surfaces.admission import Admission, MemberAdmission
from ufo.surfaces.hub_tail import HubTailer
from ufo.workspace import ws


class _StubDbos:
    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        return None


async def _workspace_with_two_agents() -> tuple[UUID, UUID, UUID]:
    workspace_id, main_agent, child_agent = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        for agent_id, name, created_at, is_main in (
            (child_agent, "exec", datetime(2026, 7, 1, tzinfo=UTC), False),
            (main_agent, "assistant", datetime(2026, 7, 2, tzinfo=UTC), True),
        ):
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=agent_id,
                    workspace_id=workspace_id,
                    name=name,
                    prompt="be brief",
                    model="claude-opus-4-8",
                    is_main=is_main,
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
    return workspace_id, main_agent, child_agent


def _context(workspace_id: UUID, surface: str) -> SurfaceContext:
    return SurfaceContext(
        workspace_id=workspace_id,
        surface=surface,
        blob=FilesystemBlobStore(root=Path()),
        _sandboxes=ConversationSandbox(
            carrier=LocalCarrier(),
            backend="local",
            off_cluster=False,
            image_ref=SANDBOX_IMAGE_REF,
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            workspace_root=Path("workspaces"),
        ),
        _admitter=MemberAdmission(
            workspace_id=workspace_id,
            admission=Admission(dbos=_StubDbos(), durable_surfaces=frozenset()),
        ),
        _tailer=HubTailer(hub=InProcessHub()),
        _stopper=UNREACHED_STOPPER,
        _credentials=CredentialStore(fernet=Fernet(Fernet.generate_key())),
        _declared_slots=(),
        _artifact_token_secret="",
        _skills=EMPTY_SKILL_REGISTRY,
        _user_skills=no_user_skills,
        _subagents=(),
        _public_base_url=None,
        _home_surface=None,
        _ingress_public_url=None,
        _deploy_sandbox_internet=False,
        _models=("auto", "claude-opus-4-8", "claude-sonnet-5"),
        _ambient_reply=UNREACHED_AMBIENT_REPLY,
    )


async def _rebind_agent(workspace_id: UUID, surface: str, agent_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.surface_installation)
            .where(
                tables.surface_installation.c.workspace_id == workspace_id,
                tables.surface_installation.c.surface == surface,
            )
            .values(agent_id=agent_id)
        )


async def _turn_agent(turn_id: UUID) -> UUID:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.turn.c.agent_id).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one()


async def test_each_installation_routes_conversations_to_its_own_agent(db: None) -> None:
    workspace_id, first_agent, second_agent = await _workspace_with_two_agents()
    with ws(workspace_id):
        company = _context(workspace_id, "slack")
        private = _context(workspace_id, "teams")
        await company.bind_installation("team:T1")
        await private.bind_installation("team:T2")
        await _rebind_agent(workspace_id, "teams", second_agent)

        company_conversation = await company.conversation_for("C1:1.0", conversation_audience(None))
        private_conversation = await private.conversation_for("C9:1.0", conversation_audience(None))
        company_turn = await company.admit(company_conversation, "hi", speaker_member_id=None)
        private_turn = await private.admit(private_conversation, "hi", speaker_member_id=None)

    assert await _turn_agent(company_turn.turn_id) == first_agent
    assert await _turn_agent(private_turn.turn_id) == second_agent


async def test_rebinding_an_installation_keeps_its_agent(db: None) -> None:
    workspace_id, _, second_agent = await _workspace_with_two_agents()
    with ws(workspace_id):
        context = _context(workspace_id, "slack")
        await context.bind_installation("team:T1")
        await _rebind_agent(workspace_id, "slack", second_agent)
        await context.bind_installation("team:T1-reinstalled")
        conversation_id = await context.conversation_for("C1:1.0", conversation_audience(None))
        admitted = await context.admit(conversation_id, "hi", speaker_member_id=None)
    assert await _turn_agent(admitted.turn_id) == second_agent


async def test_unbound_surface_lands_on_the_explicit_main_agent(db: None) -> None:
    workspace_id, main_agent, _ = await _workspace_with_two_agents()
    with ws(workspace_id):
        context = _context(workspace_id, "cli")
        conversation_id = await context.conversation_for("session", conversation_audience(None))
        admitted = await context.admit(conversation_id, "hi", speaker_member_id=None)
    assert await _turn_agent(admitted.turn_id) == main_agent


async def test_invoke_refuses_a_conversation_bound_to_another_agent(db: None) -> None:
    workspace_id, _, second_agent = await _workspace_with_two_agents()
    admission = Admission(dbos=_StubDbos(), durable_surfaces=frozenset())
    with ws(workspace_id):
        context = _context(workspace_id, "cli")
        conversation_id = await context.conversation_for("session", conversation_audience(None))
        with pytest.raises(ValueError, match="bound to another agent"):
            await admission.invoke(workspace_id, conversation_id, second_agent, "job")
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .where(tables.turn.c.conversation_id == conversation_id)
            )
        ).scalar_one()
    assert turns == 0


async def test_a_scheduled_fire_refuses_a_conversation_bound_to_another_agent(db: None) -> None:
    """The wall holds for a fire the same way it holds for a member message: the caller asserts the
    agent it believes the conversation is bound to, and admission refuses the mismatch before any
    turn row exists. Whoever owns the schedule, a stored binding can never fire into another
    agent's conversation."""
    workspace_id, _, second_agent = await _workspace_with_two_agents()
    admission = Admission(dbos=_StubDbos(), durable_surfaces=frozenset())
    with ws(workspace_id):
        context = _context(workspace_id, "cli")
        conversation_id = await context.conversation_for("session", conversation_audience(None))
        with pytest.raises(ValueError, match="bound to another agent"):
            await admission.invoke(
                workspace_id,
                conversation_id,
                second_agent,
                "run",
                "task:cross-agent",
                as_scheduled=True,
            )
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .where(tables.turn.c.conversation_id == conversation_id)
            )
        ).scalar_one()
    assert turns == 0


class _SpawnTask(BaseModel):
    task: str


class _SpawnFinding(BaseModel):
    finding: str


class _SilentClient:
    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        return None


async def test_subagent_conversation_inherits_the_parents_agent(db: None) -> None:
    workspace_id, _, second_agent = await _workspace_with_two_agents()
    parent_conversation = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=parent_conversation,
                workspace_id=workspace_id,
                agent_id=second_agent,
                surface="web",
                queue_key=str(uuid4()),
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    parent = Turn(
        id=uuid4(),
        workspace_id=workspace_id,
        conversation_id=parent_conversation,
        agent_id=second_agent,
        seq=1,
        status="running",
        inbound="parent",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    profile = SubagentProfile(
        name="research",
        prompt="research instructions",
        tool_names=("bash",),
        input_model=_SpawnTask,
        output_model=_SpawnFinding,
    )
    subagents = Subagents(
        client=_SilentClient(),
        registry=SubagentRegistry((profile,)),
        parent=parent,
        audience=conversation_audience(None),
    )
    with ws(workspace_id):
        spawned = await subagents.spawn("research", {"task": "acme"}, background=True)
    async with workspace_tx() as connection:
        child = (
            await connection.execute(
                sa.select(tables.conversation.c.agent_id)
                .select_from(
                    tables.turn.join(
                        tables.conversation,
                        tables.turn.c.conversation_id == tables.conversation.c.id,
                    )
                )
                .where(tables.turn.c.id == spawned.turn_id)
            )
        ).scalar_one()
    assert child == second_agent
