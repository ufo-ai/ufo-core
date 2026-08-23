"""hosted site source manifest"""

import sqlalchemy as sa
from alembic import op

revision: str = "sites_0008"
down_revision: str | None = "sites_0007"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("hosted_site", sa.Column("source_manifest", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("hosted_site") as batch:
        batch.drop_column("source_manifest")
