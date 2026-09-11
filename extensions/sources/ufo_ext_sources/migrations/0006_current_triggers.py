"""each source trigger wakes its conversation"""

import sqlalchemy as sa
from alembic import op

revision: str = "sources_0006"
down_revision: str | None = "sources_0005"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    trigger = sa.table("source_trigger", sa.column("delivery", sa.Text()))
    op.execute(sa.update(trigger).values(delivery="current"))
    with op.batch_alter_table("source_trigger") as batch:
        batch.alter_column(
            "delivery", existing_type=sa.Text(), nullable=False, server_default="current"
        )


def downgrade() -> None:
    with op.batch_alter_table("source_trigger") as batch:
        batch.alter_column("delivery", existing_type=sa.Text(), nullable=False, server_default=None)
