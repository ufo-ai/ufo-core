"""memory_item inventory index: the operator explorer reads every class, newest-first per workspace

The partial `memory_item_consolidate` index cannot serve the explorer — it is restricted to live
facts, and the explorer reads every class and keeps superseded rows — so without this index the
workspace-scoped, `created_at`-ordered read would scan the whole table on each page load. This
non-partial `(workspace_id, created_at)` index narrows to the one workspace and supplies the order,
so the bounded read the explorer's `LIMIT` asks for stays a bounded index scan."""

from alembic import op

revision: str = "memory_0006"
down_revision: str | None = "memory_0005"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index("memory_item_inventory", "memory_item", ["workspace_id", "created_at"])


def downgrade() -> None:
    op.drop_index("memory_item_inventory", "memory_item")
