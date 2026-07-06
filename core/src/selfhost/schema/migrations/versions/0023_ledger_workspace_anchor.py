"""ledger turn_id nullable for workspace-anchored spend"""

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("ledger") as batch:
        batch.alter_column("turn_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("ledger") as batch:
        batch.alter_column("turn_id", existing_type=sa.Uuid(), nullable=False)
