"""mark the steps an objective may run at once

A plan states its order, but order alone cannot say whether two steps must be taken in sequence or
merely happen to be listed one after the other. `independent` is the plan's own answer, so the
engine decides the fan-out shape once from data rather than the model re-deciding it — and paying
for it — on every wake.

Existing rows declare no independence, so the column lands false.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "objectives_0002"
down_revision: str | None = "objectives_0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "objective_step",
        sa.Column("independent", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("objective_step", "independent")
