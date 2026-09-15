"""lifecycle_send"""

import sqlalchemy as sa
from alembic import op

revision: str = "lifecycle_email_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("lifecycle_email",)
depends_on: str | None = "0001"


def upgrade() -> None:
    op.create_table(
        "lifecycle_send",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("ses_message_id", sa.Text(), nullable=True),
        sa.Column("delivery", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("state in ('attempted', 'sent', 'failed')", name="lifecycle_send_state"),
    )
    op.create_index(
        "lifecycle_send_reason",
        "lifecycle_send",
        ["workspace_id", "member_id", "kind", "reason"],
        unique=True,
    )
    op.create_index(
        "lifecycle_send_unreported",
        "lifecycle_send",
        ["state", "created_at"],
        postgresql_where=sa.text("delivery is null"),
    )


def downgrade() -> None:
    op.drop_index("lifecycle_send_unreported", "lifecycle_send")
    op.drop_index("lifecycle_send_reason", "lifecycle_send")
    op.drop_table("lifecycle_send")
