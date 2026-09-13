from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection
from test_spend_caps import (
    StubDbos,
    _bill,
    _dispatch,
    _insert_parked,
    _seed,
    _set_cap,
    _status,
)

from ufo.db import workspace_tx
from ufo.runtime.billing.accounting import ALLOW, BalanceGate, record_image_usage, record_turn_usage
from ufo.runtime.billing.balance import (
    TOPUP_GRACE_MICRO_USD,
    balance_park_message,
    balance_refusal_message,
    credit,
    debit,
    funded,
    mark_topup_verified,
    set_reserve,
)
from ufo.runtime.hub import Parked
from ufo.runtime.surfaces.admission import Admission
from ufo.runtime.surfaces.hub_tail import PARK_NOTICE, turn_status_frame
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import TerminalFrame, ToolIntent, Usage

DOLLAR = 1_000_000

pytestmark = pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)


def _own_key(_model: str) -> str:
    return "anthropic_api_key"


async def _insert_running(
    connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, agent_id: UUID, seq: int
) -> UUID:
    turn_id = uuid4()
    await connection.execute(
        sa.insert(tables.turn).values(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=seq,
            status="running",
            inbound="hi",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return turn_id


async def _fund(
    connection: AsyncConnection, workspace_id: UUID, dollars: int, reserve_dollars: int = 0
) -> None:
    await credit(connection, workspace_id, dollars * DOLLAR, dollars * DOLLAR, "seed")
    if reserve_dollars:
        await set_reserve(connection, workspace_id, reserve_dollars * DOLLAR)


async def _terminal(turn_id: UUID) -> TerminalFrame:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.terminal).where(tables.turn.c.id == turn_id)
            )
        ).one()
    return TerminalFrame.model_validate(row.terminal)


async def test_admission_rejects_at_the_reserve_with_a_reason(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=10, reserve_dollars=10)
    dbos = StubDbos()
    turn_id = await Admission(dbos=dbos, durable_surfaces=frozenset()).invoke(
        workspace_id, conversation_id, agent_id, "hi"
    )
    assert dbos.enqueued == []
    assert await _status(turn_id) == "cancelled"
    assert (await _terminal(turn_id)).text == balance_refusal_message(None)


async def test_admission_above_the_reserve_enqueues(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=10, reserve_dollars=2)
    dbos = StubDbos()
    turn_id = await Admission(dbos=dbos, durable_surfaces=frozenset()).invoke(
        workspace_id, conversation_id, agent_id, "hi"
    )
    assert dbos.enqueued == [str(turn_id)]


async def test_no_balance_row_allows_admission(db: None) -> None:
    """The self-host path: nothing was ever credited, so nothing gates."""
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, conversation_id = await _seed(connection)
    dbos = StubDbos()
    turn_id = await Admission(dbos=dbos, durable_surfaces=frozenset()).invoke(
        workspace_id, conversation_id, agent_id, "hi"
    )
    assert dbos.enqueued == [str(turn_id)]


async def _notices(turn_id: UUID) -> list[str]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.mid_turn_reply.c.text)
                    .where(tables.mid_turn_reply.c.turn_id == turn_id)
                    .order_by(tables.mid_turn_reply.c.round_index)
                )
            )
            .scalars()
            .all()
        )


async def _seat(connection: AsyncConnection, member_id: UUID) -> None:
    await connection.execute(
        sa.update(tables.member)
        .where(tables.member.c.id == member_id)
        .values(seated_at=sa.func.now())
    )


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


async def test_a_spent_balance_parks_a_member_message_and_says_so_once(db: None) -> None:
    """A member's message is held rather than cancelled, from the first message of a conversation
    on, and the hold is written as a reply the surface delivers. Cancelled, the message was dead the
    moment the credit ran out and no refill could wake it; parked in silence, the member read
    nothing. The member's next message joins the held turn — no second turn, no second notice — and
    the sweep that re-decides the balance every minute enqueues the one turn once credit lands."""
    async with workspace_tx() as connection:
        workspace_id, member_id, _, conversation_id = await _seed(connection)
        await _seat(connection, member_id)
        await _fund(connection, workspace_id, dollars=1, reserve_dollars=5)
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset({"cli"}))
    parked = await admission.admit_member(workspace_id, conversation_id, "help", member_id)
    assert dbos.enqueued == []
    assert await _status(parked.turn_id) == "parked"
    assert await _notices(parked.turn_id) == [balance_park_message(None)]

    joined = await admission.admit_member(workspace_id, conversation_id, "and this", member_id)
    assert joined.turn_id == parked.turn_id
    assert joined.arrival_id is not None
    assert dbos.enqueued == []
    assert await _status(parked.turn_id) == "parked"
    assert await _arrivals(parked.turn_id) == ["and this"]
    assert await _notices(parked.turn_id) == [balance_park_message(None)]

    dbos = StubDbos()
    await _dispatch(dbos)
    assert dbos.enqueued == []
    async with workspace_tx() as connection:
        await credit(connection, workspace_id, 20 * DOLLAR, 20 * DOLLAR, "top-up")
    dbos = StubDbos()
    await _dispatch(dbos)
    assert dbos.enqueued == [str(parked.turn_id)]
    assert await _notices(parked.turn_id) == [balance_park_message(None)]


async def test_folding_onto_a_turn_parked_mid_flight_tells_the_thread_it_is_held(
    db: None,
) -> None:
    """A turn the engine parked mid-flight ends its live stream and writes nothing durable, so the
    thread holds no notice. The member's next message folds onto it, and the fold is what has to say
    the thread is held — cancelling that message is what used to put the line in the thread. The
    notice rides the turn's own id, so a third and fourth message add none."""
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        await _seat(connection, member_id)
        held = await _insert_parked(connection, workspace_id, conversation_id, agent_id, seq=1)
        await _fund(connection, workspace_id, dollars=1, reserve_dollars=5)
    assert await _notices(held) == []
    dbos = StubDbos()
    admission = Admission(dbos=dbos, durable_surfaces=frozenset({"cli"}))
    folded = await admission.admit_member(workspace_id, conversation_id, "and this", member_id)
    assert folded.turn_id == held
    assert folded.arrival_id is not None
    assert dbos.enqueued == []
    assert await _status(held) == "parked"
    assert await _notices(held) == [balance_park_message(None)]

    again = await admission.admit_member(workspace_id, conversation_id, "and more", member_id)
    assert again.turn_id == held
    assert await _notices(held) == [balance_park_message(None)]


async def test_a_breached_cap_refuses_the_message_a_spent_balance_would_have_held(
    db: None,
) -> None:
    """Credit lifts a balance; it does not lift a cap. Held on both, the turn would sit past the one
    thing its notice promises — the sweep re-decides the cap too and keeps holding, and nothing
    turns a parked turn terminal — so the member would wait forever on a message that reads as
    answered soon. The cap decides first and the wait ends in-surface, as it did before the hold
    existed."""
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        await _seat(connection, member_id)
        await _bill(connection, workspace_id, conversation_id, agent_id, 10 * DOLLAR, seq=1)
        await _set_cap(connection, workspace_id, "workspace", None, 3600, DOLLAR, "reject")
        await _fund(connection, workspace_id, dollars=1, reserve_dollars=5)
    dbos = StubDbos()
    admitted = await Admission(dbos=dbos, durable_surfaces=frozenset({"cli"})).admit_member(
        workspace_id, conversation_id, "help", member_id
    )
    assert dbos.enqueued == []
    assert await _status(admitted.turn_id) == "cancelled"
    assert (await _terminal(admitted.turn_id)).text == balance_refusal_message(None)
    assert await _notices(admitted.turn_id) == []


async def test_a_parking_cap_refuses_the_message_a_spent_balance_would_have_held(
    db: None,
) -> None:
    """The same for a cap that parks rather than rejects: its window rolling is what releases it,
    which the hold notice does not say and credit does not do."""
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed(connection)
        await _seat(connection, member_id)
        await _bill(connection, workspace_id, conversation_id, agent_id, 10 * DOLLAR, seq=1)
        await _set_cap(connection, workspace_id, "workspace", None, 3600, DOLLAR, "park")
        await _fund(connection, workspace_id, dollars=1, reserve_dollars=5)
    dbos = StubDbos()
    admitted = await Admission(dbos=dbos, durable_surfaces=frozenset({"cli"})).admit_member(
        workspace_id, conversation_id, "help", member_id
    )
    assert dbos.enqueued == []
    assert await _status(admitted.turn_id) == "cancelled"
    assert (await _terminal(admitted.turn_id)).text == balance_refusal_message(None)
    assert await _notices(admitted.turn_id) == []


async def test_a_scheduled_fire_is_still_refused_by_a_spent_balance(db: None) -> None:
    """A schedule re-fires on its own; a fire held until credit lands would replay every missed
    fire at once, and a durable thread would read a hold notice per fire meanwhile."""
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, conversation_id = await _seed(connection)
        await _bill(connection, workspace_id, conversation_id, agent_id, 0, seq=1)
        await _fund(connection, workspace_id, dollars=1, reserve_dollars=5)
    dbos = StubDbos()
    turn_id = await Admission(dbos=dbos, durable_surfaces=frozenset({"cli"})).invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "fire",
        as_scheduled=True,
    )
    assert dbos.enqueued == []
    assert await _status(turn_id) == "cancelled"
    assert (await _terminal(turn_id)).text == balance_refusal_message(None)
    assert await _notices(turn_id) == []


async def test_a_credit_readmits_a_parked_turn(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1, reserve_dollars=5)
        parked = await _insert_parked(connection, workspace_id, conversation_id, agent_id, seq=2)
    dbos = StubDbos()
    await _dispatch(dbos)
    assert dbos.enqueued == []
    async with workspace_tx() as connection:
        await credit(connection, workspace_id, 20 * DOLLAR, 20 * DOLLAR, "top-up")
    dbos = StubDbos()
    await _dispatch(dbos)
    assert dbos.enqueued == [str(parked)]


async def test_a_credit_below_the_reserve_does_not_resume(db: None) -> None:
    """Resume clears the reserve, so a credit too small to give a turn room to work buys nothing —
    which is what stops a park-resume-park cycle forming."""
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1, reserve_dollars=10)
        await _insert_parked(connection, workspace_id, conversation_id, agent_id, seq=2)
        await credit(connection, workspace_id, 2 * DOLLAR, 2 * DOLLAR, "too-small")
    dbos = StubDbos()
    await _dispatch(dbos)
    assert dbos.enqueued == []


async def test_a_running_turn_stops_only_at_zero(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, _, _, _ = await _seed(connection)
        await _fund(connection, workspace_id, dollars=5, reserve_dollars=10)
        gate = BalanceGate(workspace_id)
        under = await gate.sustains(connection, pending_micro_usd=4 * DOLLAR)
        over = await gate.sustains(connection, pending_micro_usd=5 * DOLLAR)
    assert under.outcome == ALLOW
    assert over.outcome != ALLOW
    assert over.message == balance_refusal_message(None)


async def test_a_round_that_debits_nothing_is_never_held(db: None) -> None:
    """A turn whose spend the balance does not fund — a workspace's own provider key pays it — must
    not be parked by the balance. Parked, it leaves the balance exactly where it was, so the resume
    that follows parks it again at the same point forever while re-spending real provider tokens."""
    async with workspace_tx() as connection:
        workspace_id, _, _, _ = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1)
        await debit(connection, workspace_id, DOLLAR)
        free = await BalanceGate(workspace_id).sustains(connection, pending_micro_usd=0)
        costly = await BalanceGate(workspace_id).sustains(connection, pending_micro_usd=1)
    assert free.outcome == ALLOW
    assert costly.outcome != ALLOW


async def test_a_workspace_serving_itself_is_never_locked_out(db: None) -> None:
    """A turn the workspace's own key will serve debits nothing, so the balance can never rise to
    clear the entry line. Refused there, the workspace is locked out for good — including from the
    documented way to keep working without buying credit."""
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1, reserve_dollars=10)
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot="anthropic_api_key",
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        locked = await BalanceGate(workspace_id).admits(connection)
        served = await BalanceGate(workspace_id).admits(
            connection, agent_id, lambda _model: "anthropic_api_key"
        )
    assert locked.outcome != ALLOW
    assert served.outcome == ALLOW

    dbos = StubDbos()
    turn_id = await Admission(
        dbos=dbos,
        durable_surfaces=frozenset(),
        key_slot_for=lambda _model: "anthropic_api_key",
    ).invoke(
        workspace_id,
        conversation_id,
        agent_id,
        "hi",
    )
    assert dbos.enqueued == [str(turn_id)]


async def test_a_byok_turn_still_stops_once_its_own_spend_takes_the_balance_under(
    db: None,
) -> None:
    """A turn whose model rounds run on the workspace's own key still generates media and still
    makes in-sandbox calls on the platform key, and those charge. Exempting the whole turn would let
    it run past zero with nothing to stop it; the question is whether the spend in front of it
    actually charges the balance."""
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1)
        turn_id = await _insert_running(connection, workspace_id, conversation_id, agent_id, seq=2)
        gate = BalanceGate(workspace_id)
        free_and_solvent = await gate.sustains(connection, 0, turn_id)
        await record_image_usage(connection, workspace_id, turn_id, "gpt-image-2", 1, 2 * DOLLAR)
        free_but_overdrawn = await gate.sustains(connection, 0, turn_id)
    assert free_and_solvent.outcome == ALLOW
    assert free_but_overdrawn.outcome != ALLOW


async def test_a_parked_turn_that_already_charged_is_not_readmitted_on_its_own_key(
    db: None,
) -> None:
    """The own-key exemption starts free work; it must not resume a turn that already charged.
    Readmitted, such a turn parks again on its next real charge and cycles every sweep without ever
    answering — media and in-sandbox calls charge whatever key serves the model rounds."""
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=3, reserve_dollars=4)
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot="anthropic_api_key",
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        turn_id = await _insert_running(connection, workspace_id, conversation_id, agent_id, seq=2)
        await record_image_usage(connection, workspace_id, turn_id, "gpt-image-2", 1, DOLLAR)
        fresh = await BalanceGate(workspace_id).admits(connection, agent_id, _own_key)
        charged = await BalanceGate(workspace_id).admits(connection, agent_id, _own_key, turn_id)
    assert fresh.outcome == ALLOW
    assert charged.outcome != ALLOW


async def test_the_own_key_exemption_stops_at_zero(db: None) -> None:
    """A workspace serving its own key still pays the platform for media and in-sandbox calls, and
    those debit. An exemption that ignored the balance would let an overdrawn workspace spend the
    platform's money one turn at a time without any bound, since its model rounds never move the
    balance back toward the line that would refuse it."""
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, _ = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1, reserve_dollars=2)
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot="anthropic_api_key",
                ciphertext=b"sealed",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        solvent = await BalanceGate(workspace_id).admits(connection, agent_id, _own_key)
        await debit(connection, workspace_id, 5 * DOLLAR)
        overdrawn = await BalanceGate(workspace_id).admits(connection, agent_id, _own_key)
    assert solvent.outcome == ALLOW
    assert overdrawn.outcome != ALLOW


async def test_the_fold_read_does_not_promise_what_admission_refuses(db: None) -> None:
    """A surface asks which turn a message would fold into before running its own gate. That read
    carries both gates admission carries — the caps and the balance — because promising a fold the
    balance then refuses strands the message: the surface reports it joined a live turn while
    admission turned it away."""
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1, reserve_dollars=5)
        turn_id = await _insert_running(connection, workspace_id, conversation_id, agent_id, seq=1)
        spent = await BalanceGate(workspace_id).admits(connection, agent_id)

        await credit(connection, workspace_id, 50 * DOLLAR, 0, "top-up")
        funded = await BalanceGate(workspace_id).admits(connection, agent_id)
    assert turn_id is not None
    assert spent.outcome != ALLOW
    assert funded.outcome == ALLOW


async def test_a_card_that_has_paid_keeps_the_workspace_working_past_its_line(db: None) -> None:
    """The routine block this exists to stop. A refill cannot land the instant the balance crosses
    its threshold — the job ticks, then the card answers — and one turn can outspend that gap. A
    workspace whose card has already settled a charge is solvent and about to be topped up, so it
    keeps working across the gap instead of being refused for being briefly short."""
    async with workspace_tx() as connection:
        workspace_id, _, _, _ = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1, reserve_dollars=10)
        await debit(connection, workspace_id, 20 * DOLLAR)
        await mark_topup_verified(connection, workspace_id)
        entering = await BalanceGate(workspace_id).admits(connection)
    assert entering.outcome == ALLOW


async def test_the_grace_is_earned_by_paying_not_granted_on_arrival(db: None) -> None:
    """Identical balance, no settled charge behind it. Granting the overdraft to a workspace that
    has never paid would hand every fresh signup a free spend limit, so a card on file is worth
    nothing here and only a charge that cleared counts."""
    async with workspace_tx() as connection:
        workspace_id, _, _, _ = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1, reserve_dollars=10)
        await debit(connection, workspace_id, 20 * DOLLAR)
        entering = await BalanceGate(workspace_id).admits(connection)
    assert entering.outcome != ALLOW
    assert entering.message == balance_refusal_message(None)


async def test_entry_stays_the_stricter_line_once_the_grace_applies(db: None) -> None:
    """The ordering the two thresholds depend on. Moving only entry down by the grace would admit a
    turn beneath the line that stops it, so it would be parked on its first round — worse than the
    refusal it replaced. Both lines move together, and entry stays the stricter of the two."""
    async with workspace_tx() as connection:
        workspace_id, _, _, _ = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1, reserve_dollars=10)
        await mark_topup_verified(connection, workspace_id)
        await debit(connection, workspace_id, DOLLAR + TOPUP_GRACE_MICRO_USD - 5 * DOLLAR)
        gate = BalanceGate(workspace_id)
        entering = await gate.admits(connection)
        continuing = await gate.sustains(connection, pending_micro_usd=DOLLAR)
    assert entering.outcome != ALLOW
    assert continuing.outcome == ALLOW


def _billing_intent(workspace_id: UUID, operation: str = "autopay") -> ToolIntent:
    return ToolIntent(
        tool="object_action",
        input={
            "kind": "workspace",
            "action": "manage_billing",
            "name": str(workspace_id),
            "input": {"operation": operation},
        },
    )


async def test_a_spent_balance_still_admits_the_act_that_ends_the_refusal(db: None) -> None:
    """The refusal exists to stop a workspace spending money it does not have, and arranging a
    refill is how it gets money. Gating that act refuses the only thing that lifts the gate, which
    is the deadlock this system built three times before it was named. Safe to admit because a
    prepared intent runs no model round: the turn dispatches the verb and ends, so the workspace
    cannot spend against it, and the tool's own admin gate still decides who may."""
    async with workspace_tx() as connection:
        workspace_id, member_id, _agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1)
        await debit(connection, workspace_id, 5 * DOLLAR)
    dbos = StubDbos()
    admitted = await Admission(dbos=dbos, durable_surfaces=frozenset()).admit_member(
        workspace_id,
        conversation_id,
        _billing_intent(workspace_id).model_dump_json(),
        member_id,
        intent=_billing_intent(workspace_id),
    )
    assert await _status(admitted.turn_id) != "cancelled"


async def test_the_exemption_is_the_billing_verb_and_nothing_else(db: None) -> None:
    """A prepared intent is cheap, but cheap is not a reason to admit every panel act on an
    overdrawn workspace — an agent edit or a member add would then ride past the balance the same
    way. Only the verb that ends the refusal is exempt."""
    async with workspace_tx() as connection:
        workspace_id, member_id, _agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1)
        await debit(connection, workspace_id, 5 * DOLLAR)
    other = ToolIntent(
        tool="object_action",
        input={"kind": "memory", "action": "record_first_run", "input": {"body": "note"}},
    )
    dbos = StubDbos()
    admitted = await Admission(dbos=dbos, durable_surfaces=frozenset()).admit_member(
        workspace_id, conversation_id, other.model_dump_json(), member_id, intent=other
    )
    assert await _status(admitted.turn_id) == "cancelled"
    assert (await _terminal(admitted.turn_id)).text == balance_refusal_message(None)


async def test_the_park_notice_names_the_balance_rather_than_a_cap(db: None) -> None:
    """A park's reason reaches the live stream and is never stored, so the poll that ends a
    reconnecting surface's stream has to ask what holds the turn. It held one fixed sentence before,
    which named a spend cap for every park — so a workspace with no cap ever set read a cause that
    did not exist, and the fix it named was one it could not perform."""
    async with workspace_tx() as connection:
        workspace_id, _member_id, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1)
        await debit(connection, workspace_id, 5 * DOLLAR)
        turn_id = await _insert_parked(connection, workspace_id, conversation_id, agent_id, seq=1)
    with ws(workspace_id):
        assert await turn_status_frame(turn_id) == Parked(message=balance_park_message(None))


async def test_the_park_notice_names_the_cap_that_holds_the_turn(db: None) -> None:
    """A cap park states the scope and the figure the cap holds, so the member reads the limit they
    can act on rather than the word 'cap'."""
    async with workspace_tx() as connection:
        workspace_id, _member_id, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=100)
        await _set_cap(connection, workspace_id, "workspace", None, 3600, DOLLAR, "park")
        running = await _insert_running(connection, workspace_id, conversation_id, agent_id, seq=1)
        await record_turn_usage(
            connection,
            workspace_id,
            running,
            "claude-opus-4-8",
            Usage(input_tokens=1_000_000, output_tokens=1_000_000),
            "attempt",
        )
        turn_id = await _insert_parked(connection, workspace_id, conversation_id, agent_id, seq=2)
    with ws(workspace_id):
        frame = await turn_status_frame(turn_id)
    assert isinstance(frame, Parked)
    assert "workspace spend cap of $1.00" in frame.message


async def test_the_park_notice_carries_the_gates_own_key_exemption(db: None) -> None:
    """A workspace serving its turns on its own key is admitted at its reserve, so the balance is
    not what holds a turn there. Naming the balance would send the member to buy credit for a park
    the cap holds, and the sweep — which re-decides with the same exemption — would leave the turn
    exactly where it is."""
    async with workspace_tx() as connection:
        workspace_id, _member_id, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=100, reserve_dollars=1000)
        await _store_own_key(connection, workspace_id)
        await _set_cap(connection, workspace_id, "workspace", None, 3600, DOLLAR, "park")
        running = await _insert_running(connection, workspace_id, conversation_id, agent_id, seq=1)
        await record_turn_usage(
            connection,
            workspace_id,
            running,
            "claude-opus-4-8",
            Usage(input_tokens=1_000_000, output_tokens=1_000_000),
            "attempt",
        )
        turn_id = await _insert_parked(connection, workspace_id, conversation_id, agent_id, seq=2)
    with ws(workspace_id):
        without_the_key = await turn_status_frame(turn_id)
        with_the_key = await turn_status_frame(turn_id, None, _own_key)
    assert without_the_key == Parked(message=balance_park_message(None))
    assert isinstance(with_the_key, Parked)
    assert "workspace spend cap of $1.00" in with_the_key.message


async def test_a_park_no_gate_still_refuses_reads_as_a_pause(db: None) -> None:
    """Every gate clear means the sweep is about to re-admit it, so the notice states that and
    claims no cause."""
    async with workspace_tx() as connection:
        workspace_id, _member_id, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=100)
        turn_id = await _insert_parked(connection, workspace_id, conversation_id, agent_id, seq=1)
    with ws(workspace_id):
        assert await turn_status_frame(turn_id) == Parked(message=PARK_NOTICE)


async def _funded(
    connection: AsyncConnection, workspace_id: UUID, own_key_slots: tuple[str, ...] = ()
) -> bool:
    row = (
        await connection.execute(
            sa.select(tables.workspace.c.id).where(
                tables.workspace.c.id == workspace_id,
                funded(tables.workspace.c.id, own_key_slots),
            )
        )
    ).one_or_none()
    return row is not None


async def _store_own_key(connection: AsyncConnection, workspace_id: UUID) -> None:
    await connection.execute(
        sa.insert(tables.credential).values(
            workspace_id=workspace_id,
            slot="anthropic_api_key",
            ciphertext=b"sealed",
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )


async def test_the_hold_is_the_gates_entry_line(db: None) -> None:
    """`funded` is the SQL form of the decision `admits` makes before a turn starts, read by the
    jobs that would otherwise open a workspace the gate is about to refuse. The two agree on every
    side of the line — no row, above the reserve, at it, under it but inside a settled card's
    grace, past the grace — and on the own-key exemption: a workspace under the line that holds its
    own key for the model is admitted while its balance is above zero, held once it is not, and held
    when the key it holds is for some other model."""
    async with workspace_tx() as connection:
        no_row, *_ = await _seed(connection)
        above, *_ = await _seed(connection)
        await _fund(connection, above, dollars=10, reserve_dollars=2)
        at_the_line, *_ = await _seed(connection)
        await _fund(connection, at_the_line, dollars=2, reserve_dollars=2)
        in_the_grace, *_ = await _seed(connection)
        await _fund(connection, in_the_grace, dollars=1, reserve_dollars=2)
        await mark_topup_verified(connection, in_the_grace)
        past_the_grace, *_ = await _seed(connection)
        await _fund(connection, past_the_grace, dollars=1, reserve_dollars=2)
        await mark_topup_verified(connection, past_the_grace)
        await debit(connection, past_the_grace, 100 * DOLLAR)
        own_key, *_ = await _seed(connection)
        await _fund(connection, own_key, dollars=1, reserve_dollars=10)
        await _store_own_key(connection, own_key)
        own_key_at_zero, *_ = await _seed(connection)
        await _fund(connection, own_key_at_zero, dollars=1, reserve_dollars=10)
        await _store_own_key(connection, own_key_at_zero)
        await debit(connection, own_key_at_zero, DOLLAR)
        expected = [
            (no_row, (), True),
            (above, (), True),
            (at_the_line, (), False),
            (in_the_grace, (), True),
            (past_the_grace, (), False),
            (own_key, ("anthropic_api_key",), True),
            (own_key, ("openai_api_key",), False),
            (own_key, (), False),
            (own_key_at_zero, ("anthropic_api_key",), False),
        ]
        for workspace_id, slots, funded_now in expected:
            gate = BalanceGate(workspace_id)
            slot = slots[0] if slots else None
            decision = await gate.admits(connection, key_slot_for=lambda _m, s=slot: s, model="m")
            assert await _funded(connection, workspace_id, slots) is funded_now, (
                workspace_id,
                slots,
            )
            assert (decision.outcome == ALLOW) is funded_now, (workspace_id, slots)
