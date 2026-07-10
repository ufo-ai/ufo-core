"""memory_item consolidation-candidate index: the hourly sweep reads only aged live facts"""

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0005"
down_revision: str | None = "memory_0004"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index(
        "memory_item_consolidate",
        "memory_item",
        ["workspace_id", "created_at"],
        postgresql_where=sa.text("item_class = 'fact' and superseded_by is null"),
        sqlite_where=sa.text("item_class = 'fact' and superseded_by is null"),
    )


def downgrade() -> None:
    op.drop_index("memory_item_consolidate", "memory_item")
