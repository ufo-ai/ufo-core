"""Store the latest valid timezone observed for a member."""

import sqlalchemy as sa
from alembic import op

revision: str = "0091"
down_revision: str | None = "0090"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("member", sa.Column("timezone", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("member") as batch:
        batch.drop_column("timezone")
