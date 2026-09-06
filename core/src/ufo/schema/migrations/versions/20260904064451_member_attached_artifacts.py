"""a shared file says who put it in the turn

A file reaches a conversation two ways: the agent shares one it produced, or the member attaches one
to the words that open the turn. Both are the conversation's files and both belong in the shelf, but
a transcript draws each under the one who put it there, so the distinction is the row's to carry
rather than a reader's to infer from the sentence that named it.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260904064451"
down_revision: str | None = "20260904064659"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("shared_artifact") as batch:
        batch.add_column(
            sa.Column("attached_by_member", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("shared_artifact") as batch:
        batch.drop_column("attached_by_member")
