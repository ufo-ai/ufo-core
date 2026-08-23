"""drop the daily brief's tables

The revision that closed sweep's branch left these two standing, because the migrate Job completes
before the fleet rolls and the image it replaced still read `sweep_application` from a gating
`pre_tool_use` hook. That image is gone by the time this runs, so nothing reads either table and the
rows they hold answer to no code. Sweep owned no `ext_store` rows, so these are the last of it.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260823223019"
down_revision: str | None = "20260823211339"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_table("sweep_application")
    op.drop_index("sweep_edition_pending", table_name="sweep_edition")
    op.drop_table("sweep_edition")


def downgrade() -> None:
    op.create_table(
        "sweep_edition",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.Text(), nullable=False),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
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
        sa.ForeignKeyConstraint(["turn_id"], ["turn.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("workspace_id", "member_id", "local_date"),
    )
    op.create_index("sweep_edition_pending", "sweep_edition", ("workspace_id", "status"))
    op.create_table(
        "sweep_application",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversation.workspace_id", "conversation.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "member_id"],
            ["member.workspace_id", "member.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "agent_id"],
            ["agent.workspace_id", "agent.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workspace_id", "conversation_id"),
        sa.UniqueConstraint("workspace_id", "agent_id", name="sweep_application_agent"),
    )
