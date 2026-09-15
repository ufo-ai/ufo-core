"""`ledger_job_day`: a workspace's turn-less spend folded to one row per closed day, dimension,
model and price digest, so a usage read scans days rather than every background model call. The
backfill folds every closed day already in the ledger, because the rows it reads are derived and
the read falls back to the ledger for any day it does not find here.
"""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20260915181110"
down_revision: str | None = "20260915111224"
branch_labels: str | None = None
depends_on: str | None = None

BACKFILL_BATCH = 1000
"""Mirrors the runtime's `JOB_DAY_SETTLE_SECONDS`, held here so this revision keeps folding the
same days when that constant moves: `created_at` is the transaction's start, so a deploy landing
just after midnight would otherwise close a day a still-open transaction commits onto."""
SETTLE_SECONDS = 900


def settled_ceiling(now: datetime) -> datetime:
    settled = (now - timedelta(seconds=SETTLE_SECONDS)).date()
    return datetime(settled.year, settled.month, settled.day, tzinfo=UTC)


def upgrade() -> None:
    op.create_table(
        "ledger_job_day",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("workspace_id", sa.Uuid, nullable=False),
        sa.Column("day", sa.Date, nullable=False),
        sa.Column("dimension", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("price_digest", sa.Text, nullable=True),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("priced_micro_usd", sa.BigInteger, nullable=False),
        sa.Column("first_used_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount > 0", name="ledger_job_day_amount"),
        sa.CheckConstraint("priced_micro_usd >= 0", name="ledger_job_day_priced"),
    )
    op.create_index("ledger_job_day_workspace", "ledger_job_day", ["workspace_id", "day"])

    ledger = sa.table(
        "ledger",
        sa.column("workspace_id", sa.Uuid),
        sa.column("turn_id", sa.Uuid),
        sa.column("dimension", sa.Text),
        sa.column("model", sa.Text),
        sa.column("price_digest", sa.Text),
        sa.column("amount", sa.BigInteger),
        sa.column("priced_micro_usd", sa.BigInteger),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    day = sa.func.date(ledger.c.created_at)
    ceiling = settled_ceiling(datetime.now(UTC))
    bind = op.get_bind()
    folded = bind.execute(
        sa.select(
            ledger.c.workspace_id,
            day.label("day"),
            ledger.c.dimension,
            ledger.c.model,
            ledger.c.price_digest,
            sa.func.sum(ledger.c.amount).label("amount"),
            sa.func.sum(ledger.c.priced_micro_usd).label("priced"),
            sa.func.min(ledger.c.created_at).label("first_used_at"),
        )
        .where(ledger.c.turn_id.is_(None), ledger.c.created_at < ceiling)
        .group_by(
            ledger.c.workspace_id,
            day,
            ledger.c.dimension,
            ledger.c.model,
            ledger.c.price_digest,
        )
    ).all()
    if not folded:
        return
    target = sa.table(
        "ledger_job_day",
        sa.column("id", sa.Uuid),
        sa.column("workspace_id", sa.Uuid),
        sa.column("day", sa.Date),
        sa.column("dimension", sa.Text),
        sa.column("model", sa.Text),
        sa.column("price_digest", sa.Text),
        sa.column("amount", sa.BigInteger),
        sa.column("priced_micro_usd", sa.BigInteger),
        sa.column("first_used_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    stamped = bind.execute(sa.select(sa.func.now())).scalar_one()
    values = [
        {
            "id": uuid4(),
            "workspace_id": row.workspace_id,
            "day": date.fromisoformat(str(row.day)),
            "dimension": row.dimension,
            "model": row.model,
            "price_digest": row.price_digest,
            "amount": int(row.amount),
            "priced_micro_usd": int(row.priced),
            "first_used_at": row.first_used_at,
            "created_at": stamped,
            "updated_at": stamped,
        }
        for row in folded
    ]
    for start in range(0, len(values), BACKFILL_BATCH):
        bind.execute(sa.insert(target), values[start : start + BACKFILL_BATCH])


def downgrade() -> None:
    op.drop_index("ledger_job_day_workspace", table_name="ledger_job_day")
    op.drop_table("ledger_job_day")
