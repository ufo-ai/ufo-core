"""page record timestamps"""

import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("page") as batch:
        batch.alter_column(
            "source_created_at",
            new_column_name="record_created_at",
            existing_type=sa.Text(),
        )
        batch.alter_column(
            "source_updated_at",
            new_column_name="record_updated_at",
            existing_type=sa.Text(),
        )


def downgrade() -> None:
    with op.batch_alter_table("page") as batch:
        batch.alter_column(
            "record_created_at",
            new_column_name="source_created_at",
            existing_type=sa.Text(),
        )
        batch.alter_column(
            "record_updated_at",
            new_column_name="source_updated_at",
            existing_type=sa.Text(),
        )
