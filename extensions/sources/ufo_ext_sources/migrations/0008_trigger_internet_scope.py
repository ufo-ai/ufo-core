"""Source-trigger internet scope. SQL NULL inherits the agent's internet policy."""

import sqlalchemy as sa
from alembic import op

revision: str = "sources_0008"
down_revision: str | None = "sources_0007"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("source_trigger") as batch:
        batch.add_column(sa.Column("internet_access", sa.Boolean(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("source_trigger") as batch:
        batch.drop_column("internet_access")
