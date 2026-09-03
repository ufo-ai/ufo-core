"""hold the commit identity of a connected account"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260903080946"
down_revision: str | None = "20260902225627"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("connection", sa.Column("commit_name", sa.Text(), nullable=True))
    op.add_column("connection", sa.Column("commit_email", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("connection", "commit_email")
    op.drop_column("connection", "commit_name")
