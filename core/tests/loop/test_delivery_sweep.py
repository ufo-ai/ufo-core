from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from pydantic import BaseModel

from ufo.db import workspace_tx
from ufo.ext.manifest import SubagentProfile
from ufo.loop.delivery import (
    RESULT_DELIVERY_BATCH_CHILDREN,
    RESULT_DELIVERY_COOLDOWN_SECONDS,
    DeliverySweep,
)
from ufo.loop.queue import _load_turn
from ufo.loop.subagents import SubagentRegistry, SubagentResult
from ufo.schema import tables
from ufo.schema.records import TerminalFrame
from ufo.surfaces.admission import Admission, AdmissionInvoker
from ufo.workspace import ws


class _Task(BaseModel):
    task: str


class _Finding(BaseModel):
    finding: str


PROFILE = SubagentProfile(
    name="plain",
    prompt="plain instructions",
    tool_names=("bash",),
    input_model=_Task,
    output_model=_Finding,
)
REGISTRY = SubagentRegistry((PROFILE,))


@dataclass
class _RecordingClient:
    enqueued: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)


def _sweep(workspace_id: UUID) -> DeliverySweep:
    admission = Admission(dbos=_RecordingClient(), durable_surfaces=frozenset())
    return DeliverySweep(
        invoker_for=lambda scoped: AdmissionInvoker(admission=admission, workspace_id=scoped),
        registry=REGISTRY,
    )


async def _workspace_agent() -> tuple[UUID, UUID]:
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
                name="parent",
                prompt="p",
                model="m",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, agent_id


async def _parent(workspace_id: UUID, agent_id: UUID, status: str = "done") -> tuple[UUID, UUID]:
    conversation_id, turn_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="web",
                queue_key=str(uuid4()),
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status=status,
                inbound="parent",
                terminal=(
                    None
                    if status != "done"
                    else TerminalFrame(status="done", text="parent done").model_dump(mode="json")
                ),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id, conversation_id


async def _child(
    workspace_id: UUID,
    agent_id: UUID,
    parent_turn_id: UUID,
    terminal: TerminalFrame,
    result_delivery: str | None = "pending",
) -> UUID:
    child_id, conversation_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="subagent",
                queue_key=str(child_id),
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=child_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status=terminal.status,
                inbound="{}",
                terminal=terminal.model_dump(mode="json"),
                parent_turn_id=parent_turn_id,
                result_delivery=result_delivery,
                subagent_profile=PROFILE.name,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return child_id


async def _conversation_turns(conversation_id: UUID) -> list[tuple[int, str]]:
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.turn.c.seq, tables.turn.c.inbound)
                .where(tables.turn.c.conversation_id == conversation_id)
                .order_by(tables.turn.c.seq)
            )
        ).all()
    return [(row.seq, row.inbound) for row in rows]


async def _arrival_bodies(conversation_id: UUID) -> list[str]:
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.inbound_message.c.body)
                .where(tables.inbound_message.c.conversation_id == conversation_id)
                .order_by(tables.inbound_message.c.seq)
            )
        ).all()
    return [row.body for row in rows]


async def _delivery_state(child_id: UUID) -> str | None:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.turn.c.result_delivery).where(tables.turn.c.id == child_id)
            )
        ).scalar_one()


async def _age_delivery(child_id: UUID, seconds: float) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(updated_at=datetime.now(UTC) - timedelta(seconds=seconds))
            .where(tables.turn.c.id == child_id)
        )


async def test_the_sweep_hands_a_cancelled_child_back_to_its_parent(db: None) -> None:
    """`cancel_one_turn` commits the cancelled terminal from outside the child's execution, so the
    child raises past its own delivery and no tool remains for the parent to ask with. The sweep
    reads the durable row instead and posts the cancellation as the parent's next turn."""
    workspace_id, agent_id = await _workspace_agent()
    parent_turn_id, parent_conversation = await _parent(workspace_id, agent_id)
    child_id = await _child(
        workspace_id,
        agent_id,
        parent_turn_id,
        TerminalFrame(status="cancelled", error_class="TurnCancelled"),
    )
    with ws(workspace_id):
        await _sweep(workspace_id).run()
    turns = await _conversation_turns(parent_conversation)
    assert [seq for seq, _ in turns] == [1, 2]
    body = turns[1][1]
    assert f'spawn_id="{child_id}"' in body
    assert 'status="cancelled"' in body
    assert "TurnCancelled" in body
    assert await _delivery_state(child_id) == "delivered"


async def test_a_fan_out_landing_together_wakes_the_conversation_once(db: None) -> None:
    """Three children finishing between two ticks are one pass, so the first admits the parent's
    next turn and the rest fold into it as arrivals — a woken parent reads all three in one turn
    rather than being woken three times."""
    workspace_id, agent_id = await _workspace_agent()
    parent_turn_id, parent_conversation = await _parent(workspace_id, agent_id)
    children = [
        await _child(
            workspace_id,
            agent_id,
            parent_turn_id,
            TerminalFrame(status="done", text=f'{{"finding": "finding {index}"}}'),
        )
        for index in range(3)
    ]
    with ws(workspace_id):
        await _sweep(workspace_id).run()
    turns = await _conversation_turns(parent_conversation)
    assert [seq for seq, _ in turns] == [1, 2]
    folded = await _arrival_bodies(parent_conversation)
    assert len(folded) == 2
    named = "".join([turns[1][1], *folded])
    assert all(f'spawn_id="{child}"' in named for child in children)
    assert [await _delivery_state(child) for child in children] == ["delivered"] * 3


async def test_a_conversation_woken_this_moment_keeps_its_children_for_the_next_sweep(
    db: None,
) -> None:
    """The cycle bound: a child that finishes just after a delivery already woke its parent stays
    outstanding rather than waking it again in the same breath."""
    workspace_id, agent_id = await _workspace_agent()
    parent_turn_id, parent_conversation = await _parent(workspace_id, agent_id)
    delivered = await _child(
        workspace_id,
        agent_id,
        parent_turn_id,
        TerminalFrame(status="done", text='{"finding": "first"}'),
        result_delivery="delivered",
    )
    late = await _child(
        workspace_id,
        agent_id,
        parent_turn_id,
        TerminalFrame(status="done", text='{"finding": "late"}'),
    )
    with ws(workspace_id):
        await _sweep(workspace_id).run()
    assert [seq for seq, _ in await _conversation_turns(parent_conversation)] == [1]
    assert await _delivery_state(late) == "pending"

    await _age_delivery(delivered, RESULT_DELIVERY_COOLDOWN_SECONDS + 1)
    with ws(workspace_id):
        await _sweep(workspace_id).run()
    turns = await _conversation_turns(parent_conversation)
    assert [seq for seq, _ in turns] == [1, 2]
    assert f'spawn_id="{late}"' in turns[1][1]
    assert await _delivery_state(late) == "delivered"


async def test_the_sweep_posts_no_second_arrival_for_what_the_event_path_delivered(
    db: None,
) -> None:
    """Both paths deliver under one key. The event path stamps the child, so an ordinary sweep
    passes it over; a crash between the arrival and that stamp leaves the row pending, and the
    sweep's re-delivery admits nothing rather than waking the parent twice."""
    workspace_id, agent_id = await _workspace_agent()
    parent_turn_id, parent_conversation = await _parent(workspace_id, agent_id)
    child_id = await _child(
        workspace_id,
        agent_id,
        parent_turn_id,
        TerminalFrame(status="done", text='{"finding": "acme ships"}'),
    )
    child, _, _ = await _load_turn(child_id)
    await SubagentResult(
        invoker=AdmissionInvoker(
            admission=Admission(dbos=_RecordingClient(), durable_surfaces=frozenset()),
            workspace_id=workspace_id,
        ),
        registry=REGISTRY,
    ).deliver(child)
    assert await _delivery_state(child_id) == "delivered"

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(result_delivery="pending")
            .where(tables.turn.c.id == child_id)
        )
    await _age_delivery(child_id, RESULT_DELIVERY_COOLDOWN_SECONDS + 1)
    with ws(workspace_id):
        await _sweep(workspace_id).run()
    assert [seq for seq, _ in await _conversation_turns(parent_conversation)] == [1, 2]
    assert await _arrival_bodies(parent_conversation) == []
    assert await _delivery_state(child_id) == "delivered"


async def test_the_sweep_leaves_a_running_child_and_an_awaited_one_alone(db: None) -> None:
    """Outstanding is terminal *and* delegated: a child still running has nothing to hand back, and
    one its parent awaits inline answers through the spawn's return value."""
    workspace_id, agent_id = await _workspace_agent()
    parent_turn_id, parent_conversation = await _parent(workspace_id, agent_id)
    awaited = await _child(
        workspace_id,
        agent_id,
        parent_turn_id,
        TerminalFrame(status="done", text='{"finding": "awaited"}'),
        result_delivery=None,
    )
    running_id, running_conversation = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=running_conversation,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="subagent",
                queue_key=str(running_id),
                member_id=None,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=running_id,
                workspace_id=workspace_id,
                conversation_id=running_conversation,
                agent_id=agent_id,
                seq=1,
                status="running",
                inbound="{}",
                terminal=None,
                parent_turn_id=parent_turn_id,
                result_delivery="pending",
                subagent_profile=PROFILE.name,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(workspace_id):
        assert await _sweep(workspace_id).candidate_workspaces() == ()
        await _sweep(workspace_id).run()
    assert [seq for seq, _ in await _conversation_turns(parent_conversation)] == [1]
    assert await _delivery_state(awaited) is None
    assert await _delivery_state(running_id) == "pending"


async def test_a_workspace_holding_an_outstanding_child_is_the_sweeps_candidate(db: None) -> None:
    workspace_id, agent_id = await _workspace_agent()
    parent_turn_id, _ = await _parent(workspace_id, agent_id)
    child_id = await _child(
        workspace_id,
        agent_id,
        parent_turn_id,
        TerminalFrame(status="failed", error_class="RuntimeError", error_message="boom"),
    )
    assert await _sweep(workspace_id).candidate_workspaces() == (workspace_id,)
    with ws(workspace_id):
        await _sweep(workspace_id).run()
    assert await _delivery_state(child_id) == "delivered"
    assert await _sweep(workspace_id).candidate_workspaces() == ()


async def test_a_child_of_an_archived_parent_stays_pending_and_the_pass_goes_on(db: None) -> None:
    workspace_id, agent_id = await _workspace_agent()
    archived_parent, archived_conversation = await _parent(workspace_id, agent_id)
    orphans = [
        await _child(
            workspace_id,
            agent_id,
            archived_parent,
            TerminalFrame(status="done", text='{"finding": "on the retired app"}'),
        )
        for _ in range(RESULT_DELIVERY_BATCH_CHILDREN)
    ]
    live_agent = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=live_agent,
                workspace_id=workspace_id,
                name="still-here",
                prompt="be brief",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.update(tables.agent)
            .values(
                name=f"~archived-{agent_id}",
                archived_name=tables.agent.c.name,
                archived_at=sa.func.now(),
            )
            .where(tables.agent.c.id == agent_id)
        )
    live_parent, live_conversation = await _parent(workspace_id, live_agent)
    delivered = await _child(
        workspace_id,
        live_agent,
        live_parent,
        TerminalFrame(status="done", text='{"finding": "on the live app"}'),
    )

    with ws(workspace_id):
        await _sweep(workspace_id).run()

    assert {await _delivery_state(orphan) for orphan in orphans} == {"pending"}
    assert [seq for seq, _ in await _conversation_turns(archived_conversation)] == [1]
    assert await _delivery_state(delivered) == "delivered"
    assert [seq for seq, _ in await _conversation_turns(live_conversation)] == [1, 2]
    assert await _sweep(workspace_id).candidate_workspaces() == ()

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .values(name=tables.agent.c.archived_name, archived_name=None, archived_at=None)
            .where(tables.agent.c.id == agent_id)
        )
    assert await _sweep(workspace_id).candidate_workspaces() == (workspace_id,)
    with ws(workspace_id):
        await _sweep(workspace_id).run()

    assert {await _delivery_state(orphan) for orphan in orphans} == {"delivered"}
    assert [seq for seq, _ in await _conversation_turns(archived_conversation)] == [1, 2]
