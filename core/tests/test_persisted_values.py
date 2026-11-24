from dataclasses import dataclass, field
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
import sqlalchemy as sa
from dbos import EnqueueOptions
from sqlalchemy.ext.asyncio import AsyncConnection
from ufo_ext_sample.spend import SampleGate, allow
from ufo_testsupport.invoker import RecordingInvoker

from ufo.db import workspace_tx
from ufo.harness.auth.bearer import SESSION_COOKIE
from ufo.runtime.billing.accounting import OffTurnSpendRefused
from ufo.runtime.billing.spend import PARK, REJECT, GateDeploy, SpendGates
from ufo.runtime.ext.context import (
    CORE_EXTENSION,
    SPEND_REFUSAL_NOTICE_KEY,
    ExtensionContext,
    spend_refusal_notice_key,
)
from ufo.runtime.ext.manifest import JobSpec
from ufo.runtime.ext.operator import OPERATOR_COOKIE
from ufo.runtime.jobs import TURN_DISPATCH_JOB, JobRunner, TurnDispatcher, bindings_from
from ufo.runtime.surfaces.admission import Admission
from ufo.schema import tables
from ufo.schema.records import MEMBER_ADMISSION, SPEND_HOLD_ROUND_INDEX, TerminalFrame

DOLLAR = 1_000_000
REFUSED_MODEL = "gpt-6-luna"
HELD_NOTICE = "This conversation is held until the workspace has credit."
SPEND = SpendGates(gates=(SampleGate(GateDeploy(public_base_url=None, home_surface=None)),))


@dataclass
class _Enqueued:
    turns: list[str] = field(default_factory=list)

    async def enqueue_async(self, options: EnqueueOptions, workspace_id: str, turn_id: str) -> None:
        self.turns.append(turn_id)


def test_the_persisted_names_keep_their_values() -> None:
    assert SPEND_HOLD_ROUND_INDEX == -2
    assert (PARK, REJECT) == ("park", "reject")
    assert CORE_EXTENSION == "core"
    assert SPEND_REFUSAL_NOTICE_KEY == "spend_refusal_notice"
    assert spend_refusal_notice_key(REFUSED_MODEL) == f"spend_refusal_notice:{REFUSED_MODEL}"
    assert (SESSION_COOKIE, OPERATOR_COOKIE) == ("ufo_session", "ufo_debug")


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
            email="member@work.com",
            is_admin=True,
            seated_at=sa.func.now(),
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    await connection.execute(
        sa.insert(tables.agent).values(
            id=agent_id,
            workspace_id=workspace_id,
            name="Main",
            prompt="help",
            model="auto",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    await connection.execute(
        sa.insert(tables.conversation).values(
            id=conversation_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            surface="cli",
            queue_key=str(conversation_id),
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return workspace_id, member_id, agent_id, conversation_id


async def _turn(
    connection: AsyncConnection,
    workspace_id: UUID,
    conversation_id: UUID,
    agent_id: UUID,
    member_id: UUID,
    status: str,
    terminal: TerminalFrame | None = None,
) -> UUID:
    turn_id = uuid4()
    await connection.execute(
        sa.insert(tables.turn).values(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status=status,
            inbound="help",
            terminal=None if terminal is None else terminal.model_dump(mode="json"),
            admission_source=MEMBER_ADMISSION,
            speaker_member_id=member_id,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return turn_id


async def _notices(turn_id: UUID) -> list[tuple[int, str]]:
    async with workspace_tx() as connection:
        rows = await connection.execute(
            sa.select(tables.mid_turn_reply.c.round_index, tables.mid_turn_reply.c.text).where(
                tables.mid_turn_reply.c.turn_id == turn_id
            )
        )
        return [(row.round_index, row.text) for row in rows]


async def _dispatch(client: _Enqueued) -> None:
    dispatcher = TurnDispatcher(client=client, spend=SPEND)

    async def _handler(context: ExtensionContext) -> None:
        await dispatcher.run()

    spec = JobSpec(
        name=TURN_DISPATCH_JOB,
        schedule=None,
        handler=_handler,
        candidates=dispatcher.candidate_workspaces,
    )
    runner = JobRunner(bindings=bindings_from((), (spec,)), manifests=())
    key = f"{CORE_EXTENSION}:{TURN_DISPATCH_JOB}"
    for workspace_id in await runner.candidates(key):
        await runner.fire(key, workspace_id)


async def test_a_turn_held_before_the_deploy_speaks_its_hold_once_and_resumes_once(
    db: None,
) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        await allow(connection, workspace_id, 0, "reject")
        held = await _turn(connection, workspace_id, conversation_id, agent_id, member_id, "parked")
        await connection.execute(
            sa.insert(tables.mid_turn_reply).values(
                id=uuid5(NAMESPACE_URL, f"{held}/reply//-2/0"),
                workspace_id=workspace_id,
                turn_id=held,
                round_index=-2,
                span_index=0,
                text=HELD_NOTICE,
                status="pending",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    enqueued = _Enqueued()
    admission = Admission(dbos=enqueued, durable_surfaces=frozenset({"cli"}), spend=SPEND)

    folded = await admission.admit_member(workspace_id, conversation_id, "and this", member_id)

    assert folded.turn_id == held
    assert await _notices(held) == [(-2, HELD_NOTICE)]
    async with workspace_tx() as connection:
        await allow(connection, workspace_id, 20 * DOLLAR, "reject")
    await _dispatch(enqueued)
    await _dispatch(enqueued)
    assert enqueued.turns == [str(held)]
    assert await _notices(held) == [(-2, HELD_NOTICE)]


@pytest.mark.parametrize(("written", "other"), [("park", "reject"), ("reject", "park")])
async def test_a_refusal_mark_written_before_the_deploy_tells_the_member_once(
    db: None, written: str, other: str
) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        await _turn(
            connection,
            workspace_id,
            conversation_id,
            agent_id,
            member_id,
            "done",
            TerminalFrame(status="done", text="hi"),
        )
        await connection.execute(
            sa.insert(tables.ext_store).values(
                workspace_id=workspace_id,
                extension="core",
                key=f"spend_refusal_notice:{REFUSED_MODEL}",
                value=written,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    refusals = [written, other, other]

    async def _refused(context: ExtensionContext) -> None:
        raise OffTurnSpendRefused(refusals.pop(0), "no credit left", REFUSED_MODEL)

    async def _candidate() -> tuple[UUID, ...]:
        return (workspace_id,)

    invoker = RecordingInvoker()
    runner = JobRunner(
        bindings=bindings_from(
            (), (JobSpec(name="memory", schedule=None, handler=_refused, candidates=_candidate),)
        ),
        manifests=(),
        invoker_factory=lambda _: invoker,
    )
    key = f"{CORE_EXTENSION}:memory"

    await runner.fire(key, workspace_id)
    assert invoker.turns == []
    await runner.fire(key, workspace_id)
    await runner.fire(key, workspace_id)
    assert refusals == []
    assert [turn.conversation_id for turn in invoker.turns] == [conversation_id]
