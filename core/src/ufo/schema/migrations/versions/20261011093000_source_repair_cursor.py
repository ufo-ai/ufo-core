import sqlalchemy as sa
from alembic import op

revision: str = "20261011093000"
down_revision: str | None = "20261010161936"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("source", sa.Column("repair_cursor", sa.Text(), nullable=True))
    op.add_column(
        "source", sa.Column("repair_started_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("source", "repair_started_at")
    op.drop_column("source", "repair_cursor")
