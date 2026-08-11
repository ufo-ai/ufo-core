"""a source binds to the agent that reviews it"""

import sqlalchemy as sa
from alembic import op

revision: str = "coding_0003"
down_revision: str | None = "coding_0002"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("coding_review_inbox") as batch:
        batch.drop_column("conversation_id")


def downgrade() -> None:
    with op.batch_alter_table("coding_review_inbox") as batch:
        batch.add_column(sa.Column("conversation_id", sa.Uuid(), nullable=False))
