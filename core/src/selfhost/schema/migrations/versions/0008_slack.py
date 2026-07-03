"""slack"""

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("turn", sa.Column("idempotency_key", sa.Text(), nullable=True))
    op.create_index(
        "turn_idempotency_key", "turn", ["workspace_id", "idempotency_key"], unique=True
    )


def downgrade() -> None:
    op.drop_index("turn_idempotency_key", "turn")
    op.drop_column("turn", "idempotency_key")
