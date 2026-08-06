"""the conversation that ran the review"""

import sqlalchemy as sa
from alembic import op

revision: str = "coding_0002"
down_revision: str | None = "coding_0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("coding_review_run") as batch:
        batch.add_column(sa.Column("review_conversation_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "coding_review_run_review_conversation_fkey",
            "conversation",
            ["workspace_id", "review_conversation_id"],
            ["workspace_id", "id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("coding_review_run") as batch:
        batch.drop_constraint("coding_review_run_review_conversation_fkey", type_="foreignkey")
        batch.drop_column("review_conversation_id")
