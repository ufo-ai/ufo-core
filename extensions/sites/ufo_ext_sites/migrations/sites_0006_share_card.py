"""hosted site share card"""

import sqlalchemy as sa
from alembic import op

revision: str = "sites_0006"
down_revision: str | None = "sites_0005"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("hosted_site", sa.Column("share_card_blob_key", sa.Text(), nullable=True))
    op.add_column("hosted_site", sa.Column("share_card_hash", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("hosted_site") as batch:
        batch.drop_column("share_card_hash")
        batch.drop_column("share_card_blob_key")
