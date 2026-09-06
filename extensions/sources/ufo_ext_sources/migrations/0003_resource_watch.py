"""source resource watch"""

import sqlalchemy as sa
from alembic import op

revision: str = "sources_0003"
down_revision: str | None = "sources_0002"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """A watch on one resource of a source is a second row over the (conversation, binding) pair
    `source_trigger` keys, so it gets a table of its own rather than a column there. The release
    this one replaces inserts into `source_trigger` with `ON CONFLICT (workspace_id,
    conversation_id, binding)`, and the outgoing pods serve until the new ones are ready: widening
    that key would leave their statements with no unique index to infer and fail every trigger they
    write. `source_trigger` therefore keeps the shape they know, and the outgoing image never reads
    the new table, so a watch it cannot narrow wakes nobody through the roll."""
    op.create_table(
        "source_resource_watch",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("binding", sa.Text(), nullable=False),
        sa.Column("resource", sa.Text(), nullable=False),
        sa.Column("delivery", sa.Text(), nullable=False),
        sa.Column("created_by_member_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("resource <> ''", name="source_resource_watch_narrowed"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["agent.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_member_id"], ["member.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "conversation_id",
            "binding",
            "resource",
            name="source_resource_watch_resource",
        ),
    )
    op.create_index(
        "source_resource_watch_binding", "source_resource_watch", ["workspace_id", "binding"]
    )


def downgrade() -> None:
    op.drop_index("source_resource_watch_binding", "source_resource_watch")
    op.drop_table("source_resource_watch")
