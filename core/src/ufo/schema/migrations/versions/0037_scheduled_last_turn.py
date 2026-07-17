"""scheduled task last fire turn"""

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("scheduled_task", sa.Column("last_turn_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("scheduled_task", "last_turn_id")
