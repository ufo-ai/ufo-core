"""memory_item"""

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("memory",)
depends_on: str | None = "0001"


def upgrade() -> None:
    op.create_table(
        "memory_item",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("item_class", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("embedding_digest", sa.Text(), nullable=True),
        sa.Column("embedding_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "item_class in ('fact', 'episodic', 'semantic')", name="memory_item_class"
        ),
        sa.CheckConstraint(
            "subject = 'shared' or subject like 'member:%'", name="memory_item_subject"
        ),
    )
    op.create_index("memory_item_due", "memory_item", ["embedding_digest"])


def downgrade() -> None:
    op.drop_index("memory_item_due", "memory_item")
    op.drop_table("memory_item")
