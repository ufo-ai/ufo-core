"""scheduled pause causal state"""

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("turn") as batch:
        batch.alter_column("resume_enqueued_at", new_column_name="dispatch_enqueued_at")
        batch.add_column(
            sa.Column("admission_source", sa.Text(), nullable=False, server_default="internal")
        )
        batch.create_check_constraint(
            "turn_admission_source", "admission_source in ('member', 'internal')"
        )
    op.add_column("scheduled_task", sa.Column("origin_seq", sa.Integer(), nullable=True))
    op.add_column("scheduled_task", sa.Column("resume_turn_id", sa.Uuid(), nullable=True))
    op.create_index(
        "scheduled_task_pause",
        "scheduled_task",
        ["workspace_id", "conversation_id"],
        unique=True,
        postgresql_where=sa.text("schedule = '@once'"),
        sqlite_where=sa.text("schedule = '@once'"),
    )


def downgrade() -> None:
    op.drop_index("scheduled_task_pause", table_name="scheduled_task")
    op.drop_column("scheduled_task", "resume_turn_id")
    op.drop_column("scheduled_task", "origin_seq")
    with op.batch_alter_table("turn") as batch:
        batch.drop_constraint("turn_admission_source", type_="check")
        batch.drop_column("admission_source")
        batch.alter_column("dispatch_enqueued_at", new_column_name="resume_enqueued_at")
