"""hosted site deploy generation"""

import sqlalchemy as sa
from alembic import op

revision: str = "sites_0007"
down_revision: str | None = "sites_0006"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "hosted_site",
        sa.Column("deploy_generation", sa.BigInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    with op.batch_alter_table("hosted_site") as batch:
        batch.drop_column("deploy_generation")
