"""The member a turn acts for, beside the member speaking in it."""

import sqlalchemy as sa
from alembic import op

revision: str = "20260916050538"
down_revision: str | None = "20260916035845"
branch_labels: str | None = None
depends_on: str | None = None

TABLES = ("turn", "inbound_message")


def upgrade() -> None:
    for table in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("member_id", sa.Uuid(), nullable=True))
            batch.create_foreign_key(f"{table}_member_id_fkey", "member", ["member_id"], ["id"])
        op.execute(
            sa.text(
                f"update {table} set member_id = speaker_member_id "
                "where speaker_member_id is not null"
            )
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"{table}_member_id_fkey", type_="foreignkey")
            batch.drop_column("member_id")
