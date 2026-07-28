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

from ufo.audience import conversation_audience
from ufo.blob import FilesystemBlobStore
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.manifest import SubagentProfile
from ufo.ext.surface import SurfaceContext
from ufo.hub import InProcessHub
from ufo.loop.subagents import SubagentRegistry, Subagents
from ufo.scheduling import ScheduledTask
from ufo.schema import tables
from ufo.schema.records import Turn
from ufo.surfaces.admission import Admission, MemberAdmission
from ufo.surfaces.hub_tail import HubTailer
from ufo.workspace import ws


class _StubDbos:
    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        return None


async def _workspace_with_two_agents() -> tuple[UUID, UUID, UUID]:
    workspace_id, first_agent, second_agent = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        for agent_id, name, created_at in (
            (first_agent, "assistant", datetime(2026, 7, 1, tzinfo=UTC)),
            (second_agent, "exec", datetime(2026, 7, 2, tzinfo=UTC)),
        ):
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=agent_id,
                    workspace_id=workspace_id,
                    name=name,
                    prompt="be brief",
                    model="claude-opus-4-8",
                    created_at=created_at,
                    updated_at=created_at,
                )
            )
    return workspace_id, first_agent, second_agent


def _context(workspace_id: UUID, surface: str) -> SurfaceContext:
    return SurfaceContext(
        workspace_id=workspace_id,
        surface=surface,
        blob=FilesystemBlobStore(root=Path()),
        _admitter=MemberAdmission(
            workspace_id=workspace_id,
            admission=Admission(dbos=_StubDbos(), durable_surfaces=frozenset()),
        ),
        _tailer=HubTailer(hub=InProcessHub()),
        _credentials=CredentialStore(fernet=Fernet(Fernet.generate_key())),
        _artifact_token_secret="",
        _public_base_url=None,
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

    assert await _turn_agent(company_turn) == first_agent
    assert await _turn_agent(private_turn) == second_agent


async def test_rebinding_an_installation_keeps_its_agent(db: None) -> None:
    workspace_id, _, second_agent = await _workspace_with_two_agents()
    with ws(workspace_id):
        context = _context(workspace_id, "slack")
        await context.bind_installation("team:T1")
        await _rebind_agent(workspace_id, "slack", second_agent)
        await context.bind_installation("team:T1-reinstalled")
        conversation_id = await context.conversation_for("C1:1.0", conversation_audience(None))
        turn_id = await context.admit(conversation_id, "hi", speaker_member_id=None)
    assert await _turn_agent(turn_id) == second_agent


async def test_unbound_surface_lands_on_the_earliest_agent(db: None) -> None:
    workspace_id, first_agent, _ = await _workspace_with_two_agents()
    with ws(workspace_id):
        context = _context(workspace_id, "cli")
        conversation_id = await context.conversation_for("session", conversation_audience(None))
        turn_id = await context.admit(conversation_id, "hi", speaker_member_id=None)
    assert await _turn_agent(turn_id) == first_agent


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


async def test_scheduled_fire_refuses_a_task_bound_to_another_agent(db: None) -> None:
    workspace_id, _, second_agent = await _workspace_with_two_agents()
    admission = Admission(dbos=_StubDbos(), durable_surfaces=frozenset())
    with ws(workspace_id):
        context = _context(workspace_id, "cli")
        conversation_id = await context.conversation_for("session", conversation_audience(None))
        task_id = uuid4()
        fire_at = datetime(2026, 7, 25, tzinfo=UTC)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.scheduled_task).values(
                    id=task_id,
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_id=second_agent,
                    name="cross-agent",
                    schedule="0 9 * * *",
                    prompt="run",
                    description="",
                    next_run_at=fire_at,
                    claimed_by="claim-1",
                    claim_expires_at=fire_at,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        task = ScheduledTask(
            id=task_id,
            conversation_id=conversation_id,
            agent_id=second_agent,
            name="cross-agent",
            schedule="0 9 * * *",
            prompt="run",
            description="",
            next_run_at=fire_at,
            last_run_at=None,
            expires_at=None,
            origin_seq=None,
            resume_turn_id=None,
            claim_id="claim-1",
            created_at=fire_at,
            updated_at=fire_at,
            created_by_member_id=None,
        )
        with pytest.raises(ValueError, match="bound to another agent"):
            await admission.invoke_scheduled(workspace_id, task)
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
