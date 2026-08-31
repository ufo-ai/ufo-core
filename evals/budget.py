from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.onboard.onboard_control import SIGNUP_GRANT_MICRO_USD, SIGNUP_RESERVE_MICRO_USD
from ufo.runtime.billing.balance import credit
from ufo.runtime.workspace import ws
from ufo.schema import tables


@dataclass(frozen=True)
class EvalRunBudget:
    run_id: UUID
    micro_usd: int

    async def install(self, workspace_id: UUID) -> None:
        """Install this run's exact allocation once on a clean eval workspace."""
        if self.micro_usd <= 0:
            raise ValueError("eval run budget must be positive")
        with ws(workspace_id):
            async with workspace_tx() as connection:
                purchases = (
                    await connection.execute(
                        sa.select(
                            tables.balance_purchase.c.reference,
                            tables.balance_purchase.c.granted_micro_usd,
                            tables.balance_purchase.c.charged_micro_usd,
                        ).where(tables.balance_purchase.c.workspace_id == workspace_id)
                    )
                ).all()
                balance = (
                    await connection.execute(
                        sa.select(
                            tables.workspace_balance.c.balance_micro_usd,
                            tables.workspace_balance.c.reserve_micro_usd,
                        ).where(tables.workspace_balance.c.workspace_id == workspace_id)
                    )
                ).one_or_none()
                allocation = [(f"eval/{self.run_id}", self.micro_usd, 0)]
                if (
                    purchases == allocation
                    and balance is not None
                    and balance.reserve_micro_usd == 0
                ):
                    return
                ledger_rows = (
                    await connection.execute(
                        sa.select(sa.func.count())
                        .select_from(tables.ledger)
                        .where(tables.ledger.c.workspace_id == workspace_id)
                    )
                ).scalar_one()
                signup = [(f"signup/{workspace_id}", SIGNUP_GRANT_MICRO_USD, 0)]
                if ledger_rows or (purchases, balance) not in (
                    ([], None),
                    (signup, (SIGNUP_GRANT_MICRO_USD, SIGNUP_RESERVE_MICRO_USD)),
                ):
                    raise RuntimeError("eval budget requires a clean workspace")
                await connection.execute(
                    sa.delete(tables.balance_purchase).where(
                        tables.balance_purchase.c.workspace_id == workspace_id
                    )
                )
                await connection.execute(
                    sa.delete(tables.workspace_balance).where(
                        tables.workspace_balance.c.workspace_id == workspace_id
                    )
                )
                await credit(
                    connection,
                    workspace_id,
                    self.micro_usd,
                    0,
                    f"eval/{self.run_id}",
                )
