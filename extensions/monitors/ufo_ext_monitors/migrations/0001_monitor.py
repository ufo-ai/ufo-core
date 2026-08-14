"""monitor"""

import sqlalchemy as sa
from alembic import op

revision: str = "monitors_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("monitors",)
depends_on: str | None = "0001"


def upgrade() -> None:
    op.create_table(
        "monitor",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("interval_minutes", sa.Integer(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("next_steps", sa.Text(), nullable=False),
        sa.Column("metadata", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("user_description", sa.Text(), nullable=False),
        sa.Column("created_by_member_id", sa.Uuid(), nullable=True),
        sa.Column("baseline", sa.Text(), nullable=False),
        sa.Column("probes_run", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("quiet_streak", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("failure_streak", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("skipped", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_probe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_probe_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agent.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_member_id"], ["member.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="monitor_name"),
        sa.CheckConstraint("interval_minutes >= 1", name="monitor_interval"),
    )
    op.create_index("monitor_due", "monitor", ["next_probe_at", "deadline_at"])


def downgrade() -> None:
    op.drop_index("monitor_due", "monitor")
    op.drop_table("monitor")
