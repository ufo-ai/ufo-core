"""drop the transcript_access subject index no read uses"""

from alembic import op

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_index("transcript_access_subject", table_name="transcript_access")


def downgrade() -> None:
    op.create_index(
        "transcript_access_subject", "transcript_access", ["workspace_id", "subject_member_id"]
    )
