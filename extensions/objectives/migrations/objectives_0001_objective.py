"""objective, objective_step, objective_event, objective_check"""

import sqlalchemy as sa
from alembic import op

revision: str = "objectives_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("objectives",)
depends_on: str | None = "0001"


def upgrade() -> None:
    op.create_table(
        "objective",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("directive", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "conversation_id", "name"),
    )
    op.create_index("objective_conversation", "objective", ("workspace_id", "conversation_id"))
    op.create_table(
        "objective_step",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("objective_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("accepts", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["objective_id"], ["objective.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("objective_id", "title"),
    )
    op.create_table(
        "objective_event",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("actor_turn_id", sa.Uuid(), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind in ('did', 'blocked')", name="objective_event_kind"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["step_id"], ["objective_step.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("objective_event_step", "objective_event", ("step_id", "created_at"))
    op.create_table(
        "objective_check",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.Uuid(), nullable=False),
        sa.Column("verdicts", sa.JSON(), nullable=False),
        sa.Column("actor_turn_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["step_id"], ["objective_step.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("objective_check_step", "objective_check", ("step_id", "created_at"))


def downgrade() -> None:
    op.drop_index("objective_check_step", table_name="objective_check")
    op.drop_table("objective_check")
    op.drop_index("objective_event_step", table_name="objective_event")
    op.drop_table("objective_event")
    op.drop_table("objective_step")
    op.drop_index("objective_conversation", table_name="objective")
    op.drop_table("objective")
