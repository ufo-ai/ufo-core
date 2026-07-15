"""rendered arrival text"""

import sqlalchemy as sa
from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("inbound_message", sa.Column("rendered", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("inbound_message", "rendered")
