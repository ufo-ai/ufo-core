"""pause"""

import sqlalchemy as sa
from alembic import op

revision: str = "scheduled_tasks_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("scheduled_tasks",)
depends_on: str | None = "0001"


def upgrade() -> None:
    op.create_table(
        "pause",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("resume_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origin_seq", sa.Integer(), nullable=False),
        sa.Column("origin_arrival_seq", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("user_description", sa.Text(), nullable=False),
        sa.Column("created_by_member_id", sa.Uuid(), nullable=True),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agent.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_member_id"], ["member.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "conversation_id", name="pause_conversation"),
    )
    op.create_index("pause_due", "pause", ["resume_at"])


def downgrade() -> None:
    op.drop_index("pause_due", "pause")
    op.drop_table("pause")
