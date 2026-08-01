"""name the conversation whose sandbox a conversation's turns run in"""

import sqlalchemy as sa
from alembic import op

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("conversation", sa.Column("sandbox_conversation_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversation", "sandbox_conversation_id")
