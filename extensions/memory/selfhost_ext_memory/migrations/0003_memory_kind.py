"""memory_item decay inputs: memory_kind + confidence"""

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0003"
down_revision: str | None = "memory_0002"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "memory_item",
        sa.Column("memory_kind", sa.Text(), nullable=False, server_default="fact"),
    )
    op.add_column(
        "memory_item",
        sa.Column("confidence", sa.Integer(), nullable=False, server_default="5"),
    )


def downgrade() -> None:
    op.drop_column("memory_item", "confidence")
    op.drop_column("memory_item", "memory_kind")
