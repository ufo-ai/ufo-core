"""source refusal counter and park marks"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260824141446"
down_revision: str | None = "20260823202415"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("source") as batch:
        batch.add_column(
            sa.Column(
                "consecutive_refusals", sa.Integer, server_default=sa.text("0"), nullable=False
            )
        )
        batch.add_column(sa.Column("parked_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("parked_reason", sa.Text, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("source") as batch:
        batch.drop_column("parked_reason")
        batch.drop_column("parked_at")
        batch.drop_column("consecutive_refusals")
