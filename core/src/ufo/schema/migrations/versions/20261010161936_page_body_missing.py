import sqlalchemy as sa
from alembic import op

revision: str = "20261010161936"
down_revision: str | None = "20260927025054"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "page",
        sa.Column("body_missing", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("page", "body_missing")
