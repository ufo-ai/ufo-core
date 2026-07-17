"""ledger_export"""

import sqlalchemy as sa
from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "ledger_export",
        sa.Column("consumer", sa.Text(), nullable=False),
        sa.Column("ledger_id", sa.Uuid(), nullable=False),
        sa.Column("from_amount", sa.BigInteger(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("to_amount", sa.BigInteger(), nullable=False),
        sa.Column("from_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("to_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["ledger_id"], ["ledger.id"]),
        sa.PrimaryKeyConstraint("consumer", "ledger_id", "from_amount"),
        sa.CheckConstraint("to_amount > from_amount", name="ledger_export_delta"),
    )
    op.create_index(
        "ledger_export_pending",
        "ledger_export",
        ["consumer", "workspace_id"],
        postgresql_where=sa.text("acked_at is null"),
        sqlite_where=sa.text("acked_at is null"),
    )


def downgrade() -> None:
    op.drop_index("ledger_export_pending", table_name="ledger_export")
    op.drop_table("ledger_export")
