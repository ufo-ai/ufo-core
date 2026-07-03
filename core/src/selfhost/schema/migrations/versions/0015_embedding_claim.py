"""indexer embedding claim"""

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "memory_item",
        sa.Column("embedding_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "page",
        sa.Column("embedding_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("page", "embedding_claimed_at")
    op.drop_column("memory_item", "embedding_claimed_at")
