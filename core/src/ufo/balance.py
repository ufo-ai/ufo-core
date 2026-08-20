"""The workspace's prepaid balance, in micro-USD: the purchases that credit it and the reads over
them.

The balance is a mutable row rather than a sum over the purchases, because a lifetime balance has no
window to bound its sum and a gate reads it before every model round. The purchases stay the record
the balance is audited against: `sum(granted_micro_usd)` less the ledger's whole
`sum(debited_micro_usd)` equals `balance_micro_usd`, exactly, because a debit and the row recording
it share one transaction and the row records what the balance actually moved."""

import time
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.schema import tables

BALANCE_PRESENCE_TTL_SECONDS = 5.0
BALANCE_PRESENCE_CACHE_MAX = 4096
_no_balance: dict[UUID, float] = {}
# How far a workspace whose card has already paid may run past the line before a gate stops it.
# The refill job cannot be instant: it ticks, then Stripe answers, and one turn can outspend that
# gap, so a balance tested against the bare line refuses turns for a workspace that is solvent and
# about to be topped up. This absorbs the gap.
#
# It is a flat figure, not a share of the refill the member chose, because a member-scaled overdraft
# is a credit line whose limit the borrower sets: arranging a huge refill would earn a huge one. And
# it is earned rather than granted, so the exposure on a workspace that never pays is nothing at
# all — a card that has settled a charge has proved itself in the only way that counts.
TOPUP_GRACE_MICRO_USD = 100_000_000


def balance_absent(workspace_id: UUID) -> bool:
    """Connectionless fast-path: True only when a recent read found this workspace has no balance
    row, within a short TTL. A self-host deploy that never credits a balance then pays nothing per
    round. Correctness never rests on it — a credit clears the mark, and a stale one only costs the
    read it was meant to skip."""
    expiry = _no_balance.get(workspace_id)
    return expiry is not None and expiry > time.monotonic()


def _note_absent_balance(workspace_id: UUID) -> None:
    """Remember for the TTL that this workspace has no balance row. Evict expired entries once the
    map is full, so a long-lived serve seeing many workspaces never grows it without bound."""
    now = time.monotonic()
    if len(_no_balance) >= BALANCE_PRESENCE_CACHE_MAX:
        for expired in [key for key, expiry in _no_balance.items() if expiry <= now]:
            del _no_balance[expired]
    _no_balance[workspace_id] = now + BALANCE_PRESENCE_TTL_SECONDS


def _forget_absent_balance(workspace_id: UUID) -> None:
    """Drop the absent mark once a balance row is seen, so the fast path never suppresses a gate
    for a workspace that has since been credited."""
    _no_balance.pop(workspace_id, None)


@dataclass(frozen=True, slots=True)
class Balance:
    """What is left, what must stay, and what was ever put in."""

    balance_micro_usd: int
    reserve_micro_usd: int
    granted_micro_usd: int
    charged_micro_usd: int
    last_purchase_at: datetime | None


BALANCE_REFUSAL_MESSAGE = "This workspace is out of credit. An admin can set up automatic refills."


@dataclass(frozen=True, slots=True)
class AutoTopup:
    """What a workspace refills itself with, and the balance that triggers it. Both are set
    together or not at all, so there is no half-configured state to reason about at charge time."""

    amount_micro_usd: int
    threshold_micro_usd: int


async def read_auto_topup(connection: AsyncConnection, workspace_id: UUID) -> AutoTopup | None:
    """This workspace's refill settings, or None where it has none or has not reached the line.

    Answering None for a balance still above its threshold is what keeps the decision here rather
    than in the extension that holds the card: core owns when a workspace is short, the extension
    owns how it pays."""
    row = (
        await connection.execute(
            sa.select(
                tables.workspace_balance.c.balance_micro_usd,
                tables.workspace_balance.c.auto_topup_micro_usd,
                tables.workspace_balance.c.auto_topup_threshold_micro_usd,
            ).where(tables.workspace_balance.c.workspace_id == workspace_id)
        )
    ).one_or_none()
    if row is None or row.auto_topup_micro_usd is None:
        return None
    if row.balance_micro_usd > row.auto_topup_threshold_micro_usd:
        return None
    return AutoTopup(
        amount_micro_usd=int(row.auto_topup_micro_usd),
        threshold_micro_usd=int(row.auto_topup_threshold_micro_usd),
    )


async def set_auto_topup(
    connection: AsyncConnection,
    workspace_id: UUID,
    amount_micro_usd: int | None,
    threshold_micro_usd: int | None,
) -> bool:
    """Turn refilling on with both figures, or off with neither, and answer whether a balance row
    took it. A workspace with no balance has nothing to refill, so it is set against a row that
    already exists rather than creating one."""
    if (amount_micro_usd is None) != (threshold_micro_usd is None):
        raise ValueError("auto top-up needs both an amount and a threshold, or neither")
    updated = await connection.execute(
        sa.update(tables.workspace_balance)
        .where(tables.workspace_balance.c.workspace_id == workspace_id)
        .values(
            auto_topup_micro_usd=amount_micro_usd,
            auto_topup_threshold_micro_usd=threshold_micro_usd,
            updated_at=sa.func.now(),
        )
    )
    return updated.rowcount == 1


async def mark_topup_verified(connection: AsyncConnection, workspace_id: UUID) -> None:
    """Record that a card has settled a charge for this workspace, which is what earns the grace.

    Stamped once and never moved, so the grace a workspace has earned does not depend on how
    recently it last paid: a card that worked is the evidence, and a later decline is already
    answered by the refill standing down rather than by withdrawing the overdraft under a turn that
    is mid-flight."""
    await connection.execute(
        sa.update(tables.workspace_balance)
        .where(
            tables.workspace_balance.c.workspace_id == workspace_id,
            tables.workspace_balance.c.topup_verified_at.is_(None),
        )
        .values(topup_verified_at=sa.func.now(), updated_at=sa.func.now())
    )


@dataclass(frozen=True, slots=True)
class Headroom:
    """The figures a gate decides on."""

    balance_micro_usd: int
    reserve_micro_usd: int
    grace_micro_usd: int


async def configured_auto_topup(
    connection: AsyncConnection, workspace_id: UUID
) -> AutoTopup | None:
    """The refill this workspace has arranged, whether or not it has reached it.

    `read_auto_topup` answers the refill job's question — is this workspace short — so it is silent
    about a rule that exists and has not been reached yet. A caller reporting the terms back to the
    admin who set them cannot tell that silence from having no rule at all."""
    row = (
        await connection.execute(
            sa.select(
                tables.workspace_balance.c.auto_topup_micro_usd,
                tables.workspace_balance.c.auto_topup_threshold_micro_usd,
            ).where(tables.workspace_balance.c.workspace_id == workspace_id)
        )
    ).one_or_none()
    if row is None or row.auto_topup_micro_usd is None:
        return None
    return AutoTopup(
        amount_micro_usd=row.auto_topup_micro_usd,
        threshold_micro_usd=row.auto_topup_threshold_micro_usd,
    )


async def read_headroom(connection: AsyncConnection, workspace_id: UUID) -> Headroom | None:
    """What is left and what must stay, without the lifetime aggregate `read_balance` pays for —
    this read runs before every model round."""
    row = (
        await connection.execute(
            sa.select(
                tables.workspace_balance.c.balance_micro_usd,
                tables.workspace_balance.c.reserve_micro_usd,
                tables.workspace_balance.c.topup_verified_at,
            ).where(tables.workspace_balance.c.workspace_id == workspace_id)
        )
    ).one_or_none()
    if row is None:
        _note_absent_balance(workspace_id)
        return None
    return Headroom(
        balance_micro_usd=row.balance_micro_usd,
        reserve_micro_usd=row.reserve_micro_usd,
        grace_micro_usd=0 if row.topup_verified_at is None else TOPUP_GRACE_MICRO_USD,
    )


@dataclass(frozen=True, slots=True)
class Purchase:
    """One credit to the balance: what it added, what it cost, and when. A grant charges nothing, so
    the two figures are separate rather than one amount — an admin reading the list can tell money
    they paid from credit they were given."""

    granted_micro_usd: int
    charged_micro_usd: int
    created_at: datetime


async def recent_purchases(
    connection: AsyncConnection, workspace_id: UUID, limit: int
) -> tuple[Purchase, ...]:
    """The newest credits first, at most `limit` of them.

    `read_balance` sums the same rows into lifetime totals; this is the list behind that sum, for a
    screen stating where a balance came from. It is bounded at the query rather than by the caller
    slicing, because a workspace that has refilled every day for a year has a list no screen reads
    to the end of.

    The id breaks a tie on the timestamp: `now()` is the transaction's clock, so two credits written
    together carry the same instant, and ordering on time alone would let one read put them in one
    order and the next read another."""
    rows = await connection.execute(
        sa.select(
            tables.balance_purchase.c.granted_micro_usd,
            tables.balance_purchase.c.charged_micro_usd,
            tables.balance_purchase.c.created_at,
        )
        .where(tables.balance_purchase.c.workspace_id == workspace_id)
        .order_by(
            tables.balance_purchase.c.created_at.desc(),
            tables.balance_purchase.c.id.desc(),
        )
        .limit(limit)
    )
    return tuple(
        Purchase(
            granted_micro_usd=int(row.granted_micro_usd),
            charged_micro_usd=int(row.charged_micro_usd),
            created_at=row.created_at,
        )
        for row in rows
    )


async def read_balance(connection: AsyncConnection, workspace_id: UUID) -> Balance | None:
    """The balance and the lifetime totals behind it, or None where the workspace has never been
    credited. A gate reads the balance row alone; this is the whole picture an operator or an admin
    reads, so it pays for the aggregate."""
    row = (
        await connection.execute(
            sa.select(
                tables.workspace_balance.c.balance_micro_usd,
                tables.workspace_balance.c.reserve_micro_usd,
            ).where(tables.workspace_balance.c.workspace_id == workspace_id)
        )
    ).one_or_none()
    if row is None:
        _note_absent_balance(workspace_id)
        return None
    totals = (
        await connection.execute(
            sa.select(
                sa.func.coalesce(sa.func.sum(tables.balance_purchase.c.granted_micro_usd), 0),
                sa.func.coalesce(sa.func.sum(tables.balance_purchase.c.charged_micro_usd), 0),
                sa.func.max(tables.balance_purchase.c.created_at),
            ).where(tables.balance_purchase.c.workspace_id == workspace_id)
        )
    ).one()
    return Balance(
        balance_micro_usd=row.balance_micro_usd,
        reserve_micro_usd=row.reserve_micro_usd,
        granted_micro_usd=int(totals[0]),
        charged_micro_usd=int(totals[1]),
        last_purchase_at=totals[2],
    )


async def credit(
    connection: AsyncConnection,
    workspace_id: UUID,
    granted_micro_usd: int,
    charged_micro_usd: int,
    reference: str,
) -> bool:
    """Add to the balance once per reference, and answer whether this call was the one that added
    it. Runs in the caller's transaction, so a fulfilment that credits and marks its source does
    both or neither, and a second delivery of the same payment credits nothing.

    Both amounts are signed and independent: a grant charges nothing, a volume tier grants more than
    it charges, and a refund or a corrected credit carries both negative."""
    insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
    credited = (
        await connection.execute(
            insert(tables.balance_purchase)
            .values(
                id=uuid4(),
                workspace_id=workspace_id,
                granted_micro_usd=granted_micro_usd,
                charged_micro_usd=charged_micro_usd,
                reference=reference,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
            .on_conflict_do_nothing(
                index_elements=[
                    tables.balance_purchase.c.workspace_id,
                    tables.balance_purchase.c.reference,
                ]
            )
            .returning(tables.balance_purchase.c.id)
        )
    ).one_or_none()
    if credited is None:
        return False
    await connection.execute(
        insert(tables.workspace_balance)
        .values(
            workspace_id=workspace_id,
            balance_micro_usd=granted_micro_usd,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
        .on_conflict_do_update(
            index_elements=[tables.workspace_balance.c.workspace_id],
            set_={
                "balance_micro_usd": tables.workspace_balance.c.balance_micro_usd
                + granted_micro_usd,
                "updated_at": sa.func.now(),
            },
        )
    )
    _forget_absent_balance(workspace_id)
    return True


async def debit(connection: AsyncConnection, workspace_id: UUID, micro_usd: int) -> int:
    """Take a burn off the balance, in the transaction that recorded it.

    No floor: a turn that overshoots lands a negative balance the next credit absorbs. A check
    constraint here would instead fail the ledger write, and losing the record of money we have
    already spent is worse than carrying a negative number that says so.

    A workspace with no balance row is a no-op, which is the self-host case — and the answer is
    what was actually taken, not what was asked for, so a caller recording the deduction records
    the one that happened."""
    if micro_usd == 0:
        return 0
    taken = await connection.execute(
        sa.update(tables.workspace_balance)
        .where(tables.workspace_balance.c.workspace_id == workspace_id)
        .values(
            balance_micro_usd=tables.workspace_balance.c.balance_micro_usd - micro_usd,
            updated_at=sa.func.now(),
        )
    )
    return micro_usd if taken.rowcount == 1 else 0


async def set_reserve(
    connection: AsyncConnection, workspace_id: UUID, reserve_micro_usd: int
) -> bool:
    """Set the headroom a turn needs before it may begin, and answer whether a balance row took it.
    The reserve is what keeps a nearly-empty workspace from admitting a turn that can only spend one
    round and park, so it is set against a balance that already exists rather than creating one."""
    updated = await connection.execute(
        sa.update(tables.workspace_balance)
        .where(tables.workspace_balance.c.workspace_id == workspace_id)
        .values(reserve_micro_usd=reserve_micro_usd, updated_at=sa.func.now())
    )
    return updated.rowcount == 1


class BalanceExhausted(RuntimeError):
    """Raised where the balance refuses work the workspace asked for outside a turn's own
    admission — a spawn. The tool that asked answers the model with this text, so a fan-out that
    cannot be paid for stops at the first child rather than starting every one of them."""
