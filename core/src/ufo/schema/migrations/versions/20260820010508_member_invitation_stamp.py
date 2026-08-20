"""the member row names when they were added and by whom"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260820010508"
down_revision: str | None = "20260819191916"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("member") as batch:
        batch.add_column(sa.Column("invited_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("invited_by", sa.Uuid(), nullable=True))
        batch.create_foreign_key("member_invited_by_fkey", "member", ["invited_by"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("member") as batch:
        batch.drop_constraint("member_invited_by_fkey", type_="foreignkey")
        batch.drop_column("invited_by")
        batch.drop_column("invited_at")
