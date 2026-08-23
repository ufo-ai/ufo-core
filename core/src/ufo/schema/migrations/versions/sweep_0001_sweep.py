"""Daily brief editions and their deferred commit state."""

import sqlalchemy as sa
from alembic import op

revision: str = "sweep_0001"
down_revision: str | None = "0091"
branch_labels: tuple[str, ...] | None = ("sweep",)
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "sweep_edition",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_cursor", sa.DateTime(timezone=True), nullable=True),
        sa.Column("candidate_input_keys", sa.JSON(), nullable=True),
        sa.Column("candidate_finding_keys", sa.JSON(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status in ('pending', 'failed', 'completed')", name="sweep_edition_status"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["turn_id"], ["turn.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("workspace_id", "member_id", "local_date"),
    )
    op.create_index("sweep_edition_pending", "sweep_edition", ("workspace_id", "status"))


def downgrade() -> None:
    op.drop_table("sweep_edition")
