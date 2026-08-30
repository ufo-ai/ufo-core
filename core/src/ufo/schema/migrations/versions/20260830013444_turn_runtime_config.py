import sqlalchemy as sa
from alembic import op

revision: str = "20260830013444"
down_revision: str | None = "20260828010853"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("turn", sa.Column("runtime_config", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("turn", "runtime_config")
