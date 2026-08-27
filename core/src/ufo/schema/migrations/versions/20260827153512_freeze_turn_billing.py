import sqlalchemy as sa
from alembic import op

revision: str = "20260827153512"
down_revision: str | None = "20260827161500"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("turn", sa.Column("billing_identity", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("turn", "billing_identity")
