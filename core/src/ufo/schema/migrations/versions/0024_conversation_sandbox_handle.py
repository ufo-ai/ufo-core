"""conversation sandbox_handle for durable per-conversation sandbox resume"""

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("conversation", sa.Column("sandbox_handle", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversation", "sandbox_handle")
