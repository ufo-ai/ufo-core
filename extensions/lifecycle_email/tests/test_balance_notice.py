"""End-to-end proof of the balance notice: a workspace that has spent its credit reaches each
seated admin once, the row records what SES reported, and a second pass sends nothing until the
workspace has been topped up and has spent that too.

The gateway stands in as an httpx transport — the route it serves is proved by
`servers/control/tests/email_send_it.rs` and the body by the contract both ends read. What is
asserted here is the extension's own rows and the messages it composes."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from conftest import BASE_URL, HOME_SURFACE, Gateway, context
from openfeature.provider.in_memory_provider import InMemoryFlag, InMemoryProvider
from ufo_ext_flags_open import build
from ufo_ext_lifecycle_email.balance_notice import (
    ACTION_LABEL,
    BALANCE_EXHAUSTED,
    SUBJECT,
    BalanceNotice,
    notice_workspaces,
)
from ufo_ext_lifecycle_email.manifest import SENDING_FLAG, _notice
from ufo_ext_lifecycle_email.sends import FAILED, FEEDBACK_WINDOW, SENT, lifecycle_send

from ufo.db import workspace_tx
from ufo.flags import SERVED_FALSE, init_flags
from ufo.runtime.billing.balance import (
    BILLING_SCREEN_FRAGMENT,
    EXHAUSTION_WINDOW,
    credit,
)
from ufo.runtime.workspace import ws
from ufo.schema import tables

pytestmark = pytest.mark.usefixtures("db")

RESERVE = 5_000_000
OWN_KEY_SLOT = "anthropic_api_key"


async def _seed(*, admins: int = 1, members: int = 0) -> tuple[UUID, list[UUID]]:
    workspace_id = uuid4()
    people: list[UUID] = []
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        for index in range(admins + members):
            member_id = uuid4()
            people.append(member_id)
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email=f"person{index}@acme.com",
                    is_admin=index < admins,
                    seated_at=sa.func.now(),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return workspace_id, people


async def _fund(workspace_id: UUID, granted: int, reference: str) -> None:
    async with workspace_tx() as connection:
        await credit(connection, workspace_id, granted, granted, reference)
        await connection.execute(
            sa.update(tables.workspace_balance)
            .where(tables.workspace_balance.c.workspace_id == workspace_id)
            .values(reserve_micro_usd=RESERVE)
        )


async def _spend(workspace_id: UUID) -> None:
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.workspace_balance)
            .where(tables.workspace_balance.c.workspace_id == workspace_id)
            .values(balance_micro_usd=0)
        )


async def _rows(workspace_id: UUID) -> list[sa.Row]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(lifecycle_send).where(lifecycle_send.c.workspace_id == workspace_id)
                )
            ).all()
        )


async def test_each_admin_is_told_once_and_told_again_only_after_a_top_up() -> None:
    workspace_id, people = await _seed(admins=2, members=1)
    await _fund(workspace_id, 10_000_000, "first")
    await _spend(workspace_id)
    gateway = Gateway()
    ctx = context(gateway)

    with ws(workspace_id):
        await BalanceNotice(ctx=ctx).run()
        await BalanceNotice(ctx=ctx).run()

    assert sorted(message["email"] for message in gateway.sent) == [
        "person0@acme.com",
        "person1@acme.com",
    ], "each seated admin once, and the ordinary member never"
    assert {message["kind"] for message in gateway.sent} == {BALANCE_EXHAUSTED}
    assert {message["subject"] for message in gateway.sent} == {
        SUBJECT.format(workspace="acme.com")
    }
    sent = gateway.sent[0]
    assert "acme.com has no credit left" in sent["body"]
    assert sent["action_label"] == ACTION_LABEL
    assert sent["action_url"] == f"{BASE_URL}/surface/{HOME_SURFACE}{BILLING_SCREEN_FRAGMENT}"

    rows = await _rows(workspace_id)
    assert {row.state for row in rows} == {SENT}
    assert {row.member_id for row in rows} == set(people[:2])

    await _fund(workspace_id, 10_000_000, "second")
    with ws(workspace_id):
        await BalanceNotice(ctx=ctx).run()
    assert len(gateway.sent) == 2, "a funded workspace is told nothing"

    await _spend(workspace_id)
    with ws(workspace_id):
        await BalanceNotice(ctx=ctx).run()
    assert len(gateway.sent) == 4, "the next exhaustion is a new reason"
    assert len({row.reason for row in await _rows(workspace_id)}) == 2


async def test_the_delivery_ses_reports_lands_on_the_row() -> None:
    workspace_id, _ = await _seed()
    await _fund(workspace_id, 10_000_000, "first")
    await _spend(workspace_id)
    gateway = Gateway()
    ctx = context(gateway)

    with ws(workspace_id):
        await BalanceNotice(ctx=ctx).run()
    (row,) = await _rows(workspace_id)
    assert row.delivery is None, "feedback arrives on its own clock"
    assert row.ses_message_id in gateway.reported

    gateway.reported[row.ses_message_id] = "delivered"
    with ws(workspace_id):
        await BalanceNotice(ctx=ctx).run()
    (row,) = await _rows(workspace_id)
    assert row.delivery == "delivered"
    assert len(gateway.sent) == 1, "recording a delivery sends nothing"


async def test_a_refusal_fails_the_row_and_is_never_retried() -> None:
    workspace_id, _ = await _seed()
    await _fund(workspace_id, 10_000_000, "first")
    await _spend(workspace_id)
    gateway = Gateway()
    gateway.refuse = 409
    ctx = context(gateway)

    with ws(workspace_id):
        await BalanceNotice(ctx=ctx).run()
    (row,) = await _rows(workspace_id)
    assert row.state == FAILED
    assert "bounced" in row.last_error

    gateway.refuse = None
    with ws(workspace_id):
        await BalanceNotice(ctx=ctx).run()
    assert gateway.sent == [], "a decided refusal is never sent again"
    (row,) = await _rows(workspace_id)
    assert row.state == FAILED


async def test_a_deploy_with_no_billing_screen_sends_nothing() -> None:
    workspace_id, _ = await _seed()
    await _fund(workspace_id, 10_000_000, "first")
    await _spend(workspace_id)
    gateway = Gateway()
    ctx = context(gateway, portal=False)

    with ws(workspace_id):
        await BalanceNotice(ctx=ctx).run()

    assert gateway.sent == [], "a notice nobody can act on does not ship"
    assert await _rows(workspace_id) == []


async def test_the_candidates_name_the_spent_and_the_unreported() -> None:
    spent, _ = await _seed()
    await _fund(spent, 10_000_000, "first")
    await _spend(spent)

    funded, people = await _seed()
    await _fund(funded, 10_000_000, "first")

    candidates = notice_workspaces()
    named = await candidates()
    assert spent in named
    assert funded not in named, "a workspace with credit is no candidate"

    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(lifecycle_send).values(
                id=uuid4(),
                workspace_id=funded,
                member_id=people[0],
                kind=BALANCE_EXHAUSTED,
                reason="earlier",
                state=SENT,
                ses_message_id="m-1",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    assert funded in await candidates(), "a sent message SES has not reported on is work"

    stale = datetime.now(UTC) - FEEDBACK_WINDOW - timedelta(hours=1)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(lifecycle_send)
            .where(lifecycle_send.c.workspace_id == funded)
            .values(created_at=stale)
        )
    assert funded not in await candidates(), "past the window nothing more is coming"


async def test_a_workspace_on_its_own_key_is_told_nothing() -> None:
    """The gate leaves a workspace paying its own way for a model running, so its agent still
    answers and the notice would be false. The balance line alone cannot tell."""
    workspace_id, _ = await _seed()
    await _fund(workspace_id, 10_000_000, "first")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.workspace_balance)
            .where(tables.workspace_balance.c.workspace_id == workspace_id)
            .values(balance_micro_usd=1)
        )
        await connection.execute(
            sa.insert(tables.credential).values(
                workspace_id=workspace_id,
                slot=OWN_KEY_SLOT,
                ciphertext=b"k",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    gateway = Gateway()

    with ws(workspace_id):
        await BalanceNotice(ctx=context(gateway, own_key_slots=(OWN_KEY_SLOT,))).run()
    assert gateway.sent == [], "the gate admits this workspace, so nothing has stopped"
    assert await _rows(workspace_id) == []

    with ws(workspace_id):
        await BalanceNotice(ctx=context(gateway)).run()
    assert len(gateway.sent) == 1, "a deploy keying every model itself tells the same workspace"


async def test_a_told_workspace_leaves_the_candidate_set() -> None:
    """A workspace that never tops up stays below the line forever. Nothing but the ledger takes it
    out of the per-minute set, so without the join the fan-out grows with every workspace that
    spends out."""
    workspace_id, _ = await _seed()
    await _fund(workspace_id, 10_000_000, "first")
    await _spend(workspace_id)
    gateway = Gateway()
    ctx = context(gateway)
    candidates = notice_workspaces()

    assert workspace_id in await candidates()
    with ws(workspace_id):
        await BalanceNotice(ctx=ctx).run()
    (row,) = await _rows(workspace_id)
    gateway.reported[row.ses_message_id] = "delivered"
    with ws(workspace_id):
        await BalanceNotice(ctx=ctx).run()

    assert workspace_id not in await candidates(), "this exhaustion is decided"

    await _fund(workspace_id, 10_000_000, "second")
    await _spend(workspace_id)
    assert workspace_id in await candidates(), "a further grant spent is a further exhaustion"


async def test_a_pass_that_cannot_decide_leaves_the_row_attempted() -> None:
    """An unanswered send may have reached SES, so nothing repeats it: a duplicate lifecycle email
    is worse than a missing one."""
    workspace_id, _ = await _seed()
    await _fund(workspace_id, 10_000_000, "first")
    await _spend(workspace_id)

    gateway = Gateway()
    gateway.unreachable = True
    ctx = context(gateway)

    with ws(workspace_id):
        await BalanceNotice(ctx=ctx).run()
    (row,) = await _rows(workspace_id)
    assert row.state == FAILED, "the attempt is settled rather than left for another pass"


async def test_the_flag_decides_whether_this_deploy_sends_at_all() -> None:
    """Off, the job does nothing and writes nothing — so turning it on starts from the fleet as it
    stands, rather than sending every notice the flag held back."""
    workspace_id, _ = await _seed()
    await _fund(workspace_id, 10_000_000, "first")
    await _spend(workspace_id)
    gateway = Gateway()
    ctx = context(gateway)

    init_flags(
        InMemoryProvider(
            {SENDING_FLAG: InMemoryFlag(default_variant="off", variants={"off": SERVED_FALSE})}
        )
    )
    try:
        with ws(workspace_id):
            await _notice(ctx)
        assert gateway.sent == []
        assert await _rows(workspace_id) == []

        init_flags(build(0.0, {}))
        with ws(workspace_id):
            await _notice(ctx)
    finally:
        init_flags(InMemoryProvider({}))

    assert [message["kind"] for message in gateway.sent] == [BALANCE_EXHAUSTED]


async def test_a_workspace_that_crossed_the_line_long_ago_is_no_longer_work() -> None:
    """The ledger takes a workspace out of the set once the pass has acted. A pass held back by the
    flag acts on nothing and writes nothing, so with sending off in a deploy the set would only
    grow. The balance row's own stamp is the bound that holds either way: spending moves it, and
    the gate stops the spending, so it freezes where the workspace crossed the line."""
    workspace_id, _ = await _seed()
    await _fund(workspace_id, 10_000_000, "first")
    await _spend(workspace_id)
    candidates = notice_workspaces()
    assert workspace_id in await candidates()

    crossed = datetime.now(UTC) - EXHAUSTION_WINDOW - timedelta(hours=1)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.workspace_balance)
            .where(tables.workspace_balance.c.workspace_id == workspace_id)
            .values(updated_at=crossed)
        )
    assert workspace_id not in await candidates(), "nobody is waiting on a reaction this late"
