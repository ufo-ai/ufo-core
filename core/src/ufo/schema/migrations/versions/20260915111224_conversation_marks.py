"""The marks a member puts on a conversation: `conversation.archived_at` and `deleted_at`, held
out of the default listing or taken off it, and a `conversation_pin` row per member, which holds
one above the rest of that member's own listing. Every conversation already landed carries none.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260915111224"
down_revision: str | None = "20260914223548"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "conversation", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "conversation", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_table(
        "conversation_pin",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
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
        sa.PrimaryKeyConstraint("workspace_id", "conversation_id", "member_id"),
    )


def downgrade() -> None:
    op.drop_table("conversation_pin")
    op.drop_column("conversation", "deleted_at")
    op.drop_column("conversation", "archived_at")
