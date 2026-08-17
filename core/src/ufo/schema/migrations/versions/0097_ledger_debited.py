"""what a burn actually took off the balance"""

import sqlalchemy as sa
from alembic import op

revision: str = "0097"
down_revision: str | None = "0096"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "ledger",
        sa.Column("debited_micro_usd", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("ledger", "debited_micro_usd")
