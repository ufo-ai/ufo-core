from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample.manifest as sample
from sqlalchemy.ext.asyncio import AsyncConnection
from test_spend_caps import StubDbos, _bill, _insert_parked, _seed, _set_cap, _status
from ufo_ext_sample.spend import (
    EXEMPT_ACTION,
    SPEND_GATE,
    AllowanceRaised,
    OnEmpty,
    SampleGate,
    allow,
)

from ufo.db import workspace_tx
from ufo.harness.models.catalog import OPENAI_KEY_SLOT
from ufo.host.ext.loader import load_manifests
from ufo.runtime.access.credentials import member_slot
from ufo.runtime.billing.spend import (
    ADMIT_MOMENT,
    ALLOW,
    ALLOWED,
    NO_SPEND_GATES,
    PARK,
    REJECT,
    STATUS_MOMENT,
    GateDeploy,
    SpendDecision,
    SpendGates,
    built_gates,
    composed,
)
from ufo.runtime.ext.context import ExtensionContext
from ufo.runtime.ext.manifest import JobSpec
from ufo.runtime.hub import Parked
from ufo.runtime.jobs import (
    CORE_EXTENSION,
    TURN_DISPATCH_JOB,
    JobRunner,
    TurnDispatcher,
    bindings_from,
)
from ufo.runtime.seats import create_member
from ufo.runtime.surfaces.admission import Admission
from ufo.runtime.surfaces.hub_tail import PARK_NOTICE, turn_status_frame
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import SPEND_HOLD_ROUND_INDEX, TerminalFrame, ToolIntent

NOWHERE = GateDeploy(public_base_url=None, home_surface=None)
SPEND = SpendGates(gates=(SampleGate(NOWHERE),))
DOLLAR = 1_000_000


async def _seat(connection: AsyncConnection, member_id: UUID) -> None:
    await connection.execute(
        sa.update(tables.member)
        .where(tables.member.c.id == member_id)
        .values(seated_at=sa.func.now())
    )


async def _terminal(turn_id: UUID) -> TerminalFrame:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.terminal).where(tables.turn.c.id == turn_id)
            )
        ).one()
    return TerminalFrame.model_validate(row.terminal)


async def _notices(turn_id: UUID) -> list[tuple[int, str]]:
    async with workspace_tx() as connection:
        rows = await connection.execute(
            sa.select(tables.mid_turn_reply.c.round_index, tables.mid_turn_reply.c.text).where(
                tables.mid_turn_reply.c.turn_id == turn_id
            )
        )
        return [(row.round_index, row.text) for row in rows]


async def _arrivals(turn_id: UUID) -> list[str]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.inbound_message.c.body)
                    .where(tables.inbound_message.c.admitted_turn_id == turn_id)
                    .order_by(tables.inbound_message.c.seq)
                )
            )
            .scalars()
            .all()
        )


async def _dispatch(client: StubDbos) -> None:
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


def _admission(dbos: StubDbos) -> Admission:
    return Admission(dbos=dbos, durable_surfaces=frozenset({"cli"}), spend=SPEND)


def test_a_verdict_composes_reject_over_park_over_allow() -> None:
    capped = SpendDecision(outcome=PARK, message="cap")
    held = SpendDecision(outcome=PARK, message="gate")
    refused = SpendDecision(outcome=REJECT, message="refused")
    assert composed() == ALLOWED
    assert composed(ALLOWED, ALLOWED) == ALLOWED
    assert composed(capped, held) == capped
    assert composed(capped, refused, held) == refused


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_sample_gate_is_built_from_its_manifest_point_and_the_deploy(db: None) -> None:
    (manifest,) = (m for m in load_manifests() if m.name == sample.NAME)
    assert [spec.name for spec in manifest.spend_gates] == [SPEND_GATE]
    deployed = SpendGates(
        gates=built_gates(
            manifest.spend_gates,
            GateDeploy(public_base_url="https://ufo.example.com/", home_surface="web"),
        )
    )
    async with workspace_tx() as connection:
        workspace_id, *_ = await _seed(connection)
        await allow(connection, workspace_id, 0, "reject")
        decision = await deployed.admit(connection, ADMIT_MOMENT, workspace_id)
    assert decision == SpendDecision(
        outcome=REJECT,
        message="The sample allowance is spent at admit. Add allowance at "
        "https://ufo.example.com/surface/web",
    )
    with pytest.raises(ValueError, match=f"spend gates declared twice: {SPEND_GATE}"):
        built_gates((*manifest.spend_gates, *manifest.spend_gates), NOWHERE)


@pytest.mark.parametrize("on_empty", ["reject", "hold", "park"])
async def test_a_gate_holds_a_member_message_says_so_once_and_resumes_once_it_allows(
    db: None, on_empty: OnEmpty
) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, _, conversation_id = await _seed(connection)
        await _seat(connection, member_id)
        await allow(connection, workspace_id, 0, on_empty)
    dbos = StubDbos()
    admission = _admission(dbos)
    held = await admission.admit_member(workspace_id, conversation_id, "help", member_id)
    notice = [(SPEND_HOLD_ROUND_INDEX, "The sample allowance is spent at admit.")]
    assert dbos.enqueued == []
    assert await _status(held.turn_id) == "parked"
    assert await _notices(held.turn_id) == notice

    joined = await admission.admit_member(workspace_id, conversation_id, "and this", member_id)
    assert joined.turn_id == held.turn_id
    assert joined.arrival_id is not None
    assert await _arrivals(held.turn_id) == ["and this"]
    assert await _notices(held.turn_id) == notice

    await _dispatch(dbos)
    assert dbos.enqueued == []
    async with workspace_tx() as connection:
        await allow(connection, workspace_id, 20 * DOLLAR, "reject")
    await _dispatch(dbos)
    await _dispatch(dbos)
    assert dbos.enqueued == [str(held.turn_id)]
    assert await _notices(held.turn_id) == notice


async def test_folding_onto_a_turn_parked_mid_flight_tells_the_thread_it_is_held(
    db: None,
) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        await _seat(connection, member_id)
        held = await _insert_parked(connection, workspace_id, conversation_id, agent_id, seq=1)
        await allow(connection, workspace_id, 0, "park")
    assert await _notices(held) == []
    dbos = StubDbos()
    folded = await _admission(dbos).admit_member(workspace_id, conversation_id, "more", member_id)
    assert folded.turn_id == held
    assert dbos.enqueued == []
    assert await _notices(held) == [
        (SPEND_HOLD_ROUND_INDEX, "The sample allowance is spent at fold.")
    ]


DECLINED = "This turn was declined: the workspace spend cap of $1.00 is reached."
CAP_PARKED = (
    "This turn is parked: the workspace spend cap of $1.00 is reached. "
    "It resumes when the cap is raised."
)
GATE_SPENT = "The sample allowance is spent at admit."


@pytest.mark.parametrize(
    ("on_breach", "on_empty", "refusal"),
    [
        ("reject", "reject", DECLINED),
        ("park", "reject", GATE_SPENT),
        ("reject", "park", DECLINED),
        ("park", "park", CAP_PARKED),
        ("reject", "hold", DECLINED),
        ("park", "hold", GATE_SPENT),
    ],
)
async def test_a_cap_that_holds_refuses_the_message_a_gate_would_have_held(
    db: None, on_breach: str, on_empty: OnEmpty, refusal: str
) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        await _seat(connection, member_id)
        await _bill(connection, workspace_id, conversation_id, agent_id, 10 * DOLLAR, seq=1)
        await _set_cap(connection, workspace_id, "workspace", None, 3600, DOLLAR, on_breach)
        await allow(connection, workspace_id, 0, on_empty)
    dbos = StubDbos()
    admitted = await _admission(dbos).admit_member(workspace_id, conversation_id, "help", member_id)
    assert dbos.enqueued == []
    assert await _status(admitted.turn_id) == "cancelled"
    assert (await _terminal(admitted.turn_id)).text == refusal
    assert await _notices(admitted.turn_id) == []


@pytest.mark.parametrize(
    ("on_empty", "status", "notices"),
    [
        ("reject", "cancelled", []),
        ("hold", "cancelled", []),
        ("park", "parked", [(SPEND_HOLD_ROUND_INDEX, "The sample allowance is spent at admit.")]),
    ],
)
async def test_a_scheduled_fire_is_refused_by_a_gate_reject_and_held_by_a_gate_park(
    db: None, on_empty: OnEmpty, status: str, notices: list[tuple[int, str]]
) -> None:
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, conversation_id = await _seed(connection)
        await _bill(connection, workspace_id, conversation_id, agent_id, 0, seq=1)
        await allow(connection, workspace_id, 0, on_empty)
    dbos = StubDbos()
    turn_id = await _admission(dbos).invoke(
        workspace_id, conversation_id, agent_id, "fire", as_scheduled=True
    )
    assert dbos.enqueued == []
    assert await _status(turn_id) == status
    assert await _notices(turn_id) == notices


async def test_the_gate_decides_on_the_prepared_intent_it_is_shown(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, _, conversation_id = await _seed(connection)
        await _seat(connection, member_id)
        await allow(connection, workspace_id, 0, "reject")
    exempt = ToolIntent(
        tool="object_action",
        input={"kind": "workspace", "action": EXEMPT_ACTION, "name": "w", "input": {}},
    )
    other = ToolIntent(
        tool="object_action",
        input={"kind": "workspace", "action": "rename", "name": "w", "input": {}},
    )
    admission = _admission(StubDbos())
    admitted = await admission.admit_member(
        workspace_id, conversation_id, exempt.model_dump_json(), member_id, intent=exempt
    )
    refused = await admission.admit_member(
        workspace_id, conversation_id, other.model_dump_json(), member_id, intent=other
    )
    assert await _status(admitted.turn_id) == "queued"
    assert await _status(refused.turn_id) == "cancelled"
    assert (await _terminal(refused.turn_id)).text == "The sample allowance is spent at admit."
    assert await _notices(refused.turn_id) == []


async def test_a_gate_that_raises_aborts_the_admission_it_was_asked_in(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, member_id, _, conversation_id = await _seed(connection)
        await _seat(connection, member_id)
        await allow(connection, workspace_id, 0, "raise")
    with pytest.raises(AllowanceRaised):
        await _admission(StubDbos()).admit_member(workspace_id, conversation_id, "help", member_id)
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .where(tables.turn.c.workspace_id == workspace_id)
            )
        ).scalar_one()
        with pytest.raises(AllowanceRaised):
            await SPEND.sustain(connection, workspace_id, uuid4(), 0)
    assert turns == 0


async def test_a_members_own_key_is_not_the_workspaces_to_spend(db: None) -> None:
    keyed = SpendGates(gates=SPEND.gates, key_slot_for=lambda _model: OPENAI_KEY_SLOT)
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, _ = await _seed(connection)
        member = await create_member(connection, workspace_id, "member@work.com")
        await allow(connection, workspace_id, 0, "reject")
        assert (await keyed.admit(connection, ADMIT_MOMENT, workspace_id, agent_id=agent_id)) != (
            ALLOWED
        )
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot=member_slot(OPENAI_KEY_SLOT, member),
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        assert (
            await keyed.admit(connection, ADMIT_MOMENT, workspace_id, agent_id=agent_id)
        ).outcome == REJECT
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot=OPENAI_KEY_SLOT,
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        assert (
            await keyed.admit(connection, ADMIT_MOMENT, workspace_id, agent_id=agent_id)
        ) == ALLOWED


async def test_a_platform_paid_ask_is_never_self_funded(db: None) -> None:
    keyed = SpendGates(
        gates=SPEND.gates,
        key_slot_for=lambda _model: OPENAI_KEY_SLOT,
        own_key_slots=(OPENAI_KEY_SLOT,),
    )
    async with workspace_tx() as connection:
        workspace_id, *_ = await _seed(connection)
        await allow(connection, workspace_id, 0, "reject")
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot=OPENAI_KEY_SLOT,
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        own_key = await keyed.admit(connection, STATUS_MOMENT, workspace_id)
        platform_paid = await keyed.admit(
            connection, STATUS_MOMENT, workspace_id, self_funded=False
        )
    assert own_key == ALLOWED
    assert platform_paid == SpendDecision(
        outcome=REJECT, message="The sample allowance is spent at status."
    )


async def test_the_status_frame_names_what_holds_a_parked_turn(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, conversation_id = await _seed(connection)
        turn_id = await _insert_parked(connection, workspace_id, conversation_id, agent_id, seq=1)
        await allow(connection, workspace_id, 0, "park")
    with ws(workspace_id):
        assert await turn_status_frame(turn_id, SPEND) == Parked(
            message="The sample allowance is spent at status."
        )
        assert await turn_status_frame(turn_id) == Parked(message=PARK_NOTICE)
        async with workspace_tx() as connection:
            await allow(connection, workspace_id, DOLLAR, "park")
        assert await turn_status_frame(turn_id, SPEND) == Parked(message=PARK_NOTICE)


async def test_admitting_is_the_candidate_filter_the_gates_hold_workspaces_out_of(
    db: None,
) -> None:
    own_key = SpendGates(gates=SPEND.gates, own_key_slots=(OPENAI_KEY_SLOT,))
    async with workspace_tx() as connection:
        unmetered, *_ = await _seed(connection)
        allowed, *_ = await _seed(connection)
        spent, *_ = await _seed(connection)
        paying, *_ = await _seed(connection)
        await allow(connection, allowed, DOLLAR, "reject")
        await allow(connection, spent, 0, "reject")
        await allow(connection, paying, 0, "reject")
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=paying,
                slot=OPENAI_KEY_SLOT,
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        seeded = (unmetered, allowed, spent, paying)

        async def admitted(spend: SpendGates) -> set[UUID]:
            rows = await connection.execute(
                sa.select(tables.workspace.c.id).where(
                    tables.workspace.c.id.in_(seeded), spend.admitting(tables.workspace.c.id)
                )
            )
            return set(rows.scalars())

        assert await admitted(NO_SPEND_GATES) == set(seeded)
        assert await admitted(SPEND) == {unmetered, allowed}
        assert await admitted(own_key) == {unmetered, allowed, paying}


async def test_a_gate_absent_for_a_workspace_is_not_asked_its_next_round(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, *_ = await _seed(connection)
        assert not SPEND.absent(workspace_id)
        assert (await SPEND.sustain(connection, workspace_id, uuid4(), DOLLAR)).outcome == ALLOW
        assert SPEND.absent(workspace_id)
        await allow(connection, workspace_id, 0, "park")
        assert not SPEND.absent(workspace_id)
        held = await SPEND.sustain(connection, workspace_id, uuid4(), 0)
    assert held.outcome == PARK
    assert NO_SPEND_GATES.absent(workspace_id)
