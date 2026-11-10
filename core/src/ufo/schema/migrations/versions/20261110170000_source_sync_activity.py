import sqlalchemy as sa
from alembic import op

revision: str = "20261110170000"
down_revision: str | None = "20261104165749"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index(
        "turn_member_activity",
        "turn",
        ["workspace_id", "created_at"],
        postgresql_where=sa.text("admission_source = 'member'"),
        sqlite_where=sa.text("admission_source = 'member'"),
    )
    op.create_index(
        "inbound_message_member_activity",
        "inbound_message",
        ["workspace_id", "created_at"],
        postgresql_where=sa.text("admission_source = 'member'"),
        sqlite_where=sa.text("admission_source = 'member'"),
    )


def downgrade() -> None:
    op.drop_index("inbound_message_member_activity", table_name="inbound_message")
    op.drop_index("turn_member_activity", table_name="turn")
