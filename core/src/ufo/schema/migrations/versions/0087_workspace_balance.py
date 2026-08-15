"""workspace prepaid balance"""

import sqlalchemy as sa
from alembic import op

revision: str = "0087"
down_revision: str | None = "0086"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "balance_purchase",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), nullable=False),
        sa.Column("granted_micro_usd", sa.BigInteger, nullable=False),
        sa.Column("charged_micro_usd", sa.BigInteger, nullable=False),
        sa.Column("reference", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("granted_micro_usd <> 0", name="balance_purchase_granted"),
        sa.UniqueConstraint("workspace_id", "reference", name="balance_purchase_reference"),
    )
    op.create_index("balance_purchase_workspace", "balance_purchase", ["workspace_id"])
    op.create_table(
        "workspace_balance",
        sa.Column("workspace_id", sa.Uuid, sa.ForeignKey("workspace.id"), primary_key=True),
        sa.Column("balance_micro_usd", sa.BigInteger, nullable=False),
        sa.Column("reserve_micro_usd", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("workspace_balance")
    op.drop_index("balance_purchase_workspace", table_name="balance_purchase")
    op.drop_table("balance_purchase")
