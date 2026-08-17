"""what a workspace refills itself with, and the line that triggers it"""

import sqlalchemy as sa
from alembic import op

revision: str = "0099"
down_revision: str | None = "0098"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "workspace_balance", sa.Column("auto_topup_micro_usd", sa.BigInteger, nullable=True)
    )
    op.add_column(
        "workspace_balance",
        sa.Column("auto_topup_threshold_micro_usd", sa.BigInteger, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("workspace_balance", "auto_topup_threshold_micro_usd")
    op.drop_column("workspace_balance", "auto_topup_micro_usd")
