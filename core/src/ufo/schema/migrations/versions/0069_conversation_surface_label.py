"""carry the surface's own name for a conversation's origin"""

import sqlalchemy as sa
from alembic import op

revision: str = "0069"
down_revision: str | None = "0068"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("conversation", sa.Column("surface_label", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("conversation", "surface_label")
