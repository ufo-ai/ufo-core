import sqlalchemy as sa
from alembic import op

revision: str = "20260903020306"
down_revision: str | None = "20260902042606"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("turn") as batch:
        batch.add_column(
            sa.Column("external_retry_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.create_check_constraint("turn_external_retry_count", "external_retry_count >= 0")


def downgrade() -> None:
    with op.batch_alter_table("turn") as batch:
        batch.drop_constraint("turn_external_retry_count", type_="check")
        batch.drop_column("external_retry_count")
