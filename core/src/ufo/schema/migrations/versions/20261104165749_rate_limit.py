import sqlalchemy as sa
from alembic import op

revision: str = "20261104165749"
down_revision: str | None = "20261104025757"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "rate_limit",
        sa.Column("namespace", sa.Text(), nullable=False),
        sa.Column("dimension", sa.Text(), nullable=False),
        sa.Column("subject_hash", sa.LargeBinary(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("namespace", "dimension", "subject_hash", "window_start"),
    )


def downgrade() -> None:
    op.drop_table("rate_limit")
