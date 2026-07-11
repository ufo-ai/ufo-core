"""runtime_instance seats for the shared fleet: a fleet process holds no workspace, so its row
carries a null workspace_id — the executor-recovery sweep reads liveness across all seats."""

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("runtime_instance") as batch:
        batch.alter_column("workspace_id", existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    with op.batch_alter_table("runtime_instance") as batch:
        batch.alter_column("workspace_id", existing_type=sa.Uuid(), nullable=False)
