"""ext_store"""

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "ext_store",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("extension", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("workspace_id", "extension", "key"),
    )


def downgrade() -> None:
    op.drop_table("ext_store")
