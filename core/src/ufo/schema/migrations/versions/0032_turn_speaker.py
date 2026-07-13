"""turn speaker"""

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("turn") as batch:
        batch.add_column(sa.Column("speaker_member_id", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("connect_authorization_url", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("connect_authorized_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch.create_foreign_key(
            "turn_speaker_member_id_fkey", "member", ["speaker_member_id"], ["id"]
        )
        batch.create_check_constraint(
            "turn_connect_authorization",
            "(connect_authorization_url is null) = (connect_authorized_at is null)",
        )


def downgrade() -> None:
    with op.batch_alter_table("turn") as batch:
        batch.drop_constraint("turn_connect_authorization", type_="check")
        batch.drop_constraint("turn_speaker_member_id_fkey", type_="foreignkey")
        batch.drop_column("connect_authorized_at")
        batch.drop_column("connect_authorization_url")
        batch.drop_column("speaker_member_id")
