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
BALANCE_HELD_MESSAGE = (
    "This turn is parked: the workspace balance is below its reserve. It resumes when the "
    "balance is credited."
)
_no_balance: dict[UUID, float] = {}


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


async def held_below_reserve(connection: AsyncConnection, workspace_id: UUID) -> bool:
    """The gate `reserve_micro_usd` exists for, read before every model round through the spend
    decision: True when a balance row exists and the balance is under its reserve, so an operator
    setting a reserve above the balance holds the workspace's work until a credit lands. A
    workspace that was never credited answers False through the absence cache, so a self-host
    deploy pays nothing here."""
    if balance_absent(workspace_id):
        return False
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
        return False
    return row.balance_micro_usd < row.reserve_micro_usd


@dataclass(frozen=True, slots=True)
class Balance:
    """What is left, what must stay, and what was ever put in."""

    balance_micro_usd: int
    reserve_micro_usd: int
    granted_micro_usd: int
    charged_micro_usd: int
    last_purchase_at: datetime | None


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
    _no_balance.pop(workspace_id, None)
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
