"""record each admin read of another member's private transcript"""

import sqlalchemy as sa
from alembic import op

revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "transcript_access",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("reader_member_id", sa.Uuid(), nullable=False),
        sa.Column("subject_member_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.ForeignKeyConstraint(
            ["workspace_id", "conversation_id"],
            ["conversation.workspace_id", "conversation.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "reader_member_id"],
            ["member.workspace_id", "member.id"],
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "subject_member_id"],
            ["member.workspace_id", "member.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "transcript_access_conversation", "transcript_access", ["workspace_id", "conversation_id"]
    )
    op.create_index(
        "transcript_access_subject", "transcript_access", ["workspace_id", "subject_member_id"]
    )


def downgrade() -> None:
    op.drop_index("transcript_access_subject", table_name="transcript_access")
    op.drop_index("transcript_access_conversation", table_name="transcript_access")
    op.drop_table("transcript_access")
