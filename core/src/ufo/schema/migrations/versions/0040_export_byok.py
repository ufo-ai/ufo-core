"""export byok"""

import sqlalchemy as sa
from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "ledger_export",
        sa.Column("byok", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("ledger_export", "byok")
