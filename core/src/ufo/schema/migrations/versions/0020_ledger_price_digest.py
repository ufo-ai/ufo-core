"""ledger price digest audit column"""

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("ledger", sa.Column("price_digest", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ledger", "price_digest")
