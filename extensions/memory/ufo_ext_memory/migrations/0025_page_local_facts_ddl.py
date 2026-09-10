"""`memory_source` goes — RFC 0047 change 4, second half, the DDL.

`memory_0024` left nothing reading or writing the table, and the serving image never touched it.
This revision is a transaction of its own, so no in-progress data row of the revision before holds
an index entry a live pod's insert waits on while the DDL waits on that pod. `body_digest` stays
nullable: a row without one is a paragraph or summary a wiki writer produced, outside the key-based
dedup, and every reader dedups by body. The downgrade recreates the table empty.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0025"
down_revision: str | None = "memory_0024"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_table("memory_source")


def downgrade() -> None:
    op.create_table(
        "memory_source",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("memory_item_id", sa.Uuid(), nullable=False),
        sa.Column("source_uid", sa.Uuid(), nullable=False),
        sa.Column("page_uid", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["memory_item_id"],
            ["memory_item.id"],
            ondelete="CASCADE",
            name="memory_source_memory_item_id_fkey",
        ),
        sa.PrimaryKeyConstraint("memory_item_id", "page_uid", name="memory_source_pkey"),
    )
