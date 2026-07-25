"""memory information time"""

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0007"
down_revision: str | None = "memory_0006"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = "0047"


def upgrade() -> None:
    with op.batch_alter_table("memory_item") as batch:
        batch.add_column(sa.Column("as_of", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("memory_item") as batch:
        batch.drop_column("as_of")
