import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from dbos import EnqueueOptions
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.db import workspace_tx
from ufo.runtime.billing.accounting import (
    SpendEvaluator,
    record_sandbox_tokens,
    record_workspace_usage,
)
from ufo.runtime.ext.context import ExtensionContext
from ufo.runtime.ext.manifest import JobSpec
from ufo.runtime.jobs import (
    CORE_EXTENSION,
    TURN_DISPATCH_BATCH_TURNS,
    TURN_DISPATCH_JOB,
    JobRunner,
    TurnDispatcher,
    bindings_from,
)
from ufo.runtime.surfaces.admission import Admission
from ufo.schema import tables
from ufo.schema.records import TerminalFrame, Usage

pytestmark = pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)


@dataclass
class StubDbos:
    """Records the workflow arguments each enqueue carries — the turn id, and the (workspace, turn)
    pair — so a test reads back which turns admission or the dispatcher placed on the queue and
    the workspace each was scoped to, never asserting DBOS itself."""

    enqueued: list[str] = field(default_factory=list)
    scoped: list[tuple[str, str]] = field(default_factory=list)
    workflow_ids: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: EnqueueOptions, workspace_id: str, turn_id: str) -> None:
        self.enqueued.append(turn_id)
        self.scoped.append((workspace_id, turn_id))
        self.workflow_ids.append(options["workflow_id"])


@dataclass
class BlockingDbos(StubDbos):
    started: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def enqueue_async(self, options: EnqueueOptions, workspace_id: str, turn_id: str) -> None:
        await super().enqueue_async(options, workspace_id, turn_id)
        self.started.set()
        await self.release.wait()


async def _dispatch(client: object, dispatch_batch: int = TURN_DISPATCH_BATCH_TURNS) -> None:
    """Drive the turn dispatcher through the real job fan-out and workspace binding."""
    dispatcher = TurnDispatcher(client=client, dispatch_batch=dispatch_batch)

    async def _handler(context: ExtensionContext) -> None:
        await dispatcher.run()

    spec = JobSpec(
        name=TURN_DISPATCH_JOB,
        schedule=None,
        handler=_handler,
        candidates=dispatcher.candidate_workspaces,
    )
    runner = JobRunner(bindings=bindings_from((), (spec,)), manifests=())
    for workspace_id in await runner.candidates(f"{CORE_EXTENSION}:{TURN_DISPATCH_JOB}"):
        await runner.fire(f"{CORE_EXTENSION}:{TURN_DISPATCH_JOB}", workspace_id)


async def _seed(connection: AsyncConnection) -> tuple[UUID, UUID, UUID, UUID]:
    workspace_id, member_id, agent_id, conversation_id = (uuid4() for _ in range(4))
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
    await _agent(connection, workspace_id, agent_id, "assistant")
    await connection.execute(
        sa.insert(tables.conversation).values(
            id=conversation_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            surface="cli",
            queue_key=uuid4().hex,
            member_id=member_id,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return workspace_id, member_id, agent_id, conversation_id


async def _agent(
    connection: AsyncConnection, workspace_id: UUID, agent_id: UUID, name: str
) -> None:
    await connection.execute(
        sa.insert(tables.agent).values(
            id=agent_id,
            workspace_id=workspace_id,
            name=name,
            prompt="p",
            model="claude-opus-4-8",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )


async def _set_cap(
    connection: AsyncConnection,
    workspace_id: UUID,
    scope: str,
    subject_id: UUID | None,
    window_seconds: int,
    limit_micro_usd: int,
    on_breach: str,
) -> UUID:
    cap_id = uuid4()
    await connection.execute(
        sa.insert(tables.spend_cap).values(
            id=cap_id,
            workspace_id=workspace_id,
            scope=scope,
            subject_id=subject_id,
            window_seconds=window_seconds,
            limit_micro_usd=limit_micro_usd,
            on_breach=on_breach,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return cap_id


async def _shared_conversation(
    connection: AsyncConnection, workspace_id: UUID, agent_id: UUID
) -> UUID:
    conversation_id = uuid4()
    await connection.execute(
        sa.insert(tables.conversation).values(
            id=conversation_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            surface="web",
            queue_key=uuid4().hex,
            member_id=None,
            audience="shared",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return conversation_id


async def _bill(
    connection: AsyncConnection,
    workspace_id: UUID,
    conversation_id: UUID,
    agent_id: UUID,
    priced_micro_usd: int,
    seq: int,
    created_at: datetime | None = None,
    speaker_member_id: UUID | None = None,
) -> UUID:
    """A prior terminal turn and its ledger row — the spend the caps decide against."""
    turn_id = uuid4()
    stamp = created_at if created_at is not None else sa.func.now()
    await connection.execute(
        sa.insert(tables.turn).values(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=seq,
            status="done",
            inbound="x",
            speaker_member_id=speaker_member_id,
            terminal=TerminalFrame(status="done").model_dump(mode="json"),
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    await connection.execute(
        sa.insert(tables.ledger).values(
            id=uuid4(),
            workspace_id=workspace_id,
            turn_id=turn_id,
            dimension="tokens",
            amount=10,
            prompt_tokens=10,
            input_tokens=10,
            priced_micro_usd=priced_micro_usd,
            model="claude-opus-4-8",
            created_at=stamp,
            updated_at=sa.func.now(),
        )
    )
    return turn_id


async def _insert_parked(
    connection: AsyncConnection,
    workspace_id: UUID,
    conversation_id: UUID,
    agent_id: UUID,
    seq: int,
    speaker_member_id: UUID | None = None,
) -> UUID:
    turn_id = uuid4()
    await connection.execute(
        sa.insert(tables.turn).values(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=seq,
            status="parked",
            inbound="held",
            speaker_member_id=speaker_member_id,
            terminal=None,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return turn_id


async def _insert_queued(
    connection: AsyncConnection,
    workspace_id: UUID,
    conversation_id: UUID,
    agent_id: UUID,
    seq: int,
    dispatch_enqueued_at: datetime | None = None,
    running_attempt: str | None = None,
) -> UUID:
    turn_id = uuid4()
    await connection.execute(
        sa.insert(tables.turn).values(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=seq,
            status="queued",
            inbound="waiting",
            terminal=None,
            dispatch_enqueued_at=dispatch_enqueued_at,
            running_attempt=running_attempt,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return turn_id


async def _status(turn_id: UUID) -> str:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one()


async def test_workspace_cap_counts_extension_spend(db: None) -> None:
    """A background job's workspace-anchored spend (turn_id NULL) moves a workspace-scope cap, which
    sums every ledger row for the workspace."""
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, _ = await _seed(connection)
        await record_workspace_usage(
            connection, workspace_id, "claude-opus-4-8", Usage(input_tokens=1000)
        )
        await _set_cap(connection, workspace_id, "workspace", None, 3600, 1000, "park")
        decision = await SpendEvaluator(workspace_id, member_id, agent_id).decide(connection, 0)
    assert decision.outcome == "park"


async def test_member_cap_ignores_extension_spend(db: None) -> None:
    """The same workspace-anchored spend is attributed to no member, so a member-scope cap — which
    joins through the turn — never sees it."""
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, _ = await _seed(connection)
        await record_workspace_usage(
            connection, workspace_id, "claude-opus-4-8", Usage(input_tokens=1000)
        )
        await _set_cap(connection, workspace_id, "member", member_id, 3600, 1000, "reject")
        decision = await SpendEvaluator(workspace_id, member_id, agent_id).decide(connection, 0)
    assert decision.outcome == "allow"


async def test_member_cap_parks_when_over(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        await _bill(
            connection,
            workspace_id,
            conversation_id,
            agent_id,
            100,
            seq=1,
            speaker_member_id=member_id,
        )
        await _set_cap(connection, workspace_id, "member", member_id, 3600, 50, "park")
        decision = await SpendEvaluator(workspace_id, member_id, agent_id).decide(connection, 0)
    assert decision.outcome == "park"
    assert "parked" in decision.message


async def test_member_cap_counts_the_members_own_turns_wherever_they_speak(db: None) -> None:
    """A member cap sums the turns the member spoke or delegated, whichever conversation holds
    them: their turn in a workspace conversation counts, while a speakerless turn in a conversation
    they founded does not."""
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, founded = await _seed(connection)
        shared = await _shared_conversation(connection, workspace_id, agent_id)
        await _bill(
            connection, workspace_id, shared, agent_id, 40, seq=1, speaker_member_id=member_id
        )
        await _bill(connection, workspace_id, founded, agent_id, 100, seq=1)
        await _set_cap(connection, workspace_id, "member", member_id, 3600, 50, "park")
        under = await SpendEvaluator(workspace_id, member_id, agent_id).decide(connection, 0)
        await _bill(
            connection, workspace_id, shared, agent_id, 20, seq=2, speaker_member_id=member_id
        )
        over = await SpendEvaluator(workspace_id, member_id, agent_id).decide(connection, 0)
    assert under.outcome == "allow"
    assert over.outcome == "park"


async def test_member_cap_rejects_when_over(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        await _bill(
            connection,
            workspace_id,
            conversation_id,
            agent_id,
            100,
            seq=1,
            speaker_member_id=member_id,
        )
        await _set_cap(connection, workspace_id, "member", member_id, 3600, 50, "reject")
        decision = await SpendEvaluator(workspace_id, member_id, agent_id).decide(connection, 0)
    assert decision.outcome == "reject"
    assert "declined" in decision.message


async def test_under_cap_allows(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        await _bill(
            connection,
            workspace_id,
            conversation_id,
            agent_id,
            40,
            seq=1,
            speaker_member_id=member_id,
        )
        await _set_cap(connection, workspace_id, "member", member_id, 3600, 100, "park")
        decision = await SpendEvaluator(workspace_id, member_id, agent_id).decide(connection, 0)
    assert decision.outcome == "allow"


async def test_sandbox_tokens_count_toward_a_cap(db: None) -> None:
    """A priced `sandbox_tokens` row (an in-sandbox model call) sums into the cap alongside host
    token spend — _used_micro_usd sums priced rows across dimensions, so caps enforce it once
    priced."""
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        turn_id = uuid4()
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="done",
                inbound="x",
                speaker_member_id=member_id,
                terminal=TerminalFrame(status="done").model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await record_sandbox_tokens(
            connection,
            workspace_id,
            turn_id,
            "claude-opus-4-8",
            Usage(input_tokens=1000, output_tokens=2000),
        )
        await _set_cap(connection, workspace_id, "member", member_id, 3600, 50, "park")
        decision = await SpendEvaluator(workspace_id, member_id, agent_id).decide(connection, 0)
    assert decision.outcome == "park"


async def test_pending_in_flight_crosses_cap(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        await _bill(
            connection,
            workspace_id,
            conversation_id,
            agent_id,
            40,
            seq=1,
            speaker_member_id=member_id,
        )
        await _set_cap(connection, workspace_id, "member", member_id, 3600, 50, "park")
        evaluator = SpendEvaluator(workspace_id, member_id, agent_id)
        assert (await evaluator.decide(connection, 0)).outcome == "allow"
        assert (await evaluator.decide(connection, 20)).outcome == "park"


async def test_spend_outside_window_not_counted(db: None) -> None:
    old = datetime.now(UTC) - timedelta(hours=2)
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        await _bill(
            connection,
            workspace_id,
            conversation_id,
            agent_id,
            100,
            seq=1,
            created_at=old,
            speaker_member_id=member_id,
        )
        await _set_cap(connection, workspace_id, "member", member_id, 3600, 50, "park")
        decision = await SpendEvaluator(workspace_id, member_id, agent_id).decide(connection, 0)
    assert decision.outcome == "allow"


async def test_workspace_scope_sums_across_members(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        other_member, other_conversation = uuid4(), uuid4()
        await connection.execute(
            sa.insert(tables.member).values(
                id=other_member,
                workspace_id=workspace_id,
                email="c@d.e",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=other_conversation,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=other_member,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await _bill(connection, workspace_id, conversation_id, agent_id, 30, seq=1)
        await _bill(connection, workspace_id, other_conversation, agent_id, 30, seq=1)
        await _set_cap(connection, workspace_id, "workspace", None, 3600, 50, "park")
        decision = await SpendEvaluator(workspace_id, member_id, agent_id).decide(connection, 0)
    assert decision.outcome == "park"


async def test_agent_scope_counts_only_its_agent(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        other_agent = uuid4()
        await _agent(connection, workspace_id, other_agent, "second")
        await _bill(connection, workspace_id, conversation_id, other_agent, 100, seq=1)
        await _set_cap(connection, workspace_id, "agent", agent_id, 3600, 50, "park")
        decision = await SpendEvaluator(workspace_id, member_id, agent_id).decide(connection, 0)
    assert decision.outcome == "allow"


async def test_cap_for_other_member_does_not_apply(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        await _bill(
            connection,
            workspace_id,
            conversation_id,
            agent_id,
            100,
            seq=1,
            speaker_member_id=member_id,
        )
        await _set_cap(connection, workspace_id, "member", uuid4(), 3600, 50, "park")
        decision = await SpendEvaluator(workspace_id, member_id, agent_id).decide(connection, 0)
    assert decision.outcome == "allow"


async def test_admission_parks_over_cap_member_without_enqueue(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        await _bill(
            connection,
            workspace_id,
            conversation_id,
            agent_id,
            100,
            seq=1,
            speaker_member_id=member_id,
        )
        await _set_cap(connection, workspace_id, "member", member_id, 3600, 50, "park")
    dbos = StubDbos()
    turn_id = (
        await Admission(dbos=dbos, durable_surfaces=frozenset()).admit_member(
            workspace_id, conversation_id, "hi", member_id
        )
    ).turn_id
    assert dbos.enqueued == []
    assert await _status(turn_id) == "parked"


async def test_admission_caps_the_speaking_member_not_the_conversations_founder(db: None) -> None:
    """Admission decides a member cap against the turn's own member: the capped member's turn in a
    workspace conversation parks, while a workspace turn — in another workspace conversation, and
    in the very conversation the capped member founded — is not held by that member's cap."""
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, founded = await _seed(connection)
        shared = await _shared_conversation(connection, workspace_id, agent_id)
        elsewhere = await _shared_conversation(connection, workspace_id, agent_id)
        await _bill(
            connection, workspace_id, shared, agent_id, 100, seq=1, speaker_member_id=member_id
        )
        await _set_cap(connection, workspace_id, "member", member_id, 3600, 50, "park")
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset())
    held = (await admission.admit_member(workspace_id, shared, "hi", member_id)).turn_id
    assert dbos.enqueued == []
    assert await _status(held) == "parked"
    workspace_turn = await admission.invoke(workspace_id, elsewhere, agent_id, "hi")
    founders_turn = await admission.invoke(workspace_id, founded, agent_id, "hi")
    assert dbos.enqueued == [str(workspace_turn), str(founders_turn)]
    assert await _status(workspace_turn) == "queued"
    assert await _status(founders_turn) == "queued"


async def test_admission_rejects_over_cap_member_with_reason(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        await _bill(
            connection,
            workspace_id,
            conversation_id,
            agent_id,
            100,
            seq=1,
            speaker_member_id=member_id,
        )
        await _set_cap(connection, workspace_id, "member", member_id, 3600, 50, "reject")
    dbos = StubDbos()
    turn_id = (
        await Admission(dbos=dbos, durable_surfaces=frozenset()).admit_member(
            workspace_id, conversation_id, "hi", member_id
        )
    ).turn_id
    assert dbos.enqueued == []
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.terminal).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).one()
    assert row.status == "cancelled"
    assert "declined" in TerminalFrame.model_validate(row.terminal).text


async def test_dispatch_stamp_excludes_a_concurrent_sweep(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, conversation_id = await _seed(connection)
        queued = await _insert_queued(connection, workspace_id, conversation_id, agent_id, seq=1)
    blocked = BlockingDbos()
    first = asyncio.create_task(_dispatch(blocked))
    await blocked.started.wait()
    second = StubDbos()
    await _dispatch(second)
    blocked.release.set()
    await first
    assert blocked.enqueued == [str(queued)]
    assert second.enqueued == []


async def test_resume_skips_turn_still_over_cap(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        await _bill(
            connection,
            workspace_id,
            conversation_id,
            agent_id,
            100,
            seq=1,
            speaker_member_id=member_id,
        )
        await _set_cap(connection, workspace_id, "member", member_id, 3600, 50, "park")
        parked = await _insert_parked(
            connection, workspace_id, conversation_id, agent_id, seq=2, speaker_member_id=member_id
        )
    dbos = StubDbos()
    await _dispatch(dbos)
    assert dbos.enqueued == []
    assert await _status(parked) == "parked"


async def test_resume_readmits_when_cap_raised(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        await _bill(
            connection,
            workspace_id,
            conversation_id,
            agent_id,
            100,
            seq=1,
            speaker_member_id=member_id,
        )
        cap_id = await _set_cap(connection, workspace_id, "member", member_id, 3600, 50, "park")
        parked = await _insert_parked(
            connection, workspace_id, conversation_id, agent_id, seq=2, speaker_member_id=member_id
        )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.spend_cap)
            .values(limit_micro_usd=10_000, updated_at=sa.func.now())
            .where(tables.spend_cap.c.id == cap_id)
        )
    dbos = StubDbos()
    await _dispatch(dbos)
    assert dbos.enqueued == [str(parked)]
    assert await _status(parked) == "parked"


async def test_resume_touches_only_the_workspace_holding_a_parked_turn(db: None) -> None:
    """Selectivity: two workspaces exist but only ws_a holds a parked turn. One `run` — no caller
    binds a workspace — finds ws_a's turn through the single owner_tx read, binds ws_a, and enqueues
    it; ws_b, having a seeded conversation but no parked turn, is never a candidate, so nothing is
    enqueued or scoped to it. The idle workspace runs no per-workspace transaction on the sweep."""
    async with workspace_tx() as connection:
        ws_a, _, agent_a, conv_a = await _seed(connection)
        parked_a = await _insert_parked(connection, ws_a, conv_a, agent_a, seq=1)
        ws_b, _, _, _ = await _seed(connection)
    dbos = StubDbos()
    await _dispatch(dbos)
    assert dbos.enqueued == [str(parked_a)]
    assert dbos.scoped == [(str(ws_a), str(parked_a))]
    assert all(scoped_ws != str(ws_b) for scoped_ws, _ in dbos.scoped)


async def test_resume_scopes_the_cap_decision_to_each_workspace(db: None) -> None:
    """The cap decision binds each turn's own workspace. Both workspaces hold a parked turn, so one
    `run` enumerates both through owner_tx; the workspace already over its member cap keeps its turn
    parked while the one with headroom is re-admitted — the decision read each workspace's own
    spend, never the other's — and only the headroom workspace's turn is enqueued, scoped to it."""
    async with workspace_tx() as connection:
        over_ws, over_member, over_agent, over_conv = await _seed(connection)
        await _bill(
            connection, over_ws, over_conv, over_agent, 100, seq=1, speaker_member_id=over_member
        )
        await _set_cap(connection, over_ws, "member", over_member, 3600, 50, "park")
        over_parked = await _insert_parked(
            connection, over_ws, over_conv, over_agent, seq=2, speaker_member_id=over_member
        )
        free_ws, _, free_agent, free_conv = await _seed(connection)
        free_parked = await _insert_parked(connection, free_ws, free_conv, free_agent, seq=1)
    dbos = StubDbos()
    await _dispatch(dbos)
    assert dbos.enqueued == [str(free_parked)]
    assert dbos.scoped == [(str(free_ws), str(free_parked))]
    assert await _status(over_parked) == "parked"
