"""a source trigger is paused without being deleted

The outgoing image selects its own columns and writes no `paused`, so a trigger it creates during
the roll defaults to running, which is what that image means by it.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "sources_0007"
down_revision: str | None = "sources_0006"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_trigger") as batch:
        batch.add_column(
            sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("source_trigger") as batch:
        batch.drop_column("paused")
