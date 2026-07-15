"""eval_env mailbox and calendar"""

import sqlalchemy as sa
from alembic import op

revision: str = "eval_env_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("eval_env",)
depends_on: str | None = "0001"


def upgrade() -> None:
    op.create_table(
        "eval_env_email",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("folder", sa.Text(), nullable=False),
        sa.Column("sender", sa.Text(), nullable=False),
        sa.Column("recipients", sa.JSON(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("eval_env_email_workspace", "eval_env_email", ["workspace_id"])
    op.create_table(
        "eval_env_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attendees", sa.JSON(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("eval_env_event_workspace", "eval_env_event", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("eval_env_event_workspace", table_name="eval_env_event")
    op.drop_table("eval_env_event")
    op.drop_index("eval_env_email_workspace", table_name="eval_env_email")
    op.drop_table("eval_env_email")
