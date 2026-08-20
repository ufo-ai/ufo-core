"""hosted site preview blob"""

import sqlalchemy as sa
from alembic import op

revision: str = "sites_0005"
down_revision: str | None = "sites_0004"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("hosted_site", sa.Column("preview_blob_key", sa.Text(), nullable=True))
    op.add_column("hosted_site", sa.Column("preview_size_bytes", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("hosted_site") as batch:
        batch.drop_column("preview_size_bytes")
        batch.drop_column("preview_blob_key")
