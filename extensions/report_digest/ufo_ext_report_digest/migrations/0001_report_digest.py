"""The digest entry a published report is read by."""

import sqlalchemy as sa
from alembic import op

revision: str = "report_digest_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("report_digest",)
depends_on: str | None = "0091"


def upgrade() -> None:
    op.create_table(
        "report_digest_entry",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("points", sa.JSON(), nullable=False),
        sa.Column("reader", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["turn_id"], ["turn.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id", "turn_id"),
    )


def downgrade() -> None:
    op.drop_table("report_digest_entry")
