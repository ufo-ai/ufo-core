import sqlalchemy as sa
from alembic import op

revision: str = "20260917214959"
down_revision: str | None = "20260914023259"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "shared_artifact",
        sa.Column("is_workspace_export", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("shared_artifact", "is_workspace_export")
