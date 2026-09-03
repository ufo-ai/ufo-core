import sqlalchemy as sa
from alembic import op

revision: str = "20260902042606"
down_revision: str | None = "20260903080946"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("turn") as batch:
        batch.add_column(sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "turn_retry_at",
        "turn",
        ["retry_at"],
        postgresql_where=sa.text("status = 'parked' and retry_at is not null"),
        sqlite_where=sa.text("status = 'parked' and retry_at is not null"),
    )


def downgrade() -> None:
    op.drop_index("turn_retry_at", table_name="turn")
    with op.batch_alter_table("turn") as batch:
        batch.drop_column("retry_at")
