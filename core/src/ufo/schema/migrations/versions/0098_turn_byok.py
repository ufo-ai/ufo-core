"""the key that served a run attempt, frozen so a recovery bills what it re-ran under"""

import sqlalchemy as sa
from alembic import op

revision: str = "0098"
down_revision: str | None = "0097"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("turn", sa.Column("byok", sa.Boolean, nullable=True))
    op.add_column("turn", sa.Column("byok_attempt", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("turn", "byok_attempt")
    op.drop_column("turn", "byok")
