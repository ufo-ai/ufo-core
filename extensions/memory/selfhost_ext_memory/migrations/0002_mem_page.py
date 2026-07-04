"""mem_page mirror"""

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0002"
down_revision: str | None = "memory_0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "mem_page",
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("page_id"),
    )


def downgrade() -> None:
    op.drop_table("mem_page")
