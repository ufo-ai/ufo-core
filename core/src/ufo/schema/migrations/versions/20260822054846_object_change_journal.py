"""object_change journal"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260822054846"
down_revision: str | None = "20260822090000"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "object_change",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("verb", sa.Text(), nullable=False),
        sa.Column("caller", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("spec_before", sa.Text(), nullable=True),
        sa.Column("spec_after", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("verb in ('create', 'update', 'delete')", name="object_change_verb"),
    )
    op.create_index("object_change_workspace", "object_change", ["workspace_id", "created_at"])


def downgrade() -> None:
    op.drop_index("object_change_workspace", table_name="object_change")
    op.drop_table("object_change")
