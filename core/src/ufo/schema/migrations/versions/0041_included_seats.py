"""included seats"""

import sqlalchemy as sa
from alembic import op

revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("workspace") as batch:
        batch.add_column(sa.Column("included_seats", sa.Integer(), nullable=True))
        batch.create_check_constraint(
            "workspace_included_seats", "included_seats is null or included_seats > 0"
        )


def downgrade() -> None:
    with op.batch_alter_table("workspace") as batch:
        batch.drop_constraint("workspace_included_seats", type_="check")
        batch.drop_column("included_seats")
