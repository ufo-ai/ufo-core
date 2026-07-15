"""drop rendered arrival text"""

import sqlalchemy as sa
from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_column("inbound_message", "rendered")


def downgrade() -> None:
    op.add_column("inbound_message", sa.Column("rendered", sa.Text(), nullable=True))
