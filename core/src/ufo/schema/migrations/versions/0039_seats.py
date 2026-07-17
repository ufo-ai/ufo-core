"""seats"""

import sqlalchemy as sa
from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("member", sa.Column("seated_at", sa.DateTime(timezone=True), nullable=True))
    with op.batch_alter_table("workspace") as batch:
        batch.add_column(sa.Column("seat_limit", sa.Integer(), nullable=True))
        batch.create_check_constraint(
            "workspace_seat_limit", "seat_limit is null or seat_limit > 0"
        )
    op.execute("update member set seated_at = created_at")


def downgrade() -> None:
    op.drop_column("member", "seated_at")
    with op.batch_alter_table("workspace") as batch:
        batch.drop_constraint("workspace_seat_limit", type_="check")
        batch.drop_column("seat_limit")
