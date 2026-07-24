"""add synced-page browse fields"""

import sqlalchemy as sa
from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("page") as batch:
        batch.add_column(sa.Column("stream", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("title", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("source_created_at", sa.Text(), nullable=True))
        batch.add_column(sa.Column("source_updated_at", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("page") as batch:
        batch.drop_column("source_updated_at")
        batch.drop_column("source_created_at")
        batch.drop_column("title")
        batch.drop_column("stream")
