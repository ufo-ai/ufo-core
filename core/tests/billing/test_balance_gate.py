from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection
from test_spend_caps import (
    StubDbos,
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
    balance_refusal_message,
    credit,
    debit,
    mark_topup_verified,
    set_auto_topup,
    set_reserve,
)
from ufo.runtime.hub import InProcessHub, Parked
from ufo.runtime.surfaces.admission import Admission
from ufo.runtime.surfaces.hub_tail import PARK_NOTICE, HubTailer, turn_status_frame
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import TerminalFrame, ToolIntent, Usage

DOLLAR = 1_000_000

BILLING_SCREEN = "https://ufo.example.com/surface/web#/workspace/billing"
LINKED_REFUSAL = (
    "This workspace is out of credit. An admin can add credit at "
    "https://ufo.example.com/surface/web#/workspace/billing"
)


def _own_key(_model: str) -> str:
    return "anthropic_api_key"


FULL_TOKENS = Usage(input_tokens=1000, output_tokens=2000)


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


async def test_a_refused_admission_sends_the_member_to_the_billing_screen(db: None) -> None:
    """The refusal names where the fix happens, not only that a fix exists. The screen is a deploy
    fact the boot threads down, so the sentence a member reads is the one their own deploy can act
    on."""
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=10, reserve_dollars=10)
    turn_id = await Admission(
        dbos=StubDbos(), durable_surfaces=frozenset(), billing_url=BILLING_SCREEN
    ).invoke(workspace_id, conversation_id, agent_id, "hi")
    assert (await _terminal(turn_id)).text == LINKED_REFUSAL


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


async def test_a_negative_balance_rejects(db: None) -> None:
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1)
        await debit(connection, workspace_id, 5 * DOLLAR)
    dbos = StubDbos()
    turn_id = await Admission(dbos=dbos, durable_surfaces=frozenset()).invoke(
        workspace_id, conversation_id, agent_id, "hi"
    )
    assert dbos.enqueued == []
    assert await _status(turn_id) == "cancelled"


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


async def test_the_two_thresholds_do_not_meet(db: None) -> None:
    """The thrash regression, and the whole reason the gate has two methods.

    A balance sitting between zero and the reserve refuses a new turn but sustains a running one.
    Were entry and continuation one line, a resume would admit a turn that the first round's spend
    pushed straight back under — a round burnt per cycle, answering nothing."""
    async with workspace_tx() as connection:
        workspace_id, _, _, _ = await _seed(connection)
        await _fund(connection, workspace_id, dollars=5, reserve_dollars=10)
        gate = BalanceGate(workspace_id)
        entering = await gate.admits(connection)
        continuing = await gate.sustains(connection, pending_micro_usd=DOLLAR)
    assert entering.outcome != ALLOW
    assert continuing.outcome == ALLOW


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


async def test_a_held_round_sends_the_member_to_the_billing_screen(db: None) -> None:
    """The hold a member reads mid-turn carries the same link as the refusal that turns one away —
    one sentence, one place it is written."""
    async with workspace_tx() as connection:
        workspace_id, _, _, _ = await _seed(connection)
        await _fund(connection, workspace_id, dollars=5, reserve_dollars=10)
        over = await BalanceGate(workspace_id, BILLING_SCREEN).sustains(
            connection, pending_micro_usd=5 * DOLLAR
        )
    assert over.outcome != ALLOW
    assert over.message == LINKED_REFUSAL


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
    ).invoke(workspace_id, conversation_id, agent_id, "hi")
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


async def test_a_turn_that_has_debited_nothing_is_never_held(db: None) -> None:
    """The other half: a turn that has taken nothing off the balance cannot resume its way out of a
    park, so holding it would loop forever."""
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1)
        await debit(connection, workspace_id, 5 * DOLLAR)
        turn_id = await _insert_running(connection, workspace_id, conversation_id, agent_id, seq=2)
        decision = await BalanceGate(workspace_id).sustains(connection, 0, turn_id)
    assert decision.outcome == ALLOW


async def test_an_own_key_turn_is_not_held_by_the_price_it_never_paid(db: None) -> None:
    """The stall this replaced: an own-key row is priced and takes nothing, so reading the price
    would park the turn, the dispatcher would let it back in, and it would stop at the same point
    every few minutes forever — telling the member it is out of credit and never answering."""
    async with workspace_tx() as connection:
        workspace_id, _, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1)
        await debit(connection, workspace_id, 5 * DOLLAR)
        turn_id = await _insert_running(connection, workspace_id, conversation_id, agent_id, seq=2)
        await record_turn_usage(
            connection, workspace_id, turn_id, "claude-opus-4-8", FULL_TOKENS, byok=True
        )
        decision = await BalanceGate(workspace_id).sustains(connection, 0, turn_id)
    assert decision.outcome == ALLOW


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


async def test_the_grace_is_a_flat_figure_a_workspace_cannot_widen(db: None) -> None:
    """The overdraft is bounded by a constant, so what a workspace can take while unpaid does not
    move with any figure it chooses. Past the constant it is refused however much it arranged to
    refill."""
    async with workspace_tx() as connection:
        workspace_id, _, _, _ = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1)
        await set_auto_topup(connection, workspace_id, 100_000 * DOLLAR, 50_000 * DOLLAR)
        await mark_topup_verified(connection, workspace_id)
        await debit(connection, workspace_id, DOLLAR + TOPUP_GRACE_MICRO_USD)
        entering = await BalanceGate(workspace_id).admits(connection)
    assert entering.outcome != ALLOW


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


async def test_a_spent_balance_admits_the_step_before_the_refill(db: None) -> None:
    """A refill is refused until a card is on file, so reaching the provider is the act that ends
    the refusal for a workspace that has never paid — and it is the step the billing screen carries
    for exactly that workspace. The exemption keys on the action rather than its operation, which is
    what makes this true without a second gate to keep in step; this asserts it rather than leaving
    it to be read off the gate."""
    async with workspace_tx() as connection:
        workspace_id, member_id, _agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1)
        await debit(connection, workspace_id, 5 * DOLLAR)
    portal = _billing_intent(workspace_id, "portal")
    admitted = await Admission(dbos=StubDbos(), durable_surfaces=frozenset()).admit_member(
        workspace_id,
        conversation_id,
        portal.model_dump_json(),
        member_id,
        intent=portal,
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
        assert await turn_status_frame(turn_id) == Parked(message=balance_refusal_message(None))


async def test_the_park_notice_sends_the_member_to_the_billing_screen(db: None) -> None:
    """A reconnecting surface reads the park's reason off the poll, not off the live stream that
    published it, so the link has to reach that poll too — the tailer holds it and hands it down."""
    async with workspace_tx() as connection:
        workspace_id, _member_id, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=1)
        await debit(connection, workspace_id, 5 * DOLLAR)
        turn_id = await _insert_parked(connection, workspace_id, conversation_id, agent_id, seq=1)
    tailer = HubTailer(hub=InProcessHub(), billing_url=BILLING_SCREEN)
    with ws(workspace_id):
        async with tailer.tail(turn_id) as stream:
            frames = [frame async for _cursor, frame in stream]
    assert frames == [Parked(message=LINKED_REFUSAL)]


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


async def test_a_park_no_gate_still_refuses_reads_as_a_pause(db: None) -> None:
    """Every gate clear means the sweep is about to re-admit it, so the notice states that and
    claims no cause."""
    async with workspace_tx() as connection:
        workspace_id, _member_id, agent_id, conversation_id = await _seed(connection)
        await _fund(connection, workspace_id, dollars=100)
        turn_id = await _insert_parked(connection, workspace_id, conversation_id, agent_id, seq=1)
    with ws(workspace_id):
        assert await turn_status_frame(turn_id) == Parked(message=PARK_NOTICE)
