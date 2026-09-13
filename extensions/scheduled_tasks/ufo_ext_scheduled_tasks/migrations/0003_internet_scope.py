"""Scheduled-task internet scope."""

import sqlalchemy as sa
from alembic import op

revision: str = "scheduled_tasks_0003"
down_revision: str | None = "scheduled_tasks_0002"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("scheduled_task") as batch:
        batch.add_column(
            sa.Column("internet_access", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("scheduled_task") as batch:
        batch.drop_column("internet_access")
