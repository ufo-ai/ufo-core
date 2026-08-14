"""source trigger delivery"""

import sqlalchemy as sa
from alembic import op

revision: str = "sources_0002"
down_revision: str | None = "sources_0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_trigger") as batch:
        batch.add_column(sa.Column("delivery", sa.Text(), nullable=True))
    trigger = sa.table("source_trigger", sa.column("delivery", sa.Text()))
    op.execute(sa.update(trigger).values(delivery="current"))
    with op.batch_alter_table("source_trigger") as batch:
        batch.alter_column("delivery", nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("source_trigger") as batch:
        batch.drop_column("delivery")
