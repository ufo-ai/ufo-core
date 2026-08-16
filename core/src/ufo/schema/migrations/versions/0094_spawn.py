"""agents declare an I/O contract and carry an owner"""

import sqlalchemy as sa
from alembic import op

revision: str = "0094"
down_revision: str | None = "0093"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("agent") as batch:
        batch.add_column(sa.Column("input_schema", sa.JSON(none_as_null=True), nullable=True))
        batch.add_column(sa.Column("output_schema", sa.JSON(none_as_null=True), nullable=True))
        batch.add_column(sa.Column("owner_member_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("agent") as batch:
        batch.drop_column("owner_member_id")
        batch.drop_column("output_schema")
        batch.drop_column("input_schema")
