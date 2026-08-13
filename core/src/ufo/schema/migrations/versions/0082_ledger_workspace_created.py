"""ledger workspace history"""

from alembic import op

revision: str = "0082"
down_revision: str | None = "0081"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index("ledger_workspace_created", "ledger", ["workspace_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ledger_workspace_created", table_name="ledger")
