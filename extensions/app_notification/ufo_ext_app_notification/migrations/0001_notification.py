"""notification"""

import sqlalchemy as sa
from alembic import op

revision: str = "notification_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("notification",)
depends_on: str | None = "0001"


def upgrade() -> None:
    op.create_table(
        "notification",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("to_agent_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False),
        sa.Column("produced_by_agent_id", sa.Uuid(), nullable=False),
        sa.Column("produced_by_agent_name", sa.Text(), nullable=False),
        sa.Column("produced_by_turn_id", sa.Uuid(), nullable=False),
        sa.Column("produced_in_conversation_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_agent_id"], ["agent.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("occurrences >= 1", name="notification_occurrences"),
    )
    op.create_index(
        "notification_subject",
        "notification",
        ["workspace_id", "to_agent_id", "member_id", "subject"],
        unique=True,
    )
    op.create_index("notification_member", "notification", ["workspace_id", "member_id"])


def downgrade() -> None:
    op.drop_index("notification_member", "notification")
    op.drop_index("notification_subject", "notification")
    op.drop_table("notification")
