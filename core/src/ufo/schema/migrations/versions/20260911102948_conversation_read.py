"""`conversation_read`: where one member's reading of one conversation has reached.

The rail draws a thread as unread when its newest turn stands past this moment, so a workspace
that has never had the column reads every conversation as unread until the member opens it.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260911102948"
down_revision: str | None = "20260910224332"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_read",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
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
    op.drop_table("conversation_read")
