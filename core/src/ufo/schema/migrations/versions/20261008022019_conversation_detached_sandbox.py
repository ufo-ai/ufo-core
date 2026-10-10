"""The deploy sandbox a terminal-bound conversation's speakerless turns fall back to."""

import sqlalchemy as sa
from alembic import op

revision: str = "20261008022019"
down_revision: str | None = "20260927025054"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("conversation", sa.Column("detached_sandbox_handle", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversation", "detached_sandbox_handle")
