"""the turn row names what it created, ahead of any terminal"""

import sqlalchemy as sa
from alembic import op

revision: str = "0111"
down_revision: str | None = "0110"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("turn", sa.Column("created_refs", sa.JSON(none_as_null=True), nullable=True))


def downgrade() -> None:
    op.drop_column("turn", "created_refs")
