"""The reports the writer read that held no change."""

import sqlalchemy as sa
from alembic import op

revision: str = "report_digest_0002"
down_revision: str | None = "report_digest_0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "report_digest_unchanged",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["turn_id"], ["turn.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workspace_id", "turn_id"),
    )


def downgrade() -> None:
    op.drop_table("report_digest_unchanged")
