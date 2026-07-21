"""source subject and owner"""

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("source", sa.Column("subject", sa.Text, nullable=False, server_default="shared"))
    op.add_column("source", sa.Column("owner_member_id", sa.Uuid(), nullable=True))
    with op.batch_alter_table("source") as batch:
        batch.create_check_constraint(
            "source_subject", "subject = 'shared' or subject like 'member:%'"
        )
        batch.create_foreign_key(
            "source_owner_member_id_fkey", "member", ["owner_member_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("source") as batch:
        batch.drop_constraint("source_owner_member_id_fkey", type_="foreignkey")
        batch.drop_constraint("source_subject", type_="check")
    op.drop_column("source", "owner_member_id")
    op.drop_column("source", "subject")
