"""turn traceparent: a subagent's turn joins the trace of the turn that spawned it"""

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("turn", sa.Column("traceparent", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("turn", "traceparent")
